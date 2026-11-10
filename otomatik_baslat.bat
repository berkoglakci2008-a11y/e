@echo off
chcp 65001 >nul
rem Windows oturumu acilinca programi gizli (pencere olmadan) baslatir; Radar ve Giveaway arka planda calisir.
set "S=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\YorumAnalizcisi.vbs"
if /i "%1"=="kaldir" (del "%S%" & echo Otomatik baslatma kaldirildi. & pause & exit /b 0)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (echo Once baslat.bat dosyasini bir kez calistirin. & pause & exit /b 1)
> "%S%" echo Set s=CreateObject("WScript.Shell"): s.CurrentDirectory="%~dp0": s.Run """%~dp0.venv\Scripts\pythonw.exe"" ""%~dp0app.py""",0,False
echo Otomatik baslatma acildi. Bilgisayar acildiginda program arka planda calisir: http://localhost:5000/radar
echo Kapatmak icin: otomatik_baslat.bat kaldir
pause
