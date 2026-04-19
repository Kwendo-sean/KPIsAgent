from django.contrib import admin

from .models import (
    AIAnalysis, AuditLog, BankStatement, FinancialTransaction,
    KPIMetric, UserProfile, BudgetTarget, StatementTag, StatementTagging,
    TwoFactorProfile,
)


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


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "detail", "ip_address", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("user__username", "detail", "ip_address")
    readonly_fields = ("user", "action", "detail", "ip_address", "created_at")


@admin.register(BudgetTarget)
class BudgetTargetAdmin(admin.ModelAdmin):
    list_display = ("user", "metric", "target_value", "period_month", "period_year", "currency")
    list_filter = ("metric", "period_year", "currency")
    search_fields = ("user__username",)


@admin.register(StatementTag)
class StatementTagAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "color", "created_at")
    search_fields = ("user__username", "name")


@admin.register(StatementTagging)
class StatementTaggingAdmin(admin.ModelAdmin):
    list_display = ("statement", "tag", "created_at")


@admin.register(TwoFactorProfile)
class TwoFactorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_enabled", "created_at")
    list_filter = ("is_enabled",)
    readonly_fields = ("totp_secret", "backup_codes", "created_at", "updated_at")
