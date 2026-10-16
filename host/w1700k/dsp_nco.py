# SPDX-License-Identifier: MIT
"""DSP NCO control. Positive frequency shifts received baseband upward.

The optional --backend protocol implements OpenISAC's existing ordered helper
interface: positive LO correction means a negative digital frequency shift.
No samples are flushed and no receiver process is restarted by a SET command.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

BASE, MAGIC = 0x0090f100, 0x4e434f32
RUN, REQ, ACK, TARGET, STEP, APPLIED = 1, 2, 3, 4, 5, 6
BOUNDARY_LO, BOUNDARY_HI, PHASE, STATUS, RUNNING = 7, 8, 9, 10, 11
NOW, DEADLINE = 17, 18
ALLOWED_RATES = (40_000_000, 80_000_000)


def phase_step(hz, rate):
    if type(hz) not in (int, float) or not math.isfinite(hz) or abs(hz) >= rate / 2:
        raise ValueError('DSP NCO frequency must be finite and inside +/- sample_rate/2')
    return round(hz / rate * 2**32) & 0xffffffff


def frequency(step, rate):
    return (step if step < 0x80000000 else step - 2**32) * rate / 2**32


def read(session):
    values, _ = session.ctl(read=(BASE, 19))
    if values[0] != MAGIC:
        raise RuntimeError('DSP NCO control is unavailable; load current firmware and start --dsp-nco')
    return values


def set_frequency(session, hz, rate, run=None, timeout=2.0):
    step = phase_step(hz, rate)
    before = read(session)
    if not before[RUNNING] or (run is not None and before[RUN] != run):
        raise RuntimeError('DSP NCO stream stopped or changed')
    if before[REQ] != before[ACK]:
        raise RuntimeError('another DSP NCO command is pending')
    run = before[RUN]
    req = (before[ACK] + 1) & 0xffffffff
    deadline = (before[NOW] + 360_000_000) & 0xffffffff  # 500 ms in the WM's clock
    session.ctl([(BASE + 4*TARGET, run), (BASE + 4*STEP, step),
                 (BASE + 4*DEADLINE, deadline), (BASE + 4*REQ, req)])
    end = time.monotonic() + timeout
    while True:
        state = read(session)
        if state[RUN] != run or not state[RUNNING]:
            raise RuntimeError('DSP NCO stream stopped or changed during update')
        if state[ACK] == req:
            if state[STATUS] or state[APPLIED] != step:
                raise RuntimeError(f'DSP NCO update refused: status {state[STATUS]}')
            return {'frequency_hz': frequency(step, rate), 'step': step, 'run': run,
                    'sample': state[BOUNDARY_LO] | state[BOUNDARY_HI] << 32, 'phase': state[PHASE]}
        if time.monotonic() >= end:
            raise RuntimeError('DSP NCO update timed out; stop the stream before reusing control')
        time.sleep(.001)


class BackendControl:
    """Existing UE helper protocol, with one owner and one stream-run token."""
    def __init__(self, config, center, rate):
        from .unit import Unit
        from .rf import CtlSession
        if not math.isfinite(center) or rate not in ALLOWED_RATES:
            raise ValueError('DSP NCO backend requires a finite center and 40/80 MS/s')
        self.unit = Unit(config.unit)
        self.session = CtlSession(self.unit, own_wm=True)
        self.closed = False
        self.center, self.rate, self.step = center, rate, rate / 2**32
        try:
            self.boot, saved = self._state()
            self.band = saved['band']
            self._check(saved)
            current = read(self.session)
            if not current[RUNNING] or current[APPLIED] != 0:
                raise RuntimeError('start the DSP NCO path at zero frequency before opening the UE')
            self.run = current[RUN]
            self.expected_step = 0
        except BaseException:
            self.session.close()
            raise

    def _state(self):
        text = self.unit.check('cat /proc/sys/kernel/random/boot_id; cat /tmp/w1700k-state.json')
        boot, _, raw = text.partition('\n')
        return boot.strip(), json.loads(raw)

    def _check(self, saved):
        band = str(self.band)
        if (saved.get('boot_id') != self.boot or not saved.get('loaded') or saved.get('packing') != 'dsp' or
                saved['band'] != self.band or saved['bands'][band]['sample_rate'] != self.rate or
                abs(saved['bands'][band]['lo_hz'] - self.center) > .51):
            raise RuntimeError('source band, LO, rate or DSP path changed; reopen the backend')

    def set_offset(self, hz):
        if not math.isfinite(hz) or abs(hz) > 100000:
            raise ValueError('correction must be within +/-100000 Hz')
        boot, saved = self._state()
        if boot != self.boot: raise RuntimeError('router rebooted')
        self._check(saved)
        current = read(self.session)
        if current[APPLIED] != self.expected_step:
            raise RuntimeError('DSP NCO was changed by another controller')
        result = set_frequency(self.session, -hz, self.rate, self.run)
        self.expected_step = result['step']
        return -result['frequency_hz']

    def close(self):
        if self.closed: return
        self.closed = True
        try:
            state = read(self.session)
            if state[RUNNING] and state[RUN] == self.run and state[APPLIED] == self.expected_step:
                set_frequency(self.session, 0.0, self.rate, self.run)
        finally:
            self.session.close()


def serve(factory, inp, out):
    control = None
    last = 0
    try:
        for line in iter(lambda: inp.readline(1025), ''):
            request_id = 0
            try:
                fields = line.split()
                if len(line) > 1024 or len(fields) < 2: raise ValueError('malformed request')
                op, request_id = fields[0], int(fields[1])
                if request_id <= last: raise ValueError('stale request id')
                last = request_id
                if op == 'OPEN' and len(fields) == 4 and control is None:
                    control = factory(float(fields[2]), float(fields[3])); actual = 0.0
                elif op == 'SET' and len(fields) == 3 and control is not None:
                    actual = control.set_offset(float(fields[2]))
                elif op == 'CLOSE' and len(fields) == 2 and control is not None:
                    control.close();out.write(f'OK {request_id} 0 {control.step:.17g}\n');out.flush()
                    control = None
                    break
                else: raise ValueError('invalid operation or session state')
                out.write(f'OK {request_id} {actual:.17g} {control.step:.17g}\n');out.flush()
            except Exception as exc:
                if control:
                    try: control.close()
                    except Exception as restore: exc = RuntimeError(f'{exc}; restore failed: {restore}')
                    control = None
                out.write(f'ERR {request_id} {str(exc).replace(chr(10), " ")}\n');out.flush();return 1
    finally:
        if control: control.close()
    return 0


def main():
    from .config import load
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', required=True, type=Path)
    ap.add_argument('--backend', action='store_true', required=True)
    args = ap.parse_args()
    if not args.config.is_file(): ap.error('--config must exist')
    cfg = load(args.config)
    directory = Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/xr1710g-{os.getuid()}'))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = hashlib.sha256(f'{cfg.unit.user}@{cfg.unit.host}'.encode()).hexdigest()[:24]
    with (directory / f'xr1710g-pll-{name}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def interrupted(signum, frame): raise KeyboardInterrupt(f'signal {signum}')
        signal.signal(signal.SIGTERM, interrupted)
        return serve(lambda center, rate: BackendControl(cfg, center, rate), sys.stdin, sys.stdout)


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'XR1710G DSP NCO: {exc}', file=sys.stderr);sys.exit(1)
