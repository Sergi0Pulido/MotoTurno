# MotoTurno – prototipo funcional

Sistema web para organizar los turnos y los tiempos de atención de un lavadero de motos.
Hace parte del proyecto de software del curso (Universidad Iberoamericana, Actividad 4 – Diseño del prototipo).

## Qué hace
- Registra las motos (placa validada, servicio, hora de ingreso) y asigna un turno.
- Calcula la espera estimada de cada moto según el orden de llegada, la duración del servicio, los trabajadores disponibles y los puestos de lavado (reglas R1 a R8, ver `turnos/services.py`).
- Asigna a cada moto un trabajador y un puesto de lavado, y detecta los servicios retrasados.
- Permite iniciar, finalizar y cancelar servicios, y consultar el historial.
- Administra el catálogo de servicios (precio y duración) y la disponibilidad de los trabajadores.
- Muestra reportes: espera real frente a la línea base, precisión de la estimación y duración real por servicio.
- Incluye una vista pública para que el cliente consulte su turno con la placa.

## Requisitos
- Python 3.10 o superior (probado con Django 5.2 y 6.1)
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
Ejecuta 27 casos que verifican las reglas de asignación (incluidos puestos, retrasos y salida de un trabajador), los estados, la validación de placas, los reportes y el control de acceso.

## Reglas de negocio
| Regla | Descripción |
|---|---|
| R1. Orden de llegada | Se atiende estrictamente por hora de ingreso; no se puede saltar una moto. |
| R2. Capacidad | El menor valor entre trabajadores disponibles y puestos de lavado. |
| R3. Trabajador | El libre con menos servicios finalizados hoy; en empate, el que lleva más tiempo sin trabajar. |
| R4. Puesto | El puesto libre con el número más bajo. |
| R5. Estimación | Se simula la cola: cada moto toma el primer trabajador y el primer puesto que se liberen. |
| R6. Retrasos | Si un servicio supera su duración, se asume que le faltan 5 min más (configurable) y la cola se corre. |
| R7. Recálculo | En cada consulta; el panel se actualiza cada 30 segundos. |
| R8. Salida de un trabajador | Si sale mientras lava, termina ese servicio (su puesto sigue ocupado) y luego deja de contar. Sin trabajadores disponibles no se estima la espera. |

## Publicación en línea (PythonAnywhere)
1. Crear una cuenta gratuita en https://www.pythonanywhere.com y abrir una consola **Bash**.
2. Ejecutar:
   ```bash
   git clone https://github.com/Sergi0Pulido/MotoTurno.git
   cd MotoTurno
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py seed_demo
   python manage.py collectstatic --noinput
   ```
3. En la pestaña **Web**: *Add a new web app* → *Manual configuration*. Configurar *Source code* (`/home/<usuario>/MotoTurno`), *Virtualenv* (`/home/<usuario>/MotoTurno/.venv`) y *Static files* (`/static/` → `/home/<usuario>/MotoTurno/staticfiles`).
4. En el archivo WSGI, antes de `get_wsgi_application()`, definir:
   ```python
   import os, sys
   sys.path.append("/home/<usuario>/MotoTurno")
   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
   os.environ["DJANGO_DEBUG"] = "0"
   os.environ["DJANGO_SECRET_KEY"] = "<una clave larga y aleatoria>"
   ```
5. Pulsar **Reload** y **cambiar la contraseña del usuario `admin`** (`python manage.py changepassword admin`), porque la de demostración es pública.

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
