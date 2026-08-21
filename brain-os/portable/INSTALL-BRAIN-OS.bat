@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Brain OS V1 - Full Installer

rem Force UTF-8 for this launcher and every Python child process. Windows can otherwise
rem default redirected stdout to a legacy charmap (for example cp1252), which makes
rem Brain OS doctor fail when its JSON contains Vietnamese characters.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ============================================================
echo   BRAIN OS V1 - FULL INSTALL
echo ============================================================
echo Target Brain: %CD%
echo.

set "INSTALLER=%CD%\.brain-os-installer\install.py"
if not exist "%INSTALLER%" (
  echo [ERROR] Missing .brain-os-installer\install.py
  echo Extract BrainOS-V1-Portable.zip directly inside this Brain first.
  goto :fail
)

rem Prefer the Python runtime that belongs to the Javis installation containing
rem this normal ^<Javis^>\brains\^<Brain^> layout. Fall back to PATH only when
rem the venv interpreter is not present.
set "PYTHON_EXE=%CD%\..\..\.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto :python_ready
set "PYTHON_EXE="
for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if defined PYTHON_EXE goto :python_ready
for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo [ERROR] Python was not found.
  echo Start Javis once or install its Python runtime, then run this file again.
  goto :fail
)

:python_ready
echo Python: %PYTHON_EXE%
echo.

echo [1/3] PREVIEW - no Brain OS payload is written in this step...
"%PYTHON_EXE%" "%INSTALLER%"
if errorlevel 1 (
  echo.
  echo [FAILED] Preview did not pass. Nothing will be applied.
  goto :fail
)

echo.
echo [2/3] APPLY - installing only after preview passed...
"%PYTHON_EXE%" "%INSTALLER%" --apply
if errorlevel 1 (
  echo.
  echo [FAILED] Apply did not complete. Verify will not run.
  goto :fail
)

echo.
echo [3/3] VERIFY + DOCTOR - read-only post-install checks...
"%PYTHON_EXE%" "%INSTALLER%" --verify
if errorlevel 1 (
  echo.
  echo [FAILED] Post-install verification did not pass.
  goto :fail
)

echo.
echo ============================================================
echo   INSTALL COMPLETE - BRAIN OS V1 VERIFIED
echo ============================================================
echo You can now send this final screen/result for the real-vault E2E check.
if not defined BRAIN_OS_NO_PAUSE pause
exit /b 0

:fail
echo.
echo Brain OS was NOT marked as successfully installed.
echo Please keep this window/result for diagnosis.
if not defined BRAIN_OS_NO_PAUSE pause
exit /b 2
