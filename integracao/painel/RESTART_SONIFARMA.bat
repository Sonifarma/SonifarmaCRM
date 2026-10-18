@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SONIFARMA_CONTROLE.ps1" restart
pause
