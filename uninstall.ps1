# Removes the right-click menu, the Start Menu shortcut and the installed copy.
$venv = Join-Path $env:LOCALAPPDATA "Convert\venv"
if (Test-Path "$venv\Scripts\pythonw.exe") { & "$venv\Scripts\pythonw.exe" -m convert_win --uninstall-context-menu }
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Convert.lnk")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $env:LOCALAPPDATA "Convert")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $env:APPDATA "Convert")
Write-Host "Convert removed."
