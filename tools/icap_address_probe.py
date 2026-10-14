#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Full-word ICAP destination comparison and bounded address-offset probes.

Requires an idle, loaded WM v6 and exclusive controller access. Builds an
idle-only helper with clang/LLVM, fills/compares the complete reserved capture
pool, and restores scratch and registers. Does not reallocate live PLE/PSE.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

from icap_probe import Controller, load, rf, cr, execute, idle, read_words, ROUTE

REPO = Path(__file__).resolve().parents[1]
ENTRY, OUTPUT, END = 0x90d100, 0x90d800, 0x90e840
PAGES = 260


def compile_helper():
    sys.path.insert(0, str(REPO / 'firmware'))
    from stream.blob import compile_blob
    return compile_blob([str(REPO / 'tools/icap_memory_probe.c')], {}, ENTRY, OUTPUT)


def mailbox_read(w, start, count):
    words = []
    for at in range(0, count, 64):
        words += w.session.ctl(read=(start + 4*at, min(64, count-at)))[0]
    return words


def mailbox_write(w, start, words):
    for at in range(0, len(words), 64):
        w.session.ctl([(start+4*i, words[i]) for i in range(at, min(at+64, len(words)))])


def compare(w):
    words = mailbox_read(w, OUTPUT, PAGES*4)
    return [{'page': i, 'address': hex(0xe0400000+i*4096 if i < 256 else 0x464000+(i-256)*4096),
             'changed': words[i*4], 'first': words[i*4+1], 'last': words[i*4+2], 'hash': words[i*4+3]}
            for i in range(PAGES)]


