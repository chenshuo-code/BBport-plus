"""The run recorder of the Windows launcher: fault lines, exit code texts, the low-commit record and
what ends up in a crash report (no game, no GPU: the samplers find nothing to sample)."""
from paths import ROOT
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(ROOT / 'launcher'))
import bbport_diag as diag  # noqa: E402


class FaultLineTests(unittest.TestCase):
    def test_gpu_assertion_details_are_fault_lines(self):
        for line in ('GPU [Debug] <Critical> vk_swapchain.cpp:75 Create: Assertion Failed!',
                     'Failed to create swapchain: ErrorNativeWindowInUseKHR',
                     'vk::Result=ErrorUnknown',
                     'GPU hang: tick 5 not reached after 30 s (GPU at 4)',
                     'Queue submit failed: ErrorOutOfHostMemory',
                     'Swapchain presentation failed: ErrorDeviceLost',
                     'STOP: GPU library assertion failed (see GPU log above)'):
            self.assertTrue(diag.FAULT_LINE.match(line), line)

    def test_ordinary_lines_are_not(self):
        for line in ('Frame stats: 60.0 FPS, worst frame 18.3 ms (vblank 480 Hz); 0 shader/pipeline compiles',
                     'Runtime: open(/app0/dvdroot_ps4/map/m24/m24_9999.tpf.dcx) -> not found',
                     'GPU: window and Vulkan presenter ready; SDK 0x02000071, 1726 HLE symbols'):
            self.assertIsNone(diag.FAULT_LINE.match(line), line)

    def test_exit_codes(self):
        self.assertIn('GPU library assertion', diag.describe_exit(23))
        self.assertIn('not responding', diag.describe_exit(0xCFFFFFFF))
        self.assertEqual(diag.describe_exit(0xC0000005), 'access violation')


