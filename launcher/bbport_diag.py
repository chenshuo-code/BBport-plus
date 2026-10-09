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

PLUS_VERSION = '1.6-plus.3'   # bbport-plus release; the part before '-plus' is the upstream version it is based on
PLUS_REPO = 'chenshuo-code/BBport-plus'
KEEP_RUNS = 40          # run logs kept (older ones are deleted with their csv files)
KEEP_CRASHES = 200
DROP_FPS = 40.0         # a window below this ...
STEADY_FPS = 55.0       # ... after a window at or above this is a drop
EVENT_COOLDOWN = 20.0   # seconds between two drop records
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

FRAME_STATS = re.compile(r'Frame stats: ([\d.]+) FPS, worst frame ([\d.]+) ms \(vblank (\d+) Hz\); '
                         r'(\d+) shader/pipeline compiles, ([\d.]+) ms')
FRAME_PACING = re.compile(r'Frame pacing: median ([\d.]+) ms, stddev ([\d.]+) ms, p99 ([\d.]+) ms, (\d+) frames')
STALL = re.compile(r'Stall: ([\d.]+) ms frame; GPU thread on CPU ([\d.]+) ms; (\d+) images registered, '
                   r'([\d.]+) MB image uploads, ([\d.]+) MB buffer uploads')
MEMORY_PRESSURE = re.compile(r'Texture cache: memory pressure, (\d+) of (\d+) MiB')
FAULT_LINE = re.compile(r'^(Fault:|Host fault|Guest fault|STOP:|  #\d+ |  rax=|  rsi=|API:|Caller |.*[Aa]ssert|'
                        r'.*terminate called|.*VK_ERROR|.*device lost|.*DEVICE_LOST|.*out of (device )?memory|'
                        r'.*unhandled|.*Unhandled)')

# Exit codes: the runtime's own (probe.c / runtime_*.c) and the usual Windows exception codes.
EXIT_MEANING = {
    0: 'normal exit',
    1: 'stopped (Stop button) or generic failure',
    3: 'runtime fault report ("Fault:" lines) or abort()',
    20: 'STOP: unsupported PS4 import',
    21: 'STOP: unsupported runtime feature',
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
    0xFFFFFFFF: 'ended from outside with no fault report (Task Manager / taskkill / the system / a driver reset)',
}
THROTTLE = {0x1: 'idle', 0x2: 'app clocks', 0x4: 'SW power cap', 0x8: 'HW slowdown', 0x20: 'SW thermal slowdown',
            0x40: 'HW thermal slowdown', 0x80: 'HW power brake'}
THROTTLE_BAD = 0x4 | 0x8 | 0x20 | 0x40 | 0x80
SECRET_KEYS = ('online_password', 'online_npid', 'online_server', 'online_webapi', 'online_community', 'player_name')

# Launcher texts added by this fork (Simplified Chinese); other languages fall back to English.
ZH = {
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
        self.procs = collections.deque(maxlen=150)     # the game process: memory, threads, handles
        self.proc_peak = (0, 0, 0)                     # peak private MB, threads, handles
        self.files = {}
        self.crash_report = None
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
        try:
            with open(self.dir / 'perf_events.log', 'a', encoding='utf-8') as handle:
                handle.write('\n'.join(lines) + '\n')
        except OSError:
            pass
        self.emit(f'[diag] FPS drop to {fps:.0f} recorded in logs\\perf_events.log\n')

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
                free_ram, free_commit, _limit = memory_status()
                sample = {'util': util, 'vram': vram, 'vram_total': total, 'temp': temp, 'clk': clk,
                          'clk_max': clk_max, 'power': power, 'throttle': mask, 't': self.uptime()}
                with self.lock:
                    self.samples.append(sample)
                self.vram_max = max(self.vram_max, vram)
                if mask & THROTTLE_BAD and util > 60:
                    self.throttled = True
                self.write_csv('gpu', [f'{sample["t"]:.1f}', util, vram, total, temp, clk, clk_max, power,
                                       f'0x{mask:x}', decode_throttle(mask), free_ram, free_commit])
            if got:
                return

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
            free_ram, free_commit, _limit = memory_status()
            sample = {'t': self.uptime(), 'vram': int(adapter_vram), 'load': adapter_load, 'game_vram': int(game / (1 << 20))}
            with self.lock:
                self.loads.append(sample)
            self.vram_max = max(self.vram_max, sample['vram'])
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
        lines += ['', f'=== memory now: free RAM {avail_phys} MB, free commit {avail_commit} of {limit} MB ===',
                  '', '=== graphics adapters ===']
        lines += video_controllers() or ['(unknown)']
        lines += system_events_text(15)
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
        try:
            runs = sorted(self.dir.glob('run_*.log'), key=lambda p: p.name)
            runs = [p for p in runs if not p.name.endswith('_stalls.log')]
            for old in runs[:-KEEP_RUNS]:
                stem = old.name[:-len('.log')]
                for extra in self.dir.glob(stem + '*'):
                    extra.unlink(missing_ok=True)
            crashes = sorted(self.dir.glob('crash_*.txt'), key=lambda p: p.name)
            for old in crashes[:-KEEP_CRASHES]:
                old.unlink(missing_ok=True)
        except OSError:
            pass
