@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Telegram Channel Downloader v3.4 - Fresh Windows Installer
color 0A

rem ===============================================================
rem TELEGRAM CHANNEL DOWNLOADER v3.4 - STANDALONE INSTALL.BAT
rem Place this BAT beside requirements.txt and the Python source,
rem or in the folder immediately above Telegram_Channel_Downloader.
rem Installs Python 3.12 if missing, creates an isolated venv,
rem installs dependencies, and starts the GUI. No admin needed.
rem ===============================================================

set "APPDIR=%~dp0"
if not exist "%APPDIR%requirements.txt" if exist "%APPDIR%Telegram_Channel_Downloader\requirements.txt" set "APPDIR=%APPDIR%Telegram_Channel_Downloader\"
set "APPMAIN=%APPDIR%telegram_channel_downloader.py"
set "REQFILE=%APPDIR%requirements.txt"
set "VENV_PY=%APPDIR%.venv\Scripts\python.exe"
set "LOGFILE=%APPDIR%INSTALL_LOG.txt"

cls
echo ===============================================================
echo           TELEGRAM CHANNEL DOWNLOADER v3.4
echo            FRESH WINDOWS SYSTEM INSTALLER
echo ===============================================================
echo.
echo Install directory: "%APPDIR%"
echo.

if not exist "%REQFILE%" goto :missingFiles
if not exist "%APPMAIN%" goto :missingFiles

rem Keep logs local to the application; never log API secrets.
> "%LOGFILE%" echo Telegram Channel Downloader v3.4 installation log
>> "%LOGFILE%" echo Installation directory: "%APPDIR%"

call :findPython
if defined PYEXE goto :pythonReady

echo [1/5] Python 3.12 not found. Installing for this Windows user...
call :installPython
if errorlevel 1 goto :pythonFailed
call :findPython
if not defined PYEXE goto :pythonFailed

:pythonReady
echo [1/5] Python 3.12 detected:
echo       "%PYEXE%"
"%PYEXE%" --version
if errorlevel 1 goto :pythonFailed

echo.
echo [2/5] Preparing isolated Python environment...
if exist "%VENV_PY%" goto :checkVenv
"%PYEXE%" -m venv "%APPDIR%.venv" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :venvFailed

:checkVenv
"%VENV_PY%" -c "import sys; assert sys.version_info[:2] == (3, 12)" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :venvFailed
"%VENV_PY%" -m pip --version >> "%LOGFILE%" 2>&1
if not errorlevel 1 goto :installPackages
"%VENV_PY%" -m ensurepip --upgrade >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :venvFailed

:installPackages
echo [3/5] Installing Telethon, Drive API, image and link packages...
echo       This needs internet access. Output: "%LOGFILE%"
"%VENV_PY%" -m pip install --disable-pip-version-check --no-input -r "%REQFILE%" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :pipFailed

:verifyPackages
echo [4/5] Verifying Python packages and Windows GUI support...
"%VENV_PY%" -c "import tkinter; import telethon, qrcode, PIL, requests, gdown, yt_dlp, googleapiclient, google_auth_oauthlib, google_auth_httplib2; print('Dependency check OK')" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :verifyFailed
"%VENV_PY%" -m pip check >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :verifyFailed

rem Write a small reusable launcher; it uses only the app-specific venv.
echo [5/5] Creating launcher and shortcut...
> "%APPDIR%START_DOWNLOADER.bat" echo @echo off
>> "%APPDIR%START_DOWNLOADER.bat" echo setlocal
>> "%APPDIR%START_DOWNLOADER.bat" echo cd /d "%%~dp0"
>> "%APPDIR%START_DOWNLOADER.bat" echo "%%~dp0.venv\Scripts\python.exe" -B "%%~dp0telegram_channel_downloader.py"
>> "%APPDIR%START_DOWNLOADER.bat" echo if errorlevel 1 pause
set "SHORTCUT_TARGET=%APPDIR%START_DOWNLOADER.bat"
powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop'); if (-not [string]::IsNullOrWhiteSpace($d)) { $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut([IO.Path]::Combine($d,'Telegram Channel Downloader.lnk')); $s.TargetPath=$env:SHORTCUT_TARGET; $s.WorkingDirectory=$env:APPDIR; $s.Save() }" >> "%LOGFILE%" 2>&1

