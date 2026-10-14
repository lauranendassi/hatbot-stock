@echo off
REM ============================================================
REM  Synchronisation du stock (appelee automatiquement par le
REM  Planificateur de taches toutes les 5 minutes).
REM  Double-clic = synchronisation immediate, utile pour tester.
REM  Option : sync_stock.bat --forcer  (regenere tout)
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
set PY=%~dp0venv\Scripts\python.exe
if not exist "%PY%" set PY=%~dp0..\Projet_Stock\venv\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" "%~dp0scripts\sync.py" %*
if "%1"=="" if not "%NOPAUSE%"=="1" (
    echo.
    echo Journal complet : %~dp0logs\sync.log
    timeout /t 8
)
