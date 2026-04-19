import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import kpi.models


class Migration(migrations.Migration):

    dependencies = [
        ("kpi", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Add running_balance to FinancialTransaction
        migrations.AddField(
            model_name="financialtransaction",
            name="running_balance",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True),
        ),
        # Change BankStatement.file upload_to to user-isolated path
        migrations.AlterField(
            model_name="bankstatement",
            name="file",
            field=models.FileField(upload_to=kpi.models._user_upload_path),
        ),
        # Add AuditLog model
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[
                    ("UPLOAD", "Statement Uploaded"),
                    ("DELETE", "Statement Deleted"),
                    ("REPROCESS", "Statement Reprocessed"),
                    ("EXPORT", "Report Exported"),
                    ("LOGIN", "User Login"),
                    ("AI_QUERY", "AI Query Made"),
                ], max_length=20)),
                ("detail", models.CharField(blank=True, max_length=500)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
