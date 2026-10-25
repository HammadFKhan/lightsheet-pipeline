@echo off
rem Starts the lightsheet GUI. Uses the private environment created by setup_env.ps1 if present.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_gui.py
) else (
    python run_gui.py
)
if errorlevel 1 pause
