@echo off
rem ============================================================
rem  PyEngineSim one-click setup — builds a self-contained
rem  portable Python (python_embeded\) with all libraries, so a
rem  fresh PC can run the simulator with run.bat. ComfyUI-style.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYVER=3.12.7"
set "PYDIR=python_embeded"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
set "PTH=%PYDIR%\python312._pth"

echo ============================================
echo   PyEngineSim portable Python setup
echo ============================================
echo.

if exist "%PYDIR%\python.exe" goto deps

echo [1/4] Downloading portable Python %PYVER% ...
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PYURL%' -OutFile 'python_embed.zip'" || goto fail
echo [2/4] Extracting ...
powershell -NoProfile -Command "Expand-Archive -Force 'python_embed.zip' '%PYDIR%'" || goto fail
del /q python_embed.zip

rem enable site-packages (import site) AND put the project root (..) on the
rem path, so 'import engine_sim' works from the embedded interpreter
powershell -NoProfile -Command "Set-Content '%PTH%' -Value @('python312.zip','.','..','import site')"

echo [3/4] Installing pip ...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PYDIR%\get-pip.py'" || goto fail
"%PYDIR%\python.exe" "%PYDIR%\get-pip.py" --no-warn-script-location || goto fail

:deps
echo [4/4] Installing libraries (numpy, scipy, pygame-ce, sounddevice) ...
"%PYDIR%\python.exe" -m pip install --no-warn-script-location -r requirements.txt || goto fail

echo.
echo ============================================
echo   Done!  Launch the simulator with  run.bat
echo ============================================
pause
exit /b 0

:fail
echo.
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
