from django.urls import path

from . import views

urlpatterns = [
    path("", views.login_view, name="login"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("statements/", views.statements_view, name="statements"),
    path("assistant/", views.assistant_view, name="assistant"),
    path("upload/", views.upload_bank_statement, name="upload"),
    path("statement/<int:statement_id>/delete/", views.delete_bank_statement, name="delete_statement"),
    path("statement/<int:statement_id>/reprocess/", views.reprocess_statement, name="reprocess_statement"),
    path("statement/<int:statement_id>/", views.statement_detail, name="statement_detail"),
    path("comparison/", views.kpi_comparison, name="kpi_comparison"),
    path("api/ask/", views.AskAIView.as_view(), name="ask_ai"),
    path("api/report-summary/", views.ReportSummaryView.as_view(), name="report_summary"),
    path("download-report/", views.download_report_view, name="download_report"),
    path("api/kpi/<int:statement_id>/", views.KPIDataView.as_view(), name="kpi_data"),
]
