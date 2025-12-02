@echo off
echo ====================================
echo Geodb.io QGIS Plugin - Reset Config
echo ====================================
echo.
echo This will delete your saved configuration file.
echo The plugin will create a new one with corrected settings.
echo.
pause

set CONFIG_FILE=%APPDATA%\QGIS\QGIS3\profiles\default\geodb_plugin_config.json

if exist "%CONFIG_FILE%" (
    echo Deleting config file: %CONFIG_FILE%
    del "%CONFIG_FILE%"
    echo.
    echo ✓ Config file deleted successfully!
    echo.
    echo Now restart QGIS and the plugin will use the corrected URLs.
) else (
    echo Config file not found at: %CONFIG_FILE%
    echo No action needed.
)

echo.
pause