@echo off
cd /d "%~dp0Tools\AINiee"
@REM set "AINIEE_LOG_LEVEL=DEBUG"
echo AINiee detailed logging enabled: DEBUG
call .\venv\Scripts\python.exe AiNiee.py
pause
