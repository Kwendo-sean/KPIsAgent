# Hospital Financial KPI Monitoring System
## Setup & Installation Guide

### Overview
This is a Django-based hospital financial KPI monitoring system that:
- Accepts PDF bank statements as input
- Extracts financial data using Google Gemini AI
- Calculates comprehensive KPIs automatically
- Generates insights, alerts, and forecasts using AI
- Provides an interactive dashboard for top-level managers
- Allows natural language Q&A about financial data

### Prerequisites
- Python 3.8+
- XAMPP (with MySQL running)
- Google Gemini API Key
- pip (Python package manager)

---

## Installation Steps

### 1. Set Up MySQL Database (XAMPP)

**Start XAMPP:**
- Open XAMPP Control Panel
- Start Apache and MySQL services

**Create Database:**
```bash
# Open MySQL shell via XAMPP phpMyAdmin (http://localhost/phpmyadmin)
# OR via command line:
mysql -u root

# In MySQL console, run:
CREATE DATABASE hospital_kpi;
CREATE USER 'hospital_user'@'localhost' IDENTIFIED BY 'hospital_pass';
GRANT ALL PRIVILEGES ON hospital_kpi.* TO 'hospital_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 2. Clone/Download the Project

```bash
# Navigate to your project directory
cd /path/to/hospital-kpi
```

### 3. Create Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate it
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Set Environment Variables

Create a `.env` file in your project root:

```bash
# .env file
GEMINI_API_KEY=your-gemini-api-key-here
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

Get your Gemini API Key from: https://aistudio.google.com/app/apikey

### 6. Update Django Settings

Edit `settings.py` and update the database configuration (if using different credentials):

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'hospital_kpi',
        'USER': 'hospital_user',  # or 'root' if using default
        'PASSWORD': 'hospital_pass',  # or '' for default
        'HOST': '127.0.0.1',
        'PORT': '3306',
    }
}
```

### 7. Run Database Migrations

```bash
python manage.py makemigrations kpi
python manage.py migrate
```

### 8. Create Admin/Manager User

```bash
python manage.py createsuperuser
# Follow prompts to create admin user
# This user will have full access to the system
```

### 9. Create Test Managers (Optional)

```bash
python manage.py shell

# In Django shell:
from django.contrib.auth.models import User
from kpi.models import UserProfile

# Create test user
user = User.objects.create_user(
    username='finance_manager',
    email='manager@hospital.com',
    password='test123',
    first_name='John',
    last_name='Manager',
    is_staff=True
)

