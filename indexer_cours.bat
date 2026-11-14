@echo off
cd /d "%~dp0"
title Indexation des cours
if not exist ".venv\Scripts\python.exe" (
    echo Le correcteur n'est pas encore installe : lance d'abord installer.bat.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m correcteur indexer
echo.
pause
