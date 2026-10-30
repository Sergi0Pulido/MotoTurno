"""Carga datos de DEMOSTRACIÓN (no son datos reales de un lavadero).

Uso:  python manage.py seed_demo
Crea el usuario `admin` (clave `moto2026`), servicios, trabajadores y motos de ejemplo.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from turnos import services
from turnos.models import Configuracion, Orden, Servicio, Trabajador


class Command(BaseCommand):
    help = "Carga datos de demostración de MotoTurno."

    def add_arguments(self, parser):
        parser.add_argument("--vacio", action="store_true", help="Solo crea usuario, servicios y trabajadores (sin motos).")

    def handle(self, *args, **opts):
        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", "moto2026")

        cfg = Configuracion.actual()
        cfg.nombre_establecimiento = "Lavadero de motos (demo)"
        cfg.puestos_lavado = 3
        cfg.tolerancia_min = 10
        cfg.margen_retraso_min = 5
        cfg.linea_base_espera_min = 20
        cfg.save()

        datos = [("Lavado básico", 8000, 15), ("Lavado completo", 15000, 30),
                 ("Lavado y brillado", 25000, 45), ("Desengrase de cadena", 10000, 20)]
        for nombre, precio, dur in datos:
            Servicio.objects.get_or_create(nombre=nombre, defaults={"precio": precio, "duracion_min": dur})
        for nombre in ["Carlos", "Luis", "Andrés", "Mateo"]:
            Trabajador.objects.get_or_create(nombre=nombre, defaults={"disponible": nombre != "Mateo"})

        if opts["vacio"] or Orden.objects.exists():
            self.stdout.write(self.style.SUCCESS("Datos base listos."))
            return

        S = {s.nombre: s for s in Servicio.objects.all()}
        T = {t.nombre: t for t in Trabajador.objects.all()}
        ahora = timezone.now()

        # Servicios ya terminados hoy: (placa, servicio, trabajador, minutos desde el ingreso hasta hoy, espera est., espera real)
        historial = [
            ("DKL21B", "Lavado básico", "Carlos", 150, 0, 0),
            ("FRT45A", "Lavado completo", "Luis", 140, 0, 2),
            ("HGB12C", "Lavado y brillado", "Andrés", 132, 3, 4),
            ("MNP78D", "Lavado básico", "Carlos", 118, 8, 10),
            ("JWQ34E", "Desengrase de cadena", "Luis", 105, 12, 11),
            ("TRS56F", "Lavado completo", "Andrés", 96, 14, 21),
            ("ZXC90G", "Lavado básico", "Carlos", 80, 9, 8),
        ]
        for placa, srv, trab, atras, est, real in historial:
            ingreso = ahora - timedelta(minutes=atras)
            inicio = ingreso + timedelta(minutes=real)
            dur = S[srv].duracion_min + (2 if real % 2 else -1)
            Orden.objects.create(
                placa=placa, servicio=S[srv], hora_ingreso=ingreso, estado=Orden.Estado.FINALIZADO,
                trabajador=T[trab], inicio_real=inicio, fin_real=inicio + timedelta(minutes=dur),
                espera_estimada_min=est, cliente="",
            )

        # Dos en proceso (una ya retrasada) y varias pendientes
        ya = [("QWE12H", "Lavado completo", "Carlos", 34, 1), ("RTY34J", "Lavado y brillado", "Luis", 12, 2)]
        for placa, srv, trab, hace, puesto in ya:
            ingreso = ahora - timedelta(minutes=hace + 4)
            Orden.objects.create(
                placa=placa, servicio=S[srv], hora_ingreso=ingreso, estado=Orden.Estado.EN_PROCESO,
                trabajador=T[trab], puesto=puesto, inicio_real=ahora - timedelta(minutes=hace), espera_estimada_min=4,
            )
        cola = [("UIO56K", "Lavado básico", 4, "Camila R."), ("PAS78L", "Lavado completo", 3, ""),
                ("FGH90M", "Desengrase de cadena", 2, "Julián P."), ("JKL23N", "Lavado y brillado", 1, ""),
                ("BNM45P", "Lavado básico", 0, "")]
        for placa, srv, hace, cliente in cola:
            o = Orden(placa=placa, servicio=S[srv], hora_ingreso=ahora - timedelta(minutes=hace), cliente=cliente)
            services.registrar_orden(o)
        self.stdout.write(self.style.SUCCESS("Datos de demostración cargados. Usuario: admin / moto2026"))