# Create profile
UserProfile.objects.create(
    user=user,
    role='MANAGER',
    department='Finance'
)
```

### 10. Run Development Server

```bash
python manage.py runserver
```

The system should now be accessible at: http://localhost:8000

---

## Usage Guide

### Login
1. Navigate to http://localhost:8000
2. Log in with your manager credentials
3. You'll see the KPI Dashboard

### Upload Bank Statement
1. On the dashboard, use the "Upload Bank Statement" section
2. Drag and drop a PDF file or click to select
3. The system will:
   - Extract financial data from the PDF
   - Calculate all KPIs
   - Generate insights and alerts
   - Analyze for concerning trends

### View KPIs
- **KPI Cards**: Display current values, units, and status (Healthy/Warning/Critical)
- **Charts**: Visualize revenue vs expenses, profit margins, cash flow, etc.
- **Alerts**: See AI-generated warnings and recommendations

### Ask AI Questions
- Type natural language questions about financial performance
- Examples:
  - "What is our current profit margin?"
  - "Are we spending too much on operations?"
  - "How many days of expenses can we cover?"
- AI will analyze your data and provide detailed answers

### Compare Multiple Statements
- Navigate to the "KPI Comparison" page
- View trends across multiple periods
- Identify patterns and changes over time

---

## Architecture Overview

### Backend Structure
```
hospital_kpi/
├── settings.py          # Django configuration
├── urls.py              # URL routing
├── models.py            # Database models
├── views.py             # View logic
├── ai_agent.py          # Gemini AI integration
├── pdf_extractor.py     # PDF extraction utility
└── requirements.txt     # Dependencies
```

### Key Components

**Models:**
- `BankStatement`: Uploaded bank statements
- `FinancialTransaction`: Individual transactions
- `KPIMetric`: Calculated KPI values
- `AIAnalysis`: Generated insights and alerts
- `UserProfile`: Manager profile data

**AI Agent:**
- Extracts financial data from PDFs
- Calculates 15+ financial KPIs
- Generates insights and alerts
- Answers natural language questions

**Supported KPIs:**
1. Total Revenue
2. Average Daily Revenue
3. Total Expenses
4. Average Daily Expense
5. Expense-to-Revenue Ratio
6. Net Income
7. Profit Margin
8. Net Cash Flow
9. Closing Balance
10. Cash Conversion Ratio
11. Transaction Count
12. Average Transaction Size
13. Operating Margin
14. Liquidity Days
15. Financial Health Metrics

---

## Customization

### Add New KPIs
Edit `ai_agent.py` in the `calculate_kpis()` method:

```python
kpis['Your_New_KPI'] = {
    'value': calculated_value,
    'unit': 'Unit',
    'type': 'CATEGORY',
    'description': 'Description of KPI',
}
```

### Change Alert Thresholds
Edit `generate_alerts()` in `ai_agent.py`:

```python
# Alert if expense ratio is too high
if kpis['Expense_to_Revenue_Ratio']['value'] > 75:  # Change threshold
    alerts.append({...})
```

### Customize Dashboard Colors
Edit the CSS variables in `dashboard.html`:

```css
:root {
    --accent-primary: #6366f1;  /* Change colors here */
    --accent-secondary: #8b5cf6;
    ...
}
```

---

## Troubleshooting

### Issue: "No module named 'google.generativeai'"
**Solution**: 
```bash
pip install google-generativeai
```

### Issue: "MySQL connection refused"
**Solution**: 
- Ensure XAMPP MySQL is running
- Check connection credentials in settings.py
- Verify MySQL port is 3306

### Issue: "GEMINI_API_KEY not set"
**Solution**:
- Get API key from https://aistudio.google.com/app/apikey
- Add to .env file or settings.py
- Restart Django server

### Issue: PDF upload fails
**Solution**:
- Ensure PDF is text-based (not scanned image)
- Check file size (max recommended 10MB)
- Verify PyPDF2 is installed: `pip install PyPDF2`

### Issue: No KPIs calculated
**Solution**:
- Check Django logs for errors
- Ensure Gemini API key is valid
- Verify PDF has proper financial data format
- Check browser console for JavaScript errors

---

## Security Notes

⚠️ **For Production:**
1. Change `SECRET_KEY` in settings.py
2. Set `DEBUG = False`
3. Use strong database password
4. Restrict `ALLOWED_HOSTS`
5. Use HTTPS
6. Implement proper authentication
7. Add rate limiting on API endpoints
8. Store Gemini API key in environment variables

---

## API Endpoints

### Upload Bank Statement
```
POST /upload/
Content-Type: multipart/form-data
Body: bank_statement (PDF file)
```

### Get KPI Data
```
GET /api/kpi/{statement_id}/
Response: JSON with KPI metrics
```

### Ask AI
```
POST /api/ask/
Content-Type: application/json
Body: {"question": "...", "statement_id": 123}
Response: {"success": true, "answer": "..."}
```

---

## Support & Issues

For issues or questions:
1. Check the troubleshooting section above
2. Review Django logs: `python manage.py runserver` output
3. Check browser console for frontend errors
4. Verify all dependencies are installed correctly

---

## Demo Credentials (Development Only)

Username: `admin`
Password: `admin123`

⚠️ Change these in production!

---

## Version Info
- Django: 4.2.0
- Python: 3.8+
- MySQL: 5.7+
- Gemini API: Latest

---

Enjoy monitoring your hospital's financial KPIs! 🏥💰
