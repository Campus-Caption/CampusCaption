@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe python -m venv .venv
rem Install on the first run, and again whenever a requirements file changed (e.g. a new dependency after git pull)
copy /b requirements.txt + requirements-local.txt .venv\requirements.now >nul
fc /b .venv\requirements.now .venv\installed.flag >nul 2>&1 || (
  .venv\Scripts\python -m pip install -r requirements-local.txt && copy /y .venv\requirements.now .venv\installed.flag >nul
)
if not exist .env copy .env.example .env >nul
.venv\Scripts\python scripts\download_models.py

echo CampusCaption: http://127.0.0.1:8080/   
.venv\Scripts\python main.py
if errorlevel 1 pause
