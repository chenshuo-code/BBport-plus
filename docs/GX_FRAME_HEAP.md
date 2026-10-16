# The crash after about an hour: GX frame heap exhaustion

Every long session on Windows ended the same way (2026-10-08 .. 2026-10-12, five crashes at 68-79
minutes; no run under 61 minutes crashed): `bb-probe.exe` stops with an access violation in
`ucrtbase.dll+0xee4b3` (that is `memcpy`), Windows Error Reporting takes a minidump for a minute
or two (the game window freezes on its last frame meanwhile, which looks like a hang), and the
launcher then records exit code 0xFFFFFFFF or -1 ("ended from outside").

## What the dumps show

`%LOCALAPPDATA%\CrashDumps\bb-probe.exe.<pid>.dmp` (Windows keeps them automatically):

- faulting thread: one of the game's `GXWorker:N` threads;
- `memcpy(dst = NULL, src = <guest>, 0x398)` reached through the runtime's `guest_memcpy`
  (`src/runtime.c`), with the fault at a 0x20 write;
- the guest return addresses on the stack (eboot mapped at 0x800000000) lead to this game code
  (`eboot+0x21d5ad9`, a command handler that runs on the GXWorker threads):

```
r14 = gx->frame_slot[gx->frame_index]        ; two slots, alternating per frame
lock(r14+0xc8)
r13 = paged_alloc(&r14->allocator, 0x4d0, 16) ; eboot+0xe45560, pages of 0x800 bytes
unlock
memcpy(r13, src, 0x398)                      ; r13 is never checked
```

`paged_alloc` takes a new 2 KB page from the slot's heap (vtable `eboot+0x52e6fd0`, a plain
bump allocator over a 256 KB arena, created at `eboot+0x2195bd6..0x2195c4b` with
`mov edx, 0x40000`) and returns NULL when the heap returns NULL. The heap is reset every frame;
its *per-frame peak* is what grows.

## Measured in a live run (modtools/gxmon)

`gxmon.py` reads the two heaps' cursors ~500 times a second and logs the per-5-s peak:
usage starts at ~34 KB per frame and climbs steadily (3-6 KB per minute while playing, in steps
of a few hundred bytes), with no drop between areas. At that rate the 256 KB arena is full after
about 45-75 minutes, which matches every crash time. The 0x4d0 allocations themselves stay at
3 pages / 5 per frame; the growth is in other per-frame payload (float/matrix data, no vtables).

## The fix shipped: `patches/bbport-plus.xml`

"GX frame heap 4 MB": both `mov edx, 0x40000` become `0x400000` (PS4 addresses 0x2595c0a and
0x2595c47, 4 bytes each). The arenas come from the renderer's main heap (276 MB, mostly free at
that point), so 8 MB more is safe. 16 times the room moves the exhaustion past ~12 hours of play.
The launcher lists the patch on its Patches page, enabled by default.

The root cause of the growth (which per-frame jobs accumulate, and why they do not on a PS4) is
still open; the monitor and the arena snapshots (`gxsnap.py`, `gxdiff.py`) are the tools for it.
A run with the patch should show `heap_size` = 4194304 in the monitor's CSV.

## Related

The 10-11 crash at 78.7 min (exit 23, "PM4 invalid header", fixed to resync by
`liverpool.cpp`) happened in the same time window and may be another face of the same
exhaustion: a job that got a null buffer can leave a cut-short packet in the command stream.

## Finding the source of the growth: `src/runtime_gxtrace.c`

bb-probe replaces the frame-heap class's `allocate` vtable entry (eboot+0x52e6fd0+0x58) with a
recording function after the image is relocated and patched (`BB_GX_TRACE=0` disables it; the
slot is verified against the expected function first, so another game version is left alone).
Every 60 s the run log gets a `GX heap trace` block: calls and bytes per second per guest call
site, and the peak fill of every heap of that class. Calls arrive through the game's paged
allocator (eboot+0xe45560, 2 KB pages), so the site reported is that allocator's caller, read
from its frame (7 pushes above the canonical frame address).

First live run (2026-10-15, with the 4 MB patch): the class has a third, larger instance
(peaks around 2 MB, ~5000 pages/s) besides the two slot heaps; the slot heaps started at 11 KB
and grew again at the same few KB per minute.
