"""Small native commands that delegate to the same stable Python bootstrap.

Interpreter discovery belongs here; language parsing, project selection and
runtime upgrades remain in the original bootstrap and CLI.
"""


def native_launchers(bootstrap='chaos.py'):
    """Return relocatable POSIX/Windows wrappers for a trusted bootstrap path."""
    if bootstrap not in ('chaos.py', '../chaos.py'):
        raise ValueError('Unsupported bootstrap location')
    posix = r'''#!/bin/sh
# Chaos language command. Keep this file beside its bootstrap.
chaos_launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
chaos_bootstrap="$chaos_launcher_dir/@BOOTSTRAP@"
chaos_python_ready() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' </dev/null >/dev/null 2>&1
}
if [ -n "${CHAOS_PYTHON:-}" ]; then
    if chaos_python_ready "$CHAOS_PYTHON"; then
        exec "$CHAOS_PYTHON" "$chaos_bootstrap" "$@"
    fi
    printf '%s\n' 'Chaos: CHAOS_PYTHON must name a Python 3.9+ executable (without arguments).' >&2
    exit 127
fi
for chaos_python in python3 python; do
    if command -v "$chaos_python" >/dev/null 2>&1 && chaos_python_ready "$chaos_python"; then
        exec "$chaos_python" "$chaos_bootstrap" "$@"
    fi
done
printf '%s\n' 'Chaos needs Python 3.9+ as its runtime. Install it or set CHAOS_PYTHON to its executable path.' >&2
exit 127
'''.replace('@BOOTSTRAP@', bootstrap)
    windows = r'''@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "chaos_bootstrap=%~dp0@BOOTSTRAP@"
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
'''.replace('@BOOTSTRAP@', bootstrap.replace('/', '\\'))
    return {'chaos': posix.encode('utf-8'), 'chaos.cmd': windows.replace('\n', '\r\n').encode('utf-8')}


def executable_mode(name):
    """Only the POSIX launcher is executable in a generated project archive."""
    return 0o755 if name == 'chaos' else 0o644
