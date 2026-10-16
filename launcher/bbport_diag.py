# SPDX-License-Identifier: GPL-2.0-or-later
"""Run recorder of the Windows launcher: one log per run, crash reports, GPU telemetry and
frame-drop records. Everything goes to <saves folder>\\logs\\.

Files per run (stamp = start time):
  run_<stamp>.log          the game's output (user\\last_run.log keeps the newest one as before)
  run_<stamp>_fps.csv      one row per 5 s window of the game's "Frame stats" line
  run_<stamp>_stalls.log   every frame over 40 ms with the game's own breakdown (uploads, compiles)
  run_<stamp>_gpu.csv      NVIDIA telemetry every 2 s (usage, VRAM, clocks, power, throttle reasons)
  crash_<stamp>.txt        written when the game ends abnormally: exit code, the fault lines, the
                           last log lines, the Windows event log entries about it, the last GPU samples
  perf_events.log          every drop from a steady 55+ FPS to under 40 FPS, with context
  sessions.csv             one row per run (result, average / minimum FPS, drops, stalls)
  hang_<stamp>.txt         written when the game stops presenting frames while the process is alive: what
                           every thread is doing (module + offset, CPU time) and the last log lines
  <saves folder>\\save_backups\\<time>_<reason>\\savedata   copies of the saves: at start, every 10 min of play
                           when they changed, and at exit (newest 20 kept)
"""
import collections
import csv
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime

PLUS_VERSION = '1.6-plus.4'   # bbport-plus release; the part before '-plus' is the upstream version it is based on
PLUS_REPO = 'chenshuo-code/BBport-plus'
KEEP_RUNS = 10          # runs that ended normally: logs kept (older ones are deleted with their csv files)
KEEP_CRASHES = 10       # crashes kept, each with the crash report and every file of that run
KEEP_SAVE_BACKUPS = 10  # save snapshots kept (save_backups\\)
SAVE_BACKUP_INTERVAL = 600.0
SAVE_SETTLE_SECONDS = 3.0   # a save file written in the last seconds may be half written: wait
HANG_SECONDS = 30.0     # no "Frame stats" window for this long while the game process lives = a hang
HANG_MAX_REPORTS = 3
DROP_FPS = 40.0         # a window below this ...
STEADY_FPS = 55.0       # ... after a window at or above this is a drop
EVENT_COOLDOWN = 20.0   # seconds between two drop records
LOW_COMMIT_MB = 1536    # free commit memory under this is recorded (allocations start to fail)
LOW_COMMIT_COOLDOWN = 60.0
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

FRAME_STATS = re.compile(r'Frame stats: ([\d.]+) FPS, worst frame ([\d.]+) ms \(vblank (\d+) Hz\); '
                         r'(\d+) shader/pipeline compiles, ([\d.]+) ms')
FRAME_PACING = re.compile(r'Frame pacing: median ([\d.]+) ms, stddev ([\d.]+) ms, p99 ([\d.]+) ms, (\d+) frames')
STALL = re.compile(r'Stall: ([\d.]+) ms frame; GPU thread on CPU ([\d.]+) ms; (\d+) images registered, '
                   r'([\d.]+) MB image uploads, ([\d.]+) MB buffer uploads')
MEMORY_PRESSURE = re.compile(r'Texture cache: memory pressure, (\d+) of (\d+) MiB')
FAULT_LINE = re.compile(r'^(Fault:|Host fault|Guest fault|STOP:|  #\d+ |  rax=|  rsi=|API:|Caller |.*[Aa]ssert|'
                        r'.*terminate called|.*VK_ERROR|.*device lost|.*DEVICE_LOST|.*out of (device )?memory|'
                        r'.*unhandled|.*Unhandled|.*Failed to |.*vk::Result=|.*GPU hang|.*submit failed|'
                        r'.*failed: Error)')

# Exit codes: the runtime's own (probe.c / runtime_*.c) and the usual Windows exception codes.
EXIT_MEANING = {
    0: 'normal exit',
    1: 'stopped (Stop button) or generic failure',
    3: 'runtime fault report ("Fault:" lines) or abort()',
    20: 'STOP: unsupported PS4 import',
    21: 'STOP: unsupported runtime feature',
    23: 'STOP: GPU library assertion (the fault lines name the Vulkan call that failed)',
    0xC0000005: 'access violation',
    0xC000001D: 'illegal instruction',
    0xC0000094: 'integer divide by zero',
    0xC00000FD: 'stack overflow',
    0xC0000135: 'a DLL was not found',
    0xC0000142: 'a DLL failed to initialise',
    0xC0000374: 'heap corruption',
    0xC0000409: 'stack buffer overrun / fast fail (abort)',
    0xC000012D: 'out of virtual memory / commit limit',
    0x80000003: 'breakpoint',
    0xE06D7363: 'unhandled C++ exception',
    0xCFFFFFFF: 'ended while not responding (hang; Windows records an Application Hang 1002 event, no fault report)',
    0xFFFFFFFF: 'ended from outside with no fault report (Task Manager / taskkill / the system / a driver reset)',
}
THROTTLE = {0x1: 'idle', 0x2: 'app clocks', 0x4: 'SW power cap', 0x8: 'HW slowdown', 0x20: 'SW thermal slowdown',
            0x40: 'HW thermal slowdown', 0x80: 'HW power brake'}
THROTTLE_BAD = 0x4 | 0x8 | 0x20 | 0x40 | 0x80
SECRET_KEYS = ('online_password', 'online_npid', 'online_server', 'online_webapi', 'online_community', 'player_name')

