@echo off
REM ============================================================
REM  Programme la synchronisation INVISIBLE (pythonw, sans fenetre)
REM   - toutes les 5 minutes
REM   - a l'ouverture de session Windows
REM  Aucun droit administrateur requis (tache de l'utilisateur).
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
set PYW=%~dp0venv\Scripts\pythonw.exe
set SCRIPT=%~dp0scripts\sync.py
if not exist "%PYW%" (
    echo venv introuvable : lancez d'abord installer.bat
    pause & exit /b 1
)

schtasks /Create /TN "WeloobeAI_Synchro_Stock" /TR "\"%PYW%\" \"%SCRIPT%\"" /SC MINUTE /MO 5 /F
REM La tache "a l'ouverture de session" exige les droits administrateur : facultative.
schtasks /Create /TN "WeloobeAI_Synchro_Stock_Demarrage" /TR "\"%PYW%\" \"%SCRIPT%\"" /SC ONLOGON /DELAY 0001:00 /F >nul 2>&1
if errorlevel 1 echo  (Info) Tache au demarrage non creee : droits administrateur requis. Ce n'est pas genant, la synchro toutes les 5 min suffit.

REM Reglages : fonctionne sur batterie, rattrape les passages manques, 4 min max, jamais 2 en meme temps
powershell -NoProfile -Command "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew; Set-ScheduledTask -TaskName 'WeloobeAI_Synchro_Stock' -Settings $s | Out-Null; Set-ScheduledTask -TaskName 'WeloobeAI_Synchro_Stock_Demarrage' -Settings $s -ErrorAction SilentlyContinue | Out-Null"

REM --- Surveillance en continu (mise a jour ~10 s apres chaque Ctrl+S), lancee a chaque ouverture de Windows.
REM     Raccourci dans le dossier Demarrage de l'utilisateur : aucun droit administrateur requis.
powershell -NoProfile -Command "$w = New-Object -ComObject WScript.Shell; $l = $w.CreateShortcut([Environment]::GetFolderPath('Startup') + '\WeloobeAI_Surveillance_Stock.lnk'); $l.TargetPath = '%PYW%'; $l.Arguments = '\"%~dp0scripts\surveiller.py\"'; $l.WorkingDirectory = '%~dp0'; $l.WindowStyle = 7; $l.Description = 'Mise a jour automatique du stock WeloobeAI'; $l.Save()"
start "" "%PYW%" "%~dp0scripts\surveiller.py"
echo.
echo  Mise a jour automatique ACTIVE :
echo   - environ 10 secondes apres chaque enregistrement (Ctrl+S) d'un fichier de saisie
echo   - et par securite toutes les 5 minutes
echo  Elle redemarre toute seule a chaque ouverture de Windows. Plus besoin de sync_stock.bat.
echo  Verification : Planificateur de taches ^> Bibliotheque ^> WeloobeAI_Synchro_Stock
echo  Journal : %~dp0logs\sync.log
