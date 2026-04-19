from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


def _user_upload_path(instance, filename):
    """Store uploads under bank_statements/user_<id>/ for per-user isolation."""
    return f"bank_statements/user_{instance.uploaded_by_id}/{filename}"


class BankStatement(models.Model):
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE)
    file_name = models.CharField(max_length=255)
    file = models.FileField(upload_to=_user_upload_path)
    upload_date = models.DateTimeField(auto_now_add=True)
    statement_period_start = models.DateField(null=True, blank=True)
    statement_period_end = models.DateField(null=True, blank=True)
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    total_deposits = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    total_withdrawals = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    extracted_text = models.TextField(blank=True, null=True)
    is_processed = models.BooleanField(default=False)
    processing_error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-upload_date"]

    def __str__(self):
        return f"{self.file_name} - {self.upload_date.strftime('%Y-%m-%d')}"


class FinancialTransaction(models.Model):
    bank_statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="transactions")
    transaction_date = models.DateField()
    description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    transaction_type = models.CharField(
        max_length=20,
        choices=[
            ("DEPOSIT", "Deposit/Income"),
            ("WITHDRAWAL", "Withdrawal/Expense"),
            ("TRANSFER", "Transfer"),
        ],
    )
    category = models.CharField(max_length=100, null=True, blank=True)
    running_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date"]

    def __str__(self):
        return f"{self.transaction_date} - {self.description[:50]} - {self.amount}"


class KPIMetric(models.Model):
    METRIC_TYPES = [
        ("REVENUE", "Revenue Metrics"),
        ("COST", "Cost Management"),
        ("PROFITABILITY", "Profitability"),
        ("CASHFLOW", "Cash Flow"),
        ("EFFICIENCY", "Efficiency"),
        ("FINANCIAL_HEALTH", "Financial Health"),
    ]

    metric_name = models.CharField(max_length=255)
    metric_type = models.CharField(max_length=50, choices=METRIC_TYPES)
    description = models.TextField(null=True, blank=True)
    current_value = models.DecimalField(max_digits=15, decimal_places=2)
    unit = models.CharField(max_length=50)
    target_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    warning_threshold = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    critical_threshold = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[("HEALTHY", "Healthy"), ("WARNING", "Warning"), ("CRITICAL", "Critical")],
        default="HEALTHY",
    )
    bank_statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="kpi_metrics")
    calculated_date = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-calculated_date"]

    def __str__(self):
        return f"{self.metric_name} - {self.current_value} {self.unit}"


class AIAnalysis(models.Model):
    bank_statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="ai_analyses")
    analysis_type = models.CharField(
        max_length=50,
        choices=[
            ("INSIGHT", "General Insight"),
            ("ALERT", "Alert/Warning"),
            ("RECOMMENDATION", "Recommendation"),
            ("FORECAST", "Forecast"),
            ("RESPONSE", "Question Response"),
        ],
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    severity = models.CharField(
        max_length=20,
        choices=[("INFO", "Information"), ("WARNING", "Warning"), ("CRITICAL", "Critical")],
        default="INFO",
    )
    related_metrics = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.analysis_type} - {self.title}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=100,
        choices=[
            ("DIRECTOR", "Hospital Director"),
            ("CFO", "Chief Financial Officer"),
            ("MANAGER", "Finance Manager"),
            ("ANALYST", "Financial Analyst"),
        ],
    )
    department = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.role}"


class AuditLog(models.Model):
    """Records user actions for accountability and debugging."""
    ACTION_CHOICES = [
        ("UPLOAD", "Statement Uploaded"),
        ("DELETE", "Statement Deleted"),
        ("REPROCESS", "Statement Reprocessed"),
        ("EXPORT", "Report Exported"),
        ("LOGIN", "User Login"),
        ("AI_QUERY", "AI Query Made"),
        ("CATEGORY_EDIT", "Category Edited"),
        ("BUDGET_SET", "Budget Target Set"),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    detail = models.CharField(max_length=500, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.action} at {self.created_at}"


class BudgetTarget(models.Model):
    """Monthly revenue and expense targets for KPI comparison."""
    METRIC_CHOICES = [
        ("REVENUE", "Revenue"),
        ("EXPENSES", "Expenses"),
        ("NET_INCOME", "Net Income"),
        ("PROFIT_MARGIN", "Profit Margin %"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="budget_targets")
    metric = models.CharField(max_length=20, choices=METRIC_CHOICES)
    target_value = models.DecimalField(max_digits=15, decimal_places=2)
    period_month = models.IntegerField(help_text="1–12")
    period_year = models.IntegerField()
    currency = models.CharField(max_length=5, default="KES")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "metric", "period_month", "period_year")
        ordering = ["-period_year", "-period_month"]

    def __str__(self):
        return f"{self.user} — {self.metric} target for {self.period_month}/{self.period_year}"


class StatementTag(models.Model):
    """User-defined tags for organising bank statements."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=7, default="#2C7A5C")  # hex colour
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.user.username}: {self.name}"


class StatementTagging(models.Model):
    """Many-to-many link between BankStatement and StatementTag."""
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="taggings")
    tag = models.ForeignKey(StatementTag, on_delete=models.CASCADE, related_name="taggings")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("statement", "tag")


class TwoFactorProfile(models.Model):
    """Stores TOTP secret for two-factor authentication."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_profile")
    totp_secret = models.CharField(max_length=64, blank=True)
    is_enabled = models.BooleanField(default=False)
    backup_codes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"2FA for {self.user.username} ({'on' if self.is_enabled else 'off'})"
