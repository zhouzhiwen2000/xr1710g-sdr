#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded ICAP routing experiments on an already loaded, idle receiver.

Run on the receiving Linux host with exclusive controller access. Uses the
WM interpreter, never host WFSYS MMIO. No flash writes or firmware reloads.
Candidate write paths are bounded; forbidden SRAM grants are excluded.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from w1700k.config import load
from w1700k.controller import Controller
from w1700k import rf

ROUTE = 0x88009000
POOL = 0xe0400000
WINDOW = 0x00464000
# Idle-only scratch: MT7996_PROBE_WM_PACKBUF(0), unused without a packer.
CACHE_HELPER = 0x0090d000
# RV32: csrwi 0x7cc,6 (L1D WBINVAL_ALL); fence; li a0,0; ret.
CACHE_CODE = [0x7cc35073, 0x0ff0000f, 0x00000513, 0x00008067]


def cache_sync(w):
    # Pool memory is write-back cacheable on WM. Without this, a sentinel
    # can appear unchanged even after ICAP0 has successfully overwritten it.
    if w.call(CACHE_HELPER) != 0:
        raise RuntimeError('cache maintenance helper failed')


def cr(n, k):
    return 0x80023090 + n * 0x1000 + k * 4


def execute(w, ops):
    for start in range(0, len(ops), rf.CTL_MAX):
        w.run(ops[start:start + rf.CTL_MAX])


def read_words(w, base, words):
    values = w.read(range(base, base + words * 4, 4))
    if any(v is None for v in values):
        raise RuntimeError(f'failed reading {base:#x}')
    return values


def idle(w):
    # No producer/DMA may be running when this helper is used.
    execute(w, [rf.w32(cr(n, 0), 0x400) for n in range(3)] +
            [rf.w32(ROUTE + off, 0) for off in range(0, 0x30, 4)] +
            [rf.w32(ROUTE + 0x24, 0x02000000)])


