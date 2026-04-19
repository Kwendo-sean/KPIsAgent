"""
django-q2 async tasks for background statement processing.
"""
import logging

logger = logging.getLogger("kpi.tasks")


def process_statement_task(statement_id: int):
    """
    Background task: run AI extraction + KPI calculation on a BankStatement.
    Called by django-q2 worker; statement text must already be extracted and saved.
    """
    from .models import BankStatement
    from .views import process_bank_statement_with_ai

    try:
        statement = BankStatement.objects.get(pk=statement_id)
    except BankStatement.DoesNotExist:
        logger.error("process_statement_task: statement %s not found", statement_id)
        return

    logger.info("Processing statement %s in background", statement_id)
    process_bank_statement_with_ai(statement)
    logger.info("Done processing statement %s", statement_id)


def process_csv_statement_task(statement_id: int, fin_data: dict):
    """
    Background task: save pre-parsed CSV financial data and compute KPIs.
    """
    from .models import BankStatement
    from .views import _process_with_financial_data

    try:
        statement = BankStatement.objects.get(pk=statement_id)
    except BankStatement.DoesNotExist:
        logger.error("process_csv_statement_task: statement %s not found", statement_id)
        return

    logger.info("Processing CSV statement %s in background", statement_id)
    _process_with_financial_data(statement, fin_data)
    logger.info("Done processing CSV statement %s", statement_id)


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
        return

    if not user.email:
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
        logger.info("Alert email sent to %s for: %s", user.email, alert_title)
    except Exception as e:
        logger.error("Failed to send alert email: %s", e)
