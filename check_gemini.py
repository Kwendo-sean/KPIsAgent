import sys, os, django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hospital_kpi.settings')
django.setup()

import google.generativeai as genai
from django.conf import settings

api_key = settings.GEMINI_API_KEY
genai.configure(api_key=api_key)

models_to_try = [
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
    "gemini-pro",
    "models/gemini-2.0-flash",
    "models/gemini-pro",
    "gemini-2.0-flash-lite",
]

results = []
for model_name in models_to_try:
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content("Say hello in one word")
        results.append(f"SUCCESS: {model_name} => {response.text.strip()[:50]}")
        break
    except Exception as e:
        results.append(f"FAILED: {model_name} => {str(e)[:120]}")

with open("gemini_results.txt", "w") as f:
    f.write("\n".join(results))

print("Done. Check gemini_results.txt")
