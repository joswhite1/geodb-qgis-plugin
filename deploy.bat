@echo off
REM Deploy the geodb plugin to QGIS

SET PLUGIN_NAME=geodb
SET PLUGIN_DIR=%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\%PLUGIN_NAME%

echo Deploying %PLUGIN_NAME% plugin to QGIS...
echo Target: %PLUGIN_DIR%
echo.
echo NOTE: If you're getting errors about .git folders or
echo can't uninstall the plugin, run: clean_install.bat instead
echo.

REM Create plugin directory if it doesn't exist
if not exist "%PLUGIN_DIR%" mkdir "%PLUGIN_DIR%"

REM Copy Python files
echo Copying Python files...
copy /Y __init__.py "%PLUGIN_DIR%\"
copy /Y geodb.py "%PLUGIN_DIR%\"
copy /Y resources.py "%PLUGIN_DIR%\"
copy /Y resources_rc.py "%PLUGIN_DIR%\"
copy /Y metadata.txt "%PLUGIN_DIR%\"
copy /Y icon.png "%PLUGIN_DIR%\"

REM Copy directories
echo Copying directories...
if not exist "%PLUGIN_DIR%\ui" mkdir "%PLUGIN_DIR%\ui"
xcopy /Y /E /I ui "%PLUGIN_DIR%\ui"

if not exist "%PLUGIN_DIR%\api" mkdir "%PLUGIN_DIR%\api"
xcopy /Y /E /I api "%PLUGIN_DIR%\api"

if not exist "%PLUGIN_DIR%\managers" mkdir "%PLUGIN_DIR%\managers"
xcopy /Y /E /I managers "%PLUGIN_DIR%\managers"

if not exist "%PLUGIN_DIR%\models" mkdir "%PLUGIN_DIR%\models"
xcopy /Y /E /I models "%PLUGIN_DIR%\models"

if not exist "%PLUGIN_DIR%\processors" mkdir "%PLUGIN_DIR%\processors"
xcopy /Y /E /I processors "%PLUGIN_DIR%\processors"

if not exist "%PLUGIN_DIR%\utils" mkdir "%PLUGIN_DIR%\utils"
xcopy /Y /E /I utils "%PLUGIN_DIR%\utils"

if not exist "%PLUGIN_DIR%\icons" mkdir "%PLUGIN_DIR%\icons"
xcopy /Y /E /I icons "%PLUGIN_DIR%\icons"

echo.
echo ========================================
echo Plugin deployed successfully!
echo ========================================
echo.
echo IMPORTANT: If you're experiencing API URL errors:
echo.
echo Run: reset_config.bat
echo.
echo This will reset your configuration to use the correct API URLs.
echo.
echo Next steps:
echo 1. If first time or having issues, run: reset_config.bat
echo 2. Open or restart QGIS
echo 3. Go to Plugins ^> Manage and Install Plugins
echo 4. Enable the "geodb.io" plugin
echo.
echo For local development:
echo - Check "Use Local Development Server" in the plugin
echo - Make sure Django server is running: python manage.py runserver
echo.
pause