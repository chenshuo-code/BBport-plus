# Changelog

bbport-plus follows [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc). Each release names the
upstream version it is based on. Versions look like `<upstream version>-plus.<n>`; dates are release dates.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Nothing yet.

## [1.6-plus.3] - 2026-10-09

Based on upstream `windows-v1.6` (commit `e076119`). Release notes:
[docs/releases/v1.6-plus.3.md](docs/releases/v1.6-plus.3.md). Aimed at finding a crash that was seen on an AMD
machine (an access violation inside `ucrtbase.dll`, 79 minutes after Windows had flagged the game process for a
memory leak).

### Added
- `run_<time>_proc.csv`: private memory, memory in RAM, commit, thread count and handle count of the game process
  every 2 s, read through Win32 calls (works on any Windows language). The same figures are in the crash report
  (last 40 samples), in each frame-drop record and in the end-of-run summary line.
- A launcher setting *Switch off the OBS capture hook for the game* (on by default): sets
  `DISABLE_VULKAN_OBS_CAPTURE=1`, because OBS's Vulkan capture layer was loaded into the crashed process.
- `make_mods_pack.ps1 -Settings <bbport.ini>` puts graphics settings into a private pack; the installer copies them over.

## [1.6-plus.2] - 2026-10-09

Based on upstream `windows-v1.6` (commit `e076119`), like 1.6-plus.1. Release notes:
[docs/releases/v1.6-plus.2.md](docs/releases/v1.6-plus.2.md).

### Added
- `Install-BBportPlus.ps1 -ModsZip <pack>`: installs a private mods pack, so the mods appear on the launcher's
  *Mods & patches* page. `packaging/windows/make_mods_pack.ps1` makes such a pack from an installed package. bbport-plus
  itself still ships no mods.
- A `mods\README.txt` in the release explains the mod folder layout.

### Changed
- When the game ends abnormally the folder with the crash report now opens by itself, with the file selected. The
  question "open the logs folder?" is gone; after `Play Bloodborne.exe` runs a message box only informs.
- The release no longer carries `tools\Collect_Crash_History.bat`: the launcher has the same function (*Advanced →
  Collect crash history*). The script stays in the source repository for machines without the package.

## [1.6-plus.1] - 2026-10-09

First test release, based on upstream `windows-v1.6` (commit `e076119`). The native runtime (`bin\bb-probe.exe`) is
unchanged. Release notes: [docs/releases/v1.6-plus.1.md](docs/releases/v1.6-plus.1.md).

### Added
- Game language **Chinese (Simplified)**, **Chinese (Traditional)** and **Korean** in the launcher
  (*Game & effects*). The game ships `zhocn`, `zhotw` and `korkr`; the ids are the PS4 system language numbers
  11, 10 and 9.
- One log per run in `<saves folder>\logs\` instead of overwriting `last_run.log`; the last 40 runs are kept.
- Crash report `crash_<time>.txt` when the game ends abnormally (exit code not 0, not the Stop button, not an
  in-game restart): exit code and its meaning, fault lines, last output, GPU and memory state, graphics adapters,
  the related Windows event log entries, settings. A message box points to it after `--play` runs.
- Records of every drop from 55+ FPS to under 40 (`perf_events.log`) and of every frame over 40 ms
  (`run_<time>_stalls.log`), per-window FPS (`..._fps.csv`), GPU data for any vendor (`..._load.csv`) and extra
  NVIDIA data (`..._gpu.csv`), and one summary row per run (`sessions.csv`). See
  [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md).
- `Bloodborne.exe --diag-report [hours]` and `tools/windows/Collect_Crash_History.bat`: what Windows recorded about
  game crashes and graphics driver resets.
- `Install-BBportPlus.ps1` (built from `tools/windows/Install-BBportPlus.ps1.in`): downloads the pinned upstream
  package, checks its SHA-256 and puts the bbport-plus launcher over it.
- `packaging/windows/build_launcher.ps1` and `make_release.ps1`; `THIRD-PARTY-NOTICES.md`; contribution guide and
  issue templates.

### Changed
- The game language starts out as the Windows display language (Chinese, Japanese, Korean, French, Spanish, German,
  Italian, Russian, otherwise English). A saved choice is kept.
- Frame statistics (`BB_FRAME_STATS=1`) are switched on while diagnostics are on (the default).
- The update check looks at this project's releases and only opens the release page. It no longer downloads and
  replaces the package, which would remove this launcher.

[Unreleased]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.3...HEAD
[1.6-plus.3]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.2...v1.6-plus.3
[1.6-plus.2]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.1...v1.6-plus.2
[1.6-plus.1]: https://github.com/chenshuo-code/BBport-plus/releases/tag/v1.6-plus.1
