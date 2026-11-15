@echo off
setlocal
if "%OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT%"=="" (
  echo cursor-sdk-bridge-opsx: OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT is required 1>&2
  exit /b 1
)
pushd "%OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT%"
"%OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT%\bin\node.exe" "%~dp0cursor-sdk-bridge-opsx.mjs" %*
set ERR=%ERRORLEVEL%
popd
exit /b %ERR%
