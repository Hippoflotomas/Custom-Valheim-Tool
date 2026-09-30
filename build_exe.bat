@echo off
rem Builds the downloadable release: dist\ValheimPackBuilder\ (the program folder) and
rem dist\ValheimPackBuilder-<version>-windows.zip (what people download). No Python needed to run it.
rem Same steps as the GitHub release build: tests, PyInstaller, licences, self-test, zip.
rem Double-click this file, or run it from a command prompt in this folder.
setlocal
cd /d "%~dp0"

rem Use the Python that has PySide6 installed. "py -3.13" is the Windows launcher's 3.13;
rem change it here if yours is a different version.
set PY=py -3.13
%PY% --version >nul 2>&1 || set PY=python

echo Using: & %PY% -c "import sys; print(sys.executable)"
%PY% -m pip install --upgrade -r requirements.txt pyinstaller pytest || goto :fail
%PY% packaging\build.py || goto :fail

echo.
echo Done. The zip to share is in %~dp0dist
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
