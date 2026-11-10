@echo off
chcp 65001 >nul
title YouTube Yorum Analizcisi
cd /d "%~dp0"
set "PY="
python --version >nul 2>nul && set "PY=python"
if defined PY goto havepy
set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%PY%" goto havepy
set "PY="
winget --version >nul 2>nul
if errorlevel 1 goto nopython
echo Python kuruluyor (otomatik)...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%PY%" goto havepy
goto nopython
:havepy
if exist ".venv\Scripts\python.exe" goto venvok
echo Sanal ortam olusturuluyor...
"%PY%" -m venv .venv
if errorlevel 1 goto fail
:venvok
if exist ".venv\.kurulu" goto kurulu
echo Paketler kuruluyor, bu bir kez yapilir...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo ok> ".venv\.kurulu"
:kurulu
echo.
echo Uygulama basliyor: http://localhost:5000  (kapatmak icin bu pencereyi kapatin)
start "" http://localhost:5000
".venv\Scripts\python.exe" app.py
pause
exit /b 0
:nopython
echo Python otomatik kurulamadi. https://www.python.org/downloads/ adresinden kurun
echo ("Add Python to PATH" kutusunu isaretleyin), sonra bu dosyayi tekrar calistirin.
pause & exit /b 1
:fail
echo Bir hata olustu. Yukaridaki mesaji kontrol edin.
pause & exit /b 1
