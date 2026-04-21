"""
django-q2 async tasks for background statement processing.
"""
import logging
import traceback

logger = logging.getLogger("kpi.tasks")


def process_statement_task(statement_id: int):
    """
    Background task: run AI extraction + KPI calculation on a BankStatement.
    Called by django-q2 worker; statement text must already be extracted and saved.
    """
    from .models import BankStatement
    from .views import process_bank_statement_with_ai

    logger.info("━━ Task START: process_statement_task(id=%s)", statement_id)
    try:
        statement = BankStatement.objects.get(pk=statement_id)
    except BankStatement.DoesNotExist:
        logger.error("✗ Statement %s not found in DB — task aborted", statement_id)
        return

    logger.info("  File: %s", statement.file_name)
    try:
        process_bank_statement_with_ai(statement)
        logger.info("━━ Task DONE: statement %s processed successfully", statement_id)
    except Exception:
        logger.error(
            "━━ Task FAILED: statement %s\n%s",
            statement_id,
            traceback.format_exc(),
        )
        raise


def process_csv_statement_task(statement_id: int, fin_data: dict):
    """
    Background task: save pre-parsed CSV financial data and compute KPIs.
    """
    from .models import BankStatement
    from .views import _process_with_financial_data

    logger.info("━━ Task START: process_csv_statement_task(id=%s)", statement_id)
    try:
        statement = BankStatement.objects.get(pk=statement_id)
    except BankStatement.DoesNotExist:
        logger.error("✗ Statement %s not found in DB — task aborted", statement_id)
        return

    logger.info("  File: %s", statement.file_name)
    try:
        _process_with_financial_data(statement, fin_data)
        logger.info("━━ Task DONE: CSV statement %s processed successfully", statement_id)
    except Exception:
        logger.error(
            "━━ Task FAILED: CSV statement %s\n%s",
            statement_id,
            traceback.format_exc(),
        )
        raise


def send_kpi_alert_email_task(user_id: int, alert_title: str, alert_content: str):
    """
    Background task: send a KPI threshold breach email to the user.
    """
    from django.contrib.auth.models import User
    from django.core.mail import send_mail
    from django.conf import settings

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("✗ Alert email: user %s not found", user_id)
        return

    if not user.email:
        logger.warning("✗ Alert email: user %s has no email address", user_id)
        return

    try:
        send_mail(
            subject=f"[KPIConsole Alert] {alert_title}",
            message=f"Hello {user.get_full_name() or user.username},\n\n"
                    f"A financial alert was triggered:\n\n{alert_content}\n\n"
                    f"Log in to KPIConsole to review details.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
        logger.info("✓ Alert email sent to %s — %s", user.email, alert_title)
    except Exception:
        logger.error("✗ Alert email failed:\n%s", traceback.format_exc())
