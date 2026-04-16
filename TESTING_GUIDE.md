# Hospital KPI System - Testing & Sample Data Guide

## 🧪 Testing Your Setup

### Pre-Testing Checklist

- [ ] MySQL is running in XAMPP
- [ ] Django development server is running (`python manage.py runserver`)
- [ ] Can access `http://localhost:8000/login`
- [ ] Created superuser account
- [ ] All dependencies installed

---

## 🔐 Test Credentials

### Default Testing Accounts

**Superuser (First Account)**
- Username: `admin`
- Password: (whatever you set during `createsuperuser`)

**Additional Test Manager**
```bash
python manage.py shell
from django.contrib.auth.models import User
from kpi.models import UserProfile

user = User.objects.create_user(
    username='finance_manager',
    email='manager@hospital.com',
    password='manager123',
    first_name='John',
    last_name='Doe'
)
user.is_staff = True
user.save()

UserProfile.objects.create(
    user=user,
    role='CFO',
    department='Finance'
)
```

---

## 📝 Sample Bank Statement Data

If you don't have a real PDF, you can create a simple text document with this sample data, then convert to PDF:

```
HOSPITAL GENERAL BANK STATEMENT
Period: January 1, 2024 - January 31, 2024

ACCOUNT INFORMATION
Account Number: 1234567890
Account Type: Operating Account
Bank: Hospital First Credit Union

OPENING BALANCE
Opening Balance (January 1, 2024): $450,000.00

TRANSACTIONS

Jan 2, 2024 | Patient Billing Income | +$15,250.00
Jan 3, 2024 | Insurance Reimbursement | +$28,500.00
Jan 3, 2024 | Staff Payroll | -$35,000.00
Jan 4, 2024 | Medical Equipment Supply | -$8,750.00
Jan 5, 2024 | Utilities Payment | -$12,500.00
Jan 6, 2024 | Pharmaceutical Supplies | -$9,200.00
Jan 8, 2024 | Outpatient Revenue | +$6,800.00
Jan 9, 2024 | Maintenance Services | -$4,500.00
Jan 10, 2024 | Government Medicare Payment | +$45,000.00
Jan 11, 2024 | Office Supplies | -$2,150.00
Jan 12, 2024 | Emergency Department Revenue | +$18,500.00
Jan 13, 2024 | Janitorial Services | -$3,200.00
Jan 14, 2024 | Lab Testing Equipment | -$6,300.00
Jan 15, 2024 | Patient Deposits | +$5,600.00
Jan 16, 2024 | Rent Payment | -$25,000.00
Jan 17, 2024 | Surgery Department Revenue | +$35,750.00
Jan 18, 2024 | Insurance Provider Payment | +$22,300.00
Jan 19, 2024 | Medical Records Software | -$1,800.00
Jan 20, 2024 | Telehealth Service Revenue | +$4,200.00
Jan 21, 2024 | Staff Payroll | -$35,000.00
Jan 22, 2024 | Emergency Repairs | -$5,600.00
Jan 23, 2024 | Diagnostic Services Revenue | +$12,400.00
Jan 24, 2024 | Professional Licenses | -$3,000.00
Jan 25, 2024 | Pharmacy Revenue | +$8,900.00
Jan 26, 2024 | Internet & Telecom | -$2,800.00
Jan 27, 2024 | Patient Refunds | -$1,200.00
Jan 28, 2024 | X-Ray Services Revenue | +$7,600.00
Jan 29, 2024 | Continuing Education | -$2,500.00
Jan 30, 2024 | Cardiology Department Revenue | +$19,200.00
Jan 31, 2024 | Insurance Verification | -$500.00

SUMMARY
Total Deposits: $328,200.00
Total Withdrawals: $177,500.00
Net Change: +$150,700.00

CLOSING BALANCE
Closing Balance (January 31, 2024): $600,700.00
```

---

## 🛠️ Creating a Test PDF

### Option 1: Using Google Docs (Recommended)
1. Copy the sample data above
2. Paste into Google Docs
3. Format nicely
4. Download as PDF
5. Upload to the system

### Option 2: Using LibreOffice
1. Create new Writer document
2. Paste sample data
3. Export as PDF
4. Upload to system

### Option 3: Using Online Tools
1. Go to https://html2pdf.com/
2. Paste HTML version of data
3. Convert to PDF
4. Download and upload

---

## ✅ Test Scenarios

### Scenario 1: Basic Upload & KPI Calculation

**Steps:**
1. Login to dashboard
2. Upload sample bank statement PDF
3. Wait 5-10 seconds for processing

**Expected Results:**
- PDF uploaded successfully
- 15+ KPI cards appear
- Charts populate with data
- Status shows "Processed"

**Check These KPIs:**
- Total Revenue: ~$328,200
- Total Expenses: ~$177,500
- Profit Margin: ~52%
- Expense Ratio: ~54%

---

### Scenario 2: Alert Generation

**Setup:**
- Create a bank statement with HIGH expenses (80%+ of revenue)
- Or NEGATIVE profit margin

**Expected:**
- Red "CRITICAL" alert appears
- Alert box shows warning message
- KPI card shows red status

---

### Scenario 3: AI Chat Functionality

**Test Queries:**
1. "What is our current profit margin?"
   - Expected: Gets percentage from latest KPIs

2. "How many days of expenses can we cover?"
   - Expected: Shows liquidity analysis

3. "Are we profitable?"
   - Expected: Yes/No with profit margin details

4. "What were our total revenues?"
   - Expected: Shows exact amount from statement

---

### Scenario 4: Multiple Uploads

