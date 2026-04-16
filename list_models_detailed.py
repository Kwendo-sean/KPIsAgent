import google.generativeai as genai
import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
api_key = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=api_key)

with open("available_models.txt", "w") as f:
    try:
        f.write("Available models:\n")
        for m in genai.list_models():
            f.write(f"  - {m.name} (Methods: {m.supported_generation_methods})\n")
    except Exception as e:
        f.write(f"Error: {e}\n")
