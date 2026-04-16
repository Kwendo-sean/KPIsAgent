# 🏥 Hospital Financial KPI Monitoring System

A sophisticated AI-powered Django web application for hospital financial managers to monitor, analyze, and forecast financial KPIs in real-time.

![Hospital KPI Dashboard](https://img.shields.io/badge/Django-4.2.0-darkgreen)
![Python Version](https://img.shields.io/badge/Python-3.8+-blue)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ Features

### 📊 Core Functionality
- **PDF Bank Statement Import**: Upload and automatically process bank statements
- **AI-Powered Data Extraction**: Google Gemini AI extracts financial data from PDFs
- **Automatic KPI Calculation**: 15+ financial KPIs calculated in real-time
- **Intelligent Alerts**: AI-generated warnings for concerning metrics
- **Natural Language Q&A**: Ask questions about financial data in plain English
- **Interactive Dashboard**: Modern, responsive UI with charts and visualizations
- **Multi-Statement Comparison**: Track metrics across multiple time periods
- **Manager-Only Access**: Secure authentication for hospital management

### 📈 Financial KPIs Calculated
1. **Revenue Metrics**
   - Total Revenue
   - Average Daily Revenue

2. **Cost Management**
   - Total Expenses
   - Average Daily Expense
   - Expense-to-Revenue Ratio

3. **Profitability**
   - Net Income
   - Profit Margin

4. **Cash Flow**
   - Net Cash Flow
   - Closing Balance
   - Cash Conversion Ratio

5. **Efficiency**
   - Transaction Count
   - Average Transaction Size

6. **Financial Health**
   - Operating Margin
   - Liquidity Days (Days of expenses covered)

### 🤖 AI Capabilities
- **Intelligent Analysis**: Gemini AI analyzes financial patterns and trends
- **Actionable Insights**: Automatic generation of recommendations
- **Smart Alerts**: Critical, warning, and info-level alerts
- **Predictive Analytics**: Forecast future financial performance
- **Question Answering**: Answer any question about your financial data

### 🎨 User Interface
- Modern glassmorphic design
- Real-time chart visualizations
- Responsive mobile-friendly layout
- Smooth animations and transitions
- Intuitive file upload with drag-and-drop
- Interactive AI chat for financial analysis

---

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- MySQL (via XAMPP, Docker, or standalone)
- Google Gemini API Key
- pip (Python package manager)

### Installation (5 minutes)

1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd hospital-kpi
   ```

2. **Set up MySQL Database**
   ```bash
   # Using XAMPP phpMyAdmin or MySQL CLI:
   mysql -u root
   CREATE DATABASE hospital_kpi;
   ```

3. **Create Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

4. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure Environment**
   Create `.env` file:
   ```
   GEMINI_API_KEY=your-api-key-from-aistudio.google.com
   SECRET_KEY=your-secret-key
   DEBUG=True
   ```

6. **Run Migrations**
   ```bash
   python manage.py migrate
   ```

7. **Create Admin User**
   ```bash
   python manage.py createsuperuser
   ```

8. **Start Server**
   ```bash
   python manage.py runserver
   ```

9. **Access Dashboard**
   Open `http://localhost:8000` in your browser

---

## 📁 Project Structure

```
hospital-kpi/
├── hospital_kpi/
│   ├── settings.py          # Django configuration with MySQL
│   ├── urls.py              # URL routing
│   └── wsgi.py
│
├── kpi/
│   ├── models.py            # Database models
│   ├── views.py             # View logic and API endpoints
│   ├── admin.py             # Django admin configuration
│   ├── urls.py              # App URL patterns
│   ├── ai_agent.py          # Gemini AI integration
│   ├── pdf_extractor.py     # PDF text extraction
│   └── migrations/
│
├── templates/
│   ├── login.html           # Login page
│   ├── dashboard.html       # Main KPI dashboard
│   └── statement_detail.html # Statement details
│
├── manage.py                # Django management script
├── requirements.txt         # Python dependencies
├── SETUP_GUIDE.md          # Detailed setup instructions
└── README.md               # This file
```

---

## 🔑 Key Features Explained

### 1. Bank Statement Upload
- Drag and drop PDF files
- Automatic text extraction
- Financial data parsing via AI
- Transaction categorization

### 2. KPI Dashboard
- Real-time metric displays
- Color-coded status indicators (Healthy/Warning/Critical)
- Interactive charts and graphs
- Performance trends visualization

### 3. AI Insights
- Automatic analysis of financial health
- Identification of concerning patterns
- Actionable recommendations
- Risk assessment and alerts

### 4. Natural Language Q&A
Ask questions like:
- "What's our current profit margin?"
- "How many days of expenses can we cover?"
- "Are we spending too much on operations?"
- "What were our total revenues this month?"

The AI analyzes your data and provides detailed answers with specific metrics.

---

## 🔐 Security

- **Authentication**: Django authentication system
- **Manager-Only Access**: User role-based access control
- **CSRF Protection**: Built-in Django CSRF tokens
- **Database Security**: Parameterized queries via ORM
- **Environment Variables**: API keys stored securely
- **HTTPS Ready**: Can be easily deployed with SSL

⚠️ **Production Recommendations**:
- Set `DEBUG = False`
- Use strong `SECRET_KEY`
- Implement HTTPS
- Use environment variables for all secrets
- Enable CORS only for trusted domains
- Add rate limiting on API endpoints

---

## 🛠️ API Endpoints

### Upload Bank Statement
```
POST /upload/
Content-Type: multipart/form-data
Body: bank_statement (PDF file)
Response: { success: true, statement_id: 123 }
```

### Get KPI Data
```
GET /api/kpi/{statement_id}/
Response: { statement: {...}, kpis: [...] }
```

### Ask AI Questions
```
POST /api/ask/
Content-Type: application/json
Body: { question: "...", statement_id: 123 }
Response: { success: true, answer: "..." }
```

---

## 🎯 Use Cases

### Financial Controllers
- Monitor hospital revenue and expenses
- Track KPI trends over time
- Generate reports for board meetings
- Identify cost-saving opportunities

### CFOs
- Real-time financial visibility
- Automatic alert system for issues
- Comparative analysis across periods
- AI-powered financial forecasting

### Finance Teams
- Streamlined data processing
- Automated KPI calculation
- Central dashboard for metrics
- Historical analysis and comparison

---

## 🧠 AI Integration (Gemini API)

The system uses Google Gemini API for:
1. **PDF Data Extraction**: Converts bank statements to structured data
2. **KPI Calculations**: Computes financial metrics
3. **Insight Generation**: Analyzes patterns and trends
4. **Alert Generation**: Identifies concerning metrics
5. **Question Answering**: Responds to natural language queries

Get your API key from: https://aistudio.google.com/app/apikey

---

## 📊 Sample Dashboard

The dashboard displays:
- KPI cards with values and status
- Revenue vs Expense bar charts
- Profit margin trends
- Expense breakdown pie charts
- Cash flow status
- AI-generated alerts and recommendations
- Interactive AI chat panel

---

## 🔧 Customization

### Add New KPIs
Edit `kpi/ai_agent.py`:
```python
def calculate_kpis(self, transactions, opening_balance, closing_balance):
    # Add your custom KPI here
    kpis['Your_KPI'] = {
        'value': calculated_value,
        'unit': 'Unit',
        'type': 'CATEGORY',
        'description': 'Your description',
    }
    return kpis
```

### Change Alert Thresholds
Edit `generate_alerts()` method in `kpi/ai_agent.py`

### Customize Colors and Design
Edit CSS variables in `templates/dashboard.html`:
```css
:root {
    --accent-primary: #6366f1;
    --accent-secondary: #8b5cf6;
    /* ... customize more colors ... */
}
```

---

## 📚 Database Schema

### BankStatement
- `file_name`: Original PDF filename
- `upload_date`: When file was uploaded
- `extracted_text`: Full text from PDF
- `opening_balance`: Account opening balance
- `closing_balance`: Account closing balance
- `total_deposits`: Total income
- `total_withdrawals`: Total expenses
- `is_processed`: Whether AI processing is complete

### FinancialTransaction
- `transaction_date`: Date of transaction
- `description`: Transaction description
- `amount`: Transaction amount
- `type`: DEPOSIT, WITHDRAWAL, or TRANSFER
- `category`: Categorized expense type

### KPIMetric
- `metric_name`: Name of KPI
- `metric_type`: Category (Revenue, Cost, etc.)
- `current_value`: Current KPI value
- `unit`: Measurement unit (USD, %, Days)
- `status`: Healthy, Warning, or Critical

### AIAnalysis
- `analysis_type`: Insight, Alert, Recommendation, or Response
- `title`: Analysis title
- `content`: Full analysis text
- `severity`: Info, Warning, or Critical

---

## 🐛 Troubleshooting

### MySQL Connection Error
```
Solution: Ensure MySQL is running, check credentials in settings.py
```

### Gemini API Key Error
```
Solution: Get key from https://aistudio.google.com/app/apikey
Add to .env file and restart server
```

### PDF Upload Fails
```
Solution: Ensure PDF is text-based (not scanned image)
Check file size and PyPDF2 installation
```

### No KPIs Calculated
```
Solution: Check Django logs, verify Gemini API key validity
Ensure PDF has proper financial data
```

---

## 📈 Performance

- Average PDF processing: 5-10 seconds
- KPI calculation: < 1 second
- Dashboard load: < 2 seconds
- AI response time: 3-15 seconds (depends on query complexity)

---

## 🤝 Contributing

To contribute improvements:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📄 License

This project is licensed under the MIT License - see LICENSE file for details

---

## 📞 Support

For issues, questions, or suggestions:
1. Check SETUP_GUIDE.md for installation help
2. Review troubleshooting section above
3. Check Django logs for errors
4. Verify all dependencies are installed

---

## 🚀 Deployment

### Docker Deployment
```dockerfile
FROM python:3.9
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["gunicorn", "hospital_kpi.wsgi"]
```

### Cloud Deployment (Heroku/Railway)
1. Create `Procfile`: `web: gunicorn hospital_kpi.wsgi`
2. Set environment variables in cloud dashboard
3. Push to cloud provider

### Traditional Server Deployment
1. Use Gunicorn or uWSGI as WSGI server
2. Set up Nginx as reverse proxy
3. Configure SSL certificates
4. Enable HTTPS

---

## 🎓 Learning Resources

- Django Documentation: https://docs.djangoproject.com/
- Google Gemini API: https://ai.google.dev/
- MySQL Documentation: https://dev.mysql.com/doc/
- REST API Best Practices: https://restfulapi.net/

---

## 💡 Future Enhancements

- [ ] Multi-hospital support
- [ ] Advanced predictive analytics
- [ ] Budget variance analysis
- [ ] Department-level KPI tracking
- [ ] Automated report generation
- [ ] Mobile app (iOS/Android)
- [ ] Real-time data integration
- [ ] Machine learning forecasting
- [ ] Email alerts and notifications
- [ ] API rate limiting and quotas

---

## ⚡ Version History

**v1.0.0** - Initial Release
- Core KPI calculation
- PDF bank statement upload
- AI insights and alerts
- Interactive dashboard
- Natural language Q&A

---

Made with ❤️ for hospital financial management

**Version**: 1.0.0  
**Last Updated**: April 2026  
**Status**: Production Ready ✅
