@echo off
cd /d "%~dp0"

echo Stopping any running instance...
rem A running Snippets.exe keeps build\ locked. Without this the clean below
rem fails silently, PyInstaller then reuses the stale build directory, and the
rem exe it produces is incomplete and exits on launch without logging anything.
taskkill /f /im Snippets.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Cleaning...
del /f /q *.spec >nul 2>&1
rmdir /s /q build >nul 2>&1
rmdir /s /q dist >nul 2>&1
rmdir /s /q __pycache__ >nul 2>&1
if exist build (
  echo.
  echo ERROR: could not remove build\ - something still has it open.
  echo Close it and run BUILD.bat again; building on a stale build\ produces
  echo a broken exe.
  pause
  exit /b 1
)

echo Running tests...
rem Headless suites only. test_live and test_ui need an interactive desktop,
rem so they are run by hand rather than gating the build.
for %%T in (test_core test_updater test_feedback) do (
  python tests\%%T.py
  if errorlevel 1 (
    echo.
    echo ERROR: %%T failed - not building.
    pause
    exit /b 1
  )
)

echo Writing version resource...
python make_release.py versioninfo
if errorlevel 1 (
  echo.
  echo ERROR: could not write version_info.txt.
  pause
  exit /b 1
)

echo Building Snippets ^(single process: tray + hotkeys + editor^)...
python -m PyInstaller --onefile --noconsole --icon icon.ico ^
  --version-file version_info.txt ^
  --add-data "editor_ui.html;." --add-data "snip_core.py;." ^
  --collect-all webview --collect-all pystray ^
  --collect-all uiautomation --collect-all comtypes ^
  --name Snippets brain.py
if errorlevel 1 (
  echo.
  echo ERROR: build failed.
  pause
  exit /b 1
)

rmdir /s /q build >nul 2>&1
rmdir /s /q __pycache__ >nul 2>&1
del /f /q Snippets.spec >nul 2>&1

echo Writing dist\version.json...
python make_release.py
if errorlevel 1 (
  echo.
  echo ERROR: could not write dist\version.json - exe was built but the
  echo release manifest was not. Do not publish this build as-is.
  pause
  exit /b 1
)

echo.
echo Done. Run dist\Snippets.exe
echo Upload dist\Snippets.exe AND dist\version.json to the release.
pause
