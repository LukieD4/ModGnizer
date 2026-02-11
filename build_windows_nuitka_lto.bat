@echo off
setlocal enabledelayedexpansion

REM ==================================================
REM PROJECT CONFIG
REM ==================================================
set PROJECT_NAME=ModGnizer
set ENTRY_POINT=py_main.py
set ICON_PATH=program.ico
set SRCDIR=%~dp0
set DISTDIR=build_windows

cd /d "%SRCDIR%"

REM ==================================================
REM START TIMER
REM ==================================================
echo [%TIME%] Starting build...
for /f "tokens=1-4 delims=:.," %%a in ("%time%") do (
    set /a START_SEC=%%a*3600 + %%b*60 + %%c
)

REM ==================================================
REM LOCATE VSDEVCMD
REM ==================================================
set "VSDEVCMD="

if defined VSDEVCMD (
    echo [INFO] Using VSDEVCMD from environment: %VSDEVCMD%
) else (
    if exist "C:\PROGRA~2\MICROS~1\2022\BUILDT~1\Common7\Tools\vsdevcmd.bat" (
        set "VSDEVCMD=C:\PROGRA~2\MICROS~1\2022\BUILDT~1\Common7\Tools\vsdevcmd.bat"
    ) else if exist "C:\PROGRA~2\MICROS~1\2019\BUILDT~1\Common7\Tools\vsdevcmd.bat" (
        set "VSDEVCMD=C:\PROGRA~2\MICROS~1\2019\BUILDT~1\Common7\Tools\vsdevcmd.bat"
    )

    if not defined VSDEVCMD (
        if exist "C:\PROGRA~2\MICROS~1\Installer\vswhere.exe" (
            for /f "usebackq tokens=*" %%I in (`
                "C:\PROGRA~2\MICROS~1\Installer\vswhere.exe" ^
                -latest -products * ^
                -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 ^
                -property installationPath
            `) do (
                if exist "%%I\Common7\Tools\vsdevcmd.bat" (
                    set "VSDEVCMD=%%I\Common7\Tools\vsdevcmd.bat"
                )
            )
        )
    )
)

REM ==================================================
REM INITIALISE VS ENVIRONMENT
REM ==================================================
if defined VSDEVCMD (
    echo [INFO] Found vsdevcmd: %VSDEVCMD%
    call "%VSDEVCMD%" -arch=x86 -host_arch=x86
) else (
    echo [WARNING] Could not locate vsdevcmd.bat.
)

REM ==================================================
REM PYTHON SELECTION
REM ==================================================
set VENV_PY=%SRCDIR%.venv\Scripts\python.exe
if not exist "%VENV_PY%" (
    echo [WARNING] .venv not found — using system Python
    set VENV_PY=python
) else (
    echo [INFO] Using venv: %VENV_PY%
)

REM ==================================================
REM INSTALL DEPENDENCIES
REM ==================================================
echo [INFO] Installing dependencies...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt

"%VENV_PY%" -c "import nuitka" 2>nul
if errorlevel 1 (
    echo [INFO] Installing Nuitka...
    "%VENV_PY%" -m pip install --quiet nuitka
)

REM ==================================================
REM READ BUILD VERSION
REM ==================================================
set BUILDVER=0
if exist buildId.version (
    set /p BUILDVER=<buildId.version
)

echo [INFO] Build version: %BUILDVER%

REM Versioned output name
set VERSIONED_EXE=%PROJECT_NAME%-%BUILDVER%.exe
set TARGET_EXE=%DISTDIR%\%VERSIONED_EXE%

REM ==================================================
REM CLEAN PREVIOUS BUILDS (OPTIONAL: removes all versions)
REM ==================================================
echo [INFO] Cleaning previous builds...
if exist "%DISTDIR%\%PROJECT_NAME%-*.exe" del /q "%DISTDIR%\%PROJECT_NAME%-*.exe" 2>nul

REM ==================================================
REM NUITKA BUILD
REM ==================================================
echo [INFO] Building %VERSIONED_EXE%...

"%VENV_PY%" -m nuitka ^
 --onefile ^
 --lto=yes ^
 --jobs=4 ^
 --enable-plugin=pyqt5 ^
 --assume-yes-for-downloads ^
 --output-dir="%DISTDIR%" ^
 --output-filename="%VERSIONED_EXE%" ^
 --windows-console-mode=force ^
 --windows-icon-from-ico="%ICON_PATH%" ^
 --windows-file-version=1.0.0.%BUILDVER% ^
 --windows-product-version=1.0.0.%BUILDVER% ^
 --include-module=py_main ^
 --include-module=py_archive ^
 --include-module=py_imports ^
 --include-module=py_undbj ^
 --include-data-file=buildId.version=buildId.version ^
 --python-flag=no_site ^
 --python-flag=no_warnings ^
 --python-flag=no_asserts ^
 --python-flag=no_docstrings ^
 --remove-output ^
 "%ENTRY_POINT%"

REM ==================================================
REM VERIFY BUILD
REM ==================================================
if not exist "%TARGET_EXE%" (
    echo.
    echo [ERROR] Build failed — executable not created!
    pause
    exit /b 1
)

REM ==================================================
REM TIMER END
REM ==================================================
for /f "tokens=1-4 delims=:.," %%a in ("%time%") do (
    set /a END_SEC=%%a*3600 + %%b*60 + %%c
)
set /a ELAPSED=%END_SEC%-%START_SEC%
if %ELAPSED% lss 0 set /a ELAPSED+=86400

REM ==================================================
REM SUMMARY
REM ==================================================
echo.
echo ==================================================
echo   BUILD SUCCESSFUL
echo ==================================================
echo   Output: %TARGET_EXE%
for %%F in ("%TARGET_EXE%") do echo   Size: %%~zF bytes
echo   Time: %ELAPSED% seconds
echo ==================================================
echo.
pause