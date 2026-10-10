@echo off
echo ==================================================
echo RUNNING YOUR BACKTEST - STANDALONE BATCH FILE
echo ==================================================
echo.
:: Use FULL absolute paths to avoid ANY PATH issues!
echo Checking Python version...
"C:\Users\skhad\miniforge3\python.exe" --version
echo.
echo Installing dependencies...
"C:\Users\skhad\miniforge3\python.exe" -m pip install pandas numpy python-dotenv
echo.
echo Starting backtest...
"C:\Users\skhad\miniforge3\python.exe" simple_backtest_test.py
echo.
echo ==================================================
echo SCRIPT FINISHED
echo ==================================================
echo If you see an error above, please copy and paste the entire message.
echo The window will now pause. Press any key to exit.
pause