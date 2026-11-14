@echo off
setlocal
cd /d "%~dp0"
title Installation du correcteur d'annales
echo.
echo === Installation du correcteur d'annales ===
echo.

set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo Python 3.11 ou plus recent est introuvable sur ce PC.
    echo.
    echo 1. Va sur https://www.python.org/downloads/ et telecharge Python.
    echo 2. Pendant l'installation, coche la case "Add python.exe to PATH".
    echo 3. Relance ensuite ce fichier installer.bat.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement Python...
    %PY% -m venv .venv
    if errorlevel 1 goto erreur
)
echo Installation des composants (quelques minutes la premiere fois)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto erreur
".venv\Scripts\python.exe" -m correcteur preparer
if errorlevel 1 goto erreur

echo.
echo === Installation terminee ===
echo.
echo Etapes suivantes (detail dans le fichier README.md) :
echo   1. Connecte Claude : cle API dans cle_api.txt, ou Claude Code avec ton abonnement.
echo   2. Mets tes cours dans le dossier "cours", puis lance indexer_cours.bat.
echo   3. Lance diagnostic.bat pour tout verifier.
echo   4. Lance lancer.bat, puis appuie sur F8 devant une annale.
echo.
pause
exit /b 0

:erreur
echo.
echo L'installation a echoue. Verifie ta connexion Internet puis relance installer.bat.
echo Si le probleme persiste, copie le texte ci-dessus pour demander de l'aide.
echo.
pause
exit /b 1
