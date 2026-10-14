@echo off
schtasks /Delete /TN "WeloobeAI_Synchro_Stock" /F
schtasks /Delete /TN "WeloobeAI_Synchro_Stock_Demarrage" /F
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\WeloobeAI_Surveillance_Stock.lnk" 2>nul
taskkill /F /FI "WINDOWTITLE eq surveiller*" >nul 2>&1
echo Taches et surveillance supprimees (redemarrez Windows pour arreter une surveillance deja lancee).
pause
