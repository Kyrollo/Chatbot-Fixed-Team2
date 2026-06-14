"""
services/ocr-service/main.py
-----------------------------
Entry point for the OCR microservice.
Uvicorn loads this file — it simply re-exports the FastAPI app
defined in api/app.py so the ocr_service package is importable.
"""
import sys
import os

# Make sure ocr_service package is importable when uvicorn runs from this dir
sys.path.insert(0, os.path.dirname(__file__))

from api.app import app  # noqa: F401  — uvicorn uses `main:app`