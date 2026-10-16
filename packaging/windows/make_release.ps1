# Builds the bbport-plus release files in dist\release\:
#   bbport-plus-<version>-overlay.zip   the launcher (Bloodborne.exe + _internal) with docs and tools
#   Install-BBportPlus.ps1              downloads the pinned upstream package and puts the overlay over it
#   SHA256SUMS.txt, RELEASE_NOTES.md    checksums and the notes of this release (docs\releases\v<version>.md)
#
#   .\packaging\windows\make_release.ps1 -Python C:\Python314\python.exe
#
# The version is PLUS_VERSION in launcher\bbport_diag.py; the tag to create is v<version>.
# Release from a clean, committed tree: the binary is then reproducible from the tag.
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

$dirty = git status --porcelain
if ($dirty) { Write-Warning "The working tree has uncommitted changes:`n$dirty`nA release should be built from a committed tree." }
$commit = (git rev-parse --short HEAD)

$diag = Get-Content (Join-Path $root 'launcher\bbport_diag.py') -Raw
$version = [regex]::Match($diag, "PLUS_VERSION\s*=\s*'([^']+)'").Groups[1].Value
$repo = [regex]::Match($diag, "PLUS_REPO\s*=\s*'([^']+)'").Groups[1].Value
if (-not $version -or -not $repo) { throw 'PLUS_VERSION / PLUS_REPO not found in launcher\bbport_diag.py' }
$tag = "v$version"
$notesFile = Join-Path $root "docs\releases\$tag.md"
if (-not (Test-Path $notesFile)) { throw "Write the release notes first: docs\releases\$tag.md" }

& (Join-Path $PSScriptRoot 'build_launcher.ps1') -Python $Python
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) { }
$dist = Join-Path $root 'out\pyi-dist\Bloodborne'
if (-not (Test-Path (Join-Path $dist 'Bloodborne.exe'))) { throw 'the launcher was not built' }

$out = Join-Path $root 'dist\release'
$stage = Join-Path $root 'out\overlay-stage'
foreach ($p in $out, $stage) { if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force } }
New-Item -ItemType Directory -Force $out, $stage, "$stage\docs", "$stage\mods" | Out-Null

Copy-Item (Join-Path $dist 'Bloodborne.exe') $stage
Copy-Item (Join-Path $dist '_internal') (Join-Path $stage '_internal') -Recurse
Copy-Item (Join-Path $root 'docs\DIAGNOSTICS.md') "$stage\docs"
Copy-Item (Join-Path $root 'LICENSE'), (Join-Path $root 'CHANGELOG.md'), (Join-Path $root 'THIRD-PARTY-NOTICES.md') $stage
@"
bbport-plus $version (overlay)

This is the bbport-plus launcher. It is meant to be put over the official upstream package
(Supermedo/bloodborne_pc, windows-v1.6): Install-BBportPlus.ps1 does that for you, or unpack this zip over an
unpacked upstream bbport-windows folder and let it replace Bloodborne.exe and _internal.

No game files are included. Source code: https://github.com/$repo (tag $tag, commit $commit).
Changes and how to read the logs: CHANGELOG.md, docs\DIAGNOSTICS.md. If the game crashes, a crash report is written to
<saves folder>\logs and that folder opens by itself. Licence: GPL-2.0-or-later (LICENSE).
"@ | Set-Content -Path "$stage\README-bbport-plus.txt" -Encoding UTF8

@"
Mods go here: one folder per mod, with the game's folders inside (chr\, menu\, msg\, ... or dvdroot_ps4\...).
Choose which ones load, and in which order, on the launcher's "Mods & patches" page. The game files are never changed:
the launcher lays the mods over the game when it starts.

bbport-plus does not ship any mods. Mods change the game's own files, so they are not ours to redistribute.
"@ | Set-Content -Path "$stage\mods\README.txt" -Encoding UTF8
$overlayName = "bbport-plus-$version-overlay.zip"
$overlay = Join-Path $out $overlayName
# Written by hand: Windows PowerShell's own zip helpers store paths with backslashes, which the zip format forbids.
Add-Type -AssemblyName System.IO.Compression
$zipFile = [IO.File]::Open($overlay, [IO.FileMode]::Create)
$zip = New-Object IO.Compression.ZipArchive($zipFile, [IO.Compression.ZipArchiveMode]::Create)
foreach ($file in Get-ChildItem $stage -Recurse -File) {
    $name = $file.FullName.Substring($stage.Length + 1).Replace('\', '/')
    $entry = $zip.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
    $entry.LastWriteTime = $file.LastWriteTime
    $in = [IO.File]::OpenRead($file.FullName)
    $outStream = $entry.Open()
    $in.CopyTo($outStream)
    $outStream.Dispose()
    $in.Dispose()
}
$zip.Dispose()
$zipFile.Dispose()
$overlayHash = (Get-FileHash $overlay -Algorithm SHA256).Hash

$template = Get-Content (Join-Path $root 'tools\windows\Install-BBportPlus.ps1.in') -Raw
$installer = $template.Replace('__PLUS_VERSION__', $version).
    Replace('__OVERLAY_URL__', "https://github.com/$repo/releases/download/$tag/$overlayName").
    Replace('__OVERLAY_SHA256__', $overlayHash)
if ($installer -match '__[A-Z_]+__') { throw "unreplaced placeholder in the installer: $($Matches[0])" }
$installerPath = Join-Path $out 'Install-BBportPlus.ps1'
[IO.File]::WriteAllText($installerPath, $installer, (New-Object Text.UTF8Encoding $true))

Copy-Item $notesFile (Join-Path $out 'RELEASE_NOTES.md')
$sums = foreach ($f in $overlay, $installerPath) { '{0}  {1}' -f (Get-FileHash $f -Algorithm SHA256).Hash.ToLower(), (Split-Path $f -Leaf) }
$sums | Set-Content -Path (Join-Path $out 'SHA256SUMS.txt') -Encoding ASCII

Write-Output "release $tag from commit $commit"
Get-ChildItem $out | ForEach-Object { '{0,10:N1} MB  {1}' -f ($_.Length / 1MB), $_.Name }
