from django.contrib import admin

from .models import Configuracion, Orden, Servicio, Trabajador

admin.site.register([Configuracion, Servicio, Trabajador, Orden])
