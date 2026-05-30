@echo off
@title Radegast EDR Installation
echo Radegast EDR Installation
echo ================================
echo.

rem Check for administrative privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Administrative privileges are required.
    echo Please run this script in an Administrator prompt.
    echo.
    PAUSE
    exit /b 1
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
echo.

if exist "%PYTHON_EXE%" (
    echo Python already installed at %PYTHON_DIR%
    goto :install_uv
)

echo.
echo Downloading portable Python...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://cdn.adamhlavacek.com/winpython_3_13.zip' -OutFile $env:PYTHON_ZIP"
if errorlevel 1 (
    echo ERROR: Failed to download Python
    PAUSE
    exit /b 1
)
echo Download complete.
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

rem 2. Add to Current Process PATH (So we can use it immediately below)
echo.
echo Configuring environment variables...
set "PATH=%SCRIPTS_DIR%;%PATH%"

rem 3. Add to Persistent User PATH (Safely using PowerShell to avoid duplicates)
powershell -Command "$targetPath = '%SCRIPTS_DIR%'; $currentPath = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($currentPath -notlike '*' + $targetPath + '*') { [Environment]::SetEnvironmentVariable('Path', $currentPath + ';' + $targetPath, 'User'); Write-Host 'Added to User PATH.' } else { Write-Host 'Already in User PATH.' }"

:install_app
echo.
echo Writing installation script...
if exist "%INSTALL_B64%" del "%INSTALL_B64%"
if exist "%INSTALL_SCRIPT%" del "%INSTALL_SCRIPT%"

{{ install_service_block }}

echo.

echo Running installation script...
"%PYTHON_EXE%" "%INSTALL_SCRIPT%"

if errorlevel 1 (
    echo ERROR: Installation script failed with error code %ERRORLEVEL%
) else (
    echo.
    echo Installation completed successfully!
)
echo.
del "%INSTALL_SCRIPT%" 2>nul
del "%INSTALL_B64%" 2>nul
PAUSE
(goto) 2>nul & del "%~f0"
