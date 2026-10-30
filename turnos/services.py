"""Lógica de negocio de MotoTurno.

Reglas de asignación de turnos (documentadas en el informe, sección 4.2):

R1. Orden de atención: estrictamente por hora de ingreso (primero en llegar, primero en atenderse).
R2. Capacidad: min(trabajadores disponibles, puestos de lavado). Un trabajador atiende una moto a la vez.
R3. Asignación del trabajador: al iniciar un servicio se elige, entre los trabajadores disponibles y
    libres, el que tenga menos servicios finalizados en el día (reparto equitativo); si hay empate, el
    orden alfabético.
R4. Estimación: se simula la cola; cada moto pendiente ocupa el primer puesto que se desocupe durante la
    duración de su servicio. La espera es la diferencia entre el inicio simulado y la hora actual.
R5. Retrasos: si un servicio en proceso supera su duración estimada, se asume que termina "ahora" y todas
    las estimaciones se recalculan con la hora actual. El cálculo se repite en cada consulta.
R6. Sin capacidad (ningún trabajador disponible): no se calcula la espera y el sistema lo advierte.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
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


def _trabajadores_en_pool(config):
    """Trabajadores que pueden recibir motos, limitados por los puestos de lavado (R2)."""
    disponibles = list(Trabajador.objects.filter(disponible=True))
    ocupados_ids = set(
        Orden.objects.filter(estado=Orden.Estado.EN_PROCESO).values_list("trabajador_id", flat=True)
    )
    disponibles.sort(key=lambda t: (t.id not in ocupados_ids, t.nombre))
    return disponibles[: config.puestos_lavado]


def calcular_cola(ahora=None):
    """Devuelve la cola de pendientes con su inicio, fin y espera estimados (R1, R4, R5, R6)."""
    ahora = ahora or timezone.now()
    config = Configuracion.actual()
    pool = _trabajadores_en_pool(config)
    pendientes = list(
        Orden.objects.filter(estado=Orden.Estado.PENDIENTE).select_related("servicio").order_by("hora_ingreso", "id")
    )
    en_proceso = {
        o.trabajador_id: o
        for o in Orden.objects.filter(estado=Orden.Estado.EN_PROCESO).select_related("servicio")
    }

    libres_en = []
    for t in pool:
        o = en_proceso.get(t.id)
        if o and o.fin_programado:
            libres_en.append(max(ahora, o.fin_programado))  # R5
        else:
            libres_en.append(ahora)

    filas = []
    hay_libre_ahora = any(f <= ahora for f in libres_en)
    for i, orden in enumerate(pendientes, start=1):
        if not libres_en:  # R6
            filas.append(FilaCola(orden, i, None, None, None))
            continue
        k = min(range(len(libres_en)), key=lambda j: libres_en[j])
        inicio = libres_en[k]
        fin = inicio + timedelta(minutes=orden.servicio.duracion_min)
        libres_en[k] = fin
        espera = max(0, math.ceil((inicio - ahora).total_seconds() / 60))
        filas.append(FilaCola(orden, i, inicio, fin, espera, puede_iniciar=(i == 1 and hay_libre_ahora)))
    return filas


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


@transaction.atomic
def iniciar_orden(orden: Orden, ahora=None):
    ahora = ahora or timezone.now()
    if orden.estado != Orden.Estado.PENDIENTE:
        raise ValidationError("Solo se puede iniciar un servicio pendiente.")
    primera = Orden.objects.filter(estado=Orden.Estado.PENDIENTE).order_by("hora_ingreso", "id").first()
    if primera.pk != orden.pk:
        raise ValidationError("Se debe atender primero la moto que llegó antes (R1).")
    config = Configuracion.actual()
    pool = _trabajadores_en_pool(config)
    ocupados = set(Orden.objects.filter(estado=Orden.Estado.EN_PROCESO).values_list("trabajador_id", flat=True))
    libres = [t for t in pool if t.id not in ocupados]
    if not libres:
        raise ValidationError("No hay trabajadores libres o puestos de lavado disponibles.")
    hoy = timezone.localdate(ahora)
    hechos = dict(
        Orden.objects.filter(estado=Orden.Estado.FINALIZADO, fin_real__date=hoy)
        .values_list("trabajador_id").annotate(n=Count("id"))
    )
    libres.sort(key=lambda t: (hechos.get(t.id, 0), t.nombre))  # R3
    orden.trabajador = libres[0]
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
    """Servicios en proceso con avance y minutos de retraso (R5)."""
    ahora = ahora or timezone.now()
    res = []
    for o in Orden.objects.filter(estado=Orden.Estado.EN_PROCESO).select_related("servicio", "trabajador"):
        dur = o.servicio.duracion_min
        transcurrido = (ahora - o.inicio_real).total_seconds() / 60
        res.append({
            "orden": o,
            "avance": min(100, round(transcurrido / dur * 100)),
            "restante": max(0, math.ceil(dur - transcurrido)),
            "retraso": max(0, math.floor(transcurrido - dur)),
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
