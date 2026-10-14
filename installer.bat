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

REM Fichiers personnels : crees a partir des modeles UNIQUEMENT s'ils n'existent pas (jamais ecrases)
if not exist "config.ini" (
    copy "modeles\config.exemple.ini" "config.ini" >nul
    echo  config.ini cree a partir du modele : pensez a y coller votre chaine Neon et les noms des vendeurs.
)
if not exist "Gestion_stock_corrige.xlsx" (
    copy "modeles\Gestion_stock_corrige.xlsx" "Gestion_stock_corrige.xlsx" >nul
    echo  Gestion_stock_corrige.xlsx cree a partir du modele ^(15 produits, prix estimes a verifier^).
)

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
