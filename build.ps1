# Builds dist\Convert\Convert.exe and Convert-Windows.zip.   Usage:  .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m venv .build-venv
& .\.build-venv\Scripts\python.exe -m pip install --upgrade pip
& .\.build-venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
# onedir (not onefile): onefile unpacks itself on every launch, which makes the right-click menu feel slow.
& .\.build-venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name Convert `
    --icon convert_win\assets\icon.ico `
    --add-data "convert_win\assets;convert_win\assets" `
    --collect-all tkinterdnd2 --collect-all sv_ttk --collect-all pillow_heif --collect-all pypdfium2 `
    --collect-all mammoth --collect-submodules markdown_it --collect-submodules mdit_py_plugins `
    launcher.py

# Prove the packaged app has everything it needs (libraries, icons, drag-and-drop, a few real conversions).
$test = Start-Process -FilePath ".\dist\Convert\Convert.exe" -ArgumentList "--selftest" -Wait -PassThru
Get-Content (Join-Path $env:TEMP "convert-selftest.txt")
if ($test.ExitCode -ne 0) { throw "Self-test of the built exe failed" }

Compress-Archive -Force -Path dist\Convert -DestinationPath Convert-Windows.zip
Write-Host "`nBuilt Convert-Windows.zip. Unzip it, run Convert.exe and click Install." -ForegroundColor Green
