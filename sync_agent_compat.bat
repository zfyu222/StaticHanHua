@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Keep Codex, CodeBuddy and Claude Code project instructions/skills in sync.
rem The named tool is the source; every other tool is mirrored from it.
rem Usage:
rem   sync_agent_compat.bat codex
rem   sync_agent_compat.bat codebuddy
rem   sync_agent_compat.bat claude

set "TOOLS=codex codebuddy claude"
set "codex_INSTRUCTIONS=AGENTS.md"
set "codex_SKILLS=.agents\skills"
set "codebuddy_INSTRUCTIONS=CODEBUDDY.md"
set "codebuddy_SKILLS=.codebuddy\skills"
set "claude_INSTRUCTIONS=CLAUDE.md"
set "claude_SKILLS=.claude\skills"

set "SOURCE="
for %%T in (%TOOLS%) do if /I "%~1"=="%%T" set "SOURCE=%%T"
if not defined SOURCE (
  echo Usage: %~nx0 ^<codex^|codebuddy^|claude^>
  echo.
  echo Name the tool where you edited the instructions or skills; the others are overwritten from it.
  exit /b 2
)

pushd "%~dp0"
set "SOURCE_INSTRUCTIONS=!%SOURCE%_INSTRUCTIONS!"
set "SOURCE_SKILLS=!%SOURCE%_SKILLS!"

if not exist "%SOURCE_INSTRUCTIONS%" (
  echo Source instruction file not found: %SOURCE_INSTRUCTIONS%
  popd & exit /b 1
)

for %%T in (%TOOLS%) do if /I not "%%T"=="%SOURCE%" (
  set "DEST_INSTRUCTIONS=!%%T_INSTRUCTIONS!"
  set "DEST_SKILLS=!%%T_SKILLS!"

  copy /Y "%SOURCE_INSTRUCTIONS%" "!DEST_INSTRUCTIONS!" >nul
  if errorlevel 1 (
    echo Failed to copy %SOURCE_INSTRUCTIONS% to !DEST_INSTRUCTIONS!.
    popd & exit /b 1
  )
  echo Synced %SOURCE_INSTRUCTIONS% to !DEST_INSTRUCTIONS!.

  if exist "%SOURCE_SKILLS%" (
    rem /MIR also removes destination files that no longer exist in the source.
    robocopy "%SOURCE_SKILLS%" "!DEST_SKILLS!" /MIR /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS
    if !ERRORLEVEL! GEQ 8 (
      echo Failed to mirror skills to !DEST_SKILLS!. Robocopy exit code: !ERRORLEVEL!
      popd & exit /b 1
    )
    echo Mirrored skills from %SOURCE_SKILLS% to !DEST_SKILLS!.
  ) else (
    echo No source skills directory found at %SOURCE_SKILLS%; instructions were synced only.
  )
)

popd
exit /b 0
