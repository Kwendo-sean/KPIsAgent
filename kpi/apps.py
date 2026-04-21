import sys

from django.apps import AppConfig


class KpiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kpi"

    def ready(self):
        # When the qcluster management command starts, flush any stale OrmQ
        # tasks that would otherwise cause BadSignature errors.
        if "qcluster" in sys.argv:
            try:
                from django_q.models import OrmQ
                deleted, _ = OrmQ.objects.all().delete()
                if deleted:
                    import logging
                    logging.getLogger("kpi").info(
                        "Flushed %d stale queue task(s) on startup", deleted
                    )
            except Exception:
                pass
