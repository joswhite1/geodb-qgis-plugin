@echo off
REM Clean Install - Removes old plugin and installs fresh copy
REM This fixes issues with .git folders and old files in QGIS plugins directory

echo ========================================
echo GEODB PLUGIN - CLEAN INSTALL
echo ========================================
echo.
echo This script will:
echo 1. Remove the old geodb plugin folder (including .git if present)
echo 2. Install a clean copy of the plugin
echo.
echo IMPORTANT: Make sure QGIS is COMPLETELY CLOSED before continuing!
echo.
pause

SET PLUGIN_NAME=geodb
SET PLUGIN_DIR=%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\%PLUGIN_NAME%

echo.
echo Step 1: Removing old plugin folder...
echo Target: %PLUGIN_DIR%
echo.

REM Check if folder exists
if exist "%PLUGIN_DIR%" (
    echo Folder found. Attempting to remove...
    
    REM Try to remove .git folder first (this often causes issues)
    if exist "%PLUGIN_DIR%\.git" (
        echo Removing .git folder...
        rmdir /S /Q "%PLUGIN_DIR%\.git" 2>nul
        if exist "%PLUGIN_DIR%\.git" (
            echo WARNING: Could not remove .git folder automatically.
            echo Please manually delete: %PLUGIN_DIR%
            echo Then run this script again.
            pause
            exit /b 1
        )
    )
    
    REM Now remove the entire plugin folder
    rmdir /S /Q "%PLUGIN_DIR%" 2>nul
    
    REM Verify it's gone
    if exist "%PLUGIN_DIR%" (
        echo.
        echo ERROR: Could not remove plugin folder!
        echo.
        echo This usually means:
        echo - QGIS is still running (close it completely!)
        echo - File is locked by another program
        echo - Permission issue
        echo.
        echo Please:
        echo 1. Close QGIS completely
        echo 2. Close any other programs that might be using the files
        echo 3. Run this script again
        echo.
        echo If that doesn't work, manually delete this folder:
        echo %PLUGIN_DIR%
        echo.
        pause
        exit /b 1
    ) else (
        echo SUCCESS: Old plugin folder removed!
    )
) else (
    echo Plugin folder does not exist. Proceeding with installation...
)

echo.
echo Step 2: Installing clean plugin copy...
echo.

REM Create plugin directory
mkdir "%PLUGIN_DIR%"

REM Copy Python files
echo Copying core files...
copy /Y __init__.py "%PLUGIN_DIR%\" >nul
copy /Y geodb.py "%PLUGIN_DIR%\" >nul
copy /Y resources.py "%PLUGIN_DIR%\" >nul
copy /Y resources_rc.py "%PLUGIN_DIR%\" >nul
copy /Y metadata.txt "%PLUGIN_DIR%\" >nul
copy /Y icon.png "%PLUGIN_DIR%\" >nul

REM Copy directories
echo Copying ui...
mkdir "%PLUGIN_DIR%\ui" 2>nul
xcopy /Y /E /I ui "%PLUGIN_DIR%\ui" >nul

echo Copying api...
mkdir "%PLUGIN_DIR%\api" 2>nul
xcopy /Y /E /I api "%PLUGIN_DIR%\api" >nul

echo Copying managers...
mkdir "%PLUGIN_DIR%\managers" 2>nul
xcopy /Y /E /I managers "%PLUGIN_DIR%\managers" >nul

echo Copying models...
mkdir "%PLUGIN_DIR%\models" 2>nul
xcopy /Y /E /I models "%PLUGIN_DIR%\models" >nul

echo Copying processors...
mkdir "%PLUGIN_DIR%\processors" 2>nul
xcopy /Y /E /I processors "%PLUGIN_DIR%\processors" >nul

echo Copying utils...
mkdir "%PLUGIN_DIR%\utils" 2>nul
xcopy /Y /E /I utils "%PLUGIN_DIR%\utils" >nul

echo Copying icons...
mkdir "%PLUGIN_DIR%\icons" 2>nul
xcopy /Y /E /I icons "%PLUGIN_DIR%\icons" >nul

echo.
echo ========================================
echo SUCCESS! Clean installation complete!
echo ========================================
echo.
echo Plugin installed to:
echo %PLUGIN_DIR%
echo.
echo Next steps:
echo 1. Run: reset_config.bat (to reset API URLs)
echo 2. Open QGIS
echo 3. Go to Plugins ^> Manage and Install Plugins
echo 4. Enable "geodb.io" plugin
echo 5. For local dev: Check "Use Local Development Server"
echo 6. Make sure Django server is running: python manage.py runserver
echo.
pause