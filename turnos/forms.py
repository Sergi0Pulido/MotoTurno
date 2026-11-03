from django import forms

from .models import MENSAJE_PLACA, PLACA_RE, Configuracion, Orden, Servicio, Trabajador, normalizar_placa


class OrdenForm(forms.ModelForm):
    placa = forms.CharField(
        label="Placa", max_length=10,
        widget=forms.TextInput(attrs={"placeholder": "ABC12D", "maxlength": 10, "autocomplete": "off", "style": "text-transform:uppercase"}),
    )

    def clean_placa(self):
        placa = normalizar_placa(self.cleaned_data["placa"])
        if not PLACA_RE.match(placa):
            raise forms.ValidationError(MENSAJE_PLACA)
        return placa

    class Meta:
        model = Orden
        fields = ["placa", "servicio", "cliente", "telefono"]
        widgets = {
            "cliente": forms.TextInput(attrs={"placeholder": "Opcional"}),
            "telefono": forms.TextInput(attrs={"placeholder": "Opcional", "inputmode": "tel"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["servicio"].queryset = Servicio.objects.filter(activo=True)
        self.fields["servicio"].empty_label = "Seleccione un servicio"
        self.fields["servicio"].label_from_instance = lambda s: f"{s.nombre} · {s.duracion_min} min · ${s.precio:,}".replace(",", ".")


class ServicioForm(forms.ModelForm):
    class Meta:
        model = Servicio
        fields = ["nombre", "precio", "duracion_min", "activo"]


class TrabajadorForm(forms.ModelForm):
    class Meta:
        model = Trabajador
        fields = ["nombre", "disponible"]


class ConfiguracionForm(forms.ModelForm):
    class Meta:
        model = Configuracion
        fields = ["nombre_establecimiento", "puestos_lavado", "margen_retraso_min", "tolerancia_min", "linea_base_espera_min"]


class ConsultaForm(forms.Form):
    placa = forms.CharField(
        label="Placa de la moto", max_length=7,
        widget=forms.TextInput(attrs={"placeholder": "ABC12D", "autocomplete": "off", "style": "text-transform:uppercase"}),
    )
