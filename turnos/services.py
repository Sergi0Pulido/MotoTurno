"""Lógica de negocio de MotoTurno.

Reglas de asignación de turnos (documentadas en el informe, Tabla 7):

R1. Orden de llegada: las motos se atienden estrictamente por hora de ingreso. No se puede iniciar una
    moto si otra que llegó antes sigue esperando.
R2. Capacidad operativa: el menor valor entre trabajadores disponibles y puestos de lavado. Un trabajador
    atiende una moto a la vez y un puesto recibe una moto a la vez.
R3. Asignación de trabajador: entre los trabajadores disponibles y libres se elige el que lleva menos
    servicios finalizados en el día; si hay empate, el que lleva más tiempo sin trabajar.
R4. Asignación de puesto: se asigna el puesto libre con el número más bajo.
R5. Estimación de espera: se simula la cola. Cada moto pendiente toma el primer trabajador y el primer
    puesto que se liberen; su espera es el tiempo que falta para ese momento.
R6. Retrasos: si un servicio en proceso supera su duración estimada, se marca como retrasado y se asume
    que le faltan `margen_retraso_min` minutos más (5 por defecto). Toda la cola se corre.
R7. Recálculo: las estimaciones se calculan de nuevo en cada consulta (después de cada registro, inicio,
    finalización o cambio de disponibilidad o de duración). El panel se actualiza cada 30 segundos.
R8. Salida de un trabajador: si se marca como no disponible mientras atiende una moto, termina ese
    servicio (su puesto sigue ocupado hasta entonces) y después deja de contar en la capacidad.
    Sin trabajadores disponibles no se calcula la espera y el sistema lo advierte.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import Configuracion, Orden, Servicio, Trabajador, normalizar_placa


@dataclass
class FilaCola:
    orden: Orden
    posicion: int
    inicio_estimado: datetime | None
    fin_estimado: datetime | None
    espera_min: int | None
    puede_iniciar: bool = False


# ------------------------------------------------------------------ apoyo
def _en_proceso():
    return list(
        Orden.objects.filter(estado=Orden.Estado.EN_PROCESO).select_related("servicio", "trabajador")
    )


def fin_estimado(orden: Orden, ahora: datetime, config: Configuracion) -> datetime:
    """Fin estimado de un servicio en proceso; si ya se pasó de su duración aplica el margen (R6)."""
    fin = orden.inicio_real + timedelta(minutes=orden.servicio.duracion_min)
    if ahora > fin:
        return ahora + timedelta(minutes=config.margen_retraso_min)
    return fin


def capacidad(config: Configuracion | None = None) -> int:
    """R2."""
    config = config or Configuracion.actual()
    disponibles = Trabajador.objects.filter(disponible=True).count()
    return max(0, min(disponibles, config.puestos_lavado))


def trabajadores_libres():
    ocupados = Orden.objects.filter(estado=Orden.Estado.EN_PROCESO).values_list("trabajador_id", flat=True)
    return list(Trabajador.objects.filter(disponible=True).exclude(id__in=list(ocupados)))


def puestos_libres(config: Configuracion | None = None):
    """Puestos sin moto, de menor a mayor (R4)."""
    config = config or Configuracion.actual()
    en_proceso = Orden.objects.filter(estado=Orden.Estado.EN_PROCESO)
    if en_proceso.count() >= config.puestos_lavado:
        return []
    usados = set(en_proceso.exclude(puesto__isnull=True).values_list("puesto", flat=True))
    return [p for p in range(1, config.puestos_lavado + 1) if p not in usados]


def motivo_bloqueo(config: Configuracion | None = None) -> str | None:
    """Por qué no se puede iniciar la siguiente moto en este momento (None si sí se puede)."""
    config = config or Configuracion.actual()
    if not Orden.objects.filter(estado=Orden.Estado.PENDIENTE).exists():
        return "No hay motos en espera."
    if not trabajadores_libres():
        return "No hay trabajadores disponibles libres en este momento."
    if not puestos_libres(config):
        return "Todos los puestos de lavado están ocupados."
    return None


# ------------------------------------------------------------------ R5, R6, R8
def calcular_cola(ahora=None):
    """Devuelve la cola de pendientes con su inicio, fin y espera estimados."""
    ahora = ahora or timezone.now()
    config = Configuracion.actual()
    pendientes = list(
        Orden.objects.filter(estado=Orden.Estado.PENDIENTE).select_related("servicio").order_by("hora_ingreso", "id")
    )
    if capacidad(config) == 0:  # sin trabajadores disponibles no se estima
        return [FilaCola(o, i, None, None, None) for i, o in enumerate(pendientes, start=1)]

    en_proceso = _en_proceso()
    fin_de = {o.pk: fin_estimado(o, ahora, config) for o in en_proceso}

    # Cuándo queda libre cada trabajador disponible.
    ocupado_hasta = {o.trabajador_id: fin_de[o.pk] for o in en_proceso}
    trabajadores = [ocupado_hasta.get(t.id, ahora) for t in Trabajador.objects.filter(disponible=True)]

    # Cuándo queda libre cada puesto: los ocupados al terminar su moto (aunque el trabajador ya
    # no esté disponible, R8) y los demás de inmediato.
    puestos = sorted(fin_de.values())
    if len(puestos) > config.puestos_lavado:
        puestos = puestos[len(puestos) - config.puestos_lavado:]
    puestos += [ahora] * (config.puestos_lavado - len(puestos))

    se_puede_iniciar = motivo_bloqueo(config) is None
    filas = []
    for i, orden in enumerate(pendientes, start=1):
        trabajadores.sort()
        puestos.sort()
        inicio = max(trabajadores[0], puestos[0], ahora)
        fin = inicio + timedelta(minutes=orden.servicio.duracion_min)
        trabajadores[0] = fin
        puestos[0] = fin
        espera = max(0, math.ceil((inicio - ahora).total_seconds() / 60))
        filas.append(FilaCola(orden, i, inicio, fin, espera, puede_iniciar=(i == 1 and se_puede_iniciar)))
    return filas


# ------------------------------------------------------------------ operaciones
@transaction.atomic
def registrar_orden(orden: Orden):
    """Guarda la moto y deja registrada la espera estimada del momento (para medir la precisión luego)."""
    orden.placa = normalizar_placa(orden.placa)
    orden.full_clean(exclude=["numero_turno"])
    if not orden.servicio.activo:
        raise ValidationError({"servicio": "El servicio seleccionado está inactivo."})
    if Orden.objects.filter(placa=orden.placa, estado__in=[Orden.Estado.PENDIENTE, Orden.Estado.EN_PROCESO]).exists():
        raise ValidationError({"placa": "Esta moto ya tiene un servicio pendiente o en proceso."})
    orden.estado = Orden.Estado.PENDIENTE
    orden.save()
    fila = next((f for f in calcular_cola() if f.orden.pk == orden.pk), None)
    if fila and fila.espera_min is not None:
        orden.espera_estimada_min = fila.espera_min
        orden.save(update_fields=["espera_estimada_min"])
    return orden


def elegir_trabajador(ahora=None):
    """R3: menos servicios finalizados hoy; si empatan, el que lleva más tiempo sin trabajar."""
    ahora = ahora or timezone.now()
    libres = trabajadores_libres()
    if not libres:
        return None
    hoy = timezone.localdate(ahora)
    hechos = dict(
        Orden.objects.filter(estado=Orden.Estado.FINALIZADO, fin_real__date=hoy)
        .values_list("trabajador_id").annotate(n=Count("id"))
    )
    ultima = dict(
        Orden.objects.filter(fin_real__isnull=False).values_list("trabajador_id").annotate(m=Max("fin_real"))
    )
    nunca = datetime.min.replace(tzinfo=timezone.get_current_timezone())
    libres.sort(key=lambda t: (hechos.get(t.id, 0), ultima.get(t.id) or nunca, t.nombre))
    return libres[0]


@transaction.atomic
def iniciar_orden(orden: Orden, ahora=None):
    """R1 + R3 + R4."""
    ahora = ahora or timezone.now()
    if orden.estado != Orden.Estado.PENDIENTE:
        raise ValidationError("Solo se puede iniciar un servicio pendiente.")
    primera = Orden.objects.filter(estado=Orden.Estado.PENDIENTE).order_by("hora_ingreso", "id").first()
    if primera.pk != orden.pk:
        raise ValidationError("Se debe atender primero la moto que llegó antes (R1).")
    config = Configuracion.actual()
    motivo = motivo_bloqueo(config)
    if motivo:
        raise ValidationError(motivo)
    orden.trabajador = elegir_trabajador(ahora)
    orden.puesto = puestos_libres(config)[0]
    orden.estado = Orden.Estado.EN_PROCESO
    orden.inicio_real = ahora
    orden.save()
    return orden


@transaction.atomic
def finalizar_orden(orden: Orden, ahora=None):
    if orden.estado != Orden.Estado.EN_PROCESO:
        raise ValidationError("Solo se puede finalizar un servicio que está en proceso.")
    orden.estado = Orden.Estado.FINALIZADO
    orden.fin_real = ahora or timezone.now()
    orden.save()
    return orden


@transaction.atomic
def cancelar_orden(orden: Orden):
    if not orden.activa:
        raise ValidationError("Solo se puede cancelar un servicio pendiente o en proceso.")
    orden.estado = Orden.Estado.CANCELADO
    orden.save()
    return orden


def en_proceso_con_estado(ahora=None):
    """Servicios en proceso con avance, fin estimado y minutos completos de retraso (R6)."""
    ahora = ahora or timezone.now()
    config = Configuracion.actual()
    res = []
    for o in sorted(_en_proceso(), key=lambda x: (x.puesto or 0, x.inicio_real)):
        dur = o.servicio.duracion_min
        transcurrido = (ahora - o.inicio_real).total_seconds() / 60
        retrasado = transcurrido > dur
        res.append({
            "orden": o,
            "avance": min(100, round(transcurrido / dur * 100)),
            "restante": max(0, math.ceil(dur - transcurrido)),
            # Minutos completos de retraso, con mínimo 1 para que el estado siempre sea visible.
            "retraso": max(1, math.floor(transcurrido - dur)) if retrasado else 0,
            "fin_estimado": fin_estimado(o, ahora, config),
        })
    return res


def resumen_reportes(ahora=None):
    ahora = ahora or timezone.now()
    config = Configuracion.actual()
    fin = Orden.objects.filter(estado=Orden.Estado.FINALIZADO)
    con_espera = [o for o in fin if o.espera_real_min is not None]
    espera_prom = round(sum(o.espera_real_min for o in con_espera) / len(con_espera), 1) if con_espera else None

    medibles = [o for o in con_espera if o.espera_estimada_min is not None]
    dentro = [o for o in medibles if abs(o.espera_real_min - o.espera_estimada_min) <= config.tolerancia_min]
    pct_precision = round(len(dentro) / len(medibles) * 100) if medibles else None
    error_prom = (
        round(sum(abs(o.espera_real_min - o.espera_estimada_min) for o in medibles) / len(medibles), 1)
        if medibles else None
    )

    reduccion = None
    if config.linea_base_espera_min and espera_prom is not None:
        reduccion = round((config.linea_base_espera_min - espera_prom) / config.linea_base_espera_min * 100)

    por_servicio = []
    for s in Servicio.objects.all():
        os_ = [o for o in fin if o.servicio_id == s.id and o.duracion_real_min is not None]
        por_servicio.append({
            "servicio": s,
            "atendidas": len(os_),
            "duracion_real": round(sum(o.duracion_real_min for o in os_) / len(os_), 1) if os_ else None,
        })

    por_trabajador = (
        Trabajador.objects.annotate(n=Count("ordenes", filter=Q(ordenes__estado=Orden.Estado.FINALIZADO)))
        .order_by("-n", "nombre")
    )
    conteo_estados = {e.label: Orden.objects.filter(estado=e.value).count() for e in Orden.Estado}
    ingresos = sum(o.servicio.precio for o in fin)
    return {
        "config": config,
        "espera_prom": espera_prom,
        "pct_precision": pct_precision,
        "error_prom": error_prom,
        "reduccion": reduccion,
        "por_servicio": por_servicio,
        "por_trabajador": por_trabajador,
        "conteo_estados": conteo_estados,
        "total_finalizadas": fin.count(),
        "ingresos": ingresos,
        "n_medibles": len(medibles),
    }
