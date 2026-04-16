import google.generativeai as genai
import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    api_key = os.environ.get("GEMINI_API_KEY") # Check other possible names if needed

genai.configure(api_key=api_key)

with open("available_models.txt", "w") as f:
    try:
        f.write("Listing models...\n")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                f.write(f"Model: {m.name}\n")
    except Exception as e:
        f.write(f"Error: {e}\n")