if not exist "%APPDIR%downloads" mkdir "%APPDIR%downloads" >nul 2>&1

echo.
echo ===============================================================
echo [SUCCESS] Telegram Channel Downloader is installed.
echo ===============================================================
echo Start later: "%APPDIR%START_DOWNLOADER.bat"
echo Log file:    "%LOGFILE%"
echo.
echo Telegram login requires your own API ID/hash and sign-in.
echo Private Google Drive links require your own OAuth setup.
echo Saved sessions and existing download history were not changed.
echo.
echo Opening the downloader now...
start "" "%APPDIR%START_DOWNLOADER.bat"
exit /b 0

:findPython
set "PYEXE="
where py >nul 2>&1
if errorlevel 1 goto :checkCommonPythonPaths
for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
:checkCommonPythonPaths
if defined PYEXE exit /b 0
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%ProgramFiles%\Python312\python.exe" "%ProgramFiles(x86)%\Python312-32\python.exe") do if not defined PYEXE if exist "%%~P" set "PYEXE=%%~P"
exit /b 0

:installPython
rem First try the Windows Package Manager, available on most Win 10/11 PCs.
where winget >nul 2>&1
if errorlevel 1 goto :officialInstaller
echo       Trying Windows Package Manager (winget)...
winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements >> "%LOGFILE%" 2>&1
call :findPython
if defined PYEXE exit /b 0

echo       winget could not finish. Trying official Python download...
:officialInstaller
set "PYINSTALL_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PYINSTALL_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe"
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" set "PYINSTALL_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10.exe"
set "PYINSTALL_PATH=%TEMP%\TelegramDownloader-Python-3.12.10-%RANDOM%.exe"
echo       Downloading from python.org and checking digital signature...
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri $env:PYINSTALL_URL -OutFile $env:PYINSTALL_PATH -UseBasicParsing; $s=Get-AuthenticodeSignature -FilePath $env:PYINSTALL_PATH; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Python Software Foundation'){throw 'Installer signature validation failed'}; Write-Host ('Verified: ' + $s.SignerCertificate.Subject)" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :pythonDownloadFailed
"%PYINSTALL_PATH%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_tcltk=1 Include_test=0
set "PYINSTALL_RC=%errorlevel%"
del /q "%PYINSTALL_PATH%" >nul 2>&1
if not "%PYINSTALL_RC%"=="0" exit /b 1
exit /b 0

:pythonDownloadFailed
if exist "%PYINSTALL_PATH%" del /q "%PYINSTALL_PATH%" >nul 2>&1
echo [ERROR] Unable to download or verify Python's signed installer.
echo         Install Python 3.12 manually from https://www.python.org/downloads/release/python-31210/
exit /b 1

:missingFiles
echo [ERROR] Missing downloader files.
echo Put INSTALL.bat in the extracted Telegram_Channel_Downloader
echo folder with requirements.txt and telegram_channel_downloader.py,
echo or place it in the folder directly above that directory.
goto :failed

:pythonFailed
echo [ERROR] Python 3.12 could not be installed or detected.
echo Use the official Windows installer: https://www.python.org/downloads/release/python-31210/
goto :failed

:venvFailed
echo [ERROR] Failed to create or use the .venv environment.
echo Check folder permissions and available disk space.
echo If you copied an existing .venv from another PC, delete ONLY
echo that .venv folder and run INSTALL.bat again.
goto :failed

:pipFailed
echo [ERROR] Python dependencies could not be installed.
echo Check internet, proxy, antivirus and the install log.
goto :failed

:verifyFailed
echo [ERROR] A required package or Tkinter GUI component is missing.
echo Check the log and make sure Python has Tcl/Tk enabled.
goto :failed

:failed
echo.
echo Installation log: "%LOGFILE%"
if exist "%LOGFILE%" powershell -NoProfile -Command "Get-Content -LiteralPath $env:LOGFILE -Tail 18" 2>nul
echo.
pause
exit /b 1
