# bbport-plus

Maintained by **GameOClock** (bbport-plus). Built on the work of Super Medo, deadinside28, AdrianCsT
and shadPS4; the launcher's *About* page and [CHANGELOG.md](CHANGELOG.md) name what came from where.

A Windows fork of [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc) (which is built on
[deadinside28's bbport](https://github.com/deadinside28/bloodborne_pc)), aimed at **finding the cause of crashes
and stutter** and at players who use Chinese. Same licence as upstream: GPL-2.0-or-later. Based on upstream
`windows-v1.6` (commit `e076119`); see [CHANGELOG.md](CHANGELOG.md).

> **No game files are included, and none belong in this repository.** You need your own decrypted dump of
> Bloodborne (game version 1.09). This project is not affiliated with Sony Interactive Entertainment or
> FromSoftware.

## What this fork adds

- **Chinese (Simplified and Traditional) and Korean** in the launcher's *Game language* list.
- **One log per run** (upstream overwrote `last_run.log` on every start, so the log of a crash was gone after the
  next launch), kept in `<saves folder>\logs\`.
- **Automatic crash report** when the game ends abnormally: exit code, the runtime's fault lines, the last output,
  GPU and memory state, graphics adapters, and the matching Windows event log entries (application errors, graphics
  driver resets, low memory). The folder with the report opens by itself; there is nothing to click or start.
- **Frame-drop and stutter records**: every fall from a steady 55+ FPS to under 40, and every frame over 40 ms with
  the game's own breakdown (shader compiles, texture uploads). Works with any GPU; NVIDIA cards also log
  temperature, power and throttle reasons.
- **Advanced → Collect crash history** (or `Bloodborne.exe --diag-report`): what Windows recorded about crashes and
  driver resets from before the logs existed.
- **Mods**: pick which mods load, and in which order, on *Mods & patches*. bbport-plus ships none (see *Mods* below).

Details and how to read the files: [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md).

### 中文说明

这是 bloodborne_pc(Windows 原生移植)的分支,重点是**定位闪退和卡顿的原因**:

- 启动器的游戏语言增加简体中文、繁体中文、韩语。
- 每次运行单独保存日志,不再被下一次启动覆盖;日志在存档目录的 `logs\` 下。
- 游戏异常退出时自动生成崩溃报告(退出码、故障行、显卡和内存状态、Windows 事件日志中的相关记录),并自动打开报告所在文件夹,无需任何操作。
- 自动记录帧率从稳定 55 以上掉到 40 以下的时刻,以及每一个超过 40 ms 的卡顿帧。任意显卡可用。
- 启动器 *Advanced* 页的“收集崩溃历史”:导出 Windows 对闪退和显卡驱动重置的记录。
- MOD 在 *Mods & patches* 页勾选加载、调整顺序;本项目不附带任何 MOD。

仓库不含任何游戏文件,需要自备 1.09 版的解密转储。

## Install

Download `Install-BBportPlus.ps1` from the [latest release](https://github.com/chenshuo-code/BBport-plus/releases/latest) and run

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-BBportPlus.ps1 -Destination D:\Games\bbport-plus
```

It downloads the official upstream package (`windows-v1.6`) from its own release page, checks its SHA-256,
downloads the bbport-plus overlay of the release (also checked), and puts the launcher over the package. bbport-plus
itself does not redistribute the upstream binaries, so NVIDIA's DLSS runtime and the other upstream parts come
straight from upstream. The destination has to be on an **NTFS** drive (mods need links; exFAT cannot do that).

Then start `Bloodborne.exe`, choose your game folder on *Game & effects* and press **PLAY**. To install by hand,
unpack the upstream zip and unpack the release's `bbport-plus-<version>-overlay.zip` over its `bbport-windows`
folder. Safe to read first: the script is plain PowerShell and is short.

### 安装

在[最新发行版](https://github.com/chenshuo-code/BBport-plus/releases/latest)下载 `Install-BBportPlus.ps1`,用上面的命令运行即可。
脚本会从上游官方发行页下载 v1.6 并校验哈希,再叠加本项目的启动器;安装目录必须在 NTFS 磁盘上。游戏语言在启动器
*Game & effects* 页选择,列表里有简体中文和繁体中文。

## Mods

The launcher lays mods over the game when it starts and never changes the game files. Put each mod in its own folder
under `mods\` (with the game's folders inside, such as `chr\`, `menu\`, `msg\`, or a `dvdroot_ps4\` folder) and choose
which ones load, and in which order, on *Mods & patches*.

**bbport-plus does not include or host any mods.** Mods change the game's own files, which belong to Sony and
FromSoftware, and every mod has its own author and terms. To move your own mods to another PC of yours, make a private
pack and install it with the installer:

```powershell
.\packaging\windows\make_mods_pack.ps1 -Package D:\Games\bbport-plus -Out D:\my-mods-pack.zip
.\Install-BBportPlus.ps1 -Destination E:\Games\bbport-plus -ModsZip D:\my-mods-pack.zip
```

Keep such a pack private; do not upload it.

## Build

```powershell
.\packaging\windows\build_launcher.ps1 -Package D:\Games\bbport-windows   # launcher only, over an upstream package
.\packaging\windows\make_release.ps1 -Python C:\Python314\python.exe       # release files in dist\release\
```

Both need a Windows Python 3.10+. The native part is built as described in the upstream README below.

## Reporting a problem

Open an [issue](https://github.com/chenshuo-code/BBport-plus/issues/new/choose) and attach the newest
`crash_<time>.txt` (crash) or `perf_events.log` (stutter) from `<saves folder>\logs\`, and say which graphics card
and driver you use. Details: [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md). Please do not attach game files.
Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## Staying in step with upstream

The changes are in `launcher/bbport_launcher_win.py` (small hooks), the new `launcher/bbport_diag.py`,
`packaging/windows/build_launcher.ps1`, `tools/windows/` and the documents. To take a newer upstream version:

```bash
git remote add upstream https://github.com/Supermedo/bloodborne_pc.git
git fetch upstream windows
git merge upstream/windows
```

---

# Bloodborne for Windows

**Bloodborne running natively on Windows 10 and 11, by [Supermedo](https://github.com/Supermedo) Mohammed Albarghouthi.**

The original PlayStation 4 game runs directly on your PC: its own x86-64 code runs natively,
and the graphics are translated to Vulkan. No emulator window, no setup scripts: unpack the zip,
start `Bloodborne.exe`, pick your game folder and press **PLAY**.

**[Download the latest version](https://github.com/Supermedo/bloodborne_pc/releases/latest)** · **[Join the Discord](https://discord.gg/yTMG8c4Bqm)**

> **No game files are included.** You need your own decrypted dump of Bloodborne
> (CUSA03173). Version 1.09 is needed for the community patches (60/90/unlocked FPS,
> resolution, effects); other versions run at 30 FPS.
> This project is not affiliated with Sony Interactive Entertainment or FromSoftware.

## Features

- **Online play (beta):** messages, bloodstains and wandering ghosts from other hunters through
  [The Hunter's Dream](https://thehuntersdream.com), and bells and summons through a shadNet
  server (shadPS4's public one by default).
- **Launcher with every setting in one window**, in 13 languages: English, Arabic, Russian,
  Spanish, Portuguese, French, German, Italian, Polish, Turkish, Chinese, Japanese, Korean.
- **Updates from the launcher:** when a new version is out it tells you, and **Update**
  installs it. Your saves, settings and mods are kept.
- **NVIDIA DLSS** on GeForce RTX cards (RTX 20 series and newer).
- **AMD FSR 3.1 and FSR 4** upscaling, plus native-resolution TAA.
- **Unlocked frame rate** with a frame cap (up to 120 by default), or 30/60/90 FPS.
- **Output resolutions** from 720p to 4K, presets from Native AA to Ultra Performance.
- **Cheats page:** never die, stealth, silent footsteps, Rally that never fades, enemy control,
  and gameplay tweaks (camera distance, no camera auto-rotation, easier running, ragdoll
  physics).
- **Game effects** on and off: chromatic aberration, depth of field, motion blur, SSAO, the
  game's own AA, dynamic light shadows, screen-space reflections, model detail.
- **Mods and third-party patches**, loaded without changing your game files.
- **Add to Steam** puts the game in your Steam library in one click.
- **`Bloodborne.exe --play`** starts the game straight away with your saved settings (*Add to
  Steam* uses it; good for a shortcut too).
- **Controller and keyboard**, an in-game settings menu (Insert or L3+R3), name entry on
  screen, a desktop shortcut, and a button to clear the shader cache.

## Requirements

- Windows 10 (1903 or later) or Windows 11, 64-bit
- A graphics card with Vulkan 1.3 and a current driver
- About 6 GB of free memory (RAM + page file), 10 GB for 1440p or 4K output
- DLSS: NVIDIA GeForce RTX 20 series or newer

Nothing else to install: everything the game needs is in the zip.

## How to play

1. Download the zip from [Releases](https://github.com/Supermedo/bloodborne_pc/releases/latest)
   and unpack it anywhere.
2. Start `Bloodborne.exe`.
3. On **Game & effects**, choose your game folder (the one with `eboot.bin`).
4. Press **PLAY**.

In the game, **Insert** (or **L3+R3** on a controller) opens the settings menu.
Keyboard: WASD move, arrows camera, Space Cross, Left Shift Circle, E Square, Q Triangle,
1/3 L1/R1, R/F L2/R2, Z/C L3/R3, I/K/J/L d-pad, Enter Options, Tab touchpad.

## Playing online (beta)

1. Register a shadNet account at [shadnet.shadps4.net](https://shadnet.shadps4.net).
2. In the launcher, open **Online co-op**, tick **Play online** and enter your account name
   (not your email) and password.
3. Press **Test connection**: all three lines should say "reachable". Then press **PLAY** and
   choose online at the title screen. The Hunter's Dream welcome message means you are online.

Everyone you want to play with needs game version 1.09 and the same co-op server. A private
shadNet server (from the [shadp2p](https://github.com/Wozzardman/shadp2p) project) works too:
put its address in **Server address**.

## Known issues

- The character preview on the character creation screen stays empty. The character is
  created correctly and looks right in the game.
- Some AMD graphics cards still crash when the game world loads; fixes are in progress.
- Online play is new. Messages, bloodstains and ghosts work; summoning other players with the
  bells has not been confirmed yet. Please report how it goes on Discord.
- Above about 120 FPS the game's movement slows down (a limit of the game itself): keep the
  frame cap at 120 or lower.

## Problems and feedback

Ask on the [Discord server](https://discord.gg/yTMG8c4Bqm), or open an [issue](https://github.com/Supermedo/bloodborne_pc/issues) and attach
`user\last_run.log` from the game folder, with your graphics card and what happened.
If the game shows only a black screen, try **Advanced → Clear shader cache** first.

## Building from source

See [packaging/windows/README.md](packaging/windows/README.md): MSYS2 CLANG64, `bash build.sh`,
then `bash packaging/windows/package.sh`. DLSS is built separately with
`packaging/windows/build_dlss.sh`.

## Credits

Built on [bbport](https://github.com/deadinside28/bloodborne_pc), the native Linux port of
Bloodborne by deadinside28, and on the [shadPS4](https://github.com/shadps4-emu/shadPS4)
renderer. The original README is in [docs/original-readme](docs/original-readme/README.md).

Online play uses the network and PSN code of [shadp2p](https://github.com/Wozzardman/shadp2p)
by Wozzardman (the shadPS4 Bloodborne co-op project), the shadNet server of the
[shadPS4](https://shadps4.net) team, and [The Hunter's Dream](https://thehuntersdream.com) server
by droogie and its team.

Also used: [FSR-Vulkan](https://github.com/FireBurn/FSR-Vulkan) and the AMD FidelityFX SDK
(FSR), FSR 4 assets from [FireBurn/Q2RTX](https://github.com/FireBurn/Q2RTX), the DLSS bridge
adapted from [IFreemz/shadPS4-Bloodborne-DLSS-FSR](https://github.com/IFreemz/shadPS4-Bloodborne-DLSS-FSR),
the [NVIDIA DLSS SDK](https://github.com/NVIDIA/DLSS) (`nvngx_dlss.dll` under NVIDIA's license),
[LibAtrac9](https://github.com/Thealexbarney/LibAtrac9), [SDL3](https://github.com/libsdl-org/SDL),
[FFmpeg](https://ffmpeg.org), [Dear ImGui](https://github.com/ocornut/imgui),
[sirit](https://github.com/shadps4-emu/sirit), [magic_enum](https://github.com/Neargye/magic_enum),
[miniz](https://github.com/richgel999/miniz), [xbyak](https://github.com/herumi/xbyak),
[wepoll](https://github.com/piscisaureus/wepoll), [cpp-httplib](https://github.com/yhirose/cpp-httplib),
[Protocol Buffers](https://protobuf.dev), [OpenSSL](https://www.openssl.org),
[miniupnpc](https://miniupnp.tuxfamily.org), [JSON for Modern C++](https://github.com/nlohmann/json),
[Vulkan Memory Allocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator),
[MSYS2](https://www.msys2.org) / [LLVM](https://llvm.org), [PyInstaller](https://pyinstaller.org)
and [Pillow](https://python-pillow.org). Game patches by Kyo, Lance McDonald, auser1337,
illusion, emoose and the Bloodborne community.

NVIDIA, GeForce RTX and DLSS are trademarks of NVIDIA Corporation. The icon is original
artwork.

## License

GNU GPL v2 or later ([LICENSE](LICENSE)). Third-party components keep their own licenses
(see `licenses\` in the download).
