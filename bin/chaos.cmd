@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "chaos_bootstrap=%~dp0..\chaos.py"
if defined CHAOS_PYTHON goto configured
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" <nul >nul 2>&1
if not errorlevel 1 goto py_launcher
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" <nul >nul 2>&1
if not errorlevel 1 goto python_launcher
echo Chaos needs Python 3.9+ as its runtime. Install it or set CHAOS_PYTHON to its executable path. 1>&2
exit /b 127
:configured
"%CHAOS_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" <nul >nul 2>&1
if errorlevel 1 goto invalid_config
"%CHAOS_PYTHON%" "%chaos_bootstrap%" %*
exit /b %errorlevel%
:py_launcher
py -3 "%chaos_bootstrap%" %*
exit /b %errorlevel%
:python_launcher
python "%chaos_bootstrap%" %*
exit /b %errorlevel%
:invalid_config
echo Chaos: CHAOS_PYTHON must name a Python 3.9+ executable without arguments. 1>&2
exit /b 127
