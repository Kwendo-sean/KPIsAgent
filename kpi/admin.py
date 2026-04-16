from django.contrib import admin

from .models import AIAnalysis, BankStatement, FinancialTransaction, KPIMetric, UserProfile


@admin.register(BankStatement)
class BankStatementAdmin(admin.ModelAdmin):
    list_display = ("file_name", "uploaded_by", "upload_date", "is_processed", "closing_balance")
    list_filter = ("is_processed", "upload_date")
    search_fields = ("file_name", "uploaded_by__username")
    readonly_fields = ("upload_date", "updated_at", "extracted_text")


@admin.register(FinancialTransaction)
class FinancialTransactionAdmin(admin.ModelAdmin):
    list_display = ("transaction_date", "description", "amount", "transaction_type", "category")
    list_filter = ("transaction_type", "category", "transaction_date")
    search_fields = ("description", "category")
    readonly_fields = ("created_at",)


@admin.register(KPIMetric)
class KPIMetricAdmin(admin.ModelAdmin):
    list_display = ("metric_name", "metric_type", "current_value", "unit", "status", "calculated_date")
    list_filter = ("metric_type", "status", "calculated_date")
    search_fields = ("metric_name", "description")
    readonly_fields = ("calculated_date", "updated_at")


@admin.register(AIAnalysis)
class AIAnalysisAdmin(admin.ModelAdmin):
    list_display = ("title", "analysis_type", "severity", "created_at")
    list_filter = ("analysis_type", "severity", "created_at")
    search_fields = ("title", "content")
    readonly_fields = ("created_at",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "department")
    list_filter = ("role", "department")
    search_fields = ("user__username", "user__email", "department")