def snapshot(w, out, label, engine, wpath, owner, sentinel, wpath2=0, keep_data=True, pool_span=16384):
    idle(w)
    # Sparse sentinels cover all 16 KiB of each destination, at 64-byte
    # intervals. These are capture-only buffers in our loaded firmware.
    offsets = {'pool': list(range(0, pool_span, 64 if pool_span == 16384 else 4096)),
               'window': list(range(0, 16384, 64))}
    bases = [('pool', POOL), ('window', WINDOW)]
    execute(w, [rf.w32(base + off, sentinel ^ off) for name, base in bases for off in offsets[name]])
    cache_sync(w)
    before = {name: w.read(base + off for off in offsets[name]) for name, base in bases}
    if any(v != [sentinel ^ off for off in offsets[name]] for name, v in before.items()):
        raise RuntimeError('sentinel readback failed; aborting capture')
    # A finite 1024-beat capture, within the documented 16 KiB window.
    execute(w, [rf.w32(cr(engine, 0), 0), rf.w32(cr(engine, 0), 0x400),
                rf.w32(cr(engine, 2), 0), rf.w32(cr(engine, 3), 0xffc),
                rf.w32(cr(engine, 8), 0), rf.w32(cr(engine, 10), 0),
                rf.w32(ROUTE + 4, 0xff000000), rf.w32(ROUTE + 12, owner << 3),
                rf.w32(ROUTE + 0x28, wpath), rf.w32(ROUTE + 0x2c, wpath2),
                rf.w32(cr(engine, 0), 0x401), rf.w32(cr(engine, 0), 0x403),
                rf.delay(1000)])
    regs = w.read(cr(engine, k) for k in range(13))
    idle(w)  # disable EN and release bank grants before reading the data
    cache_sync(w)
    after = {name: w.read(base + off for off in offsets[name]) for name, base in bases}
    result = {'label': label, 'engine': engine, 'wpath': hex(wpath), 'wpath2': hex(wpath2), 'owner': owner,
              'registers': regs, 'completed': not bool(regs[0] & 2),
              'changed_sentinels': {name: sum(a != b for a, b in zip(before[name], after[name])) for name in before},
              'sentinel_count_per_destination': len(offsets['pool']), 'pool_probe_span_bytes': pool_span,
              'changed_offsets': {name: [off for off, a, b in zip(offsets[name], before[name], after[name]) if a != b]
                                  for name in before}}
    for name, base in [('pool', POOL), ('window', WINDOW)]:
        if result['changed_sentinels'][name] and (keep_data or name == 'pool'):
            data = read_words(w, base, 4096)
            (out / f'{label}-{name}.bin').write_bytes(struct.pack('<4096I', *data))
    (out / f'{label}.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--sweep', action='store_true', help='also test each of the 22 writable bits in WPATH and WPATH2')
    ap.add_argument('--pool-select', action='store_true', help='test ICAP1 WPATH bit 11 with/without bit 0, observing the whole 1 MiB pool')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    c = Controller(load(args.config))
    saved = None
    try:
        c.attach()
        if not c.loaded or c.packing:
            raise RuntimeError('requires an already loaded receiver with streaming stopped')
        saved = (c.band, c.bands[0], dict(c.rf[0]))
        c.configure(0, 40_000_000)
        c.filter(20)
        w = c._wm()
        if w.status()['version'] != 6:
            raise RuntimeError('scratch layout verified only for WM version 6')
        addresses = [ROUTE + off for off in range(0, 0x30, 4)] + [cr(n, k) for n in range(3) for k in range(13)]
        regs = w.read(addresses)
        if any(v is None for v in regs) or any(regs[12 + n * 13] & 3 for n in range(3)):
            raise RuntimeError('unknown registers or active capture engine; refusing probe')
        helper_before = w.session.ctl(read=(CACHE_HELPER, len(CACHE_CODE)))[0]
        results = []
        try:
            w.session.ctl([(CACHE_HELPER + 4*i, word) for i, word in enumerate(CACHE_CODE)])
            (args.out / 'before.json').write_text(json.dumps(dict(zip(map(hex, addresses), regs)), indent=2))
            cases = [
                ('icap0-pool-control', 0, 0x1, 0),
                ('icap1-window-control', 1, 0x200000, 1),
                ('icap1-pool-only-owner1', 1, 0x1, 1),
                ('icap1-pool-only-owner0', 1, 0x1, 0),
                ('icap1-pool-and-window', 1, 0x200001, 1),
                ('icap1-pool-and-window-owner0', 1, 0x200001, 0),
                ('icap1-all-known-wpaths', 1, 0x200401, 1),
                ('icap0-pool-control-after', 0, 0x1, 0),
            ]
            for index, case in enumerate(cases):
                results.append(snapshot(w, args.out, *case, 0xa5c30000 + index * 0x10000))
                if case[0].startswith('icap0-pool-control') and results[-1]['changed_sentinels']['pool'] != 256:
                    raise RuntimeError('ICAP0 positive control failed; pool-negative results are inconclusive')
                if case[0] == 'icap1-window-control' and results[-1]['changed_sentinels']['window'] != 256:
                    raise RuntimeError('ICAP1 positive control failed; refusing further probes')
            if args.sweep:
                # Explore the low 22 bits of each write-path word. Keep all
                # unsafe grants zero, the range bounded, and ICAP0 off.
                for bank in (0, 1):
                    for bit in range(22):
                        label = f'icap1-wpath{bank + 1}-bit{bit:02d}'
                        wpath = 0x200001 | ((1 << bit) if bank == 0 else 0)
                        wpath2 = (1 << bit) if bank == 1 else 0
                        results.append(snapshot(w, args.out, label, 1, wpath, 1,
                                                0xb0000000 + (bank * 22 + bit) * 0x10000,
                                                wpath2, keep_data=False))
                results.append(snapshot(w, args.out, 'icap1-all-write-path-bits', 1, 0x3fffff, 1,
                                        0xc1000000, 0x3fffff))
                results.append(snapshot(w, args.out, 'icap0-pool-after-sweep', 0, 1, 0, 0xc2000000))
                if results[-1]['changed_sentinels']['pool'] != 256:
                    raise RuntimeError('post-sweep ICAP0 positive control failed')
            if args.pool_select:
                for index, (wpath, owner) in enumerate([(0x800, 0), (0x800, 1), (0x200800, 0),
                                                       (0x200800, 1), (0x200801, 0), (0x200801, 1)]):
                    results.append(snapshot(w, args.out, f'icap1-select11-{index}', 1, wpath, owner,
                                            0xc3000000 + index * 0x10000, pool_span=0x100000))
                results.append(snapshot(w, args.out, 'icap0-pool-after-select11', 0, 1, 0, 0xc9000000))
                if results[-1]['changed_sentinels']['pool'] != 256:
                    raise RuntimeError('post-select ICAP0 positive control failed')
        finally:
            idle(w)
            # Restore stopped engines first; restore route grant words last.
            execute(w, [rf.w32(a, v) for a, v in zip(addresses, regs)
                        if a >= 0x80023000 and a < 0x80026000 and (a - 0x80023090) % 0x1000 in (0, 8, 12, 32, 40)])
            execute(w, [rf.w32(a, v) for a, v in zip(addresses[:12], regs[:12])])
            w.session.ctl([(CACHE_HELPER + 4*i, word) for i, word in enumerate(helper_before)])
        (args.out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    finally:
        try:
            if saved is not None:
                band, cfg, rfs = saved
                c.configure(0, cfg.sample_rate, cfg.lo_hz, cfg.capture_rate)
                c.filter(rfs['filter_mhz'])
                c.gain(rfs['agc'], rfs['lna'], rfs['lpf'])
                c.configure(band)
        finally:
            c.close()


if __name__ == '__main__':
    main()
