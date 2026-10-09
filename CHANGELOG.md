# Changelog

bbport-plus follows [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc). Each entry
names the upstream version it is based on.

## Unreleased (based on upstream windows-v1.6, commit e076119)

### Added
- Game language **Chinese (Simplified)**, **Chinese (Traditional)** and **Korean** in the launcher
  (`Game & effects`). The game ships `zhocn`, `zhotw` and `korkr`; the ids are the PS4 system language numbers
  11, 10 and 9.
- One log per run in `<saves folder>\logs\` instead of overwriting `last_run.log`, with a crash report
  (`crash_<time>.txt`) when the game ends abnormally, a message box after `--play` runs, and a record of every
  drop from 55+ FPS to under 40 FPS and of every frame over 40 ms. Works with any graphics card; NVIDIA cards
  also get temperature, power and throttle reasons. See [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md).
- `Bloodborne.exe --diag-report [hours]` and `tools/windows/Collect_Crash_History.bat`: what Windows recorded
  about game crashes and graphics driver resets.
- `packaging/windows/build_launcher.ps1`: builds only the launcher over an upstream release package.

### Changed
- Frame statistics (`BB_FRAME_STATS=1`) are switched on while diagnostics are on (the default).
- The launcher no longer checks for updates by itself, because an upstream release would replace this launcher.
  *Check for updates* still works by hand.
