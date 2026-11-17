@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing_env
"%~dp0.venv\Scripts\python.exe" -B -c "import importlib.util,sys; sys.exit(0 if sys.version_info[:2] == (3,14) and sys.prefix != sys.base_prefix and importlib.util.find_spec('retouching_tool') is not None else 1)" >nul 2>nul
if errorlevel 1 goto invalid_env
"%~dp0.venv\Scripts\python.exe" -B -m retouching_tool %*
exit /b %errorlevel%
:missing_env
echo ENVIRONMENT_MISSING: Run "%~dp0prepare_env.cmd" first. No packages were installed. 1>&2
call "%~dp0tools\bootstrap_error.cmd" ENVIRONMENT_MISSING
exit /b 1
:invalid_env
echo ENVIRONMENT_INVALID: Use 64-bit Python 3.14 and rerun "%~dp0prepare_env.cmd". 1>&2
call "%~dp0tools\bootstrap_error.cmd" ENVIRONMENT_INVALID
exit /b 1
