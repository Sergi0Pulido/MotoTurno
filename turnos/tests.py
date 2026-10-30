from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Configuracion, Orden, Servicio, Trabajador


def crear_base(trabajadores=("Ana", "Beto"), puestos=3):
    cfg = Configuracion.actual()
    cfg.puestos_lavado = puestos
    cfg.linea_base_espera_min = 30
    cfg.save()
    basico = Servicio.objects.create(nombre="Básico", precio=8000, duracion_min=15)
    completo = Servicio.objects.create(nombre="Completo", precio=15000, duracion_min=30)
    ts = [Trabajador.objects.create(nombre=n) for n in trabajadores]
    return basico, completo, ts


def nueva(placa, servicio, ingreso):
    return services.registrar_orden(Orden(placa=placa, servicio=servicio, hora_ingreso=ingreso))


class ReglasDeAsignacion(TestCase):
    def setUp(self):
        self.basico, self.completo, self.ts = crear_base()
        self.t0 = timezone.now().replace(microsecond=0)

    # R1 – primero en llegar, primero en atenderse
    def test_cola_respeta_orden_de_llegada(self):
        nueva("AAA11A", self.completo, self.t0 - timedelta(minutes=5))
        nueva("BBB22B", self.basico, self.t0 - timedelta(minutes=10))
        cola = services.calcular_cola(self.t0)
        self.assertEqual([f.orden.placa for f in cola], ["BBB22B", "AAA11A"])

    # R2 / R4 – con dos trabajadores libres las dos primeras motos empiezan de inmediato
    def test_estimacion_con_capacidad_de_dos(self):
        nueva("AAA11A", self.completo, self.t0 - timedelta(minutes=3))
        nueva("BBB22B", self.completo, self.t0 - timedelta(minutes=2))
        nueva("CCC33C", self.basico, self.t0 - timedelta(minutes=1))
        cola = services.calcular_cola(self.t0)
        self.assertEqual([f.espera_min for f in cola], [0, 0, 30])
        self.assertEqual(cola[2].fin_estimado, self.t0 + timedelta(minutes=45))

    # R2 – los puestos de lavado limitan la capacidad aunque haya más trabajadores
    def test_puestos_limitan_capacidad(self):
        Trabajador.objects.create(nombre="Cami")
        cfg = Configuracion.actual(); cfg.puestos_lavado = 1; cfg.save()
        nueva("AAA11A", self.completo, self.t0 - timedelta(minutes=3))
        nueva("BBB22B", self.basico, self.t0 - timedelta(minutes=2))
        cola = services.calcular_cola(self.t0)
        self.assertEqual([f.espera_min for f in cola], [0, 30])

    # R3 – reparto equitativo entre trabajadores
    def test_inicio_asigna_trabajador_con_menos_servicios_del_dia(self):
        a, b = self.ts
        previo = Orden.objects.create(
            placa="ZZZ99Z", servicio=self.basico, hora_ingreso=self.t0 - timedelta(hours=1),
            estado=Orden.Estado.FINALIZADO, trabajador=a, inicio_real=self.t0 - timedelta(minutes=50),
            fin_real=self.t0 - timedelta(minutes=35),
        )
        o = nueva("AAA11A", self.basico, self.t0 - timedelta(minutes=2))
        services.iniciar_orden(o, self.t0)
        o.refresh_from_db()
        self.assertEqual(o.trabajador, b)
        self.assertEqual(o.estado, Orden.Estado.EN_PROCESO)

    # R1 – no se puede saltar la cola
    def test_no_permite_iniciar_una_moto_que_no_es_la_primera(self):
        nueva("AAA11A", self.basico, self.t0 - timedelta(minutes=5))
        segunda = nueva("BBB22B", self.basico, self.t0 - timedelta(minutes=2))
        with self.assertRaises(ValidationError):
            services.iniciar_orden(segunda, self.t0)

    # R5 – un retraso se refleja en las estimaciones siguientes
    def test_retraso_recalcula_estimaciones(self):
        a, b = self.ts
        Trabajador.objects.filter(pk=b.pk).update(disponible=False)  # capacidad = 1
        o1 = nueva("AAA11A", self.completo, self.t0 - timedelta(minutes=40))
        services.iniciar_orden(o1, self.t0 - timedelta(minutes=35))  # debía terminar hace 5 min
        nueva("BBB22B", self.basico, self.t0 - timedelta(minutes=1))
        cola = services.calcular_cola(self.t0)
        self.assertEqual(cola[0].espera_min, 0)  # se asume que termina "ahora"
        self.assertEqual(cola[0].inicio_estimado, self.t0)

    # R6 – sin trabajadores no se estima
    def test_sin_trabajadores_no_hay_estimacion(self):
        Trabajador.objects.update(disponible=False)
        nueva("AAA11A", self.basico, self.t0)
        cola = services.calcular_cola(self.t0)
        self.assertIsNone(cola[0].espera_min)
        self.assertFalse(cola[0].puede_iniciar)


