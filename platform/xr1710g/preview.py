#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Serve periodic finite-capture spectra over the existing SSH control link.

This is a low-bandwidth viewer, not continuous IQ streaming. Run on the Linux
host with NumPy installed. RF control and captures are serialized; NAND is
never accessed by this program or the running receiver kernel.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import shlex
import sys
import threading
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'host'))
from w1700k.config import load
from w1700k.controller import Controller
from w1700k.stream import GO_ADDR, GO_MAGIC, STOP_MAGIC, OUT_FILE
from w1700k.unit import DEVICE

RATE = 40_000_000
SECONDS = 0.025
SAVE_BYTES = 2 * 1024 * 1024


def spectrum(raw, center_hz, sample_rate=RATE):
    words = np.frombuffer(raw, dtype='<u4')
    if np.any(words & 0xc000c000):
        raise ValueError('capture contains invalid raw IQ bits')
    nfft = 2048
    count = min(len(words) // nfft, 256)
    if count < 64:
        raise ValueError('capture is too short for the spectrum')
    count -= count % 64
    words = words[:count * nfft]
    i = ((words >> 16) & 0x3fff).astype(np.int32)
    q = (words & 0x3fff).astype(np.int32)
    iq = (((i ^ 0x2000) - 0x2000) + 1j * ((q ^ 0x2000) - 0x2000)) / 8192
    window = np.hanning(nfft)
    transform = np.fft.fftshift(np.fft.fft(iq.reshape(count, nfft) * window, axis=1), axes=1)
    psd = abs(transform)**2 / (sample_rate * np.sum(window**2))
    average = psd.mean(axis=0).reshape(512, 4).mean(axis=1)
    heat = psd.reshape(64, count // 64, 256, 8).mean(axis=(1, 3))
    frequency = np.fft.fftshift(np.fft.fftfreq(nfft, 1/sample_rate))
    frequency = (frequency.reshape(512, 4).mean(axis=1) + center_hz) / 1e6
    return {'frequency_mhz': np.round(frequency, 6).tolist(),
            'power': np.round(10*np.log10(np.maximum(average, 1e-30)), 2).tolist(),
            'waterfall': np.round(10*np.log10(np.maximum(heat, 1e-30)), 1).tolist(),
            'center_mhz': center_hz / 1e6, 'sample_rate': sample_rate,
            'record_ms': len(iq) / sample_rate * 1000, 'fft': nfft,
            'power_unit': 'dBFS/Hz', 'calibrated_dbm': False}


class Receiver:
    def __init__(self, config):
        self.config = load(config)
        self.controller = Controller(self.config)
        self.lock = threading.Lock()
        self.last = None

    def _attach(self):
        c = self.controller
        lines = c.unit.check('/usr/sbin/verify-storage && cat /proc/cmdline && '
                             'cat /proc/sys/kernel/random/boot_id').splitlines()
        if len(lines) != 3 or lines[0] != 'XR1710G_RAM_STORAGE_OK' or 'ubi.' in lines[1]:
            raise RuntimeError('the receiver is not using the guarded RAM root')
        if c.boot_id is not None and c.boot_id != lines[2]:
            c.close()
            self.controller = c = Controller(self.config)
            self.last = None
        c.attach()
        if c.packing is not None:
            raise RuntimeError('stop the existing stream before taking a finite capture')
        return c

    def capture(self, band, lo_mhz):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('a capture is already running')
        c = self.controller
        armed = False
        started = time.monotonic()
        try:
            c = self._attach()
            if not c.loaded:
                c.bands = {b: replace(cfg, sample_rate=RATE, capture_rate=RATE)
                           for b, cfg in c.bands.items()}
                c.rf = {b: dict(rf, filter_mhz=20) for b, rf in c.rf.items()}
                c.load(band=band)
            c.configure(band, RATE)
            c.filter(20)
            if lo_mhz is not None:
                c.tune(round(lo_mhz * 1e6))
            wm = c._wm()
            c.stream.install_own(wm, 'raw')
            armed = True
            base = '/work/xr1710g-preview'
            argv = [c.unit.tool('mt7996-stream'), DEVICE, '--seconds', str(SECONDS),
                    '--idle-ms', '300', '--go', hex(GO_ADDR), hex(GO_MAGIC),
                    '--stop', hex(GO_ADDR), hex(STOP_MAGIC), '--threads', '2', '--touch',
                    '--cpu', '1', '--data', str(SAVE_BYTES), base + '.raw',
                    '--meta', base + '.meta']
            result = c.unit.run(shlex.join(argv) + f' > {OUT_FILE} 2>&1', timeout=30)
            summary = c.stream.collect_own(wm, 'raw')
            armed = False
            reader = summary.reader or {}
            if result.returncode or not summary.ok or summary.overrun or \
                    reader.get('malformed', 1) or reader.get('lapped', 1):
                raise RuntimeError('finite capture failed: ' + json.dumps(asdict(summary)))
            raw = c.unit.read(base + '.raw')
            digest = hashlib.sha256(raw).hexdigest()
            expected = c.unit.check('sha256sum ' + base + '.raw').split()[0]
            if digest != expected:
                raise RuntimeError('SSH capture SHA256 mismatch')
            state = c.state()
            output = spectrum(raw, state['radio']['lo_hz'])
            output.update(band=band, sha256=digest, source_hash_verified=True,
                          capture_seconds=SECONDS, capture=asdict(summary), state=state,
                          elapsed_seconds=round(time.monotonic()-started, 3),
                          captured_at=time.time(), mode='periodic finite capture over SSH')
            self.last = output
            return output
        finally:
            try:
                if armed:
                    c.stream.signal_stop()
            finally:
                self.lock.release()


def handler(receiver):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, body, content_type='application/json'):
            if isinstance(body, dict):
                body = json.dumps(body, separators=(',', ':')).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                self.respond(200, (Path(__file__).parent / 'preview.html').read_bytes(),
                             'text/html; charset=utf-8')
            elif self.path == '/api/status':
                self.respond(200, {'mode': 'finite SSH captures', 'busy': receiver.lock.locked(),
                                  'state': receiver.controller.state(),
                                  'last_capture': receiver.last and receiver.last['captured_at']})
            else:
                self.respond(404, {'error': 'not found'})

        def do_POST(self):
            if self.path != '/api/capture':
                return self.respond(404, {'error': 'not found'})
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.respond(403, {'error': 'cross-origin requests are refused'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 4096:
                    raise ValueError('invalid request size')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError('request must be a JSON object')
                band = request.get('band', 1)
                lo = request.get('lo_mhz')
                if type(band) is not int or band not in (0, 1, 2):
                    raise ValueError('band must be 0, 1 or 2')
                if lo is not None and (type(lo) not in (int, float) or not math.isfinite(lo)):
                    raise ValueError('LO must be a finite frequency in MHz')
                data = receiver.capture(band, lo)
            except (ValueError, TypeError) as error:
                return self.respond(400, {'error': str(error)})
            except Exception as error:
                return self.respond(503, {'error': str(error)})
            self.respond(200, data)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--listen', default='127.0.0.1:8073')
    args = parser.parse_args()
    address, port = args.listen.rsplit(':', 1)
    receiver = Receiver(args.config)
    server = ThreadingHTTPServer((address, int(port)), handler(receiver))
    print('XR1710G finite-capture viewer: http://' + args.listen, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        receiver.controller.close()


if __name__ == '__main__':
    main()
