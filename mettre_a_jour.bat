@echo off
setlocal
rem Le script se recopie d'abord dans le dossier temporaire : il peut ainsi remplacer
rem sa propre version dans le dossier du correcteur sans s'interrompre en route.
if /i not "%~1"=="--depuis-temp" (
    copy /y "%~f0" "%TEMP%\correcteur_mettre_a_jour.bat" >nul
    "%TEMP%\correcteur_mettre_a_jour.bat" --depuis-temp "%~dp0."
)
cd /d "%~2"
title Mise a jour du correcteur
echo.
echo === Mise a jour du correcteur d'annales ===
echo.
if not exist ".venv\Scripts\python.exe" (
    echo Le correcteur n'est pas encore installe : lance d'abord installer.bat.
    goto echec
)
if defined CORRECTEUR_TEST_SANS_PAUSE goto telecharger
echo Ferme d'abord le correcteur s'il est ouvert : clic droit sur l'icone verte
echo pres de l'horloge, puis Quitter. Appuie ensuite sur une touche.
pause >nul

:telecharger
set "CORRECTEUR_URL=https://github.com/aquagwadaa-stack/raccourcis-r-ponse-annale/archive/refs/heads/ccr-bc1aaef1-31d5pn.zip"
set "CORRECTEUR_TMP=%TEMP%\correcteur_mise_a_jour"
echo Telechargement de la derniere version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; $d = $env:CORRECTEUR_TMP; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; New-Item -ItemType Directory -Path $d | Out-Null; Invoke-WebRequest -UseBasicParsing -Uri $env:CORRECTEUR_URL -OutFile (Join-Path $d 'maj.zip'); Expand-Archive -Path (Join-Path $d 'maj.zip') -DestinationPath $d -Force"
if errorlevel 1 goto echec_telechargement
set "SRC="
for /d %%D in ("%CORRECTEUR_TMP%\raccourcis-r-ponse-annale-*") do set "SRC=%%D"
if not defined SRC goto echec_telechargement

echo Copie des nouveaux fichiers : tes cours, ton historique et tes reglages ne sont pas touches.
robocopy "%SRC%" "%CD%" /E /XD .venv cours index historique captures .git /XF config.toml config.ancien.toml cle_api.txt .env /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto echec_copie
echo Mise a jour des composants...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto echec_composants
".venv\Scripts\python.exe" -m correcteur preparer
rmdir /s /q "%CORRECTEUR_TMP%" 2>nul
echo.
echo === Mise a jour terminee ===
echo.
if defined CORRECTEUR_TEST_SANS_PAUSE exit /b 0
echo Lancement du correcteur...
start "" ".venv\Scripts\pythonw.exe" -m correcteur
echo Tu peux fermer cette fenetre.
pause
exit /b 0

:echec_telechargement
echo.
echo Le telechargement a echoue. Verifie ta connexion Internet puis relance mettre_a_jour.bat.
goto echec

:echec_copie
echo.
echo La copie des fichiers a echoue. Ferme le correcteur puis relance mettre_a_jour.bat.
goto echec

:echec_composants
echo.
echo L'installation des composants a echoue. Verifie ta connexion Internet puis relance mettre_a_jour.bat.
goto echec

:echec
echo.
if not defined CORRECTEUR_TEST_SANS_PAUSE pause
exit /b 1
