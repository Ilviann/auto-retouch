@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYLAUNCHER_ALLOW_INSTALL=0"
where py >nul 2>nul
if errorlevel 1 goto path_python
py -3.14 -B -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,14) else 1)" >nul 2>nul
if errorlevel 1 goto path_python
py -3.14 -B "%~dp0tools\prepare_env.py" %*
exit /b %errorlevel%
:path_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -B -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,14) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
python -B "%~dp0tools\prepare_env.py" %*
exit /b %errorlevel%
:missing_python
echo PYTHON_NOT_FOUND: Install 64-bit Python 3.14, then rerun prepare_env.cmd. 1>&2
call "%~dp0tools\bootstrap_error.cmd" PYTHON_NOT_FOUND
exit /b 1
