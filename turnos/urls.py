from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.panel, name="panel"),
    path("ingreso/", auth_views.LoginView.as_view(template_name="turnos/login.html"), name="login"),
    path("salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("registrar/", views.registrar, name="registrar"),
    path("turno/<int:pk>/", views.turno, name="turno"),
    path("orden/<int:pk>/<str:accion>/", views.accion_orden, name="accion_orden"),
    path("servicios/", views.servicios, name="servicios"),
    path("servicios/<int:pk>/", views.servicio_editar, name="servicio_editar"),
    path("trabajadores/", views.trabajadores, name="trabajadores"),
    path("trabajadores/<int:pk>/alternar/", views.trabajador_alternar, name="trabajador_alternar"),
    path("seguimiento/", views.seguimiento, name="seguimiento"),
    path("reportes/", views.reportes, name="reportes"),
    path("configuracion/", views.configuracion, name="configuracion"),
    path("consulta/", views.consulta, name="consulta"),
]
