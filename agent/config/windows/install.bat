@echo off
@title Radegast EDR Installation
echo Radegast EDR Installation
echo ================================
echo.

rem Capture token if passed as argument (e.g. UAC elevation relaunch)
if "%RADEGAST_TOKEN%"=="" set "RADEGAST_TOKEN=%~1"

rem Check for administrative privileges and elevate if needed
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrative privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -ArgumentList '%RADEGAST_TOKEN%' -Verb RunAs"
    exit /b 0
)

set PYTHON_VERSION=3.14
set APP_DIR=%ProgramFiles%\Radegast
set PYTHON_DIR=%APP_DIR%\python
set PYTHON_EXE=%PYTHON_DIR%\python.exe
set PYTHONW_EXE=%PYTHON_DIR%\pythonw.exe
set SCRIPTS_DIR=%PYTHON_DIR%\Scripts
set PYTHON_ZIP=%TEMP%\winpython_3_13.zip
set INSTALL_SCRIPT=%TEMP%\install_inline.py
set INSTALL_B64=%TEMP%\install_inline.b64
set "EXPECTED_HASH=d48f56ce9bd928f51a4485972e37816c0d0a69ef07846fc182b26d2d0ca63722"

echo.

if exist "%PYTHON_EXE%" (
    echo Python already installed at %PYTHON_DIR%
    goto :install_uv
)

echo.
echo Downloading portable Python...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://esoadamo.gitlab.io/windows-python-bat-installer/winpython_3_13_uv.zip' -OutFile $env:PYTHON_ZIP"
if errorlevel 1 (
    echo ERROR: Failed to download Python
    PAUSE
    exit /b 1
)
echo Download complete.
echo.

echo Verifying file integrity...
powershell -Command "$computedHash = (Get-FileHash -Path $env:PYTHON_ZIP -Algorithm SHA256).Hash; if ($computedHash -ne '%EXPECTED_HASH%') { write-host 'HASH MISMATCH!'; exit 1 } else { write-host 'Hash verified successfully.' }"
if errorlevel 1 (
    echo ERROR: Hash verification failed! The downloaded file may be corrupted or tampered with.
    if exist "%PYTHON_ZIP%" del "%PYTHON_ZIP%"
    PAUSE
    exit /b 1
)
echo.

echo Extracting Python to %APP_DIR%...
if not exist "%APP_DIR%" mkdir "%APP_DIR%"
powershell -Command "Expand-Archive -Path $env:PYTHON_ZIP -DestinationPath $env:APP_DIR -Force"
if errorlevel 1 (
    echo ERROR: Failed to extract Python
    PAUSE
    exit /b 1
)
del "%PYTHON_ZIP%"
echo Python extracted successfully.
echo.
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python executable not found at %PYTHON_EXE%
    PAUSE
    exit /b 1
)
echo.

:install_uv
echo Checking for uv...
set "UV_EXE=%SCRIPTS_DIR%\uv.exe"

rem 1. Install uv via pip if not found
if not exist "%UV_EXE%" (
    echo Installing uv via pip...
    "%PYTHON_EXE%" -m pip install uv
    if errorlevel 1 (
        echo ERROR: Failed to install uv
        PAUSE
        exit /b 1
    )
) else (
    echo uv is already installed.
)

:install_app
echo.
echo Writing installation script...
if exist "%INSTALL_B64%" del "%INSTALL_B64%"
if exist "%INSTALL_SCRIPT%" del "%INSTALL_SCRIPT%"

{{ install_service_block }}

echo.

echo Running installation script...
(
    del /f /q "%~f0" 2>nul
    "%PYTHON_EXE%" "%INSTALL_SCRIPT%"
    if errorlevel 1 (
        echo ERROR: Installation script failed.
    ) else (
        echo.
        echo Installation completed successfully!
    )
    del "%INSTALL_SCRIPT%" 2>nul
    del "%INSTALL_B64%" 2>nul
    echo.
    pause
)