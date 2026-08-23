@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Keep Codex and CodeBuddy project instructions/skills in sync.
rem Usage:
rem   sync_agent_compat.bat codebuddy-to-codex
rem   sync_agent_compat.bat codex-to-codebuddy

if /I "%~1"=="codebuddy-to-codex" (
  set "SOURCE_INSTRUCTIONS=CODEBUDDY.md"
  set "DEST_INSTRUCTIONS=AGENTS.md"
  set "SOURCE_SKILLS=.codebuddy\skills"
  set "DEST_SKILLS=.agents\skills"
  goto :sync
)

if /I "%~1"=="codex-to-codebuddy" (
  set "SOURCE_INSTRUCTIONS=AGENTS.md"
  set "DEST_INSTRUCTIONS=CODEBUDDY.md"
  set "SOURCE_SKILLS=.agents\skills"
  set "DEST_SKILLS=.codebuddy\skills"
  goto :sync
)

echo Usage: %~nx0 ^<codebuddy-to-codex^|codex-to-codebuddy^>
echo.
echo Run the direction matching the tool where you edited the instructions or skills.
exit /b 2

:sync
if not exist "%SOURCE_INSTRUCTIONS%" (
  echo Source instruction file not found: %SOURCE_INSTRUCTIONS%
  exit /b 1
)

copy /Y "%SOURCE_INSTRUCTIONS%" "%DEST_INSTRUCTIONS%" >nul
if errorlevel 1 (
  echo Failed to copy %SOURCE_INSTRUCTIONS% to %DEST_INSTRUCTIONS%.
  exit /b 1
)

if exist "%SOURCE_SKILLS%" (
  if not exist "%DEST_SKILLS%" mkdir "%DEST_SKILLS%"
  robocopy "%SOURCE_SKILLS%" "%DEST_SKILLS%" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS
  set "ROBOCOPY_EXIT=%ERRORLEVEL%"
  if !ROBOCOPY_EXIT! GEQ 8 (
    echo Failed to copy skills. Robocopy exit code: !ROBOCOPY_EXIT!
    exit /b !ROBOCOPY_EXIT!
  )
) else (
  echo No source skills directory found at %SOURCE_SKILLS%; instructions were synced only.
)

echo Synced %SOURCE_INSTRUCTIONS% to %DEST_INSTRUCTIONS%.
echo Synced skills from %SOURCE_SKILLS% to %DEST_SKILLS%.
exit /b 0
