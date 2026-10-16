# Changelog

bbport-plus follows [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc). Each release names the
upstream version it is based on. Versions look like `<upstream version>-plus.<n>`; dates are release dates.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 1.6-plus.4 (unreleased; based on upstream windows-v1.6, commit e076119)

bbport-plus is maintained by **GameOClock**. It builds on the work of Super Medo (the Windows port),
deadinside28 (bbport itself), AdrianCsT and the shadPS4 project; the launcher's *About* page and the
entries below name what was taken from where.

### Added
- Game language **Chinese (Simplified)**, **Chinese (Traditional)** and **Korean** in the launcher
  (*Game & effects*). The game ships `zhocn`, `zhotw` and `korkr`; the ids are the PS4 system language numbers
  11, 10 and 9.
- One log per run in `<saves folder>\logs\` instead of overwriting `last_run.log`, with a crash report
  (`crash_<time>.txt`) when the game ends abnormally, a message box after `--play` runs, and a record of every
  drop from 55+ FPS to under 40 FPS and of every frame over 40 ms. Works with any graphics card; NVIDIA cards
  also get temperature, power and throttle reasons. See [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md).
- `Bloodborne.exe --diag-report [hours]` and `tools/windows/Collect_Crash_History.bat`: what Windows recorded
  about game crashes and graphics driver resets.
- **Save backups**: the launcher copies `<saves folder>\savedata` to `<saves folder>\save_backups\<time>_<reason>\`
  when the game starts, every 10 minutes of play when the saves changed, and when the game exits. The newest 10
  are kept. To go back, close the game and copy the files of a backup folder over `savedata`.
- **Hang snapshot**: when the game stops printing frame statistics for 30 s while its process is alive, the
  launcher writes `hang_<time>.txt`: every thread with its name, CPU time, instruction pointer (module +
  offset) and the code addresses on its stack, plus the last log lines. A frozen game can now be diagnosed
  without a debugger.
- `packaging/windows/build_launcher.ps1`: builds only the launcher over an upstream release package.
- An **About** page in the launcher: the requirements (game dump 1.09, Windows, graphics card, processor,
  memory and the page file, disk), this build's version, author and license with links to the source and
  the changes, and the projects it builds on. The sidebar names the author and the full version
  (`1.6-plus.4`), which the crash reports carry too.
- From [AdrianCsT/bloodborne_pc](https://github.com/AdrianCsT/bloodborne_pc) (GPL-2.0-or-later):
  - The AMD device loss with object motion vectors: the motion varyings sat at locations 30/31, past
    the 128-component interface limit once built-ins are counted, and AMD's driver did not survive it;
    they are at 26/27 now and the colour slots between a shader's last target and the motion attachment
    are cleared (the shader cache and pipeline keys are versioned up, so the first start recompiles).
    Object motion vectors stay off on AMD under Windows by default (`vk_object_motion.cpp`).
  - The crash when the player is attacked on Intel 12th-gen and later CPUs (compact XSAVE frames
    overwrote the guest's red zone; `patches/redzone.json`, `scripts/link_modules.py`).
  - The texture collector takes the driver's live WDDM budget on discrete cards too.
  - Crash reports name the thread and walk the x64 unwind tables, so faults inside system DLLs
    (`ucrtbase.dll`) get a call chain.
  - 1 ms timer resolution in the game process; split flexible mappings keep their own pool offset;
    the LRU cache's early stop (`invoke_result_t`) works.
- From [deadinside28/bloodborne_pc](https://github.com/deadinside28/bloodborne_pc) 0.5 (GPL-2.0-or-later,
  with the file headers kept), ported onto the Windows port without its new memory model:
  - **Texture collector by idle time**: an area's textures are freed 20 s after the game stops using
    them (`BB_GC_IDLE_SECONDS`), 5 s under memory pressure (`BB_GC_PRESSURE_IDLE_SECONDS`), and the
    usage compared with the budget is all of our VRAM, not only images. VRAM no longer grows with
    every area visited, and with it the system commit charge that ended runs with an access
    violation in `ucrtbase.dll`.
  - **Background pipeline compilation** (`BB_ASYNC_PIPELINES`, on): new graphics pipelines of passes
    drawn in each of the last 8 frames are compiled on worker threads while their draws go without
    them for a frame or two; `BB_ASYNC_PIPELINES=0` restores compilation on the GPU thread,
    `BB_ASYNC_PIPELINE_THREADS=N` sets the workers.
  - **GPU breadcrumbs** (`BB_BREADCRUMBS`, on where `VK_AMD_buffer_marker` exists): every draw,
    dispatch and pass is numbered and the GPU writes the numbers as it passes them. A device loss,
    a failed submission or a GPU wait over 5 s (the hang watchdog) prints which command the GPU
    never finished, with its shaders and counts.
  - `scripts/game_check.py GAME_DIR`: checks every game file for a damaged extraction (stale
    compressed sectors from some PKG tools), which hung the game while loading shaders.

### Fixed
- **The crash after about an hour of play** (five out of five sessions over 65 minutes ended with an access
  violation in `memcpy`, often seen as a freeze while Windows wrote a crash dump): the game's two per-frame
  renderer heaps are 256 KB and their use grows while playing until a `GXWorker` thread copies into a null
  pointer. `patches/bbport-plus.xml` raises both heaps to 4 MB (on by default, listed on the Patches page).
  Diagnosis, tools and the open question in [docs/GX_FRAME_HEAP.md](docs/GX_FRAME_HEAP.md).
- A render pass whose scene proxy or upscaler redirect has no view keeps the image's own view, and a
  depth/stencil attachment without a view is dropped before recording (both reported in the log): on
  2026-10-15 the AMD driver read a null object inside vkCmdBeginRendering and ended the game after 23 min.
- Every runtime fault report now comes with a minidump (`<saves folder>\logsault_<time>.dmp`, the newest
  three kept): registers and stacks of all threads, for faults inside drivers and system DLLs.
- The draw pipeline's recording thread no longer faults on a null command chunk while a chunk is handed to
  the recorder (seen at the end of a boss fight, 2026-10-16): a replacement is swapped in first
  (Mrsuss60/bloodborne_pc 196ba93).
- `GX heap trace` in the run log (`src/runtime_gxtrace.c`): which game code allocates from those heaps, per
  minute, to find what accumulates. `BB_GX_TRACE=0` turns it off.
- Moving or resizing the game window (and any other swapchain recreation) ended the game with
  `vk_swapchain.cpp Create: Assertion Failed! ... ErrorNativeWindowInUseKHR` on AMD cards: the old
  swapchain was destroyed before the new one was created, and the driver still held the surface. The
  old chain is now passed as `oldSwapchain` and released afterwards; a creation that still fails is
  retried a few times before the game stops. `ErrorUnknown` and a lost surface at present time are
  handled like an out-of-date swapchain (recreated) instead of an assertion.
- A GPU that never finishes a submission, or a queue submission that failed (an out-of-memory
  result was ignored, so the tick it should signal never came), hung the game silently: the window
  stopped, GPU load fell to 0 and Windows ended the process as "not responding" with no report.
  Waits on the GPU are now bounded: a warning after 5 s, exit code 23 with a `GPU hang` fault line
  after 30 s (`BB_GPU_WAIT_LIMIT=<seconds>`, 0 to wait forever), and a failed submission stops the
  game at once with its Vulkan result.
- Crash reports: exit code 23 is explained; the detail line of a GPU assertion (the Vulkan call
  and result) is included in the fault lines; `Check()` calls on command-buffer begin / end / reset
  name the call instead of only `vk::Result=...`.
- Diagnostics record the lowest free commit memory of the run (crash report and run summary), and
  `perf_events.log` gets a `low commit memory` entry when it falls under 1.5 GB: from there Windows
  refuses allocations, which showed up as an access violation in `ucrtbase.dll` on entering a boss
  arena. A larger page file is the remedy.
- Builds again with current MSYS2 CLANG64 (clang 23, libc++ 23, newer MinGW headers): the FidelityFX
  SDK 1.1.4 sources get a MinGW shim (`gpu/shim/ffx_mingw_compat.hpp`, `_countof` / `swprintf_s` /
  `log2` / `<new>`), sirit includes `<cstdlib>` for `std::abort`, and an unused flag in
  `runtime_services.c` is gone (`-Wunused-but-set-global` under `-Werror`). On a Git for Windows
  checkout with `core.autocrlf=true`, MSYS2's git needs the same setting for `build.sh` to apply the
  fsr-vulkan patches (`git config --global core.autocrlf true` in the MSYS2 shell).

### Changed
- Log retention: the last 10 crashes are kept complete (crash report plus the log and csv files of
  that run) and the last 10 other runs; older files are deleted. Before: 40 runs and 200 reports,
  and a crash's run files could be pushed out by later runs.
- `Play Bloodborne.exe` is gone: everything starts through `Bloodborne.exe`, and `Bloodborne.exe
  --play` is the direct start with the saved settings (*Add to Steam* uses it). One file less to
  ship and to explain.
- The game language starts out as the Windows display language (Chinese, Japanese, Korean, French, Spanish, German,
  Italian, Russian, otherwise English). A saved choice is kept.
- Frame statistics (`BB_FRAME_STATS=1`) are switched on while diagnostics are on (the default).
- The update check looks at this project's releases and only opens the release page. It no longer downloads and
  replaces the package, which would remove this launcher.

[Unreleased]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.3...HEAD
[1.6-plus.3]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.2...v1.6-plus.3
[1.6-plus.2]: https://github.com/chenshuo-code/BBport-plus/compare/v1.6-plus.1...v1.6-plus.2
[1.6-plus.1]: https://github.com/chenshuo-code/BBport-plus/releases/tag/v1.6-plus.1

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
