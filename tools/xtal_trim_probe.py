#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read the crystal trim, or try one adjacent code and restore it.

Run on the receiver host with sole RF-control access. This controls the shared
reference oscillator, not the analog filter RC trim and not a digital NCO.
Requires our WM v6 and the verified die-2 crystal strap. Does not write EEPROM.
The current board's ppm/code and settling time require an external reference.
"""
import argparse
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from w1700k.config import load
from w1700k.unit import Unit
from w1700k.wm import Wm
from w1700k import rf

REGS = [rf.reg(2, rf.TOP, a) for a in (0x654, 0x658)]
MASK = 0x7f000000


def validate_access(w):
    if w.status()['version'] != 6:
        raise RuntimeError('requires our WM v6')
    strap = w.read([0x700056f0])[0]
    if strap is None or strap & (1 << 18):
        raise RuntimeError('unverified crystal routing; refusing die-2 trim')


def read(w):
    validate_access(w)
    values = w.run([rf.ar(r) for r in REGS])
    codes = [(v & MASK) >> 24 for v in values]
    if codes[0] != codes[1]:
        raise RuntimeError('crystal trim words disagree')
    return values, codes[0]


def restore(unit, saved):
    # Open a fresh channel: an interrupted command may have lost its session.
    w = Wm(unit)
    try:
        validate_access(w)  # allow recovery after a partial two-word write
        w.run([rf.armw(r, MASK, v & MASK) for r, v in zip(REGS, saved)])
        values, code = read(w)
        if values != saved:
            raise RuntimeError('crystal trim restoration did not match saved words')
        return {'code': code, 'words': [hex(v) for v in values],
                'locks': rf.synth_locks(w.session), 'traps': w.status()['traps']}
    finally:
        w.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', required=True, type=Path)
    ap.add_argument('--step', type=int, choices=(-1, 1), help='try one adjacent code, then restore')
    ap.add_argument('--seconds', type=float, default=2.0, help='hold for (0, 30] seconds')
    args = ap.parse_args()
    if not 0 < args.seconds <= 30:
        ap.error('--seconds must be within (0, 30]')
    unit = Unit(load(args.config).unit)
    w = Wm(unit)
    saved = None
    changed = False
    def interrupted(signum, _frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        saved, code = read(w)
        traps = w.status()['traps']
        locks = rf.synth_locks(w.session)
        print(json.dumps({'phase': 'before', 'code': code,
                          'words': [hex(v) for v in saved], 'locks': locks, 'traps': traps}), flush=True)
        if args.step is None:
            return
        target = code + args.step
        if not all(locks) or not 0 <= target <= 127:
            raise RuntimeError('requires locked synthesizers and a valid adjacent code')
        changed = True  # restore even if only the first SPI write succeeds
        w.run([rf.armw(r, MASK, target << 24) for r in REGS])
        actual, applied = read(w)
        expected = [(v & ~MASK) | (target << 24) for v in saved]
        if actual != expected:
            raise RuntimeError('trim write readback mismatch')
        time.sleep(args.seconds)
        locks = rf.synth_locks(w.session)
        if not all(locks) or w.status()['traps'] != traps:
            raise RuntimeError('lock or trap status changed')
        print(json.dumps({'phase': 'applied', 'code': applied,
                          'words': [hex(v) for v in actual], 'locks': locks}), flush=True)
    finally:
        w.close()  # close only this channel, preserving the shared SSH master
        if changed:
            result = restore(unit, saved)
            print(json.dumps(dict(phase='restored', **result)), flush=True)
            if not all(result['locks']):
                raise RuntimeError('PLL unlocked after restoration')


if __name__ == '__main__':
    main()
