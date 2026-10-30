# MotoTurno – prototipo funcional

Sistema web para organizar los turnos y los tiempos de atención de un lavadero de motos.
Hace parte del proyecto de software del curso (Universidad Iberoamericana, Actividad 4 – Diseño del prototipo).

## Qué hace
- Registra las motos (placa validada, servicio, hora de ingreso) y asigna un turno.
- Calcula la espera estimada de cada moto según el orden de llegada, la duración del servicio y los trabajadores disponibles (reglas R1 a R6, ver `turnos/services.py`).
- Permite iniciar, finalizar y cancelar servicios, y consultar el historial.
- Administra el catálogo de servicios (precio y duración) y la disponibilidad de los trabajadores.
- Muestra reportes: espera real frente a la línea base, precisión de la estimación y duración real por servicio.
- Incluye una vista pública para que el cliente consulte su turno con la placa.

## Requisitos
- Python 3.10 o superior (probado con Python 3.12 y Django 6.0)
- Git

## Instalación y ejecución (Windows PowerShell)
```powershell
git clone https://github.com/Sergi0Pulido/MotoTurno.git
cd MotoTurno
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```
En Linux o macOS la activación del entorno es `source .venv/bin/activate`.

Abra http://127.0.0.1:8000/ en el navegador.

| Acceso | Dirección | Credenciales |
|---|---|---|
| Personal del lavadero | http://127.0.0.1:8000/ | usuario `admin` · clave `moto2026` |
| Cliente (consulta de turno) | http://127.0.0.1:8000/consulta/ | solo la placa |

`seed_demo` carga **datos de demostración** (servicios, trabajadores y motos de ejemplo). Para empezar sin motos use `python manage.py seed_demo --vacio`.

## Pruebas automáticas
```powershell
python manage.py test
```
Ejecuta 20 casos que verifican las reglas de asignación, los estados, la validación de placas, los reportes y el control de acceso.

## Estructura
```
manage.py
config/            configuración del proyecto Django
turnos/
  models.py        Servicio, Trabajador, Orden, Configuración
  services.py      reglas de negocio (cola, estimación, estados, reportes)
  views.py, urls.py, forms.py
  templates/turnos/  11 plantillas HTML
  static/turnos/     estilos (diseño adaptable)
  management/commands/seed_demo.py
  tests.py
```

## Limitaciones de esta versión
Base de datos SQLite, sin pagos, reservas, notificaciones ni aplicación móvil nativa (fuera del alcance de la versión 1 definida en la formulación del proyecto).
