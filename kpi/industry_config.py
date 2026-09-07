"""
Industry-specific KPI definitions, AI context, and transaction category hints.
"""

INDUSTRIES = {
    # ── Healthcare ─────────────────────────────────────────────
    "HOSPITAL": {
        "label": "Hospital / Healthcare Facility",
        "sector": "Healthcare",
        "revenue_keywords": ["NHIF","PATIENT","CONSULTATION","PHARMACY","SURGERY","ADMISSION",
                             "INSURANCE","MATERNITY","RADIOLOGY","EMERGENCY","DENTAL","LAB"],
        "expense_keywords": ["MEDICAL SUPPLIES","DRUGS","PHARMACEUTICAL","STAFF SALARY",
                             "UTILITIES","RENT","EQUIPMENT","MAINTENANCE","LAUNDRY"],
        "ai_context": (
            "You are a financial analyst for a hospital or healthcare facility. "
            "Key metrics: patient revenue, cost per patient day, bed occupancy revenue, "
            "NHIF reimbursement rate, pharmaceutical margin, and operating cost ratio. "
            "Note seasonal admission trends and insurance reimbursement cycles."
        ),
    },
    "PHARMACY": {
        "label": "Pharmacy",
        "sector": "Healthcare",
        "revenue_keywords": ["DRUG SALES","PRESCRIPTION","OTC","MEDICINE","SUPPLEMENT"],
        "expense_keywords": ["DRUG PURCHASE","SUPPLIER","PHARMACIST SALARY","RENT","LICENSE"],
        "ai_context": (
            "You are a financial analyst for a pharmacy. "
            "Key metrics: gross margin on drug sales, prescription-to-OTC ratio, "
            "inventory turnover, shrinkage rate, and supplier payment cycles."
        ),
    },
    "DENTAL": {
        "label": "Dental / Optical Practice",
        "sector": "Healthcare",
        "revenue_keywords": ["CONSULTATION","PROCEDURE","FILLING","EXTRACTION","CROWN",
                             "BRACES","OPTICAL","GLASSES","LENS"],
        "expense_keywords": ["DENTAL SUPPLIES","EQUIPMENT","STAFF SALARY","RENT","UTILITIES"],
        "ai_context": (
            "You are a financial analyst for a dental or optical practice. "
            "Key metrics: revenue per chair/station, procedure mix, patient retention, "
            "and supply cost ratio."
        ),
    },
    "MED_LAB": {
        "label": "Medical Laboratory / Diagnostics",
        "sector": "Healthcare",
        "revenue_keywords": ["TEST","SAMPLE","DIAGNOSTIC","RADIOLOGY","SCAN","LABORATORY"],
        "expense_keywords": ["REAGENTS","EQUIPMENT","STAFF SALARY","MAINTENANCE","RENT"],
        "ai_context": (
            "You are a financial analyst for a medical laboratory. "
            "Key metrics: cost per test, equipment utilisation, turnaround time cost, "
            "and referral revenue."
        ),
    },
    # ── Finance & Banking ──────────────────────────────────────
    "MICROFINANCE": {
        "label": "Bank / Microfinance Institution",
        "sector": "Finance & Banking",
        "revenue_keywords": ["INTEREST INCOME","LOAN REPAYMENT","FEES","CHARGES","COMMISSION"],
        "expense_keywords": ["INTEREST EXPENSE","STAFF SALARY","OPERATIONAL COSTS",
                             "PROVISIONS","RENT"],
        "ai_context": (
            "You are a financial analyst for a bank or microfinance institution. "
            "Key metrics: net interest margin, loan portfolio quality, cost-to-income ratio, "
            "return on assets, and liquidity ratio."
        ),
    },
    "INSURANCE": {
        "label": "Insurance Company",
        "sector": "Finance & Banking",
        "revenue_keywords": ["PREMIUM","POLICY","COMMISSION INCOME","INVESTMENT INCOME"],
        "expense_keywords": ["CLAIMS","REINSURANCE","COMMISSION EXPENSE","STAFF SALARY","ADMIN"],
        "ai_context": (
            "You are a financial analyst for an insurance company. "
            "Key metrics: loss ratio, expense ratio, combined ratio, premium growth, "
            "and claims frequency."
        ),
    },
    "SACCO": {
        "label": "SACCO / Credit Union",
        "sector": "Finance & Banking",
        "revenue_keywords": ["MEMBER SAVINGS","LOAN INTEREST","DIVIDENDS","FEES"],
        "expense_keywords": ["INTEREST ON SAVINGS","STAFF SALARY","OPERATIONAL COSTS",
                             "DIVIDEND PAYOUT"],
        "ai_context": (
            "You are a financial analyst for a SACCO or credit union. "
            "Key metrics: member savings growth, loan-to-savings ratio, delinquency rate, "
            "and dividend yield."
        ),
    },
    # ── Retail & Commerce ──────────────────────────────────────
    "RETAIL": {
        "label": "General Retail / Shop",
        "sector": "Retail & Commerce",
        "revenue_keywords": ["SALES","CASH SALES","TILL","POS","MPESA TILL","LIPA NA"],
        "expense_keywords": ["STOCK PURCHASE","RENT","STAFF SALARY","UTILITIES","PACKAGING"],
        "ai_context": (
            "You are a financial analyst for a retail shop. "
            "Key metrics: gross margin, sales per day, stock turnover rate, shrinkage, "
            "and rent-to-sales ratio."
        ),
    },
    "SUPERMARKET": {
        "label": "Supermarket / Grocery Store",
        "sector": "Retail & Commerce",
        "revenue_keywords": ["SALES","TILL","POS","GROCERY SALES","FRESH PRODUCE"],
        "expense_keywords": ["STOCK PURCHASE","SUPPLIER","STAFF","UTILITIES","RENT","COLD CHAIN"],
        "ai_context": (
            "You are a financial analyst for a supermarket or grocery store. "
            "Key metrics: average basket size, category margin, shrinkage rate, "
            "inventory days, and footfall revenue correlation."
        ),
    },
    "ECOMMERCE": {
        "label": "E-commerce Business",
        "sector": "Retail & Commerce",
        "revenue_keywords": ["ONLINE SALES","MPESA","CARD PAYMENT","MARKETPLACE","DELIVERY FEE"],
        "expense_keywords": ["PRODUCT COST","LOGISTICS","MARKETING","PLATFORM FEE",
                             "PACKAGING","RETURNS"],
        "ai_context": (
            "You are a financial analyst for an e-commerce business. "
            "Key metrics: customer acquisition cost, average order value, return rate, "
            "fulfilment cost per order, and payment gateway fees."
        ),
    },
    # ── Food & Hospitality ─────────────────────────────────────
    "RESTAURANT": {
        "label": "Restaurant / Café / Fast Food",
        "sector": "Food & Hospitality",
        "revenue_keywords": ["FOOD SALES","DRINK SALES","TILL","POS","DELIVERY","CATERING"],
        "expense_keywords": ["FOOD COST","BEVERAGE COST","STAFF","RENT","UTILITIES",
                             "CLEANING","PACKAGING"],
        "ai_context": (
            "You are a financial analyst for a restaurant or food service business. "
            "Key metrics: food cost %, beverage cost %, labour cost %, covers per day, "
            "average spend per cover, and prime cost ratio."
        ),
    },
    "HOTEL": {
        "label": "Hotel / Lodging / Guesthouse",
        "sector": "Food & Hospitality",
        "revenue_keywords": ["ROOM REVENUE","F&B","EVENTS","SPA","CONFERENCE","BOOKING"],
        "expense_keywords": ["HOUSEKEEPING","MAINTENANCE","STAFF","FOOD COST","UTILITIES",
                             "LAUNDRY","MARKETING"],
        "ai_context": (
            "You are a financial analyst for a hotel or lodging facility. "
            "Key metrics: RevPAR, occupancy rate, ADR (average daily rate), "
            "F&B margin, and GOPPAR."
        ),
    },
    "CATERING": {
        "label": "Event Catering / Banqueting",
        "sector": "Food & Hospitality",
        "revenue_keywords": ["EVENT PAYMENT","CATERING FEE","DEPOSIT","BOOKING"],
        "expense_keywords": ["FOOD COST","EQUIPMENT HIRE","STAFF","TRANSPORT","PACKAGING"],
        "ai_context": (
            "You are a financial analyst for a catering or events company. "
            "Key metrics: cost per cover, food cost %, equipment utilisation, "
            "and event pipeline revenue."
        ),
    },
    # ── Education ──────────────────────────────────────────────
    "SCHOOL": {
        "label": "School / College / University",
        "sector": "Education",
        "revenue_keywords": ["FEES","SCHOOL FEES","TUITION","CAPITATION","REGISTRATION",
                             "EXAM FEES","BOARDING"],
        "expense_keywords": ["STAFF SALARY","TEACHING MATERIALS","UTILITIES","MAINTENANCE",
                             "EXAM FEES","RENT","TRANSPORT"],
        "ai_context": (
            "You are a financial analyst for an educational institution. "
            "Key metrics: fee collection rate, cost per student, staff-to-student ratio cost, "
            "capitation utilisation, and term-over-term fee growth."
        ),
    },
    "TRAINING": {
        "label": "Training Center / Coaching Institute",
        "sector": "Education",
        "revenue_keywords": ["TRAINING FEE","COURSE FEE","CERTIFICATION","WORKSHOP"],
        "expense_keywords": ["TRAINER SALARY","MATERIALS","VENUE","MARKETING","UTILITIES"],
        "ai_context": (
            "You are a financial analyst for a training or coaching business. "
            "Key metrics: revenue per trainee, course fill rate, trainer utilisation, "
            "and marketing cost per enrolment."
        ),
    },
    # ── Real Estate & Construction ─────────────────────────────
    "REAL_ESTATE": {
        "label": "Real Estate Agency / Property Management",
        "sector": "Real Estate & Construction",
        "revenue_keywords": ["RENT COLLECTED","COMMISSION","MANAGEMENT FEE","SALE PROCEEDS","LEASE"],
        "expense_keywords": ["MAINTENANCE","STAFF SALARY","MARKETING","LEGAL FEES",
                             "INSURANCE","UTILITIES"],
        "ai_context": (
            "You are a financial analyst for a real estate agency or property management company. "
            "Key metrics: rent collection rate, occupancy rate, management fee income, "
            "cost per managed unit, and tenant arrears ratio."
        ),
    },
    "CONSTRUCTION": {
        "label": "Construction / Contracting",
        "sector": "Real Estate & Construction",
        "revenue_keywords": ["CONTRACT PAYMENT","PROGRESS BILLING","RETENTION RELEASE",
                             "PROJECT PAYMENT"],
        "expense_keywords": ["MATERIALS","LABOUR","SUBCONTRACTOR","EQUIPMENT HIRE",
                             "FUEL","SITE COSTS"],
        "ai_context": (
            "You are a financial analyst for a construction or contracting business. "
            "Key metrics: gross margin per project, labour cost ratio, materials cost ratio, "
            "retention receivable, and cash conversion cycle."
        ),
    },
    # ── Non-Profit & Public ────────────────────────────────────
    "NGO": {
        "label": "NGO / Non-Profit Organization",
        "sector": "Non-Profit & Public",
        "revenue_keywords": ["DONATION","GRANT","FUNDING","CONTRIBUTION","SPONSORSHIP"],
        "expense_keywords": ["PROGRAMME COSTS","STAFF SALARY","ADMIN","TRAVEL","REPORTING"],
        "ai_context": (
            "You are a financial analyst for an NGO or non-profit. "
            "Key metrics: programme expenditure ratio, administrative cost ratio, "
            "donor fund utilisation, and reserve ratio."
        ),
    },
    "CHURCH": {
        "label": "Church / Religious Organization",
        "sector": "Non-Profit & Public",
        "revenue_keywords": ["TITHE","OFFERING","DONATION","PROJECT CONTRIBUTION","FUNDRAISING"],
        "expense_keywords": ["PASTOR SALARY","UTILITIES","MAINTENANCE","OUTREACH","ADMIN"],
        "ai_context": (
            "You are a financial analyst for a church or religious organisation. "
            "Key metrics: giving trends, programme spend ratio, building fund progress, "
            "and reserve levels."
        ),
    },
    "GOVERNMENT": {
        "label": "Government / Public Sector Entity",
        "sector": "Non-Profit & Public",
        "revenue_keywords": ["EXCHEQUER","ALLOCATION","GRANTS","LEVIES","FEES"],
        "expense_keywords": ["RECURRENT EXPENDITURE","CAPITAL EXPENDITURE","STAFF SALARY","SUPPLIES"],
        "ai_context": (
            "You are a financial analyst for a government or public sector entity. "
            "Key metrics: budget utilisation rate, recurrent vs capital expenditure ratio, "
            "and fund absorption rate."
        ),
    },
    # ── Manufacturing & Agriculture ────────────────────────────
    "MANUFACTURING": {
        "label": "Manufacturing / Production",
        "sector": "Manufacturing & Agriculture",
        "revenue_keywords": ["PRODUCT SALES","CONTRACT MANUFACTURING","WHOLESALE","EXPORT"],
        "expense_keywords": ["RAW MATERIALS","LABOUR","ENERGY","MAINTENANCE","PACKAGING",
                             "LOGISTICS"],
        "ai_context": (
            "You are a financial analyst for a manufacturing business. "
            "Key metrics: COGS ratio, raw material cost %, production efficiency, "
            "energy cost per unit, and working capital cycle."
        ),
    },
    "AGRICULTURE": {
        "label": "Agriculture / Agribusiness / Farm",
        "sector": "Manufacturing & Agriculture",
        "revenue_keywords": ["CROP SALES","LIVESTOCK","PRODUCE","HARVEST","EXPORT",
                             "CONTRACT FARMING"],
        "expense_keywords": ["INPUTS","SEEDS","FERTILISER","LABOUR","FUEL","IRRIGATION",
                             "STORAGE","TRANSPORT"],
        "ai_context": (
            "You are a financial analyst for an agricultural or agribusiness operation. "
            "Key metrics: revenue per acre, input cost ratio, seasonal cash flow patterns, "
            "produce price volatility impact, and storage loss rate."
        ),
    },
    # ── Professional Services ──────────────────────────────────
    "LAW_FIRM": {
        "label": "Law Firm",
        "sector": "Professional Services",
        "revenue_keywords": ["LEGAL FEES","RETAINER","LITIGATION FEE","CONVEYANCING",
                             "CONSULTATION FEE"],
        "expense_keywords": ["ADVOCATE SALARY","COURT FEES","RENT","UTILITIES","INSURANCE","ADMIN"],
        "ai_context": (
            "You are a financial analyst for a law firm. "
            "Key metrics: revenue per fee earner, matter profitability, lock-up "
            "(WIP + debtors), collection rate, and overhead ratio."
        ),
    },
    "ACCOUNTING_FIRM": {
        "label": "Accounting / Audit Firm",
        "sector": "Professional Services",
        "revenue_keywords": ["AUDIT FEES","TAX ADVISORY","BOOKKEEPING FEE","CONSULTING FEE",
                             "RETAINER"],
        "expense_keywords": ["STAFF SALARY","PROFESSIONAL FEES","SOFTWARE","RENT",
                             "CPD TRAINING","INSURANCE"],
        "ai_context": (
            "You are a financial analyst for an accounting or audit firm. "
            "Key metrics: revenue per staff, client retention rate, utilisation rate, "
            "and fee collection days."
        ),
    },
    "CONSULTING": {
        "label": "Consulting / Advisory Firm",
        "sector": "Professional Services",
        "revenue_keywords": ["CONSULTING FEE","ADVISORY FEE","RETAINER","PROJECT FEE"],
        "expense_keywords": ["STAFF SALARY","TRAVEL","MARKETING","TECHNOLOGY","RENT"],
        "ai_context": (
            "You are a financial analyst for a consulting or advisory firm. "
            "Key metrics: revenue per consultant, project margin, pipeline conversion rate, "
            "and client lifetime value."
        ),
    },
    # ── Transport & Logistics ──────────────────────────────────
    "TRANSPORT": {
        "label": "Transport / Logistics / Fleet Management",
        "sector": "Transport & Logistics",
        "revenue_keywords": ["FREIGHT","HAULAGE","MATATU","BUS REVENUE","DELIVERY FEE",
                             "TRANSPORT FEE"],
        "expense_keywords": ["FUEL","DRIVER SALARY","MAINTENANCE","INSURANCE","TYRES",
                             "VEHICLE LOAN","LICENSING"],
        "ai_context": (
            "You are a financial analyst for a transport or logistics business. "
            "Key metrics: revenue per kilometre, fuel cost per kilometre, "
            "vehicle utilisation rate, maintenance cost per vehicle, and fleet ROI."
        ),
    },
}

# Flat list of (key, label) for Django model choices
INDUSTRY_CHOICES = [(k, v["label"]) for k, v in INDUSTRIES.items()]

# Sector-grouped list for template rendering
INDUSTRY_SECTORS: dict[str, list[tuple[str, str]]] = {}
for _k, _v in INDUSTRIES.items():
    INDUSTRY_SECTORS.setdefault(_v["sector"], []).append((_k, _v["label"]))


def get_industry(key: str) -> dict:
    return INDUSTRIES.get(key, INDUSTRIES["HOSPITAL"])


def get_industry_label(key: str) -> str:
    return INDUSTRIES.get(key, {}).get("label", "Unknown Industry")


def get_ai_context(key: str) -> str:
    return INDUSTRIES.get(key, INDUSTRIES["HOSPITAL"]).get("ai_context", "")


def get_categories(key: str) -> dict:
    ind = INDUSTRIES.get(key, INDUSTRIES["HOSPITAL"])
    return {
        "revenue": ind.get("revenue_keywords", []),
        "expense": ind.get("expense_keywords", []),
    }
