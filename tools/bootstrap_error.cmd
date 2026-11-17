@echo off
rem Fixed diagnostic codes only; called before usable Python/package bootstrap.
setlocal
set "logdir=%~dp0..\logs"
set "logfile=%logdir%\bootstrap-errors.log"
if not exist "%logdir%" mkdir "%logdir%" >nul 2>nul
if not exist "%logdir%" goto log_failed
if not exist "%logfile%" goto append
for %%F in ("%logfile%") do if %%~zF GEQ 65536 move /y "%logfile%" "%logfile%.1" >nul 2>nul
if errorlevel 1 goto log_failed
:append
>>"%logfile%" echo [%date% %time% local] %~1
if errorlevel 1 goto log_failed
echo Bootstrap diagnostics: "%logfile%" 1>&2
exit /b 0
:log_failed
echo LOG_WRITE_FAILED: Could not save bootstrap diagnostics; the error above is available only in the console. 1>&2
exit /b 1