class RecorderTests(unittest.TestCase):
    def make(self, base, diagnostics=True):
        ini = base / 'bbport.ini'
        ini.write_text('upscaler=fsr4\n', encoding='utf-8')
        (base / 'mods.json').write_text('{"order": [], "disabled": []}', encoding='utf-8')
        return diag.RunRecorder(base, {'fullscreen': 'True', 'online_password': 'secret'}, ini, base, '1.6',
                                diagnostics=diagnostics)

    def test_low_commit_is_recorded_once_per_cooldown_and_reported(self):
        # ignore_cleanup_errors: the typeperf sampler thread may still hold a csv file at the end.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            rec = self.make(base)
            rec.feed('GPU [Debug] <Critical> vk_swapchain.cpp:75 Create: Assertion Failed!\n')
            rec.feed('Failed to create swapchain: ErrorNativeWindowInUseKHR\n')
            rec.note_commit(5000, 37256, 'ctx a')
            rec.note_commit(1021, 37256, 'ctx b')   # under the threshold: one record
            rec.note_commit(900, 37256, 'ctx c')    # inside the cooldown: no record, the minimum moves
            self.assertEqual(rec.commit_low[1], 900)
            report = rec.finish(23)
            text = report.read_text(encoding='utf-8')
            self.assertIn('exit code 23 (0x00000017): STOP: GPU library assertion', text)
            self.assertIn('Failed to create swapchain: ErrorNativeWindowInUseKHR', text.split('=== memory now')[0])
            self.assertIn('Lowest free commit memory during the run: 900 MB of 37256 MB', text)
            self.assertIn('scarce', text)
            self.assertNotIn('secret', text)
            events = (base / 'logs' / 'perf_events.log').read_text(encoding='utf-8')
            self.assertEqual(events.count('low commit memory'), 1)
            self.assertIn('1021 MB free of 37256 MB', events)
            self.assertIn('ctx b', events)
            run_log = next((base / 'logs').glob('run_*.log')).read_text(encoding='utf-8')
            self.assertIn('Lowest free commit memory', run_log)
            self.assertIn('[diag] free commit memory down to 1021 MB', run_log)

    def test_prune_keeps_crashes_complete_and_last_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            logs = base / 'logs'
            logs.mkdir()
            # 14 crashes (older than every ok run) and 15 ok runs, oldest first.
            for i in range(14):
                stamp = f'20260101_{i:06d}'
                for suffix in ('.log', '_fps.csv', '_load.csv', '_proc.csv', '_stalls.log'):
                    (logs / f'run_{stamp}{suffix}').write_text('x')
                (logs / f'crash_{stamp}.txt').write_text('x')
            for i in range(15):
                stamp = f'20260102_{i:06d}'
                (logs / f'run_{stamp}.log').write_text('x')
                (logs / f'run_{stamp}_fps.csv').write_text('x')
            rec = self.make(base, diagnostics=False)   # no sampler threads; its own run log still exists
            rec.prune()
            rec.finish(0)
            names = {p.name for p in logs.iterdir()}
            crashes = sorted(n for n in names if n.startswith('crash_'))
            self.assertEqual(len(crashes), diag.KEEP_CRASHES)
            self.assertEqual(crashes[0], 'crash_20260101_000004.txt')
            for stamp in ('20260101_000004', '20260101_000013'):   # kept crashes keep all their files
                for suffix in ('.log', '_fps.csv', '_load.csv', '_proc.csv', '_stalls.log'):
                    self.assertIn(f'run_{stamp}{suffix}', names)
            self.assertNotIn('run_20260101_000003.log', names)
            self.assertNotIn('run_20260101_000003_fps.csv', names)
            ok_runs = sorted(n for n in names if n.startswith('run_20260102_') and n.endswith('.log'))
            self.assertEqual(len(ok_runs), diag.KEEP_RUNS - 1)      # plus this test's own new run
            self.assertEqual(ok_runs[0], 'run_20260102_000006.log')
            self.assertNotIn('run_20260102_000005_fps.csv', names)

    def test_no_commit_line_without_samples(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            rec = self.make(base)
            rec.note_commit(0, 0, 'no memory status')   # memory_status() unavailable: ignored
            self.assertIsNone(rec.commit_low)
            report = rec.finish(0xC0000005)
            self.assertNotIn('Lowest free commit', report.read_text(encoding='utf-8'))


class SaveBackupTests(unittest.TestCase):
    def make_saves(self, base):
        slot = base / 'savedata' / '1' / 'CUSA01363' / 'SPRJ0005'
        slot.mkdir(parents=True)
        (slot / 'userdata0000').write_bytes(b'A' * 100)
        (slot / 'backup0000').write_bytes(b'A' * 100)
        return slot

    def test_backup_copies_then_skips_unchanged_then_copies_changes(self):
        import os
        import time
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            slot = self.make_saves(base)
            old = time.time() - 60
            for f in slot.iterdir():
                os.utime(f, (old, old))
            first = diag.backup_saves(base, 'play')
            self.assertIsNotNone(first)
            self.assertEqual((first / 'savedata' / '1' / 'CUSA01363' / 'SPRJ0005' / 'userdata0000').read_bytes(),
                             b'A' * 100)
            self.assertIsNone(diag.backup_saves(base, 'play'))          # identical to the newest backup
            (slot / 'userdata0000').write_bytes(b'B' * 100)
            os.utime(slot / 'userdata0000', (old + 5, old + 5))
            time.sleep(1.1)                                             # backup folders are named by the second
            second = diag.backup_saves(base, 'exit')
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual((second / 'savedata' / '1' / 'CUSA01363' / 'SPRJ0005' / 'userdata0000').read_bytes(),
                             b'B' * 100)
            self.assertEqual((first / 'savedata' / '1' / 'CUSA01363' / 'SPRJ0005' / 'userdata0000').read_bytes(),
                             b'A' * 100)                                 # the older one is untouched

    def test_file_written_a_moment_ago_is_not_copied_half_written(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            self.make_saves(base)                                       # mtime = now
            self.assertIsNone(diag.backup_saves(base, 'play', settle=30.0))
            self.assertIsNotNone(diag.backup_saves(base, 'exit', settle=0.0))

    def test_only_the_newest_are_kept(self):
        import os
        import time
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            slot = self.make_saves(base)
            for i in range(5):
                f = slot / 'userdata0000'
                f.write_bytes(bytes([65 + i]) * 100)
                for g in slot.iterdir():                     # the other file was created just now too
                    os.utime(g, (time.time() - 100 + i, time.time() - 100 + i))
                self.assertIsNotNone(diag.backup_saves(base, 'play', keep=3))
                time.sleep(1.05)
            kept = sorted(p.name for p in (base / 'save_backups').iterdir())
            self.assertEqual(len(kept), 3)

    def test_no_savedata_folder_is_fine(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self.assertIsNone(diag.backup_saves(tmp, 'play'))


@unittest.skipUnless(sys.platform == 'win32', 'thread snapshots use the Windows API')
class HangSnapshotTests(unittest.TestCase):
    def test_snapshot_of_a_live_process_lists_threads_and_code_addresses(self):
        import subprocess
        import time
        child = subprocess.Popen([sys.executable, '-c', 'import threading,time\n'
                                  'threading.Thread(target=time.sleep,args=(60,),daemon=True).start()\n'
                                  'time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.0)
            text = diag.thread_snapshot(child.pid, gap=0.2)
            self.assertIsNone(child.poll())                              # resumed and still alive
        finally:
            child.kill()
            child.wait()
        self.assertIn('threads (', text)
        self.assertRegex(text, r'thread \d+ ')
        self.assertRegex(text, r'rip [\w.]+\.(exe|dll|pyd)\+0x[0-9a-f]+')

    def test_snapshot_of_a_missing_process_explains(self):
        self.assertIn('cannot open process', diag.thread_snapshot(0x7FFFFFF0, gap=0.0))


class KnownSignatureTests(unittest.TestCase):
    def test_memcpy_in_ucrtbase_names_the_frame_heap_patch(self):
        lines = ['=== Windows Application log ===',
                 '2026-10-12T19:21:36Z  Application Error  id 1000: bb-probe.exe | 0.0.0.0 | 6acbb78c | ucrtbase.dll '
                 '| 10.0.26100.9444 | 2bbefd73 | c0000005 | 00000000000ee4b3 | 0x11b8']
        self.assertIn('GX frame heap 4 MB', diag.known_signature(lines))

    def test_other_faults_get_no_hint(self):
        self.assertIsNone(diag.known_signature(['amdvlk64.dll | c0000005 | 0x1234']))
        self.assertIsNone(diag.known_signature([]))


if __name__ == '__main__':
    unittest.main()
