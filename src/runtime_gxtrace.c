/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Who allocates from the game's per-frame renderer heaps (docs/GX_FRAME_HEAP.md).
 *
 * The heaps are C++ objects; their `allocate(this, size, align)` is the vtable entry at
 * eboot+0x52e6fd0+0x58. After the image is relocated and patched, that slot is replaced by `hook`,
 * which records the guest call site, the size and whether the allocation failed, then calls the
 * original. The hook itself only updates a table under a spinlock (it runs on the game's GXWorker
 * threads, on their stacks); a host thread prints the report every REPORT_SECONDS to the game
 * output (the run log): calls and bytes per second for each site, and the peak fill of each heap.
 * A site whose bytes per second grow over the minutes is the one that accumulates.
 * BB_GX_TRACE=0 leaves the vtable alone. Bloodborne 1.09 only: the slot is checked first. */
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif
#include "runtime.h"

#define VTABLE_SLOT UINT64_C(0x52e7028)   /* frame-heap vtable (eboot+0x52e6fd0) + 0x58 */
#define ALLOCATE_FN UINT64_C(0xe453b0)    /* the game's allocate: lock, bump-allocate, unlock */
#define PAGED_SITE UINT64_C(0xe455c2)     /* return address of the paged allocator's call into the heap */
#define TEXT_BYTES UINT64_C(0x50d96dc)    /* the eboot's code segment: a return address lies below this */
#define HEAP_START 0x08                   /* heap object: arena start */
#define HEAP_CURSOR 0x18                  /* heap object: bump cursor */
#define SITES 96
#define HEAPS 4
#define REPORT_SECONDS 60

typedef void *(ABI *AllocateFn)(void *self, uint64_t size, uint64_t align);
typedef struct { uint64_t site, calls, bytes, nulls; } Site;
typedef struct { void *heap; uint64_t peak; } Heap;

static AllocateFn original;
static unsigned char *base;
static atomic_flag lock = ATOMIC_FLAG_INIT;
static Site sites[SITES];
static size_t nsites;
static Heap heaps[HEAPS];
static uint64_t calls_total, dropped, failures;
static time_t started;

/* True when the bytes before `p` are a call instruction (E8 rel32, FF /2 forms, with or without
 * a REX prefix): what precedes every return address. */
static int is_return_address(const unsigned char *p) {
    if (p[-5] == 0xE8) return 1;                                            /* call rel32 */
    if (p[-2] == 0xFF && (p[-1] & 0xF8) == 0xD0) return 1;                  /* call reg */
    if (p[-3] == 0xFF && (p[-2] & 0xF8) == 0x50) return 1;                  /* call [reg+disp8] */
    if (p[-6] == 0xFF && (p[-5] & 0xF8) == 0x90) return 1;                  /* call [reg+disp32] */
    if (p[-6] == 0xFF && p[-5] == 0x15) return 1;                           /* call [rip+disp32] */
    if (p[-2] == 0xFF && (p[-1] & 0xF8) == 0x10) return 1;                  /* call [reg] */
    if (p[-3] == 0x41 && p[-2] == 0xFF && (p[-1] & 0xF8) == 0xD0) return 1; /* call r8-r15 */
    if (p[-4] == 0x41 && p[-3] == 0xFF && (p[-2] & 0xF8) == 0x50) return 1; /* call [r8-15+disp8] */
    if (p[-7] == 0x41 && p[-6] == 0xFF && (p[-5] & 0xF8) == 0x90) return 1; /* call [r8-15+disp32] */
    return 0;
}

static void acquire(void) { while (atomic_flag_test_and_set_explicit(&lock, memory_order_acquire)) {} }
static void release(void) { atomic_flag_clear_explicit(&lock, memory_order_release); }

static int by_bytes(const void *a, const void *b) {
    const Site *x = a, *y = b;
    return x->bytes < y->bytes ? 1 : x->bytes > y->bytes ? -1 : 0;
}

