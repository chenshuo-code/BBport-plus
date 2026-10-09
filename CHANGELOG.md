# Changelog

bbport-plus follows [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc). Each release names the
upstream version it is based on. Versions look like `<upstream version>-plus.<n>`; dates are release dates.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Nothing yet.

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
- Frame statistics (`BB_FRAME_STATS=1`) are switched on while diagnostics are on (the default).
- The update check looks at this project's releases and only opens the release page. It no longer downloads and
  replaces the package, which would remove this launcher.

[Unreleased]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.1...HEAD
[1.6-plus.1]: https://github.com/chenshuo-code/BBport-plus/releases/tag/v1.6-plus.1
