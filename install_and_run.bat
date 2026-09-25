@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   Bilim Markazi - o'rnatish va ishga tushirish
echo ========================================
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [X] Kutubxonalarni o'rnatishda xato.
  echo Internetni tekshiring va qayta urinib ko'ring.
  pause
  exit /b 1
)

python check_setup.py
if errorlevel 1 (
  echo.
  echo [X] .env yoki kutubxona sozlamalarida muammo bor.
  pause
  exit /b 1
)

echo.
echo [OK] Bot ishga tushmoqda...
python bot.py
pause
