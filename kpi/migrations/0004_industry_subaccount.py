from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('kpi', '0003_new_features'),
    ]

    operations = [
        # 1. Create SubAccount
        migrations.CreateModel(
            name='SubAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('industry', models.CharField(
                    choices=[
                        ('HOSPITAL', 'Hospital / Healthcare Facility'),
                        ('PHARMACY', 'Pharmacy'),
                        ('DENTAL', 'Dental / Optical Practice'),
                        ('MED_LAB', 'Medical Laboratory / Diagnostics'),
                        ('MICROFINANCE', 'Bank / Microfinance Institution'),
                        ('INSURANCE', 'Insurance Company'),
                        ('SACCO', 'SACCO / Credit Union'),
                        ('RETAIL', 'General Retail / Shop'),
                        ('SUPERMARKET', 'Supermarket / Grocery Store'),
                        ('ECOMMERCE', 'E-commerce Business'),
                        ('RESTAURANT', 'Restaurant / Café / Fast Food'),
                        ('HOTEL', 'Hotel / Lodging / Guesthouse'),
                        ('CATERING', 'Event Catering / Banqueting'),
                        ('SCHOOL', 'School / College / University'),
                        ('TRAINING', 'Training Center / Coaching Institute'),
                        ('REAL_ESTATE', 'Real Estate Agency / Property Management'),
                        ('CONSTRUCTION', 'Construction / Contracting'),
                        ('NGO', 'NGO / Non-Profit Organization'),
                        ('CHURCH', 'Church / Religious Organization'),
                        ('GOVERNMENT', 'Government / Public Sector Entity'),
                        ('MANUFACTURING', 'Manufacturing / Production'),
                        ('AGRICULTURE', 'Agriculture / Agribusiness / Farm'),
                        ('LAW_FIRM', 'Law Firm'),
                        ('ACCOUNTING_FIRM', 'Accounting / Audit Firm'),
                        ('CONSULTING', 'Consulting / Advisory Firm'),
                        ('TRANSPORT', 'Transport / Logistics / Fleet Management'),
                    ],
                    default='HOSPITAL', max_length=50,
                )),
                ('contact_name', models.CharField(blank=True, max_length=100)),
                ('contact_email', models.EmailField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sub_accounts',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['name']},
        ),

        # 2. Add sub_account FK to BankStatement
        migrations.AddField(
            model_name='bankstatement',
            name='sub_account',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='statements',
                to='kpi.subaccount',
            ),
        ),

        # 3. Add industry + organization_name to UserProfile
        migrations.AddField(
            model_name='userprofile',
            name='industry',
            field=models.CharField(
                choices=[
                    ('HOSPITAL', 'Hospital / Healthcare Facility'),
                    ('PHARMACY', 'Pharmacy'),
                    ('DENTAL', 'Dental / Optical Practice'),
                    ('MED_LAB', 'Medical Laboratory / Diagnostics'),
                    ('MICROFINANCE', 'Bank / Microfinance Institution'),
                    ('INSURANCE', 'Insurance Company'),
                    ('SACCO', 'SACCO / Credit Union'),
                    ('RETAIL', 'General Retail / Shop'),
                    ('SUPERMARKET', 'Supermarket / Grocery Store'),
                    ('ECOMMERCE', 'E-commerce Business'),
                    ('RESTAURANT', 'Restaurant / Café / Fast Food'),
                    ('HOTEL', 'Hotel / Lodging / Guesthouse'),
                    ('CATERING', 'Event Catering / Banqueting'),
                    ('SCHOOL', 'School / College / University'),
                    ('TRAINING', 'Training Center / Coaching Institute'),
                    ('REAL_ESTATE', 'Real Estate Agency / Property Management'),
                    ('CONSTRUCTION', 'Construction / Contracting'),
                    ('NGO', 'NGO / Non-Profit Organization'),
                    ('CHURCH', 'Church / Religious Organization'),
                    ('GOVERNMENT', 'Government / Public Sector Entity'),
                    ('MANUFACTURING', 'Manufacturing / Production'),
                    ('AGRICULTURE', 'Agriculture / Agribusiness / Farm'),
                    ('LAW_FIRM', 'Law Firm'),
                    ('ACCOUNTING_FIRM', 'Accounting / Audit Firm'),
                    ('CONSULTING', 'Consulting / Advisory Firm'),
                    ('TRANSPORT', 'Transport / Logistics / Fleet Management'),
                ],
                default='HOSPITAL', max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='organization_name',
            field=models.CharField(blank=True, max_length=200),
        ),

        # 4. Update role choices on UserProfile
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=[
                    ('OWNER', 'Business Owner'),
                    ('DIRECTOR', 'Director / CEO'),
                    ('CFO', 'Chief Financial Officer'),
                    ('MANAGER', 'Finance Manager'),
                    ('ANALYST', 'Financial Analyst'),
                    ('ACCOUNTANT', 'Accountant'),
                ],
                default='MANAGER', max_length=100,
            ),
        ),
    ]
