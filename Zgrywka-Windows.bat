@echo off
rem Zgrywka MXB - uruchamianie na Windows (dwuklik).
rem Za pierwszym razem przygotowuje srodowisko w folderze .venv (kilka minut, ok. 700 MB).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "VENV=%~dp0.venv"

if exist "%VENV%\Scripts\python.exe" goto install

echo Pierwsze uruchomienie - przygotowuje program (to potrwa kilka minut)...
for %%V in (3.13 3.12 3.11 3.10 3.14) do (
    py -%%V -c "import sys" >nul 2>nul && (
        py -%%V -m venv "%VENV%" && goto install
    )
)
python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>nul && (
    python -m venv "%VENV%" && goto install
)
echo.
echo Nie znaleziono Pythona 3.10-3.14.
echo Zainstaluj go ze strony https://www.python.org/downloads/windows/
echo (przy instalacji zaznacz "Add python.exe to PATH"), a potem uruchom ten plik ponownie.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:install
rem Biblioteki instalujemy ponownie tylko wtedy, gdy zmienila sie lista w requirements.txt.
fc /b "requirements.txt" "%VENV%\requirements.installed" >nul 2>nul
if not errorlevel 1 goto run
echo Instaluje biblioteki...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir --upgrade pip >nul
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -r "requirements.txt"
if errorlevel 1 (
    echo.
    echo Instalacja bibliotek nie powiodla sie - sprawdz polaczenie z internetem i sprobuj ponownie.
    pause
    exit /b 1
)
copy /y "requirements.txt" "%VENV%\requirements.installed" >nul

:run
start "" "%VENV%\Scripts\pythonw.exe" -m zgrywka %*
exit /b 0
