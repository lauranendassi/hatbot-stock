@echo off
REM ============================================================
REM  INSTALLATION (une seule fois) - systeme de stock WeloobeAI
REM  1. cree l'environnement Python (venv) s'il n'existe pas
REM  2. installe les bibliotheques
REM  3. cree les fichiers Excel manquants + premiere synchronisation
REM  4. programme la synchronisation automatique
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creation de l'environnement Python...
    py -3.13 -m venv venv || python -m venv venv
)
"venv\Scripts\python.exe" -m pip install --upgrade pip >nul
"venv\Scripts\python.exe" -m pip install -r scripts\requirements.txt || goto :erreur

"venv\Scripts\python.exe" scripts\init_systeme.py || goto :erreur
call "%~dp0installer_tache.bat"
echo.
echo  Installation terminee.
pause
exit /b 0

:erreur
echo.
echo  ERREUR pendant l'installation : lisez le message ci-dessus.
pause
exit /b 1
