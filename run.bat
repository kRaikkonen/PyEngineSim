@echo off
rem ============================================================
rem  PyEngineSim launcher
rem  Uses the bundled portable Python (python_embeded) if present,
rem  otherwise falls back to the system Python (py / python).
rem ============================================================
cd /d "%~dp0"

if exist "python_embeded\python.exe" (
    "python_embeded\python.exe" -s run.py %*
    goto :end
)

where py >nul 2>nul
if %errorlevel%==0 (
    py run.py %*
    goto :end
)

python run.py %*

:end
if %errorlevel% neq 0 (
    echo.
    echo PyEngineSim exited with an error. If this is the first run,
    echo run install.bat once to set up the bundled Python + libraries.
    pause
)
