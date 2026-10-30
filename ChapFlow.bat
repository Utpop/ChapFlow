@echo off
rem ---------------------------------------------------------------
rem  ChapFlow launcher
rem  Finds a Python interpreter that actually has PySide6 installed,
rem  then starts the GUI with pythonw (no console window).
rem  Keep this file next to the "src" folder.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "APP=src\chapflow_gui.py"
if not exist "%APP%" goto noapp

set "PYCMD="
py -3 -c "import PySide6" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto found

python -c "import PySide6" >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto found

if exist "%LOCALAPPDATA%\Python\pythoncore-3.15-64\python.exe" "%LOCALAPPDATA%\Python\pythoncore-3.15-64\python.exe" -c "import PySide6" >nul 2>nul
if not errorlevel 1 set "PYCMD=%LOCALAPPDATA%\Python\pythoncore-3.15-64\python.exe"
if defined PYCMD goto found

if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -c "import PySide6" >nul 2>nul
if not errorlevel 1 set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if defined PYCMD goto found

goto nodeps

:found
if not exist "logs" mkdir "logs" >nul 2>nul
del /q "logs\launcher.txt" >nul 2>nul
%PYCMD% -c "import sys,os;p=os.path.join(os.path.dirname(sys.executable),'pythonw.exe');open(r'logs\launcher.txt','w').write(p if os.path.exists(p) else sys.executable)" >nul 2>nul
set "LAUNCH="
if exist "logs\launcher.txt" set /p LAUNCH=<"logs\launcher.txt"
if defined LAUNCH goto launch_exe
start "" %PYCMD% "%APP%" %*
goto end

:launch_exe
start "" "%LAUNCH%" "%APP%" %*
goto end

:noapp
echo.
echo  [ChapFlow] Cannot find "%APP%".
echo  Keep ChapFlow.bat in the project root, next to the "src" folder.
echo.
pause
goto end

:nodeps
echo.
echo  [ChapFlow] No Python with PySide6 was found on this machine.
echo.
echo  Detected interpreters:
py -3 -V 2>nul
python -V 2>nul
echo.
echo  Fix it with either command:
echo      py -3 -m pip install PySide6-Essentials
echo      python -m pip install PySide6-Essentials
echo.
echo  Then run again. To see the raw error:
echo      python src\chapflow_gui.py
echo  Log file: logs\gui-error.log
echo.
pause

:end
endlocal