static ABI void *hook(void *self, uint64_t size, uint64_t align) {
    uint64_t site = (uint64_t)((unsigned char *)__builtin_return_address(0) - base);
    /* Nearly every call comes from the game's paged allocator (eboot+0xe45560, which takes 2 KB pages
     * here); the interesting caller is one level up. That function keeps a frame: push rbp; push
     * r15, r14, r13, r12, rbx; push rax; then `call [vtable+0x58]`, so from our canonical frame
     * address its return address is the 8th slot. */
    if (site == PAGED_SITE) {
        /* The return address into that allocator's caller is the next return address up the
         * stack: a code address in the eboot whose preceding bytes are a call instruction. The
         * stack is scanned from our frame (frame layouts proved unreliable here). */
        uint64_t *slot = __builtin_frame_address(0);
        for (int k = 0; k < 64; ++k) {
            const uint64_t v = slot[k];
            const uint64_t off = v - (uint64_t)(uintptr_t)base;
            if (v <= (uint64_t)(uintptr_t)base + 0x1000 || off >= TEXT_BYTES || off == PAGED_SITE) continue;
            if (!is_return_address(base + off)) continue;
            site = off;
            break;
        }
    }
    void *result = original(self, size, align);
    uint64_t used = 0, start = 0, cursor = 0;
    if (self) {
        memcpy(&start, (const unsigned char *)self + HEAP_START, 8);
        memcpy(&cursor, (const unsigned char *)self + HEAP_CURSOR, 8);
        if (cursor >= start) used = cursor - start;
    }
    acquire();
    size_t i = 0;
    while (i < nsites && sites[i].site != site) ++i;
    if (i == nsites) {
        if (nsites < SITES) { sites[nsites].site = site; sites[nsites].calls = sites[nsites].bytes = sites[nsites].nulls = 0; ++nsites; }
        else { ++dropped; i = SITES; }
    }
    if (i < SITES) {
        sites[i].calls++;
        sites[i].bytes += size;
        if (!result) sites[i].nulls++;
    }
    for (size_t h = 0; h < HEAPS; ++h) {
        if (heaps[h].heap == self || !heaps[h].heap) {
            heaps[h].heap = self;
            if (used > heaps[h].peak) heaps[h].peak = used;
            break;
        }
    }
    ++calls_total;
    if (!result) ++failures;
    release();
    return result;
}

static void *reporter(void *arg) {
    (void)arg;
    Site snapshot[SITES];
    Heap hs[HEAPS];
    time_t last = time(NULL);
    for (;;) {
#ifdef _WIN32
        Sleep(REPORT_SECONDS * 1000);
#else
        sleep(REPORT_SECONDS);
#endif
        time_t now = time(NULL);
        double seconds = (double)(now - last);
        if (seconds <= 0) seconds = 1;
        acquire();
        size_t n = nsites;
        memcpy(snapshot, sites, n * sizeof *sites);
        memcpy(hs, heaps, sizeof hs);
        uint64_t calls = calls_total, failed = failures;
        calls_total = 0;
        failures = 0;
        for (size_t k = 0; k < nsites; ++k) sites[k].calls = sites[k].bytes = sites[k].nulls = 0;
        for (size_t h = 0; h < HEAPS; ++h) heaps[h].peak = 0;
        release();
        last = now;
        qsort(snapshot, n, sizeof *snapshot, by_bytes);
        printf("GX heap trace +%llds: %.0f calls/s from %zu sites%s%s; heap peaks:", (long long)(now - started),
               (double)calls / seconds, n, dropped ? " (table full: some dropped)" : "",
               failed ? " FAILED ALLOCATIONS" : "");
        for (size_t h = 0; h < HEAPS; ++h)
            if (hs[h].heap) printf(" %p %llu B", hs[h].heap, (unsigned long long)hs[h].peak);
        printf("\n");
        for (size_t i = 0; i < n && i < 16; ++i)
            printf("  site +0x%07llx  %8.1f calls/s  %10.0f B/s  avg %5.0f B%s\n", (unsigned long long)snapshot[i].site,
                   (double)snapshot[i].calls / seconds, (double)snapshot[i].bytes / seconds,
                   snapshot[i].calls ? (double)snapshot[i].bytes / (double)snapshot[i].calls : 0.0,
                   snapshot[i].nulls ? "  <- FAILED allocations" : "");
        fflush(stdout);
    }
    return NULL;
}

void runtime_gxtrace_install(unsigned char *image, uint64_t image_size) {
    /* Opt-in (BB_GX_TRACE=1): the hook runs on the game's render worker threads and changes their
     * timing; it is a diagnostic, not part of normal play. */
    const char *env = getenv("BB_GX_TRACE");
    if (!env || strcmp(env, "1")) return;
    if (VTABLE_SLOT + 8 > image_size) { printf("GX heap trace: image too small, not installed\n"); return; }
    uintptr_t current;
    memcpy(&current, image + VTABLE_SLOT, 8);
    if (current != (uintptr_t)(image + ALLOCATE_FN)) {
        printf("GX heap trace: vtable slot holds %#llx, expected %#llx (another game version?): not installed\n",
               (unsigned long long)current, (unsigned long long)(uintptr_t)(image + ALLOCATE_FN));
        return;
    }
    base = image;
    original = (AllocateFn)(image + ALLOCATE_FN);
    started = time(NULL);
    pthread_t thread;
    if (pthread_create(&thread, NULL, reporter, NULL)) { printf("GX heap trace: no reporter thread, not installed\n"); return; }
    pthread_detach(thread);
    uintptr_t h = (uintptr_t)hook;
    memcpy(image + VTABLE_SLOT, &h, 8);
    printf("GX heap trace: frame-heap allocate hooked; a per-site report every %d s (BB_GX_TRACE=0 disables)\n",
           REPORT_SECONDS);
}
