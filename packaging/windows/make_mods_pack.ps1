# Packs the mods of an installed bbport-plus into one zip, to carry them to another PC of your own:
#   <zip>\mods\<mod folders>   and   <zip>\mods.json (which mods are on, and their order)
# Install it with   Install-BBportPlus.ps1 -ModsZip <zip>   or unpack it over an installed package.
#
#   .\packaging\windows\make_mods_pack.ps1 -Package D:\Games\bbport-plus -Out D:\bbport-plus-mods-pack.zip
#
# Keep the pack private. Mods change the game's own files, so this project never ships them and neither should you:
# do not upload the pack to GitHub or any public place. The file name is ignored by .gitignore (*mods-pack*.zip).
param(
    [Parameter(Mandatory)][string]$Package,
    [Parameter(Mandatory)][string]$Out
)
$ErrorActionPreference = 'Stop'
$mods = Join-Path $Package 'mods'
if (-not (Test-Path -LiteralPath $mods)) { throw "No mods folder in $Package" }
$folders = @(Get-ChildItem -LiteralPath $mods -Directory)
if ($folders.Count -eq 0) { throw "$mods has no mod folders" }
if (Test-Path -LiteralPath $Out) { throw "$Out already exists; choose another name or delete it first" }

Add-Type -AssemblyName System.IO.Compression
$zipFile = [IO.File]::Open($Out, [IO.FileMode]::CreateNew)
$zip = New-Object IO.Compression.ZipArchive($zipFile, [IO.Compression.ZipArchiveMode]::Create)
function Add-ToZip([string]$path, [string]$name) {
    $entry = $zip.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
    $entry.LastWriteTime = (Get-Item -LiteralPath $path).LastWriteTime
    $in = [IO.File]::OpenRead($path)
    $stream = $entry.Open()
    $in.CopyTo($stream)
    $stream.Dispose()
    $in.Dispose()
}
try {
    $base = (Resolve-Path -LiteralPath $mods).Path
    foreach ($file in Get-ChildItem -LiteralPath $mods -Recurse -File) {
        # entry names use forward slashes, as the zip format requires
        Add-ToZip $file.FullName ('mods/' + $file.FullName.Substring($base.Length + 1).Replace('\', '/'))
    }
    $json = Join-Path $Package 'mods.json'
    if (Test-Path -LiteralPath $json) { Add-ToZip $json 'mods.json' }
} finally {
    $zip.Dispose()
    $zipFile.Dispose()
}
'{0}: {1} mod folders, {2:N1} MB' -f $Out, $folders.Count, ((Get-Item -LiteralPath $Out).Length / 1MB)
