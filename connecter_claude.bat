@echo off
setlocal
cd /d "%~dp0"
title Connexion a Claude
echo.
echo === Connexion du correcteur a ton compte Claude ===
echo.

call :trouver_claude
if defined CLAUDE goto installe

echo Claude Code n'est pas encore installe sur ce PC : installation en cours.
echo Rien ne s'affiche pendant le telechargement, c'est normal.
echo Patiente une a deux minutes sans fermer cette fenetre.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://claude.ai/install.ps1 | iex"
call :trouver_claude
if defined CLAUDE goto installe
echo.
echo L'installation de Claude Code a echoue.
echo Verifie ta connexion Internet, puis relance connecter_claude.bat.
echo Si le probleme continue, copie le texte de cette fenetre pour demander de l'aide.
goto echec

:installe
echo.
echo Claude Code est installe.
echo Si un message plus haut parle de PATH, ignore-le : le correcteur trouve Claude Code tout seul.
if defined CORRECTEUR_TEST_SANS_CONNEXION goto fin
call :est_connecte
if not errorlevel 1 goto deja_connecte

echo.
echo Il reste a le relier a ton compte Claude. Ton navigateur va s'ouvrir :
echo   1. connecte-toi a ton compte Claude si la page te le demande ;
echo   2. clique sur le bouton qui autorise Claude Code ;
echo   3. reviens ensuite sur cette fenetre.
echo Si la page affiche un code au lieu de se fermer : copie-le, clique dans
echo cette fenetre, colle-le avec un clic droit, puis appuie sur Entree.
echo.
pause
"%CLAUDE%" auth login --claudeai
call :est_connecte
if not errorlevel 1 goto connecte
echo.
echo La connexion n'a pas abouti. Relance connecter_claude.bat pour reessayer.
echo Rappel : il faut un abonnement Claude payant, Pro ou Max.
goto echec

:connecte
echo.
echo === Connexion reussie ===
goto suite

:deja_connecte
echo Ton compte Claude est deja connecte : rien d'autre a faire.

:suite
echo.
echo Etape suivante : lance diagnostic.bat pour tout verifier.

:fin
echo.
pause
exit /b 0

:echec
echo.
pause
exit /b 1

:trouver_claude
set "CLAUDE="
if exist "%USERPROFILE%\.local\bin\claude.exe" set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if defined CLAUDE exit /b 0
for /f "delims=" %%F in ('where claude 2^>nul') do if not defined CLAUDE set "CLAUDE=%%F"
exit /b 0

:est_connecte
"%CLAUDE%" auth status --json 2>nul | findstr /r /c:"loggedIn.: *true" >nul
exit /b %errorlevel%
