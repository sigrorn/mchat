@echo off
REM Build mchat Windows installer
REM Prerequisites: .venv-win with pyinstaller and pillow installed

echo === Building mchat ===

cd /d "%~dp0\.."

echo [1/3] Running PyInstaller...
.venv-win\Scripts\pyinstaller.exe --clean --noconfirm mchat.spec
if errorlevel 1 (
    echo PyInstaller failed!
    exit /b 1
)

echo [2/3] Build complete. Output in dist\mchat\
echo.

if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    echo [3/3] Building installer with Inno Setup...
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
    if errorlevel 1 (
        echo Inno Setup failed!
        exit /b 1
    )
    echo Installer created in Output\
) else (
    echo [3/3] Inno Setup not found — skipping installer creation.
    echo       Install Inno Setup 6 to create the installer.
    echo       Download: https://jrsoftware.org/isdl.php
)

echo.
echo === Done ===
