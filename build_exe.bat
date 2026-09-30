@echo off
rem Builds dist\ValheimPackBuilder.exe - a single standalone file, no Python needed to run it.
rem Double-click this file, or run it from a command prompt in this folder.
setlocal
cd /d "%~dp0"

rem Use the Python that has PySide6 installed. "py -3.13" is the Windows launcher's 3.13;
rem change it here if yours is a different version.
set PY=py -3.13
%PY% --version >nul 2>&1 || set PY=python

echo Using: & %PY% -c "import sys; print(sys.executable)"
%PY% -m pip install --upgrade -r requirements.txt pyinstaller || goto :fail

%PY% -m PyInstaller --noconfirm --clean ^
  --onefile --windowed ^
  --name ValheimPackBuilder ^
  --add-data "packbuilder\data;packbuilder\data" ^
  main.py || goto :fail

echo.
echo Done: %~dp0dist\ValheimPackBuilder.exe
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
