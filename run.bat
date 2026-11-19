@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist .venv\installed.flag (
  .venv\Scripts\python -m pip install -r requirements-local.txt && echo ok> .venv\installed.flag
)
if not exist .env copy .env.example .env >nul
.venv\Scripts\python scripts\download_models.py

echo CampusCaption: http://127.0.0.1:8080/   
.venv\Scripts\python main.py
if errorlevel 1 pause
