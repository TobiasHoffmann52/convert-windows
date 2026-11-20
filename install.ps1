# Installs Convert from source for the current user and adds the right-click menu. No admin rights needed.
#   .\install.ps1                 install
#   .\install.ps1 -WithFfmpeg     also install ffmpeg (needed for audio and video) with winget
param([switch]$WithFfmpeg)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$home_ = Join-Path $env:LOCALAPPDATA "Convert"
$venv = Join-Path $home_ "venv"

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "Python 3.10+ is required. Install it with:  winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
if ($py.Name -like "py*") { $pyArgs = @("-3") } else { $pyArgs = @() }

New-Item -ItemType Directory -Force $home_ | Out-Null
& $py.Source @pyArgs -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install $here
$pyw = "$venv\Scripts\pythonw.exe"

& $pyw -m convert_win --install-context-menu

# Start Menu shortcut
$ico = & "$venv\Scripts\python.exe" -c "import convert_win, pathlib; print(pathlib.Path(convert_win.__file__).parent / 'assets' / 'icon.ico')"
$lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Convert.lnk"
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sc.TargetPath = $pyw; $sc.Arguments = "-m convert_win"; $sc.IconLocation = $ico; $sc.Save()

if ($WithFfmpeg) { winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements }
Write-Host "`nDone. Right-click a file > Convert...  (Windows 11: Show more options)" -ForegroundColor Green
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not $WithFfmpeg) {
    Write-Host "Audio/video conversion needs ffmpeg:  winget install Gyan.FFmpeg" -ForegroundColor Yellow
}