# Launcher texts added by this fork (Simplified Chinese); other languages fall back to English.
ZH = {
    'Saves': '存档',
    'Backups of your saves: when the game starts, every 10 minutes of play when they changed, and when it exits. '
    'The newest 10 are kept.': '存档备份:游戏启动时、游玩中每 10 分钟(有变化时)、退出时各备份一次,保留最近 10 份。',
    'Backups': '备份列表',
    'Restore selected': '恢复所选',
    'Back up now': '立即备份',
    'Refresh': '刷新',
    'Open folder': '打开文件夹',
    'Restoring replaces the current saves with the chosen backup; the current ones are backed up first. '
    'Close the game before restoring.': '恢复会用所选备份覆盖当前存档,覆盖前会先把当前存档再备份一份。恢复前请先关闭游戏。',
    'while playing': '游戏中', 'at exit': '退出时', 'before a restore': '恢复前', 'manual': '手动',
    '(no backups yet: they appear once the game has run)': '(还没有备份:游戏运行过后会出现)',
    'Saves folder: {}': '存档位置:{}',
    'Backed up: {}': '已备份:{}',
    'Nothing to back up (no saves, or identical to the newest backup).': '没有可备份的内容(没有存档,或和最新备份完全相同)。',
    'Choose a backup in the list first.': '请先在列表里选一份备份。',
    'Close the game first: it would overwrite the restored saves.': '请先关闭游戏,否则它会覆盖恢复后的存档。',
    'Restore the saves from this backup?\n\n{}\n\nThe current saves are backed up first.':
        '用这份备份恢复存档?\n\n{}\n\n当前存档会先备份一份。',
    'Restore failed: {}': '恢复失败:{}',
    'Restored {} (the previous saves are in {}).': '已恢复 {}(之前的存档在 {} 里)。',
    'the newest backup': '最新的备份',
    'Record crash reports, GPU data and frame drops (recommended)': '记录崩溃报告、显卡数据和掉帧(推荐)',
    'Open logs folder': '打开日志文件夹',
    'Switch off the OBS capture hook for the game (recommended if OBS is installed)': '为游戏关闭 OBS 捕获钩子(装了 OBS 时建议开启)',
    'Collect crash history (72 h)': '收集崩溃历史(72 小时)',
    'Report saved:\n{}': '报告已保存:\n{}',
    'The game ended abnormally (exit code {}).\nThe crash report was saved and its folder is open:\n{}':
        '游戏异常退出(退出码 {})。\n已保存崩溃报告,所在文件夹已自动打开:\n{}',
    'Crash report saved: {}': '崩溃报告已保存:{}',
    'Chinese, Simplified (简体中文)': '简体中文',
    'Chinese, Traditional (繁體中文)': '繁體中文',
    'Korean (한국어)': '한국어',
    # About page
    'About': '关于',
    'Requirements, this build and the projects it builds on.': '配置要求、本构建的信息,以及它所基于的项目。',
    'Requirements': '配置要求',
    'Game': '游戏',
    'You need the game itself: a decrypted dump of Bloodborne for PS4 (CUSA03173, CUSA03023, CUSA00900 ...) '
    'with update 1.09 merged in. The community patches (60/90 FPS, uncapped) are for 1.09 only; other '
    'versions run at 30 FPS. No game files are included.':
        '需要游戏本体:PS4 版《血源诅咒》的已解密 dump(CUSA03173、CUSA03023、CUSA00900 等),并合并 1.09 更新。'
        '社区补丁(60/90 FPS、解锁帧率)只支持 1.09,其它版本以 30 FPS 运行。本程序不包含任何游戏文件。',
    'Windows': 'Windows',
    'Windows 10 (1903 or newer) or Windows 11, 64-bit. A current graphics driver with Vulkan 1.3.':
        'Windows 10(1903 或更新)或 Windows 11,64 位。支持 Vulkan 1.3 的最新显卡驱动。',
    'Graphics card': '显卡',
    'Minimum: Vulkan 1.3, 8 GB VRAM (GeForce GTX 1070 / Radeon RX 5700 class) for 1080p. '
    'Recommended: 12 GB or more for 1440p and 4K (RTX 3060 12 GB / RX 6700 XT and up). FSR 4 needs the INT8 '
    'shader features (RDNA2/3, RTX 20 and up); DLSS needs an RTX card. Integrated graphics are not enough.':
        '最低:Vulkan 1.3,8 GB 显存(GeForce GTX 1070 / Radeon RX 5700 级别),1080p。'
        '推荐:12 GB 以上,用于 1440p 和 4K(RTX 3060 12 GB / RX 6700 XT 及以上)。FSR 4 需要 INT8 着色器特性'
        '(RDNA2/3、RTX 20 及以上);DLSS 需要 RTX 显卡。核显不够用。',
    'Processor': '处理器',
    'x86-64 with AVX2 (Intel Core 8th gen / AMD Ryzen 2000 or newer), 6 cores or more recommended.':
        '支持 AVX2 的 x86-64(Intel 8 代酷睿 / AMD Ryzen 2000 或更新),推荐 6 核以上。',
    'Memory': '内存',
    '16 GB RAM minimum, 32 GB recommended. The port commits far more memory than a native game (the whole PS4 '
    'memory image plus GPU copies: 30 to 50 GB of commit charge at 1440p), so the Windows page file must be large: '
    'set it to a fixed size of at least 16 GB, 32 GB recommended (System > Advanced system settings > Performance > '
    'Virtual memory). With the automatic 4 GB page file the game ends after an hour with an access violation.':
        '最低 16 GB 内存,推荐 32 GB。这个移植层提交的内存远多于原生游戏(整个 PS4 内存镜像加上 GPU 副本,'
        '1440p 下提交量 30~50 GB),所以 Windows 页面文件必须调大:固定大小至少 16 GB,推荐 32 GB'
        '(系统 > 高级系统设置 > 性能 > 虚拟内存)。用默认的 4 GB 自动页面文件,游戏玩一小时左右会因访问冲突退出。',
    'Disk': '硬盘',
    'About 45 GB for the game dump, 1 GB for the port and up to 4 GB for the shader cache, on an NTFS drive '
    '(mods use links).':
        '游戏 dump 约 45 GB,移植程序 1 GB,着色器缓存最多 4 GB;需要 NTFS 分区(MOD 依赖链接)。',
    'Other': '其它',
    'Capture and overlay software (OBS, Bilibili Live, Discord overlay) hooks into the game process and adds '
    'memory; close it when the game is unstable. The game is tested on AMD Radeon RX 7900 XT and NVIDIA RTX cards.':
        '录屏和叠加层软件(OBS、哔哩哔哩直播姬、Discord 叠加层)会注入游戏进程并占用内存;游戏不稳定时请先关掉它们。'
        '本构建在 AMD Radeon RX 7900 XT 和 NVIDIA RTX 显卡上测试过。',
    'This build': '本构建',
    'Version': '版本',
    'based on upstream windows-v{}': '基于上游 windows-v{}',
    'Author': '作者',
    'License': '许可证',
    'GPL-2.0-or-later. The source code of this build, its changes and the full credits are on GitHub.':
        'GPL-2.0-or-later。本构建的源代码、改动记录和完整致谢都在 GitHub 上。',
    'Source code': '源代码',
    'Changes': '更新记录',
    'Built on the work of': '基于以下项目的工作',
    'The Windows port of bbport (windows-v1.6): runtime, launcher, DLSS, online co-op.':
        'bbport 的 Windows 移植(windows-v1.6):运行时、启动器、DLSS、联机合作。',
    'bbport, the native port itself; from its 0.5: the texture collector by idle time, background '
    'pipeline compilation, GPU breadcrumbs, the game-file check.':
        'bbport 原生移植本身;来自其 0.5 版:按闲置时间回收纹理、后台编译管线、GPU breadcrumbs、游戏文件检查。',
    'The AMD object-motion fix (shader varyings), the Intel 12th-gen attack crash fix, the live VRAM '
    'budget, crash reports with stack unwinding.':
        'AMD 的 object motion 修复(着色器 varying)、Intel 12 代被攻击时崩溃的修复、实时显存预算、带调用栈展开的崩溃报告。',
    'The Vulkan renderer and shader recompiler the GPU layer is derived from.':
        'GPU 层所源自的 Vulkan 渲染器和着色器重编译器。',
    'Also': '以及',
    'AMD FidelityFX (FSR 3.1 / FSR 4), FireBurn/FSR-Vulkan, NVIDIA DLSS, LibAtrac9, SDL3, FFmpeg, the authors of the '
    'community patches (Bloodborne.xml) and of the mods, and everyone who sent crash reports.':
        'AMD FidelityFX(FSR 3.1 / FSR 4)、FireBurn/FSR-Vulkan、NVIDIA DLSS、LibAtrac9、SDL3、FFmpeg、'
        '社区补丁(Bloodborne.xml)和 MOD 的作者们,以及每一位发来崩溃报告的人。',
}


PROBE_EXE = 'bb-probe.exe'   # the game process whose memory, threads and handles are recorded


def find_process(exe_name):
    """(pid, thread count) of the busiest process with this exe name, or None (Toolhelp snapshot)."""
    class Entry(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('usage', ctypes.c_ulong), ('pid', ctypes.c_ulong),
                    ('heap', ctypes.c_size_t), ('module', ctypes.c_ulong), ('threads', ctypes.c_ulong),
                    ('parent', ctypes.c_ulong), ('priority', ctypes.c_long), ('flags', ctypes.c_ulong),
                    ('exe', ctypes.c_wchar * 260)]
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    except (AttributeError, OSError):
        return None
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    best = None
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.exe.lower() == exe_name.lower() and (best is None or entry.threads > best[1]):
                best = (entry.pid, entry.threads)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return best


