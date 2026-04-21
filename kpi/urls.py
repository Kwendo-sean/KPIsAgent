from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────────
    path("", views.login_view, name="login"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("login/verify/", views.two_factor_verify_view, name="two_factor_verify"),

    # ── Core pages ────────────────────────────────────────────────────────
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("statements/", views.statements_view, name="statements"),
    path("assistant/", views.assistant_view, name="assistant"),
    path("comparison/", views.kpi_comparison, name="kpi_comparison"),

    # ── Statement actions ─────────────────────────────────────────────────
    path("upload/", views.upload_bank_statement, name="upload"),
    path("statement/<int:statement_id>/", views.statement_detail, name="statement_detail"),
    path("statement/<int:statement_id>/delete/", views.delete_bank_statement, name="delete_statement"),
    path("statement/<int:statement_id>/reprocess/", views.reprocess_statement, name="reprocess_statement"),
    path("statement/<int:statement_id>/status/", views.statement_status, name="statement_status"),

    # ── Exports ───────────────────────────────────────────────────────────
    path("statement/<int:statement_id>/export/transactions/", views.export_transactions_csv, name="export_transactions_csv"),
    path("statement/<int:statement_id>/export/kpis/", views.export_kpis_csv, name="export_kpis_csv"),
    path("statement/<int:statement_id>/export/xlsx/", views.export_transactions_xlsx, name="export_transactions_xlsx"),

    # ── Category correction ───────────────────────────────────────────────
    path("api/transaction/<int:tx_id>/category/", views.update_transaction_category, name="update_transaction_category"),

    # ── Tags ─────────────────────────────────────────────────────────────
    path("tags/", views.tags_view, name="tags"),
    path("api/tags/create/", views.create_tag, name="create_tag"),
    path("api/tags/<int:tag_id>/delete/", views.delete_tag, name="delete_tag"),
    path("api/statement/<int:statement_id>/tags/", views.toggle_statement_tag, name="toggle_statement_tag"),

    # ── Budget targets ────────────────────────────────────────────────────
    path("budget/", views.budget_targets_view, name="budget_targets"),
    path("api/budget/save/", views.save_budget_target, name="save_budget_target"),
    path("api/budget/<int:target_id>/delete/", views.delete_budget_target, name="delete_budget_target"),

    # ── Audit log ─────────────────────────────────────────────────────────
    path("audit/", views.audit_log_view, name="audit_log"),

    # ── Gap & duplicate detection ─────────────────────────────────────────
    path("api/gaps/", views.gap_detection_api, name="gap_detection"),
    path("api/duplicates/", views.duplicate_detection_api, name="duplicate_detection"),

    # ── 2FA ───────────────────────────────────────────────────────────────
    path("settings/2fa/", views.two_factor_setup, name="two_factor_setup"),

    # ── Report ────────────────────────────────────────────────────────────
    path("download-report/", views.download_report_view, name="download_report"),
    path("report/builder/", views.report_builder_view, name="report_builder"),
    path("report/generate/", views.generate_report_pdf, name="generate_report_pdf"),

    # ── AI endpoints ──────────────────────────────────────────────────────
    path("api/ask/", views.AskAIView.as_view(), name="ask_ai"),
    path("api/report-summary/", views.ReportSummaryView.as_view(), name="report_summary"),

    # ── REST API ──────────────────────────────────────────────────────────
    path("api/statements/", views.StatementListAPI.as_view(), name="api_statements"),
    path("api/statements/<int:statement_id>/transactions/", views.TransactionListAPI.as_view(), name="api_transactions"),
    path("api/statements/<int:statement_id>/kpis/", views.KPIListAPI.as_view(), name="api_kpis"),
    path("api/kpi/<int:statement_id>/", views.KPIDataView.as_view(), name="kpi_data"),
    path("api/currency/", views.currency_rates_view, name="currency_rates"),

    # ── PWA ───────────────────────────────────────────────────────────────
    path("manifest.json", views.pwa_manifest, name="pwa_manifest"),
    path("sw.js", views.service_worker, name="service_worker"),

    # ── Misc ──────────────────────────────────────────────────────────────
    path("health/", views.health_check, name="health_check"),
    path("api/queue/flush/", views.flush_queue_view, name="flush_queue"),

    # ── Password reset (Django built-ins) ─────────────────────────────────
    path("password-reset/", auth_views.PasswordResetView.as_view(template_name="password_reset.html"), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(template_name="password_reset_done.html"), name="password_reset_done"),
    path("password-reset/confirm/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="password_reset_confirm.html"), name="password_reset_confirm"),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(template_name="password_reset_complete.html"), name="password_reset_complete"),
]
