from django.contrib.auth.models import User
from django.db import models


class BankStatement(models.Model):
    """Model to store uploaded bank statements."""

    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE)
    file_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="bank_statements/")
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
    """Model to store individual transactions extracted from bank statements."""

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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date"]

    def __str__(self):
        return f"{self.transaction_date} - {self.description[:50]} - {self.amount}"


class KPIMetric(models.Model):
    """Model to store calculated KPI metrics."""

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
        choices=[
            ("HEALTHY", "Healthy"),
            ("WARNING", "Warning"),
            ("CRITICAL", "Critical"),
        ],
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
    """Model to store AI-generated insights and analysis."""

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
        choices=[
            ("INFO", "Information"),
            ("WARNING", "Warning"),
            ("CRITICAL", "Critical"),
        ],
        default="INFO",
    )
    related_metrics = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.analysis_type} - {self.title}"


class UserProfile(models.Model):
    """Extended user profile for manager roles."""

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