def process_stats(pid):
    """(private MB, working set MB, commit MB, handle count) of a process, or None. Independent of the
    Windows display language, unlike performance counters."""
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + \
                   [(name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged', 'paged', 'peak_nonpaged',
                                                         'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        handle = kernel32.OpenProcess(0x1000 | 0x0010, 0, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    except (AttributeError, OSError):
        return None
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        handles = ctypes.c_ulong(0)
        kernel32.GetProcessHandleCount(handle, ctypes.byref(handles))
        mb = 1 << 20
        return counters.private // mb, counters.ws // mb, counters.pagefile // mb, handles.value
    finally:
        kernel32.CloseHandle(handle)

# Windows primary language id -> the game's language id (PS4 system language numbers). The game has more
# languages than the launcher lists; those fall back to English.
GAME_LANGUAGES = {0x11: '0', 0x09: '1', 0x0c: '2', 0x0a: '3', 0x07: '4', 0x10: '5', 0x19: '8', 0x12: '9'}


def system_game_language(lang_id=None):
    """The game language that matches the Windows display language ('11' Simplified Chinese, '10' Traditional,
    '1' English when the game has no such language)."""
    if lang_id is None:
        try:
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        except (AttributeError, OSError):
            return '1'
    primary, sub = lang_id & 0x3ff, lang_id >> 10
    if primary == 0x04:  # Chinese: Traditional for Taiwan, Hong Kong and Macau, Simplified otherwise
        return '10' if sub in (0x01, 0x03, 0x05) else '11'
    return GAME_LANGUAGES.get(primary, '1')

def plus_key(text):
    """Sort key of a bbport-plus tag such as v1.6-plus.1 -> ((1, 6), 1); unknown tags sort first."""
    match = re.match(r'v?(\d+(?:\.\d+)*)-plus\.(\d+)', text or '')
    if not match:
        return ((0,), 0)
    return (tuple(int(n) for n in match.group(1).split('.')), int(match.group(2)))


def latest_plus_release():
    """(tag, release page) of the newest bbport-plus release on GitHub."""
    import urllib.request
    request = urllib.request.Request(f'https://api.github.com/repos/{PLUS_REPO}/releases/latest',
                                     headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'bbport-plus'})
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    return release['tag_name'], release.get('html_url') or f'https://github.com/{PLUS_REPO}/releases/latest'


def memory_status():
    """(available physical MB, available commit MB, commit limit MB) via GlobalMemoryStatusEx."""
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total_phys', ctypes.c_ulonglong),
                    ('avail_phys', ctypes.c_ulonglong), ('total_page', ctypes.c_ulonglong),
                    ('avail_page', ctypes.c_ulonglong), ('total_virt', ctypes.c_ulonglong),
                    ('avail_virt', ctypes.c_ulonglong), ('avail_ext', ctypes.c_ulonglong)]
    status = Status()
    status.length = ctypes.sizeof(Status)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return 0, 0, 0
    mb = 1 << 20
    return status.avail_phys // mb, status.avail_page // mb, status.total_page // mb


def describe_exit(code):
    name = EXIT_MEANING.get(code)
    if name is None and code >= 0x80000000:
        name = 'Windows exception / NTSTATUS'
    return name or 'unknown'


def decode_throttle(mask):
    names = [label for bit, label in THROTTLE.items() if mask & bit]
    unknown = mask & ~sum(THROTTLE)
    if unknown:
        names.append(f'other bits 0x{unknown:x}')
    return ', '.join(names) or 'none'


def run_tool(command, timeout=15):
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout.decode('utf-8', errors='replace')


def windows_events(log, query, keep=None, limit=12):
    """Entries of a Windows event log as one line each: time, provider, id and the data fields."""
    out = run_tool(['wevtutil', 'qe', log, f'/q:{query}', f'/c:{limit}', '/rd:true', '/f:xml'])
    if not out.strip():
        return []
    try:
        root = ET.fromstring('<Events>' + out + '</Events>')
    except ET.ParseError:
        return []
    lines = []
    for event in root:
        fields = {}
        data = []
        for node in event.iter():
            tag = node.tag.rsplit('}', 1)[-1]
            if tag == 'TimeCreated':
                fields['time'] = node.get('SystemTime', '')
            elif tag == 'Provider':
                fields['provider'] = node.get('Name', '')
            elif tag == 'EventID':
                fields['id'] = (node.text or '').strip()
            elif tag == 'Data' and node.text:
                data.append(node.text.strip())
        text = ' | '.join(data)
        if keep and not re.search(keep, text, re.I):
            continue
        lines.append(f'{fields.get("time", "?")}  {fields.get("provider", "?")}  id {fields.get("id", "?")}: {text}')
    return lines


GPU_PROVIDERS = ("Provider[@Name='nvlddmkm' or @Name='Display' or @Name='amdkmdag' or @Name='amdkmdap' "
                 "or @Name='amdwddmg' or @Name='igfx' or @Name='Microsoft-Windows-Dxgkrnl']")


def video_controllers():
    out = run_tool(['powershell', '-NoProfile', '-Command',
                    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' | driver ' + $_.DriverVersion "
                    "+ ' | ' + $_.VideoProcessor }"], 20)
    return [line.strip() for line in out.splitlines() if line.strip()]


def system_events_text(minutes=None, hours=None):
    """Event log sections about a crash: application errors of bb-probe.exe / Bloodborne.exe, graphics
    driver resets (TDR) and memory / power events, for the last `minutes` or `hours`."""
    span = f'{(minutes * 60000) if minutes else hours * 3600000}'
    window = f'TimeCreated[timediff(@SystemTime) <= {span}]'
    label = f'last {minutes} min' if minutes else f'last {hours} h'
    app = windows_events('Application', f'*[System[(EventID=1000 or EventID=1001 or EventID=1002) and {window}]]',
                         keep=r'bb-probe|Bloodborne', limit=40)
    system = windows_events('System', f'*[System[(EventID=4101 or EventID=4116 or EventID=2004 or EventID=41 '
                                      f'or EventID=6008) and {window}]]', limit=40)
    gpu = windows_events('System', f'*[System[{GPU_PROVIDERS} and {window}]]', limit=40)
    lines = ['', f'=== Windows Application log: errors of bb-probe.exe / Bloodborne.exe ({label}) ===']
    lines += app or ['(no entry: Windows did not record an application error)']
    lines += ['', f'=== Windows System log: driver reset 4101/4116, low memory 2004, power loss 41/6008 ({label}) ===']
    lines += system or ['(no entry)']
    lines += ['', f'=== Windows System log: graphics driver / Dxgkrnl messages ({label}) ===']
    lines += gpu or ['(no entry)']
    return lines


# Crash signatures with a known cause: (substrings that must all appear in the Windows event lines, hint).
KNOWN_SIGNATURES = (
    (('ucrtbase.dll', 'c0000005'),
     'known signature: an access violation inside the C runtime (memcpy) called by the game itself. Seen when a '
     'renderer per-frame heap of the game is full after about an hour of play (a GXWorker thread then copies into '
     'a null pointer). The patch "GX frame heap 4 MB" in patches/bbport-plus.xml prevents it; check that it is '
     'enabled on the Patches page. Details: docs/GX_FRAME_HEAP.md'),
)


def known_signature(event_lines):
    """The hint of the first known crash signature found in the Windows event lines, or None."""
    text = chr(10).join(event_lines)
    for needles, hint in KNOWN_SIGNATURES:
        if all(n in text for n in needles):
            return hint
    return None


