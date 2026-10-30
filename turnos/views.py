from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import ConfiguracionForm, ConsultaForm, OrdenForm, ServicioForm, TrabajadorForm
from .models import Configuracion, Orden, Servicio, Trabajador, normalizar_placa


def _errores(request, exc):
    if hasattr(exc, "message_dict"):
        for lista in exc.message_dict.values():
            for m in lista:
                messages.error(request, m)
    else:
        for m in exc.messages:
            messages.error(request, m)


@login_required
def panel(request):
    ahora = timezone.now()
    cola = services.calcular_cola(ahora)
    en_proceso = services.en_proceso_con_estado(ahora)
    config = Configuracion.actual()
    hoy = timezone.localdate(ahora)
    disponibles = Trabajador.objects.filter(disponible=True).count()
    esperas = [f.espera_min for f in cola if f.espera_min is not None]
    ctx = {
        "cola": cola,
        "en_proceso": en_proceso,
        "capacidad": services.capacidad(config),
        "disponibles": disponibles,
        "puestos": config.puestos_lavado,
        "bloqueo": services.motivo_bloqueo(config),
        "margen": config.margen_retraso_min,
        "finalizadas_hoy": Orden.objects.filter(estado=Orden.Estado.FINALIZADO, fin_real__date=hoy).count(),
        "espera_prom": round(sum(esperas) / len(esperas)) if esperas else 0,
        "espera_max": max(esperas) if esperas else 0,
        "ahora": ahora,
        "refresco": True,
    }
    return render(request, "turnos/panel.html", ctx)


@login_required
def registrar(request):
    form = OrdenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        orden = form.save(commit=False)
        try:
            services.registrar_orden(orden)
        except ValidationError as exc:
            _errores(request, exc)
        else:
            messages.success(request, f"Moto {orden.placa} registrada con el turno {orden.numero_turno}.")
            return redirect("turno", pk=orden.pk)
    return render(request, "turnos/registrar.html", {"form": form})


@login_required
def turno(request, pk):
    orden = get_object_or_404(Orden, pk=pk)
    fila = next((f for f in services.calcular_cola() if f.orden.pk == orden.pk), None)
    return render(request, "turnos/turno.html", {"orden": orden, "fila": fila})


@login_required
@require_POST
def accion_orden(request, pk, accion):
    orden = get_object_or_404(Orden, pk=pk)
    funciones = {
        "iniciar": services.iniciar_orden,
        "finalizar": services.finalizar_orden,
        "cancelar": services.cancelar_orden,
    }
    if accion not in funciones:
        messages.error(request, "Acción no válida.")
    else:
        try:
            funciones[accion](orden)
            messages.success(request, f"Turno {orden.numero_turno} ({orden.placa}): {orden.get_estado_display().lower()}.")
        except ValidationError as exc:
            _errores(request, exc)
    destino = request.POST.get("next", "")
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
        return redirect(destino)
    return redirect("panel")


@login_required
def servicios(request):
    form = ServicioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Servicio guardado.")
        return redirect("servicios")
    return render(request, "turnos/servicios.html", {"form": form, "lista": Servicio.objects.all()})


@login_required
def servicio_editar(request, pk):
    obj = get_object_or_404(Servicio, pk=pk)
    form = ServicioForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Servicio actualizado.")
        return redirect("servicios")
    return render(request, "turnos/formulario.html", {
        "form": form, "titulo": "Editar servicio", "sub": obj.nombre,
        "boton": "Guardar cambios", "volver": "servicios",
    })


@login_required
def trabajadores(request):
    form = TrabajadorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Trabajador agregado.")
        return redirect("trabajadores")
    en_proceso = {o.trabajador_id: o for o in Orden.objects.filter(estado=Orden.Estado.EN_PROCESO)}
    lista = [{"t": t, "ocupado": t.id in en_proceso, "orden": en_proceso.get(t.id)} for t in Trabajador.objects.all()]
    config = Configuracion.actual()
    return render(request, "turnos/trabajadores.html", {
        "form": form, "lista": lista, "capacidad": services.capacidad(config), "puestos": config.puestos_lavado,
    })


@login_required
@require_POST
def trabajador_alternar(request, pk):
    t = get_object_or_404(Trabajador, pk=pk)
    t.disponible = not t.disponible
    t.save()
    messages.success(request, f"{t.nombre}: {'disponible' if t.disponible else 'no disponible'}.")
    return redirect("trabajadores")


@login_required
def seguimiento(request):
    qs = Orden.objects.select_related("servicio", "trabajador").order_by("-hora_ingreso", "-id")
    estado = request.GET.get("estado", "")
    q = request.GET.get("q", "").strip().upper()
    if estado in Orden.Estado.values:
        qs = qs.filter(estado=estado)
    if q:
        qs = qs.filter(placa__icontains=q)
    pagina = Paginator(qs, 12).get_page(request.GET.get("page"))
    return render(request, "turnos/seguimiento.html", {
        "pagina": pagina, "estado": estado, "q": q, "estados": Orden.Estado.choices,
    })


@login_required
def reportes(request):
    return render(request, "turnos/reportes.html", services.resumen_reportes())


@login_required
def configuracion(request):
    form = ConfiguracionForm(request.POST or None, instance=Configuracion.actual())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuración guardada.")
        return redirect("configuracion")
    return render(request, "turnos/formulario.html", {
        "form": form, "titulo": "Configuración del establecimiento",
        "sub": "Parámetros que usan las reglas de asignación y los reportes.",
        "boton": "Guardar configuración",
    })


def consulta(request):
    """Vista pública para que el cliente consulte su turno con la placa."""
    form = ConsultaForm(request.GET or None)
    resultado = None
    buscado = False
    if request.GET and form.is_valid():
        buscado = True
        placa = normalizar_placa(form.cleaned_data["placa"])
        orden = Orden.objects.filter(placa=placa).order_by("-hora_ingreso").first()
        if orden:
            fila = next((f for f in services.calcular_cola() if f.orden.pk == orden.pk), None)
            resultado = {"orden": orden, "fila": fila}
    return render(request, "turnos/consulta.html", {"form": form, "resultado": resultado, "buscado": buscado})