def measure(w, out, label, engine, start, wpath, owner=8, full_route=False):
    idle(w)
    seed = 0x13572468
    if w.call(ENTRY, 0, seed) != PAGES or w.call(ENTRY, 1, seed) != PAGES:
        raise RuntimeError('helper failed')
    before = compare(w)
    if any(p['changed'] for p in before):
        raise RuntimeError('full-word sentinel positive control failed')
    # Restrict both engines to the already verified 16 KiB capture address
    # range. Each finite capture is 1 KiB (64 beats), never ring mode.
    if start % 4 or not 0 <= start <= 0xf00:
        raise ValueError('start outside safe finite capture address range')
    if engine is not None:
        ops = [rf.w32(cr(engine, 0), 0), rf.w32(cr(engine, 0), 0x400),
               rf.w32(cr(engine, 2), start), rf.w32(cr(engine, 3), start+0xfc),
               rf.w32(cr(engine, 8), 0), rf.w32(cr(engine, 10), 0),
               rf.w32(ROUTE+4, 0xff000000), rf.w32(ROUTE+12, owner),
               rf.w32(ROUTE+0x28, wpath)]
        if full_route:
            ops += [rf.w32(ROUTE+0x18, 0x30001000), rf.w32(ROUTE+0x1c, 0x70005000),
                    rf.w32(ROUTE+0x20, 0x1000), rf.w32(ROUTE+0x2c, 0x180)]
        ops += [rf.w32(cr(engine, 0), 0x401), rf.w32(cr(engine, 0), 0x403), rf.delay(1000)]
        execute(w, ops)
        regs = w.read(cr(engine, k) for k in range(13))
    else:
        regs = []
    idle(w)
    if w.call(ENTRY, 1, seed) != PAGES:
        raise RuntimeError('comparison helper failed')
    pages = compare(w)
    result = {'label': label, 'engine': engine, 'start_units': start, 'capture_bytes': 1024,
              'wpath': hex(wpath), 'owner': hex(owner), 'full_route': full_route, 'registers': regs,
              'completed': not regs or not bool(regs[0] & 2),
              'pool_changed_words': sum(p['changed'] for p in pages[:256]),
              'window_changed_words': sum(p['changed'] for p in pages[256:]),
              'compared_pool_bytes': 1048576, 'compared_window_bytes': 16384,
              'pages': pages}
    (out / (label+'.json')).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'pages'}), flush=True)
    # Baselines prove both that the helper sees hardware writes and that the
    # start address controls the expected physical range.
    changed = [p for p in pages if p['changed']]
    if engine == 0:
        expected_page = start*4//4096
        if (result['pool_changed_words'] != 256 or result['window_changed_words'] or
            len(changed) != 1 or changed[0]['page'] != expected_page or
            changed[0]['first'] != start*4 % 4096 or changed[0]['last'] != start*4 % 4096+1020):
            raise RuntimeError('ICAP0 address mapping control failed')
    elif engine is None:
        if changed:
            raise RuntimeError('capture buffers change even without a capture')
    elif wpath == 0x200000 and result['window_changed_words'] != 256:
        raise RuntimeError('ICAP1 window positive control failed')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--helper', type=Path, help='prebuilt icap_memory_probe.c linked at 0x90d100')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    code = args.helper.read_bytes() if args.helper else compile_helper()
    if not code or len(code) % 4 or len(code) > OUTPUT-ENTRY:
        raise ValueError('invalid helper size')
    (args.out/'helper.bin').write_bytes(code)
    c = Controller(load(args.config))
    saved = None
    try:
        c.attach()
        if not c.loaded or c.packing:
            raise RuntimeError('requires loaded, stopped receiver')
        saved = c.band, c.bands[0], dict(c.rf[0])
        c.configure(0, 40_000_000); c.filter(20)
        w = c._wm()
        if w.status()['version'] != 6:
            raise RuntimeError('requires WM v6 scratch layout')
        # Read-only evidence from the actual ROM and its live partition globals.
        rom = read_words(w, 0x822900, 1024)
        (args.out/'rom-822900.bin').write_bytes(struct.pack('<1024I', *rom))
        globals_ = w.read([0x222b434, 0x222b438, 0x222b558])
        partition = {'icap_pos': globals_[0]&255, 'pse_pos': globals_[0]>>8&255,
                     'pse_banks': globals_[0]>>16, 'ple_banks': globals_[1]&65535,
                     'ple_pos': globals_[2]&255, 'icap_banks': globals_[2]>>16}
        (args.out/'partition.json').write_text(json.dumps(partition, indent=2)+'\n')
        if partition != {'icap_pos':0,'pse_pos':2,'pse_banks':128,'ple_banks':64,'ple_pos':1,'icap_banks':512}:
            raise RuntimeError(f'capture pool ownership differs: {partition}')
        addresses = [ROUTE+i for i in range(0, 0x30, 4)] + [cr(n,k) for n in range(3) for k in range(13)]
        registers = w.read(addresses)
        if any(v is None for v in registers) or any(registers[12+n*13]&3 for n in range(3)):
            raise RuntimeError('unknown registers or capture already enabled')
        scratch = mailbox_read(w, ENTRY, (END-ENTRY)//4)
        try:
            mailbox_write(w, ENTRY, list(struct.unpack(f'<{len(code)//4}I', code)))
            rows = []
            rows.append(measure(w,args.out,'no-capture-control',None,0,0))
            for start in (0,0x100,0x800):
                rows.append(measure(w,args.out,f'icap0-offset-{start:x}',0,start,1,0))
                for label, path, owner, full in [('window',0x200000,8,False),
                        ('select11',0x800,8,False), ('window-select11',0x200800,8,False),
                        ('both-selectors',0x200801,8,False),
                        ('type0-select11',0x23c800,0x02249008,True),
                        ('type0-both-selectors',0x23c801,0x02249008,True)]:
                    rows.append(measure(w,args.out,f'icap1-{label}-{start:x}',1,start,path,owner,full))
            rows.append(measure(w,args.out,'icap0-final-control',0,0,1,0))
            (args.out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
        finally:
            idle(w)
            execute(w,[rf.w32(a,v) for a,v in zip(addresses[12:],registers[12:])
                       if (a-0x80023090)%0x1000 in (0,8,12,32,40)])
            execute(w,[rf.w32(a,v) for a,v in zip(addresses[:12],registers[:12])])
            mailbox_write(w, ENTRY, scratch)
        (args.out/'helper-sha256.txt').write_text(hashlib.sha256(code).hexdigest()+'\n')
    finally:
        try:
            if saved:
                band,cfg,rfs = saved
                c.configure(0,cfg.sample_rate,cfg.lo_hz,cfg.capture_rate)
                c.filter(rfs['filter_mhz']); c.gain(rfs['agc'],rfs['lna'],rfs['lpf']); c.configure(band)
        finally:
            c.close()


if __name__ == '__main__':
    main()
