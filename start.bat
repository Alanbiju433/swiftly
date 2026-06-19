@echo off
echo ==========================================
echo  Swiftly Delivery App - Starting...
echo ==========================================

:: Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python from https://python.org
    pause
    exit /b 1
)

:: Install dependencies
echo Installing dependencies...
pip install -r requirements.txt --quiet

:: Launch the app
echo.
echo Starting Swiftly...
echo.
echo  Customer  ->  http://127.0.0.1:5000/
echo  Manager   ->  http://127.0.0.1:5000/manager
echo  Driver    ->  http://127.0.0.1:5000/driver
echo.
echo Press Ctrl+C to stop the server.
echo.

:: Open browser
start "" http://127.0.0.1:5000/

python app.py
pause
