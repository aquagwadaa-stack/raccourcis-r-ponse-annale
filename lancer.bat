@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Le correcteur n'est pas encore installe : lance d'abord installer.bat.
    pause
    exit /b 1
)
rem pythonw : pas de fenetre noire. Le correcteur apparait pres de l'horloge (icone verte).
start "" ".venv\Scripts\pythonw.exe" -m correcteur