def collect_history(hours, out_dir):
    """--diag-report: what Windows kept about crashes of the game in the last `hours` hours. Works for any
    graphics card and for runs that happened before the launcher wrote its own logs."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    avail_phys, avail_commit, limit = memory_status()
    lines = [f'Bloodborne diagnostic report  {datetime.now():%Y-%m-%d %H:%M:%S}  (last {hours} h)',
             f'Windows {platform.version()}, {os.cpu_count()} logical CPUs, free RAM {avail_phys} MB, '
             f'commit limit {limit} MB', '', '=== graphics adapters ===']
    lines += video_controllers() or ['(unknown)']
    lines += system_events_text(hours=hours)
    path = out_dir / f'diag_report_{datetime.now():%Y%m%d_%H%M%S}.txt'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


# ---- save backups ---------------------------------------------------------------------------
def save_signature(root):
    """((relative path, size, mtime_ns), ...) of every file under root, and the newest mtime."""
    items = []
    newest = 0
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            st = path.stat()
            items.append((path.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
            newest = max(newest, st.st_mtime_ns)
    return items, newest


def backup_saves(user_dir, reason, keep=KEEP_SAVE_BACKUPS, settle=SAVE_SETTLE_SECONDS):
    """Copies <user_dir>/savedata to <user_dir>/save_backups/<time>_<reason>/savedata unless it is
    identical to the newest backup. Returns the new folder, or None (nothing to do / not now)."""
    src = Path(user_dir) / 'savedata'
    if not src.is_dir():
        return None
    root = Path(user_dir) / 'save_backups'
    try:
        items, newest = save_signature(src)
        if not items:
            return None
        if time.time() - newest / 1e9 < settle:
            return None                      # being written right now: the next tick takes it
        existing = sorted(p for p in root.glob('*') if p.is_dir()) if root.is_dir() else []
        if existing:
            try:
                last = json.loads((existing[-1] / 'signature.json').read_text(encoding='utf-8'))
                if [list(i) for i in items] == last:
                    return None
            except (OSError, ValueError):
                pass
        dst = root / f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_{reason}'
        shutil.copytree(src, dst / 'savedata')
        after, _ = save_signature(dst / 'savedata')
        if [(a, b) for a, b, _ in after] != [(a, b) for a, b, _ in items]:
            shutil.rmtree(dst, ignore_errors=True)   # the game wrote while we copied: try again later
            return None
        (dst / 'signature.json').write_text(json.dumps([list(i) for i in items]), encoding='utf-8')
        existing = sorted(p for p in root.glob('*') if p.is_dir())
        for old in existing[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
        return dst
    except OSError:
        return None


# ---- thread snapshot of a hung game ---------------------------------------------------------
def thread_snapshot(pid, gap=1.5, stack_qwords=384, max_hits=20):
    """Text: every thread of `pid` with its name, CPU time spent in the last `gap` seconds, where it is
    (instruction pointer as module+offset) and the code addresses found on its stack. Threads are
    suspended one at a time for a moment. Windows only; returns an explanation when it cannot work."""
    import ctypes.wintypes as wt
    if os.name != 'nt':
        return 'thread snapshot: Windows only\n'
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    vp = ctypes.c_void_p
    k32.OpenProcess.restype = vp
    k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k32.OpenThread.restype = vp
    k32.OpenThread.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k32.CloseHandle.argtypes = (vp,)
    k32.SuspendThread.argtypes = (vp,)
    k32.ResumeThread.argtypes = (vp,)
    k32.GetThreadContext.argtypes = (vp, vp)
    k32.GetThreadTimes.argtypes = (vp, vp, vp, vp, vp)
    k32.ReadProcessMemory.argtypes = (vp, vp, vp, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    k32.VirtualQueryEx.argtypes = (vp, vp, vp, ctypes.c_size_t)
    k32.CreateToolhelp32Snapshot.restype = vp
    k32.CreateToolhelp32Snapshot.argtypes = (wt.DWORD, wt.DWORD)
    k32.Thread32First.argtypes = (vp, vp)
    k32.Thread32Next.argtypes = (vp, vp)
    k32.LocalFree.argtypes = (vp,)
    psapi.EnumProcessModulesEx.argtypes = (vp, vp, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD)
    psapi.GetModuleFileNameExW.argtypes = (vp, vp, ctypes.c_wchar_p, wt.DWORD)
    psapi.GetModuleInformation.argtypes = (vp, vp, vp, wt.DWORD)

    class ThreadEntry(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ThreadID', wt.DWORD),
                    ('th32OwnerProcessID', wt.DWORD), ('tpBasePri', ctypes.c_long),
                    ('tpDeltaPri', ctypes.c_long), ('dwFlags', wt.DWORD)]

    class ModuleInfo(ctypes.Structure):
        _fields_ = [('base', vp), ('size', wt.DWORD), ('entry', vp)]

    class MemInfo(ctypes.Structure):
        _fields_ = [('BaseAddress', vp), ('AllocationBase', vp), ('AllocationProtect', wt.DWORD),
                    ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD),
                    ('Protect', wt.DWORD), ('Type', wt.DWORD)]

    process = k32.OpenProcess(0x0010 | 0x0400 | 0x0008, False, pid)   # VM_READ | QUERY_INFORMATION | VM_OPERATION
    if not process:
        return f'thread snapshot: cannot open process {pid} (error {ctypes.get_last_error()})\n'
    out = []
    try:
        # modules
        mods = []
        need = wt.DWORD(0)
        arr = (vp * 1024)()
        if psapi.EnumProcessModulesEx(process, arr, ctypes.sizeof(arr), ctypes.byref(need), 3):
            for i in range(min(1024, need.value // ctypes.sizeof(vp))):
                info = ModuleInfo()
                if not psapi.GetModuleInformation(process, arr[i], ctypes.byref(info), ctypes.sizeof(info)):
                    continue
                name = ctypes.create_unicode_buffer(520)
                psapi.GetModuleFileNameExW(process, arr[i], name, 520)
                mods.append((info.base or 0, info.size, os.path.basename(name.value)))
        mods.sort()

        def where(addr):
            for base, size, name in mods:
                if base <= addr < base + size:
                    return f'{name}+0x{addr - base:x}'
            return None

        exec_cache = {}

        def executable(addr):
            key = addr >> 16
            if key not in exec_cache:
                mi = MemInfo()
                ok = k32.VirtualQueryEx(process, vp(addr), ctypes.byref(mi), ctypes.sizeof(mi))
                exec_cache[key] = bool(ok and mi.State == 0x1000 and mi.Protect & 0xF0 and
                                       not mi.Protect & 0x100)
            return exec_cache[key]

        # thread ids (never the calling thread: suspending it would hang the snapshot when pid is our own)
        me = k32.GetCurrentThreadId()
        tids = []
        snap = k32.CreateToolhelp32Snapshot(0x4, 0)
        entry = ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if snap and k32.Thread32First(snap, ctypes.byref(entry)):
            while True:
                if entry.th32OwnerProcessID == pid and entry.th32ThreadID != me:
                    tids.append(entry.th32ThreadID)
                if not k32.Thread32Next(snap, ctypes.byref(entry)):
                    break
        if snap:
            k32.CloseHandle(snap)

        def cpu_ms(handle):
            t = [ctypes.c_ulonglong(0) for _ in range(4)]
            k32.GetThreadTimes(handle, *(ctypes.byref(x) for x in t))
            return (t[2].value + t[3].value) / 10000.0      # kernel + user, 100 ns units

        handles = {}
        for tid in tids:
            h = k32.OpenThread(0x0002 | 0x0008 | 0x0040 | 0x0800 | 0x0020, False, tid)
            if h:
                handles[tid] = h
        before = {tid: cpu_ms(h) for tid, h in handles.items()}
        time.sleep(gap)
        out.append(f'{len(tids)} threads ({len(handles)} opened); modules: ' +
                   ', '.join(f'{n}@0x{b:x}' for b, _, n in mods[:6]))
        rows = []
        buf = (ctypes.c_ubyte * (1232 + 16))()
        aligned = (ctypes.addressof(buf) + 15) & ~15
        for tid, h in handles.items():
            busy = cpu_ms(h) - before[tid]
            desc = ctypes.c_void_p()
            name = ''
            if hasattr(k32, 'GetThreadDescription') and k32.GetThreadDescription(h, ctypes.byref(desc)) >= 0 and desc.value:
                name = ctypes.wstring_at(desc.value)
                k32.LocalFree(desc)
            rip = rsp = 0
            stack_hits = []
            suspended = k32.SuspendThread(h) != 0xFFFFFFFF
            try:
                if suspended:
                    ctypes.memset(aligned, 0, 1232)
                    ctypes.c_uint.from_address(aligned + 0x30).value = 0x100003   # CONTEXT_CONTROL | INTEGER
                    if k32.GetThreadContext(h, vp(aligned)):
                        rsp = ctypes.c_ulonglong.from_address(aligned + 0x98).value
                        rip = ctypes.c_ulonglong.from_address(aligned + 0xF8).value
                    if rsp:
                        raw = (ctypes.c_ubyte * (stack_qwords * 8))()
                        got = ctypes.c_size_t(0)
                        if k32.ReadProcessMemory(process, vp(rsp), raw, len(raw), ctypes.byref(got)):
                            for i in range(got.value // 8):
                                v = int.from_bytes(bytes(raw[i * 8:i * 8 + 8]), 'little')
                                if 0x10000 < v < 0x7FFFFFFFFFFF and (where(v) or executable(v)):
                                    stack_hits.append((i * 8, v))
            finally:
                if suspended:
                    k32.ResumeThread(h)
            rows.append((busy, tid, name, rip, rsp, stack_hits))
        for h in handles.values():
            k32.CloseHandle(h)
        rows.sort(key=lambda r: -r[0])
        for busy, tid, name, rip, rsp, hits in rows:
            loc = (where(rip) or ('guest/jit 0x%x' % rip if rip else 'unknown'))
            out.append(f'\nthread {tid} {name!r}: {busy:.0f} ms CPU in {gap:g} s, rip {loc} (0x{rip:x}), rsp 0x{rsp:x}')
            shown = 0
            for off, v in hits:
                if shown >= max_hits:
                    break
                out.append(f'    +0x{off:03x}  0x{v:x}  {where(v) or "executable memory"}')
                shown += 1
    finally:
        k32.CloseHandle(process)
    return '\n'.join(out) + '\n'


class RunRecorder:
    """Created for every game start; feed() gets each output line, finish() the exit code."""

    def __init__(self, log_dir, app_settings, ini_path, data_dir, version, diagnostics=True):
        self.log_dir = Path(log_dir)
        self.dir = self.log_dir / 'logs'
        self.diagnostics = diagnostics
        self.app_settings = dict(app_settings)
        self.ini_path = Path(ini_path)
        self.data_dir = Path(data_dir)
        self.version = version
        self.started = time.time()
        self.stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.user_stopped = False
        self.restarted = False
        self.closed = False
        self.tail = collections.deque(maxlen=400)
        self.samples = collections.deque(maxlen=150)   # GPU samples, newest last
        self.lock = threading.Lock()
        self.fps = []                                  # (t, fps, worst_ms, compiles, compile_ms)
        self.recent = collections.deque(maxlen=6)      # last window FPS values
        self.last_event = -1e9
        self.drops = 0
        self.stalls = 0
        self.worst_stall = 0.0
        self.pending_stalls = []                       # stall blocks since the last Frame stats line
        self.current_stall = None
        self.last_pacing = ''
        self.last_pressure = ''
        self.vram_max = 0
        self.throttled = False
        self.smi = None
        self.perf = None
        self.loads = collections.deque(maxlen=150)     # vendor-neutral GPU samples
        self.commit_low = None                         # (t, MB): lowest free commit memory seen
        self.commit_limit = 0
        self.last_commit_event = -1e9
        self.procs = collections.deque(maxlen=150)     # the game process: memory, threads, handles
        self.proc_peak = (0, 0, 0)                     # peak private MB, threads, handles
        self.files = {}
        self.crash_report = None
        self.last_window_time = time.time()
        self.hang_reports = 0
        self.last_hang = -1e9
        threading.Thread(target=self.save_backup_loop, daemon=True).start()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.log = open(self.dir / f'run_{self.stamp}.log', 'w', encoding='utf-8', buffering=1)
            self.last_run = open(self.log_dir / 'last_run.log', 'w', encoding='utf-8', buffering=1)
        except OSError:
            self.log = self.last_run = None
        self.write_header()
        if diagnostics:
            self.open_csv('fps', ['t_s', 'fps', 'worst_frame_ms', 'compiles', 'compile_ms'])
            self.stall_log = self.open_text('stalls')
            threading.Thread(target=self.sample_gpu, daemon=True).start()
            threading.Thread(target=self.sample_windows_counters, daemon=True).start()
            threading.Thread(target=self.watch_hang, daemon=True).start()
            threading.Thread(target=self.sample_process, daemon=True).start()
            threading.Thread(target=lambda: self.emit('Adapters: ' + ' / '.join(video_controllers()) + '\n'),
                             daemon=True).start()
        else:
            self.stall_log = None

    # ---- files -------------------------------------------------------------------------------
    def open_text(self, suffix):
        try:
            return open(self.dir / f'run_{self.stamp}_{suffix}.log', 'w', encoding='utf-8', buffering=1)
        except OSError:
            return None

    def open_csv(self, name, header):
        try:
            handle = open(self.dir / f'run_{self.stamp}_{name}.csv', 'w', encoding='utf-8', newline='')
        except OSError:
            return
        writer = csv.writer(handle)
        writer.writerow(header)
        handle.flush()
        self.files[name] = (handle, writer)

    def write_csv(self, name, row):
        entry = self.files.get(name)
        if entry and not self.closed:
            try:
                entry[1].writerow(row)
                entry[0].flush()
            except (OSError, ValueError):  # closed while a sampler thread was writing
                pass

    def uptime(self):
        return time.time() - self.started

    def emit(self, text):
        for handle in (self.log, self.last_run):
            if handle:
                try:
                    handle.write(text)
                except (OSError, ValueError):
                    pass

    def write_header(self):
        avail_phys, avail_commit, limit = memory_status()
        gpu = ''
        if shutil.which('nvidia-smi'):
            gpu = run_tool(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,power.limit',
                            '--format=csv,noheader'], 8).strip().replace('\r', '')
        self.emit(f'=== bbport launcher v{self.version} (bbport-plus {PLUS_VERSION}), run {self.stamp} ===\n'
                  f'Windows {platform.version()}, {os.cpu_count()} logical CPUs, free RAM {avail_phys} MB, '
                  f'free commit {avail_commit} of {limit} MB\n'
                  f'NVIDIA: {gpu or "nvidia-smi not found"}\n'
                  f'Diagnostics: {"on" if self.diagnostics else "off"} (logs folder: {self.dir})\n')

    # ---- game output -------------------------------------------------------------------------
    def feed(self, text):
        text = text.replace('\r\n', '\n')
        self.emit(text)
        with self.lock:
            self.tail.append(text.rstrip('\n'))
        if 'restarting through run.py' in text:
            self.restarted = True
        if not self.diagnostics:
            return
        try:
            self.parse(text)
        except Exception as error:  # a parsing slip must never stop the log
            self.emit(f'[diag] parse error: {error!r}\n')

    def parse(self, text):
        if self.current_stall is not None:
            if text.startswith('       '):
                self.current_stall.append(text.rstrip('\n'))
                return
            self.close_stall()
        match = STALL.search(text)
        if match:
            self.current_stall = [f'[{self.uptime():7.1f}s] ' + text.rstrip('\n')]
            self.stalls += 1
            self.worst_stall = max(self.worst_stall, float(match.group(1)))
            return
        match = MEMORY_PRESSURE.search(text)
        if match:
            self.last_pressure = f'{match.group(1)} of {match.group(2)} MiB'
            return
        match = FRAME_PACING.search(text)
        if match:
            self.last_pacing = text.strip()
            return
        match = FRAME_STATS.search(text)
        if match:
            self.on_window(float(match.group(1)), float(match.group(2)), int(match.group(3)),
                           int(match.group(4)), float(match.group(5)), text.strip())

    def close_stall(self):
        block = self.current_stall
        self.current_stall = None
        if not block:
            return
        self.pending_stalls.append(block)
        if self.stall_log:
            self.stall_log.write('\n'.join(block) + '\n')

    def on_window(self, fps, worst, vblank, compiles, compile_ms, line):
        self.close_stall()
        self.last_window_time = time.time()
        t = self.uptime()
        self.fps.append((t, fps, worst, compiles, compile_ms))
        self.write_csv('fps', [f'{t:.1f}', fps, worst, compiles, compile_ms])
        baseline = max(self.recent) if self.recent else 0.0
        stalls, self.pending_stalls = self.pending_stalls, []
        if fps < DROP_FPS and t - self.last_event >= EVENT_COOLDOWN and \
                (baseline >= STEADY_FPS or (len(self.recent) >= 3 and max(self.recent) < DROP_FPS and t - self.last_event >= 120)):
            self.record_drop(t, fps, baseline, line, stalls)
        self.recent.append(fps)

    def record_drop(self, t, fps, baseline, line, stalls):
        self.drops += 1
        self.last_event = t
        sample = self.samples[-1] if self.samples else None
        gpu = 'no GPU sample'
        if sample:
            gpu = (f'GPU {sample["util"]}% | VRAM {sample["vram"]}/{sample["vram_total"]} MB | {sample["temp"]} C | '
                   f'clock {sample["clk"]}/{sample["clk_max"]} MHz | {sample["power"]} W | '
                   f'throttle: {decode_throttle(sample["throttle"])}')
        if self.loads:
            load = self.loads[-1]
            gpu += (f'{" || " if sample else ""}adapter 3D {load["load"]:.0f}% | adapter VRAM {load["vram"]} MB | '
                    f'game VRAM {load["game_vram"]} MB')
        if self.procs:
            proc = self.procs[-1]
            gpu += (f'\ngame process: {proc["private"]} MB private, {proc["ws"]} MB in RAM, {proc["threads"]} threads, '
                    f'{proc["handles"]} handles')
        avail_phys, avail_commit, limit = memory_status()
        kind = 'drop' if baseline >= STEADY_FPS else 'low FPS (no steady 55+ before)'
        lines = [f'--- {datetime.now():%Y-%m-%d %H:%M:%S}  run {self.stamp}  +{t:.0f}s  {kind}: '
                 f'{fps:.1f} FPS after {baseline:.1f} ---',
                 f'recent windows: {", ".join(f"{v:.0f}" for v in self.recent)} -> {fps:.0f}',
                 line, self.last_pacing or 'no pacing line', gpu,
                 f'texture cache: {self.last_pressure or "no pressure reported"}',
                 f'RAM free {avail_phys} MB, commit free {avail_commit} of {limit} MB',
                 f'{len(stalls)} frames over 40 ms in this window:']
        for block in stalls[:8]:
            lines += ['  ' + row for row in block]
        lines.append('')
        self.write_event(lines)
        self.emit(f'[diag] FPS drop to {fps:.0f} recorded in logs\\perf_events.log\n')

    def write_event(self, lines):
        try:
            with open(self.dir / 'perf_events.log', 'a', encoding='utf-8') as handle:
                handle.write('\n'.join(lines) + '\n')
        except OSError:
            pass

    def note_commit(self, free_commit, limit, context):
        """Keeps the lowest free commit memory of the run and records it when it gets scarce: from
        there on allocations fail, which the game shows as an access violation (often in
        ucrtbase.dll) or a Vulkan error rather than an out-of-memory message."""
        if not limit:
            return
        t = self.uptime()
        self.commit_limit = limit
        if self.commit_low is None or free_commit < self.commit_low[1]:
            self.commit_low = (t, free_commit)
        if free_commit >= LOW_COMMIT_MB or t - self.last_commit_event < LOW_COMMIT_COOLDOWN:
            return
        self.last_commit_event = t
        self.write_event([f'--- {datetime.now():%Y-%m-%d %H:%M:%S}  run {self.stamp}  +{t:.0f}s  low commit memory: '
                          f'{free_commit} MB free of {limit} MB ---',
                          context, self.last_pacing or 'no pacing line',
                          'Windows is nearly out of commit memory (RAM + page file). Allocations fail from here, '
                          'which ends the game without an out-of-memory message. Enlarge the page file '
                          '(System > Advanced system settings > Performance > Virtual memory).', ''])
        self.emit(f'[diag] free commit memory down to {free_commit} MB, recorded in logs\\perf_events.log\n')

    # ---- GPU telemetry -----------------------------------------------------------------------
    def sample_gpu(self):
        exe = shutil.which('nvidia-smi')
        if not exe:
            return
        fields = 'utilization.gpu,memory.used,memory.total,temperature.gpu,clocks.sm,clocks.max.sm,power.draw,'
        for throttle in ('clocks_throttle_reasons.active', 'clocks_event_reasons.active'):
            command = [exe, f'--query-gpu={fields}{throttle}', '--format=csv,noheader,nounits', '-lms', '2000']
            try:
                self.smi = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            except OSError:
                return
            got = False
            self.open_csv('gpu', ['t_s', 'gpu_util_pct', 'vram_used_mb', 'vram_total_mb', 'temp_c', 'clock_sm_mhz',
                                  'clock_max_mhz', 'power_w', 'throttle_hex', 'throttle', 'free_ram_mb', 'free_commit_mb'])
            for raw in iter(self.smi.stdout.readline, b''):
                parts = [p.strip() for p in raw.decode('utf-8', errors='replace').split(',')]
                if len(parts) != 8:
                    continue
                got = True
                try:
                    mask = int(parts[7], 16)
                    util, vram, total, temp = (int(float(parts[i])) for i in range(4))
                    clk, clk_max = int(float(parts[4])), int(float(parts[5]))
                    power = float(parts[6]) if parts[6] not in ('[N/A]', '') else 0.0
                except ValueError:
                    continue
                free_ram, free_commit, limit = memory_status()
                sample = {'util': util, 'vram': vram, 'vram_total': total, 'temp': temp, 'clk': clk,
                          'clk_max': clk_max, 'power': power, 'throttle': mask, 't': self.uptime()}
                with self.lock:
                    self.samples.append(sample)
                self.vram_max = max(self.vram_max, vram)
                self.note_commit(free_commit, limit, f'GPU {util}% | VRAM {vram}/{total} MB | RAM free {free_ram} MB')
                if mask & THROTTLE_BAD and util > 60:
                    self.throttled = True
                self.write_csv('gpu', [f'{sample["t"]:.1f}', util, vram, total, temp, clk, clk_max, power,
                                       f'0x{mask:x}', decode_throttle(mask), free_ram, free_commit])
            if got:
                return

    def save_backup_loop(self):
        """Save snapshots: when the game starts, then every SAVE_BACKUP_INTERVAL s if they changed."""
        attempts, last = 0, time.time()
        while not self.closed:
            if attempts < 12:                         # at start: retry a few times (file being written)
                made = backup_saves(self.log_dir, 'play')
                attempts = 99 if made else attempts + 1
                last = time.time()
            elif time.time() - last >= SAVE_BACKUP_INTERVAL:
                made = backup_saves(self.log_dir, 'play')
                last = time.time()
            else:
                made = None
            if made:
                self.emit(f'[diag] save backup: {made}\n')
            time.sleep(5)

    def watch_hang(self):
        """The game prints a "Frame stats" window every 5 s while it renders. No window for HANG_SECONDS
        while the process lives (and it already rendered) is a hang: record what every thread does."""
        while not self.closed:
            time.sleep(5)
            if self.closed or not self.fps or self.hang_reports >= HANG_MAX_REPORTS:
                continue
            quiet = time.time() - self.last_window_time
            if quiet < HANG_SECONDS or time.time() - self.last_hang < 90:
                continue
            pid = self.find_game_pid()
            if not pid:
                continue
            self.last_hang = time.time()
            self.hang_reports += 1
            try:
                self.write_hang_report(pid, quiet)
            except Exception as error:   # a diagnostic must never hurt the game
                self.emit(f'[diag] hang report failed: {error!r}\n')

    def write_hang_report(self, pid, quiet):
        snapshot = thread_snapshot(pid)
        with self.lock:
            tail = list(self.tail)[-60:]
            gpu = list(self.loads)[-8:]
        free_ram, free_commit, limit = memory_status()
        path = self.dir / f'hang_{self.stamp}.txt'
        mode = 'a' if path.exists() else 'w'
        with open(path, mode, encoding='utf-8') as f:
            f.write(f'=== hang report {self.hang_reports}: no frame window for {quiet:.0f} s, run {self.stamp}, '
                    f'+{self.uptime():.0f} s, game pid {pid} ===\n')
            f.write(f'free RAM {free_ram} MB, free commit {free_commit} of {limit} MB\n')
            if gpu:
                f.write('recent GPU samples: ' + '; '.join(str(g) for g in gpu) + '\n')
            f.write('\n--- threads ---\n' + snapshot)
            f.write('\n--- last log lines ---\n' + '\n'.join(tail) + '\n\n')
        self.emit(f'[diag] the game stopped presenting frames {quiet:.0f} s ago: thread snapshot in {path}\n')

    def find_game_pid(self):
        out = run_tool(['tasklist', '/FI', 'IMAGENAME eq bb-probe.exe', '/FO', 'CSV', '/NH'], 8)
        match = re.search(r'"bb-probe\.exe","(\d+)"', out)
        return int(match.group(1)) if match else None

    def sample_windows_counters(self):
        """Any GPU vendor: Windows performance counters through typeperf (adapter VRAM, 3D engine
        load, the game's own VRAM). typeperf expands the (*) instances once, when it starts, so it
        is started again when the game process appears (its instances did not exist before)."""
        state = {'restarted_for': None, 'pid': None, 'checked': 0.0}
        self.open_csv('load', ['t_s', 'adapter_vram_mb', 'adapter_3d_pct', 'game_vram_mb', 'free_ram_mb', 'free_commit_mb'])
        while not self.closed and self.typeperf_once(state):
            pass

    def typeperf_once(self, state):
        """One typeperf process; True when it was stopped to be started again."""
        counters = [r'\GPU Adapter Memory(*)\Dedicated Usage', r'\GPU Engine(*engtype_3D)\Utilization Percentage',
                    r'\GPU Process Memory(*)\Dedicated Usage']
        try:
            self.perf = subprocess.Popen(['typeperf', *counters, '-si', '2'], stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except OSError:
            return False
        column = re.compile(r'\\(GPU [A-Za-z ]+)\(([^)]*)\)\\(.*)$')
        header = None
        for raw in iter(self.perf.stdout.readline, b''):
            row = [c.strip().strip('"') for c in raw.decode('utf-8', errors='replace').strip().split('","')]
            if len(row) < 2:
                continue
            if header is None:
                if not row[0].lstrip('"').startswith('(PDH'):
                    continue
                header = [column.search(name) for name in row]
                continue
            if not re.match(r'\d\d/\d\d/\d{4}', row[0].lstrip('"')) or len(row) != len(header):
                continue
            if state['pid'] is None and time.time() - state['checked'] > 8:
                state['checked'] = time.time()
                state['pid'] = self.find_game_pid()
            pid = state['pid']
            if pid and state['restarted_for'] != pid and \
                    not any(m and m.group(2).startswith(f'pid_{pid}_') for m in header[1:]):
                state['restarted_for'] = pid
                self.perf.terminate()
                return True
            vram, load, game = {}, {}, 0.0
            for match, value in zip(header[1:], row[1:]):
                if not match:
                    continue
                try:
                    number = float(value)
                except ValueError:
                    continue
                obj, instance = match.group(1), match.group(2)
                luid = re.search(r'luid_0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+', instance)
                luid = luid.group(0) if luid else instance
                if obj == 'GPU Adapter Memory':
                    vram[luid] = vram.get(luid, 0) + number
                elif obj == 'GPU Engine':
                    load[luid] = load.get(luid, 0) + number
                elif obj == 'GPU Process Memory' and pid and instance.startswith(f'pid_{pid}_'):
                    game += number
            adapter_vram = max(vram.values(), default=0) / (1 << 20)
            adapter_load = min(100.0, max(load.values(), default=0))
            free_ram, free_commit, limit = memory_status()
            sample = {'t': self.uptime(), 'vram': int(adapter_vram), 'load': adapter_load, 'game_vram': int(game / (1 << 20))}
            with self.lock:
                self.loads.append(sample)
            self.vram_max = max(self.vram_max, sample['vram'])
            self.note_commit(free_commit, limit, f'adapter 3D {adapter_load:.0f}% | adapter VRAM {sample["vram"]} MB | '
                                                 f'game VRAM {sample["game_vram"]} MB | RAM free {free_ram} MB')
            self.write_csv('load', [f'{sample["t"]:.1f}', sample['vram'], f'{adapter_load:.0f}', sample['game_vram'],
                                    free_ram, free_commit])
        return False

    def sample_process(self):
        """Every 2 s: private memory, working set, commit, threads and handles of the game process, so a leak
        (memory, threads or handles growing with every map change) shows in the numbers."""
        self.open_csv('proc', ['t_s', 'private_mb', 'working_set_mb', 'commit_mb', 'threads', 'handles'])
        while not self.closed:
            time.sleep(2)
            found = find_process(PROBE_EXE)
            stats = process_stats(found[0]) if found else None
            if not stats:
                continue
            private, working, commit, handles = stats
            sample = {'t': self.uptime(), 'private': private, 'ws': working, 'commit': commit,
                      'threads': found[1], 'handles': handles}
            with self.lock:
                self.procs.append(sample)
            peak = self.proc_peak
            self.proc_peak = (max(peak[0], private), max(peak[1], found[1]), max(peak[2], handles))
            self.write_csv('proc', [f'{sample["t"]:.1f}', private, working, commit, found[1], handles])

    # ---- end of the run ----------------------------------------------------------------------
    def settings_text(self):
        lines = []
        try:
            lines += ['[bbport.ini]'] + [l for l in self.ini_path.read_text(encoding='utf-8').splitlines() if '=' in l]
        except OSError:
            lines.append('[bbport.ini] unreadable')
        lines.append('[launcher settings]')
        lines += [f'{k} = {v}' for k, v in self.app_settings.items() if k not in SECRET_KEYS]
        try:
            mods = json.loads((self.data_dir / 'mods.json').read_text(encoding='utf-8'))
            lines.append(f'[mods] order: {", ".join(mods.get("order", []))}')
            lines.append(f'[mods] disabled: {", ".join(mods.get("disabled", []))}')
        except (OSError, ValueError):
            pass
        return '\n'.join(lines)

    def classify(self, code):
        if self.user_stopped:
            return 'stopped'
        if self.restarted:
            return 'restart'
        return 'ok' if code == 0 else 'crash'

    def finish(self, code):
        """Closes the files and returns the crash report path (None unless the run ended abnormally)."""
        self.close_stall()
        result = self.classify(code)
        duration = self.uptime()
        windows = [w[1] for w in self.fps]
        low = sum(1 for v in windows if v < DROP_FPS)
        average = sum(windows) / len(windows) if windows else 0.0
        summary = (f'=== run ended after {duration / 60:.1f} min, exit code {code} (0x{code & 0xFFFFFFFF:08X}: '
                   f'{describe_exit(code & 0xFFFFFFFF)}), result: {result} ===\n')
        if windows:
            summary += (f'FPS windows: {len(windows)}, average {average:.1f}, minimum {min(windows):.1f}, '
                        f'under {DROP_FPS:.0f}: {low}; drops recorded: {self.drops}; frames over 40 ms: {self.stalls} '
                        f'(worst {self.worst_stall:.0f} ms); peak VRAM {self.vram_max} MB\n')
        if self.commit_low:
            summary += self.commit_text() + '\n'
        if self.procs:
            first, last = self.procs[0], self.procs[-1]
            summary += (f'Game process: private memory {first["private"]} -> {last["private"]} MB '
                        f'(peak {self.proc_peak[0]} MB), threads {first["threads"]} -> {last["threads"]} '
                        f'(peak {self.proc_peak[1]}), handles {first["handles"]} -> {last["handles"]} '
                        f'(peak {self.proc_peak[2]})\n')
        self.emit(summary)
        if self.diagnostics and result == 'crash':
            self.crash_report = self.write_crash_report(code, duration)
            self.emit(f'[diag] crash report: {self.crash_report}\n')
        self.stop_sampler()
        self.closed = True
        made = backup_saves(self.log_dir, 'exit', settle=0.0)
        if made:
            try:
                self.log.write(f'[diag] save backup: {made}\n') if self.log else None
            except (OSError, ValueError):
                pass
        for handle in [self.log, self.last_run, self.stall_log] + [f[0] for f in self.files.values()]:
            try:
                if handle:
                    handle.close()
            except OSError:
                pass
        if self.diagnostics:
            self.append_session(result, code, duration, windows, average, low)
            self.prune()
        return self.crash_report

    def stop_sampler(self):
        for process in (self.smi, self.perf):
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def commit_text(self):
        t_low, low = self.commit_low
        text = f'Lowest free commit memory during the run: {low} MB of {self.commit_limit} MB (at +{t_low:.0f} s)'
        if low < LOW_COMMIT_MB:
            text += ' -- scarce: allocations can fail; enlarge the Windows page file'
        return text

    def write_crash_report(self, code, duration):
        unsigned = code & 0xFFFFFFFF
        with self.lock:
            tail = list(self.tail)
            samples = list(self.samples)[-30:]
        lines = [f'Bloodborne crash report  {datetime.now():%Y-%m-%d %H:%M:%S}',
                 f'launcher v{self.version}, run {self.stamp}, game ran {duration / 60:.1f} min',
                 f'exit code {unsigned} (0x{unsigned:08X}): {describe_exit(unsigned)}', '',
                 '=== fault lines from the game output ===']
        faults = [row for row in tail if FAULT_LINE.match(row)]
        lines += faults[-40:] or ['(none: the process ended without a fault report, e.g. killed by the system, '
                                  'a driver reset or a crash inside a system DLL)']
        if self.fps:
            last = self.fps[-3:]
            lines += ['', '=== last FPS windows (seconds into the run, fps, worst frame ms, compiles, compile ms) ===']
            lines += [f'{t:7.1f}  {fps:6.1f}  {worst:7.1f}  {n:3d}  {ms:7.1f}' for t, fps, worst, n, ms in last]
        if samples:
            lines += ['', '=== last GPU samples (seconds, GPU %, VRAM MB, temp, clock, power, throttle) ===']
            lines += [f'{s["t"]:7.1f}  {s["util"]:3d}%  {s["vram"]:5d}/{s["vram_total"]}  {s["temp"]}C  '
                      f'{s["clk"]}/{s["clk_max"]}MHz  {s["power"]:.0f}W  {decode_throttle(s["throttle"])}'
                      for s in samples]
        with self.lock:
            loads = list(self.loads)[-15:]
            procs = list(self.procs)[-40:]
        if procs:
            lines += ['', '=== game process, last samples (seconds, private MB, in RAM MB, commit MB, threads, handles) ===']
            lines += [f'{s["t"]:7.1f}  {s["private"]:6d}  {s["ws"]:6d}  {s["commit"]:6d}  {s["threads"]:5d}  {s["handles"]:6d}'
                      for s in procs]
        if loads:
            lines += ['', '=== last adapter samples (seconds, adapter 3D %, adapter VRAM MB, game VRAM MB) ===']
            lines += [f'{s["t"]:7.1f}  {s["load"]:3.0f}%  {s["vram"]:6d}  {s["game_vram"]:6d}' for s in loads]
        avail_phys, avail_commit, limit = memory_status()
        lines += ['', f'=== memory now: free RAM {avail_phys} MB, free commit {avail_commit} of {limit} MB ===']
        if self.commit_low:
            lines.append(self.commit_text())
        lines += ['', '=== graphics adapters ===']
        lines += video_controllers() or ['(unknown)']
        events = system_events_text(15)
        lines += events
        hint = known_signature(events)
        if hint:
            lines += ['', '=== ' + hint + ' ===']
        lines += ['', '=== settings ===', self.settings_text(), '',
                  f'=== last {len(tail)} output lines ===']
        lines += tail
        path = self.dir / f'crash_{self.stamp}.txt'
        try:
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        except OSError:
            return None
        return path

    def append_session(self, result, code, duration, windows, average, low):
        path = self.dir / 'sessions.csv'
        new = not path.exists()
        try:
            with open(path, 'a', encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle)
                if new:
                    writer.writerow(['start', 'minutes', 'result', 'exit_code', 'avg_fps', 'min_fps', 'windows',
                                     'windows_under_40', 'drops', 'frames_over_40ms', 'worst_stall_ms',
                                     'peak_vram_mb', 'gpu_throttled'])
                writer.writerow([datetime.fromtimestamp(self.started).strftime('%Y-%m-%d %H:%M:%S'),
                                 f'{duration / 60:.1f}', result, code & 0xFFFFFFFF, f'{average:.1f}',
                                 f'{min(windows):.1f}' if windows else '', len(windows), low, self.drops,
                                 self.stalls, f'{self.worst_stall:.0f}', self.vram_max, int(self.throttled)])
        except OSError:
            pass

    def prune(self):
        """Keeps the newest KEEP_CRASHES crashes complete (report + log + csv files of that run) and
        the newest KEEP_RUNS other runs; everything older is deleted. A crash is never pushed out
        by later runs that went well."""
        try:
            crashes = sorted(self.dir.glob('crash_*.txt'), key=lambda p: p.name)
            for old in crashes[:-KEEP_CRASHES]:
                self.delete_run('run_' + old.name[len('crash_'):-len('.txt')])
                old.unlink(missing_ok=True)
            hangs = sorted(self.dir.glob('hang_*.txt'), key=lambda p: p.name)
            for old in hangs[:-KEEP_CRASHES]:
                self.delete_run('run_' + old.name[len('hang_'):-len('.txt')])
                old.unlink(missing_ok=True)
            kept = {'run_' + p.name[len('crash_'):-len('.txt')] for p in crashes[-KEEP_CRASHES:]}
            kept |= {'run_' + p.name[len('hang_'):-len('.txt')] for p in hangs[-KEEP_CRASHES:]}
            for old in sorted(self.dir.glob('fault_*.dmp'), key=lambda p: p.name)[:-3]:
                old.unlink(missing_ok=True)       # minidumps written by bb-probe's fault handler
            runs = sorted(self.dir.glob('run_*.log'), key=lambda p: p.name)
            runs = [p for p in runs if not p.name.endswith('_stalls.log') and p.name[:-len('.log')] not in kept]
            for old in runs[:-KEEP_RUNS]:
                self.delete_run(old.name[:-len('.log')])
        except OSError:
            pass

    def delete_run(self, stem):
        """run_<stamp>.log and the csv / stalls files of that run."""
        for extra in self.dir.glob(stem + '*'):
            extra.unlink(missing_ok=True)
