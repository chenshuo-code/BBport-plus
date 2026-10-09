# Collects what Windows recorded about crashes of Bloodborne (bb-probe.exe / Bloodborne.exe) and graphics
# driver resets, for any graphics card. Standalone: needs nothing but Windows. Run Collect_Crash_History.bat.
#   -Hours N   how far back to look (default 72)
param([int]$Hours = 72)

$span = $Hours * 3600000
$window = "TimeCreated[timediff(@SystemTime) <= $span]"
$out = Join-Path $PSScriptRoot ("diag_report_{0}.txt" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

function Get-EventLines([string]$log, [string]$query, [string]$keep, [int]$limit = 40) {
    $xml = & wevtutil qe $log "/q:$query" "/c:$limit" /rd:true /f:xml 2>$null
    if (-not $xml) { return @() }
    try { [xml]$doc = '<Events>' + ($xml -join "`n") + '</Events>' } catch { return @() }
    foreach ($e in $doc.Events.Event) {
        $id = $e.System.EventID
        if ($id -isnot [string]) { $id = $id.'#text' }
        $data = @($e.EventData.Data | ForEach-Object { if ($_ -is [string]) { $_ } else { $_.'#text' } }) -join ' | '
        if ($keep -and $data -notmatch $keep) { continue }
        '{0}  {1}  id {2}: {3}' -f $e.System.TimeCreated.SystemTime, $e.System.Provider.Name, $id, $data
    }
}

$os = Get-CimInstance Win32_OperatingSystem
$lines = @(
    ('Bloodborne diagnostic report  {0:yyyy-MM-dd HH:mm:ss}  (last {1} h)' -f (Get-Date), $Hours),
    ('Windows {0}, {1} logical CPUs, RAM {2:N0} MB, free RAM {3:N0} MB' -f $os.Version, [Environment]::ProcessorCount,
        ($os.TotalVisibleMemorySize / 1KB), ($os.FreePhysicalMemory / 1KB)),
    ('Virtual memory (commit) limit {0:N0} MB, free {1:N0} MB' -f ($os.TotalVirtualMemorySize / 1KB), ($os.FreeVirtualMemory / 1KB)),
    '', '=== graphics adapters ==='
)
$lines += Get-CimInstance Win32_VideoController | ForEach-Object { '{0} | driver {1} | {2}' -f $_.Name, $_.DriverVersion, $_.VideoProcessor }
$pagefiles = Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue
$lines += '', '=== page file ==='
$lines += if ($pagefiles) { $pagefiles | ForEach-Object { '{0}: allocated {1} MB, peak use {2} MB' -f $_.Name, $_.AllocatedBaseSize, $_.PeakUsage } } else { '(none / system managed, not reported)' }

$app = Get-EventLines 'Application' "*[System[(EventID=1000 or EventID=1001 or EventID=1002) and $window]]" 'bb-probe|Bloodborne'
$lines += '', "=== Windows Application log: errors of bb-probe.exe / Bloodborne.exe (last $Hours h) ==="
$lines += if ($app) { $app } else { '(no entry: Windows did not record an application error)' }

$sys = Get-EventLines 'System' "*[System[(EventID=4101 or EventID=4116 or EventID=2004 or EventID=41 or EventID=6008) and $window]]" ''
$lines += '', "=== Windows System log: driver reset 4101/4116, low memory 2004, power loss 41/6008 (last $Hours h) ==="
$lines += if ($sys) { $sys } else { '(no entry)' }

$providers = "Provider[@Name='nvlddmkm' or @Name='Display' or @Name='amdkmdag' or @Name='amdkmdap' or @Name='amdwddmg' or @Name='igfx' or @Name='Microsoft-Windows-Dxgkrnl']"
$gpu = Get-EventLines 'System' "*[System[$providers and $window]]" ''
$lines += '', "=== Windows System log: graphics driver / Dxgkrnl messages (last $Hours h) ==="
$lines += if ($gpu) { $gpu } else { '(no entry)' }

$lines | Set-Content -Path $out -Encoding UTF8
Write-Output $out
