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
set TARGET_EXE=%DISTDIR%\%PROJECT_NAME%.exe

cd /d "%SRCDIR%"

REM ==================================================
REM START TIMER
REM ==================================================
echo [%TIME%] Starting build...
for /f "tokens=1-4 delims=:.," %%a in ("%time%") do (
    set /a START_SEC=%%a*3600 + %%b*60 + %%c
)

REM ==================================================
REM LOCATE VSDEVCMD (safe short-path version)
REM ==================================================

set "VSDEVCMD="

if defined VSDEVCMD (
    echo [INFO] Using VSDEVCMD from environment: %VSDEVCMD%
) else (
    REM --- Use short 8.3 paths to avoid parentheses issues
    if exist "C:\PROGRA~2\MICROS~1\2022\BUILDT~1\Common7\Tools\vsdevcmd.bat" (
        set "VSDEVCMD=C:\PROGRA~2\MICROS~1\2022\BUILDT~1\Common7\Tools\vsdevcmd.bat"
    ) else if exist "C:\PROGRA~2\MICROS~1\2019\BUILDT~1\Common7\Tools\vsdevcmd.bat" (
        set "VSDEVCMD=C:\PROGRA~2\MICROS~1\2019\BUILDT~1\Common7\Tools\vsdevcmd.bat"
    )

    REM --- Try vswhere (also using short path)
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
    echo [INFO] Initializing Visual Studio build environment...
    call "%VSDEVCMD%" -arch=x86 -host_arch=x86
) else (
    echo [WARNING] Could not locate vsdevcmd.bat automatically.
    echo [WARNING] Build may fail without MSVC environment.
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
REM DEPENDENCY INSTALL (from requirements.txt)
REM ==================================================
echo [INFO] Installing dependencies from requirements.txt...

"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt

REM Ensure Nuitka is available (build dependency)
"%VENV_PY%" -c "import nuitka" 2>nul
if errorlevel 1 (
    echo [INFO] Installing Nuitka...
    "%VENV_PY%" -m pip install --quiet nuitka
)

REM ==================================================
REM READ BUILD NUMBER
REM ==================================================
set BUILDVER=0
if exist buildId.version (
    set /p BUILDVER=<buildId.version
)

echo [INFO] Build version: %BUILDVER%

REM ==================================================
REM CLEAN OUTPUT
REM ==================================================
echo [INFO] Cleaning previous build...
if exist "%TARGET_EXE%" del /q "%TARGET_EXE%" 2>nul

REM ==================================================
REM NUITKA BUILD
REM ==================================================
echo [INFO] Building %PROJECT_NAME%...

"%VENV_PY%" -m nuitka ^
 --onefile ^
 --lto=yes ^
 --jobs=4 ^
 --enable-plugin=pyqt5 ^
 --assume-yes-for-downloads ^
 --output-dir="%DISTDIR%" ^
 --output-filename="%PROJECT_NAME%.exe" ^
 --windows-console-mode=force ^
 --windows-icon-from-ico="%ICON_PATH%" ^
 --windows-file-version=1.0.0.%BUILDVER% ^
 --windows-product-version=1.0.0.%BUILDVER% ^
 --include-module=py_main ^
 --include-module=py_archive ^
 --include-module=py_imports ^
 --include-module=py_undbj ^
 --nofollow-import-to=pytest ^
 --nofollow-import-to=unittest ^
 --nofollow-import-to=tkinter ^
 --nofollow-import-to=test ^
 --nofollow-import-to=setuptools ^
 --nofollow-import-to=distutils ^
 --nofollow-import-to=numpy ^
 --nofollow-import-to=pandas ^
 --nofollow-import-to=matplotlib ^
 --nofollow-import-to=scipy ^
 --nofollow-import-to=sqlalchemy ^
 --nofollow-import-to=django ^
 --nofollow-import-to=flask ^
 --nofollow-import-to=boto3 ^
 --nofollow-import-to=awscli ^
 --nofollow-import-to=botocore ^
 --nofollow-import-to=openpyxl ^
 --nofollow-import-to=pygame ^
 --nofollow-import-to=pyglet ^
 --nofollow-import-to=pyside2 ^
 --nofollow-import-to=pyside6 ^
 --nofollow-import-to=PySide ^
 --nofollow-import-to=wx ^
 --nofollow-import-to=kivy ^
 --nofollow-import-to=pycrypto ^
 --nofollow-import-to=cryptography ^
 --nofollow-import-to=mysql ^
 --nofollow-import-to=psycopg2 ^
 --nofollow-import-to=PIL ^
 --nofollow-import-to=Image ^
 --nofollow-import-to=pyautogui ^
 --nofollow-import-to=selenium ^
 --nofollow-import-to=requests_toolbelt ^
 --nofollow-import-to=pygments ^
 --nofollow-import-to=docutils ^
 --nofollow-import-to=markdown ^
 --nofollow-import-to=markdown2 ^
 --nofollow-import-to=pyttsx3 ^
 --nofollow-import-to=speech_recognition ^
 --nofollow-import-to=pydub ^
 --nofollow-import-to=mutagen ^
 --nofollow-import-to=eyeD3 ^
 --nofollow-import-to=opencv-python ^
 --nofollow-import-to=pyzbar ^
 --nofollow-import-to=qrcode ^
 --nofollow-import-to=reportlab ^
 --nofollow-import-to=xlrd ^
 --nofollow-import-to=xlwt ^
 --nofollow-import-to=xlsxwriter ^
 --nofollow-import-to=tabula ^
 --nofollow-import-to=pdfminer ^
 --nofollow-import-to=PyPDF2 ^
 --nofollow-import-to=pyarrow ^
 --nofollow-import-to=fastparquet ^
 --nofollow-import-to=pyinstaller ^
 --nofollow-import-to=cx_Freeze ^
 --nofollow-import-to=py2exe ^
 --nofollow-import-to=pywebview ^
 --nofollow-import-to=webview ^
 --nofollow-import-to=twisted ^
 --nofollow-import-to=scapy ^
 --nofollow-import-to=paramiko ^
 --nofollow-import-to=fabric ^
 --nofollow-import-to=ansible ^
 --nofollow-import-to=pyperclip ^
 --nofollow-import-to=clipboard ^
 --nofollow-import-to=pywin32 ^
 --nofollow-import-to=wmi ^
 --nofollow-import-to=comtypes ^
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