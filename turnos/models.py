import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

# Placa de moto en Colombia: tres letras, dos dígitos y una letra (ABC12D);
# también se admite el formato anterior de tres letras y tres dígitos.
PLACA_RE = re.compile(r"^[A-Z]{3}\d{2}[A-Z0-9]$")


def normalizar_placa(valor):
    """Quita espacios y guiones y pasa a mayúsculas (ej.: ' abc-12d ' -> 'ABC12D')."""
    return (valor or "").strip().upper().replace("-", "").replace(" ", "")


class Configuracion(models.Model):
    """Parámetros del establecimiento (una sola fila)."""

    nombre_establecimiento = models.CharField(max_length=80, default="Lavadero de motos")
    puestos_lavado = models.PositiveSmallIntegerField(
        "Puestos de lavado", default=3, validators=[MinValueValidator(1)],
        help_text="Número máximo de motos que se pueden lavar al mismo tiempo.",
    )
    tolerancia_min = models.PositiveSmallIntegerField(
        "Tolerancia de la estimación (min)", default=5,
        help_text="Diferencia aceptable entre la espera estimada y la real.",
    )
    linea_base_espera_min = models.PositiveSmallIntegerField(
        "Línea base de espera (min)", null=True, blank=True,
        help_text="Espera promedio medida antes de usar el sistema; sirve para medir la reducción.",
    )

    class Meta:
        verbose_name = "Configuración"
        verbose_name_plural = "Configuración"

    def __str__(self):
        return self.nombre_establecimiento

    @classmethod
    def actual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Servicio(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    precio = models.PositiveIntegerField("Precio (COP)", validators=[MinValueValidator(1000)])
    duracion_min = models.PositiveSmallIntegerField("Duración estimada (min)", validators=[MinValueValidator(1)])
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["duracion_min", "nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.duracion_min} min)"


class Trabajador(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    disponible = models.BooleanField("Disponible hoy", default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "trabajadores"

    def __str__(self):
        return self.nombre


class Orden(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        EN_PROCESO = "EN_PROCESO", "En proceso"
        FINALIZADO = "FINALIZADO", "Finalizado"
        CANCELADO = "CANCELADO", "Cancelado"

    numero_turno = models.PositiveIntegerField(default=0)
    placa = models.CharField(max_length=6)
    cliente = models.CharField(max_length=80, blank=True)
    telefono = models.CharField(max_length=15, blank=True)
    servicio = models.ForeignKey(Servicio, on_delete=models.PROTECT, related_name="ordenes")
    hora_ingreso = models.DateTimeField(default=timezone.now)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    trabajador = models.ForeignKey(Trabajador, null=True, blank=True, on_delete=models.SET_NULL, related_name="ordenes")
    inicio_real = models.DateTimeField(null=True, blank=True)
    fin_real = models.DateTimeField(null=True, blank=True)
    espera_estimada_min = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Espera estimada en el momento del registro."
    )

    class Meta:
        ordering = ["hora_ingreso", "id"]

    def __str__(self):
        return f"Turno {self.numero_turno} · {self.placa}"

    def clean(self):
        self.placa = normalizar_placa(self.placa)
        if not PLACA_RE.match(self.placa):
            raise ValidationError({"placa": "La placa debe tener el formato ABC12D (3 letras, 2 números y 1 letra o número)."})

    def save(self, *args, **kwargs):
        if not self.numero_turno:
            hoy = timezone.localdate(self.hora_ingreso)
            self.numero_turno = Orden.objects.filter(hora_ingreso__date=hoy).count() + 1
        super().save(*args, **kwargs)

    # ---- tiempos reales (en minutos) ----
    @property
    def espera_real_min(self):
        if self.inicio_real:
            return round((self.inicio_real - self.hora_ingreso).total_seconds() / 60)
        return None

    @property
    def duracion_real_min(self):
        if self.inicio_real and self.fin_real:
            return round((self.fin_real - self.inicio_real).total_seconds() / 60)
        return None

    @property
    def fin_programado(self):
        if self.estado == self.Estado.EN_PROCESO and self.inicio_real:
            return self.inicio_real + timedelta(minutes=self.servicio.duracion_min)
        return None

    @property
    def activa(self):
        return self.estado in (self.Estado.PENDIENTE, self.Estado.EN_PROCESO)
