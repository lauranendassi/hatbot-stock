@echo off
schtasks /Delete /TN "WeloobeAI_Synchro_Stock" /F
schtasks /Delete /TN "WeloobeAI_Synchro_Stock_Demarrage" /F
echo Taches supprimees.
pause
