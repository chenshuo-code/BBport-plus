# Diagnostics: logs, crash reports and frame-drop records

bbport-plus records every run, so a crash or a stutter can be investigated afterwards and the files can
be attached to a bug report. It is on by default (**Advanced → Diagnostics → Record crash reports, GPU
data and frame drops**). The code is `launcher/bbport_diag.py`.

All files go to `<saves folder>\logs\` (the saves folder is set on the *Game & effects* page; by default
`user\` next to `Bloodborne.exe`). **Advanced → Open logs folder** opens it.

| File | Content |
|---|---|
| `run_<time>.log` | Everything the game printed during one run. `user\last_run.log` still holds the newest one. Older runs are kept (the last 40). |
| `run_<time>_fps.csv` | One row per 5 s window of the game's `Frame stats` line: FPS, worst frame, shader / pipeline compiles and their time. |
| `run_<time>_stalls.log` | Every frame over 40 ms, with the game's own breakdown: images registered, MB uploaded, time in each stage. Map changes show up here. |
| `run_<time>_load.csv` | Any GPU vendor: adapter VRAM, 3D engine load, the game's VRAM, free RAM and free commit memory every 2 s (Windows performance counters). |
| `run_<time>_gpu.csv` | NVIDIA only (`nvidia-smi`): usage, VRAM, temperature, clocks, power and the reasons the GPU is throttling. |
| `perf_events.log` | Every drop from a steady 55+ FPS to under 40 FPS, with the frame statistics, pacing, GPU / VRAM state, texture-cache pressure and the stalls in that window. |
| `crash_<time>.txt` | Written when the game ends abnormally (see below). |
| `sessions.csv` | One row per run: duration, result (`ok`, `crash`, `stopped`, `restart`), average / minimum FPS, drops, stalls, peak VRAM. |

## What counts as a crash

The game process exits with a non-zero code, and it was not stopped with the **Stop** button and not
restarted by the in-game *Apply and restart*. Exit codes: `3` is the runtime's own fault report (the
`Fault:` lines) or an `abort()`, `20` / `21` are `STOP:` messages for an unsupported PS4 feature,
`0xC0000005` and friends are Windows exceptions, and `0xFFFFFFFF` means the process was ended from
outside (Task Manager, the system, a driver reset) without leaving a report.

The crash report contains: the exit code and its meaning; the fault lines from the game output; the last
FPS windows, GPU samples and memory state; the graphics adapters and driver versions; the Windows event
log entries about the game (application errors of `bb-probe.exe`, graphics driver resets 4101 / 4116,
low memory 2004, power loss 41 / 6008) from the 15 minutes before; the settings (online account data is
left out); and the last 400 output lines. Nothing has to be started or clicked: when the game ends
abnormally the folder with the report opens by itself, with the file selected. After a run started without the
launcher window (`Play Bloodborne.exe`) a message box says so as well.

## Crashes from before the logs existed

`Bloodborne.exe --diag-report 72` (or **Advanced → Collect crash history**) writes a report with only the
Windows event log part for the last 72 hours. `tools\windows\Collect_Crash_History.bat` (in the source
repository, not in the release) does the same without the game package, for a machine that only needs to be
inspected.

## Reading a drop

`perf_events.log` shows, in this order: the window that fell, the recent windows, the frame-pacing line,
GPU and VRAM state, how far the texture cache is over its budget, free RAM and free commit memory, and the
slow frames in that window. Typical readings:

- **compiles > 0 with a high compile time, stalls with few uploads** → shader / pipeline compilation (first
  visit of an area; the shader cache in the saves folder fills up over time).
- **stalls with large `MB image uploads`** → texture streaming on a map change.
- **GPU load near 100 % and clocks well under `clock_max`, or a throttle reason** → the GPU is the limit
  (power / temperature); common on laptops.
- **VRAM at its total and `memory pressure` lines** → the texture cache is thrashing; close other programs
  or lower the output resolution / preset.
- **free commit memory close to 0** → the page file is too small for the game (it commits well over
  10 GB at 1080p); enlarge the Windows page file.

## Sending a report

Attach the newest `crash_<time>.txt` (and `perf_events.log` for stutter). They contain your Windows version,
graphics card and driver, and the game settings, but not your online account name or password.
