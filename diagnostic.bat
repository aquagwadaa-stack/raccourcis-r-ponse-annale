@echo off
cd /d "%~dp0"
title Diagnostic du correcteur
if not exist ".venv\Scripts\python.exe" (
    echo Le correcteur n'est pas encore installe : lance d'abord installer.bat.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m correcteur diagnostic
echo.
pause
