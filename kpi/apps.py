import sys

from django.apps import AppConfig


class KpiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kpi"

    def ready(self):
        # Local-mode safety: warn loudly if cloud keys are configured on a
        # deployment that must never contact an external provider. Logs key
        # NAMES only — never a value.
        try:
            from .ai_agent import local_ai_enabled, warn_if_cloud_keys_present
            if local_ai_enabled():
                import logging
                logging.getLogger("kpi").info(
                    "LOCAL_AI_MODE is ENABLED — all inference is local; "
                    "external AI providers are blocked"
                )
                warn_if_cloud_keys_present()
        except Exception:
            pass

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
