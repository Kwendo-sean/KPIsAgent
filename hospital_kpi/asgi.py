import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_kpi.settings")

application = get_asgi_application()