**Steps:**
1. Upload first bank statement
2. Upload second statement for different month
3. Go to comparison page

**Expected:**
- Both statements listed
- KPI comparison shows trends
- Can see month-over-month changes

---

## 🔍 Debugging Tips

### Check Django Logs
```bash
# Terminal where you ran runserver
# Look for error messages
# Check for 500 errors
```

### Check Database
```bash
mysql -u hospital_user -p hospital_kpi
SHOW TABLES;
SELECT * FROM kpi_bankstatement;
SELECT * FROM kpi_kpimetric;
```

### Check AI Processing
```python
# In Django shell
python manage.py shell

from kpi.models import BankStatement, AIAnalysis

# Check latest upload
stmt = BankStatement.objects.latest('upload_date')
print(stmt.is_processed)
print(stmt.processing_error)

# Check if KPIs exist
stmt.kpi_metrics.all()

# Check if analyses exist
AIAnalysis.objects.filter(bank_statement=stmt)
```

### Enable Debug Logging
```python
# In settings.py, add:
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
}
```

---

## 🚀 Performance Testing

### Load Testing
```bash
# Install locust
pip install locust

# Create locustfile.py with:
from locust import HttpUser, task, between

class UserBehavior(HttpUser):
    wait_time = between(1, 3)
    
    @task
    def load_dashboard(self):
        self.client.get("/dashboard/")

# Run test
locust -f locustfile.py -u 100 -r 10
```

### Response Time Checks
- Dashboard load: Should be < 2 seconds
- PDF upload: Should be < 20 seconds
- AI response: Should be < 15 seconds
- KPI calculation: Should be < 5 seconds

---

## 📊 Sample Expected KPI Values

With the sample data provided:

```
Total Revenue           $328,200.00
Total Expenses          $177,500.00
Net Income              $150,700.00

Profit Margin           45.9%
Expense Ratio           54.1%
Operating Margin        45.9%

Daily Revenue           $10,941.00
Daily Expense           $5,916.67

Liquidity Days          101.4 days
Cash Conversion Ratio   45.9%

Transaction Count       30
Avg Transaction Size    $16,823.33
```

---

## 🧹 Cleanup & Reset

### Reset Database
```bash
# Delete all data (careful!)
python manage.py flush

# Or delete specific table
python manage.py shell
from kpi.models import BankStatement
BankStatement.objects.all().delete()
```

### Clear Media Files
```bash
# Remove uploaded PDFs
rm -rf media/bank_statements/*
```

### Create Fresh Superuser
```bash
python manage.py createsuperuser
```

---

## 🔐 Security Testing

### Test Authentication
1. Try accessing `/dashboard/` without login
   - Should redirect to login page
2. Try accessing with invalid credentials
   - Should show error message
3. Try accessing with valid credentials
   - Should load dashboard

### Test File Upload Security
1. Try uploading non-PDF file
   - Should reject or show error
2. Try uploading malicious file
   - Should validate safely
3. Check file size limits
   - Large files should be handled gracefully

---

## 📈 Data Validation Testing

### Test KPI Calculations
1. Verify: Net Income = Revenue - Expenses
2. Verify: Profit Margin = (Net Income / Revenue) * 100
3. Verify: Liquidity Days = Balance / (Daily Expenses)

### Test Data Extraction
1. Check extracted transactions match PDF
2. Verify dates are parsed correctly
3. Confirm amounts match exactly

---

## 🎯 User Acceptance Testing (UAT)

### Checklist for Stakeholders

**Functional**
- [ ] Can login successfully
- [ ] Can upload PDF files
- [ ] KPIs display correctly
- [ ] Alerts appear for concerning metrics
- [ ] Charts show data accurately
- [ ] Can ask AI questions
- [ ] Can compare multiple statements
- [ ] Can logout successfully

**Performance**
- [ ] Dashboard loads quickly
- [ ] AI responses are timely
- [ ] PDF upload completes in reasonable time
- [ ] Charts render smoothly

**Usability**
- [ ] Interface is intuitive
- [ ] Buttons are responsive
- [ ] Error messages are clear
- [ ] Help text is available

**Data Accuracy**
- [ ] KPIs match manual calculations
- [ ] Extracted data is accurate
- [ ] Alerts trigger appropriately
- [ ] No data loss between uploads

---

## 📞 Support During Testing

If you encounter issues:

1. **Check logs** in terminal running Django
2. **Verify configuration** in settings.py
3. **Check database** connection and tables
4. **Test API** endpoints with curl or Postman
5. **Clear cache** and restart server
6. **Check internet** connection for Gemini API

### Sample curl Test
```bash
# Test login
curl -X POST http://localhost:8000/login/ \
  -d "username=admin&password=your_password"

# Test upload
curl -X POST http://localhost:8000/upload/ \
  -F "bank_statement=@statement.pdf" \
  -H "X-CSRFToken: token"
```

---

## ✨ What to Expect

### On First Upload:
1. PDF loads successfully
2. Processing spinner shows
3. After ~10 seconds, KPI cards appear
4. Charts populate with data
5. Alerts and insights display

### On Dashboard:
1. 6-8 KPI cards in green (healthy)
2. If expense ratio > 70%, cards turn yellow/red
3. Charts show revenue vs expenses
4. Alerts section shows any warnings
5. AI chat ready for questions

---

## 🎉 You're Ready to Test!

Start with Scenario 1 and work your way through the test cases. Your system is now fully functional!

**Quick Commands:**
```bash
# Start server
python manage.py runserver

# Access dashboard
# http://localhost:8000

# Login with your superuser
# credentials

# Upload sample PDF
# Watch KPIs calculate!
```

Happy testing! 🚀