class RegistroYEstados(TestCase):
    def setUp(self):
        self.basico, self.completo, self.ts = crear_base()

    def test_placa_valida_se_normaliza(self):
        o = services.registrar_orden(Orden(placa=" abc-12d ", servicio=self.basico))
        self.assertEqual(o.placa, "ABC12D")
        self.assertEqual(o.numero_turno, 1)
        self.assertIsNotNone(o.espera_estimada_min)

    def test_placa_invalida_se_rechaza(self):
        for mala in ["12345", "ABCD12", "AB123C", ""]:
            with self.assertRaises(ValidationError, msg=mala):
                services.registrar_orden(Orden(placa=mala, servicio=self.basico))

    def test_no_duplica_moto_activa(self):
        services.registrar_orden(Orden(placa="AAA11A", servicio=self.basico))
        with self.assertRaises(ValidationError):
            services.registrar_orden(Orden(placa="AAA11A", servicio=self.completo))

    def test_servicio_inactivo_no_se_puede_usar(self):
        self.basico.activo = False; self.basico.save()
        with self.assertRaises(ValidationError):
            services.registrar_orden(Orden(placa="AAA11A", servicio=self.basico))

    def test_flujo_completo_de_estados(self):
        o = services.registrar_orden(Orden(placa="AAA11A", servicio=self.basico))
        services.iniciar_orden(o)
        self.assertEqual(o.estado, Orden.Estado.EN_PROCESO)
        services.finalizar_orden(o)
        self.assertEqual(o.estado, Orden.Estado.FINALIZADO)
        self.assertIsNotNone(o.duracion_real_min)
        with self.assertRaises(ValidationError):
            services.cancelar_orden(o)

    def test_cancelar_pendiente(self):
        o = services.registrar_orden(Orden(placa="AAA11A", servicio=self.basico))
        services.cancelar_orden(o)
        self.assertEqual(o.estado, Orden.Estado.CANCELADO)
        self.assertEqual(services.calcular_cola(), [])

    def test_numero_de_turno_consecutivo(self):
        a = services.registrar_orden(Orden(placa="AAA11A", servicio=self.basico))
        b = services.registrar_orden(Orden(placa="BBB22B", servicio=self.basico))
        self.assertEqual((a.numero_turno, b.numero_turno), (1, 2))


class Reportes(TestCase):
    def test_reduccion_y_precision(self):
        basico, _, ts = crear_base()
        t0 = timezone.now()
        # espera real 12 min (estimada 10) y 20 min (estimada 10)
        for placa, real in (("AAA11A", 12), ("BBB22B", 20)):
            ing = t0 - timedelta(hours=1)
            Orden.objects.create(
                placa=placa, servicio=basico, hora_ingreso=ing, estado=Orden.Estado.FINALIZADO, trabajador=ts[0],
                inicio_real=ing + timedelta(minutes=real), fin_real=ing + timedelta(minutes=real + 15),
                espera_estimada_min=10,
            )
        r = services.resumen_reportes()
        self.assertEqual(r["espera_prom"], 16.0)
        self.assertEqual(r["reduccion"], 47)      # (30 - 16) / 30
        self.assertEqual(r["pct_precision"], 50)  # solo la primera queda dentro de ±5 min
        self.assertEqual(r["error_prom"], 6.0)


class Pantallas(TestCase):
    def setUp(self):
        crear_base()
        self.user = get_user_model().objects.create_user("tester", password="clave-segura-1")

    def test_paginas_privadas_exigen_ingreso(self):
        for nombre in ["panel", "registrar", "servicios", "trabajadores", "seguimiento", "reportes", "configuracion"]:
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn("/ingreso/", r["Location"])

    def test_paginas_con_sesion(self):
        self.client.login(username="tester", password="clave-segura-1")
        for nombre in ["panel", "registrar", "servicios", "trabajadores", "seguimiento", "reportes", "configuracion"]:
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)

    def test_registro_desde_formulario(self):
        self.client.login(username="tester", password="clave-segura-1")
        s = Servicio.objects.first()
        r = self.client.post(reverse("registrar"), {"placa": "kjh45a", "servicio": s.pk}, follow=True)
        self.assertContains(r, "KJH45A")
        self.assertTrue(Orden.objects.filter(placa="KJH45A").exists())

    def test_formulario_acepta_placa_con_guion_y_rechaza_formato_invalido(self):
        self.client.login(username="tester", password="clave-segura-1")
        s = Servicio.objects.first()
        r = self.client.post(reverse("registrar"), {"placa": "abc-12d", "servicio": s.pk}, follow=True)
        self.assertTrue(Orden.objects.filter(placa="ABC12D").exists())
        r = self.client.post(reverse("registrar"), {"placa": "12345", "servicio": s.pk})
        self.assertContains(r, "formato ABC12D")
        self.assertFalse(Orden.objects.filter(placa="12345").exists())

    def test_consulta_publica_del_cliente(self):
        s = Servicio.objects.first()
        services.registrar_orden(Orden(placa="KJH45A", servicio=s))
        r = self.client.get(reverse("consulta"), {"placa": "kjh45a"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "KJH45A")
        r = self.client.get(reverse("consulta"), {"placa": "XXX99X"})
        self.assertContains(r, "No encontramos")
