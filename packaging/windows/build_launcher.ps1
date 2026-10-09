# Builds only the launcher (Bloodborne.exe + _internal) with PyInstaller, so launcher/*.py can be worked on
# without the MSYS2 CLANG64 toolchain. The native part (bin\bb-probe.exe and DLLs) comes from an upstream
# release package; this script puts the freshly built launcher over it.
#
#   .\packaging\windows\build_launcher.ps1 -Package D:\Games\bbport-windows
#   .\packaging\windows\build_launcher.ps1                       # build only, output in out\pyi-dist
#
# Needs a Windows Python 3.10+ (python.org). A private venv with PyInstaller and Pillow is created in out\pyenv.
param(
    [string]$Package,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

$venv = Join-Path $root 'out\pyenv'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "could not create a venv with '$Python'" }
}
& $py -m pip install -q --disable-pip-version-check pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

# The scripts run inside Bloodborne.exe (--script): the standard modules they import come along.
$hidden = 'argparse', 'base64', 'collections', 'hashlib', 'json', 're', 'shutil', 'struct', 'tempfile',
          'xml.etree.ElementTree', 'urllib.request', 'ctypes.wintypes', 'csv', 'datetime', 'platform'
$hiddenArgs = $hidden | ForEach-Object { '--hidden-import'; $_ }

# PyInstaller logs to stderr; that must not stop the script.
$ErrorActionPreference = 'Continue'
& $py -m PyInstaller --noconfirm --clean --log-level ERROR --windowed --name Bloodborne `
    --icon "$root\launcher\bloodborne.ico" --distpath out\pyi-dist --workpath out\pyi-work --specpath out\pyi-work `
    --paths "$root\scripts" @hiddenArgs "$root\launcher\bbport_launcher_win.py" 2>&1 | Out-Null
$status = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($status -ne 0) { throw "PyInstaller failed ($status)" }

$dist = Join-Path $root 'out\pyi-dist\Bloodborne'
Write-Output "built: $dist"
if ($Package) {
    if (-not (Test-Path (Join-Path $Package 'bin\bb-probe.exe'))) {
        throw "'$Package' does not look like an upstream bbport-windows package (bin\bb-probe.exe is missing)"
    }
    Copy-Item (Join-Path $dist 'Bloodborne.exe') $Package -Force
    robocopy (Join-Path $dist '_internal') (Join-Path $Package '_internal') /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Output "installed into $Package"
}
