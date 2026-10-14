#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Experimental dual RX at 40 Msps/channel using the current firmware.

Run on the receiving host, with exclusive controller access and streaming
stopped. Requires numpy and built host/stream and host/recv. Starts a raw
stream, disables the ICAP0 single-path packer through the WM, discards the
transition, then records and separates the two paths. The ordinary UI and
shared-memory ABI remain single-channel: do not attach them to this stream.
Normal stop restores the original ICAP format. No flash or firmware reload.
"""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'host'))
from w1700k.config import load
from w1700k.controller import Controller
from w1700k import rf

CR10 = 0x800230b8
RATE = 40_000_000


def split_paths(samples):
    """Host ci16 words per beat: p0[t], p0[t+1], p1[t], p1[t+1]."""
    samples = np.asarray(samples)
    if samples.ndim != 2 or samples.shape[1] != 2 or samples.shape[0] % 4:
        raise ValueError('expected whole four-word beats of signed I,Q pairs')
    beats = samples.reshape(-1, 4, 2)
    return beats[:, :2].reshape(-1, 2).copy(), beats[:, 2:].reshape(-1, 2).copy()


def analyze(path):
    values = np.fromfile(path, dtype='<i2').reshape(-1, 2)
    paths = split_paths(values)
    stats = []
    for index, iq in enumerate(paths):
        iq.astype('<i2').tofile(path.with_name(path.stem + f'-ch{index}.ci16'))
        x = iq.astype(np.float64)
        stats.append({'samples': len(iq), 'rms': float(np.sqrt(np.mean(np.sum(x*x, axis=1)))),
                      'nonzero_samples': int(np.count_nonzero(np.any(iq != 0, axis=1))),
                      'mean_iq': np.mean(x, axis=0).tolist()})
    x = paths[0][:, 0].astype(float) + 1j * paths[0][:, 1]
    y = paths[1][:, 0].astype(float) + 1j * paths[1][:, 1]
    x -= x.mean(); y -= y.mean()
    norm = np.linalg.norm(x) * np.linalg.norm(y)
    return {'channels': stats, 'identical_sample_fraction': float(np.mean(np.all(paths[0] == paths[1], axis=1))),
            'correlation_magnitude': float(abs(np.vdot(x, y)) / norm) if norm else None}


def checks(result):
    continuous = result['continuous']
    rate = continuous['samples'] / continuous['seconds'] / 2
    normal = result['normal']['paths']
    transport = result['transport_before_zero_control']
    check = {'rate_per_channel_40Msps': abs(rate / RATE - 1) < 0.01,
             'two_nonzero_paths': all(x['nonzero_samples'] > 100 for x in normal['channels']),
             'paths_not_identical': normal['identical_sample_fraction'] < 0.99,
             'transport_clean': all(transport[k] == 0 for k in
                                    ('lost', 'overruns', 'bad', 'socket_drops', 'spare_words', 'stale_slices', 'stale_lines')),
             'producer_clean_stop': result['producer']['status'] == 7 and not result['producer']['overrun']}
    if 'zero_path1' in result:
        zero = result['zero_path1']['paths']['channels']
        restored = result['restored']['paths']['channels']
        check['path1_zero_control'] = zero[0]['nonzero_samples'] > 100 and zero[1]['nonzero_samples'] == 0
        check['both_paths_restored'] = all(x['nonzero_samples'] > 100 for x in restored)
    return check


def record(folder, name, shared, seconds, keep=False):
    argv = [str(REPO / 'host/recv/build/sdr-recv'), '--stream', shared, '--seconds', str(seconds), '--wait', '10']
    if keep:
        argv += ['--out', str(folder / (name + '.ci16')), '--capacity-mib', '64']
    p = subprocess.run(argv, capture_output=True, text=True, timeout=seconds + 20)
    (folder / (name + '.log')).write_text(p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'{name}: recorder failed: {p.stderr}')
    result = json.loads([x for x in p.stdout.splitlines() if x.startswith('{')][-1])
    if result['lost'] or result['overruns'] or result['ended'] != 'seconds':
        raise RuntimeError(f'{name}: incomplete recording: {result}')
    if keep:
        result['paths'] = analyze(folder / (name + '.ci16'))
        for index, stats in enumerate(result['paths']['channels']):
            meta = {'channel': index, 'sample_rate': RATE, 'format': 'ci16_le',
                    'scale': 'signed 14-bit ADC values in int16 I,Q', 'samples': stats['samples'],
                    'first_slice': result['first_slice'], 'samples_per_channel_per_data_slice': 1024,
                    'source': name + '.ci16', 'phase_coherence_verified': False}
            (folder / f'{name}-ch{index}.ci16.json').write_text(json.dumps(meta, indent=2) + '\n')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--band', type=int, choices=(0, 1, 2), default=0)
    ap.add_argument('--seconds', type=int, choices=range(1, 601), default=60)
    ap.add_argument('--port', type=int, default=7770)
    ap.add_argument('--zero-control', action='store_true', help='also zero path 1 at the DFE, then restore it')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    config = load(args.config)
    if (not 1 <= args.port <= 65535 - config.export.ports or
            abs(args.port - config.export.port) < config.export.ports):
        raise ValueError('choose a separate valid UDP port for the experiment')
    config = replace(config, export=replace(config.export, port=args.port))
    c = Controller(config)
    shared = f'/xr1710g-dual-probe-{args.port}'
    receiver = None
    saved = None
    path_reg = 0x830ad464 + args.band * 0x100000
    old_path = None
    result = {'band': args.band, 'channels': 2, 'sample_rate_per_channel': RATE,
              'layout': 'p0[t],p0[t+1],p1[t],p1[t+1]; each sample int16 I,Q',
              'phase_coherence_verified': False}
    log = (args.out / 'transport.log').open('w')
    try:
        c.attach()
        if not c.loaded or c.packing:
            raise RuntimeError('requires an already loaded receiver with streaming stopped')
        saved = (c.band, c.bands[args.band], dict(c.rf[args.band]))
        c.configure(args.band, RATE)
        c.filter(20)
        c.gain(False, 2, 5)
        w = c._wm()
        old_path = w.read([path_reg])[0]
        if old_path is None:
            raise RuntimeError('cannot read DFE path control')
        receiver = subprocess.Popen([str(REPO / 'host/stream/build/w1700k-stream'), '--name', shared,
                                     '--bind', config.export.dst_ip, '--port', str(args.port), '--report', '1',
                                     '--ring-mib', '128'], stdout=log, stderr=subprocess.STDOUT)
        time.sleep(0.5)
        if receiver.poll() is not None:
            raise RuntimeError('receiver did not start')
        c.start()
        w.run([rf.w32(CR10, 0), rf.rmw32(path_reg, 2, 0)])
        result['cr10_readback'] = w.read([CR10])[0]
        if result['cr10_readback'] & 0xffff0000:
            raise RuntimeError('ICAP packer remains enabled')
        time.sleep(0.5)  # exclude the live format change and publication backlog
        result['normal'] = record(args.out, 'normal', shared, 0.03, True)
        result['continuous'] = record(args.out, 'continuous', shared, args.seconds)
        time.sleep(1.1)
        # Integrity here excludes the deliberately zero-filled control below.
        log.flush()
        rows = [json.loads(x) for x in (args.out / 'transport.log').read_text().splitlines() if x.startswith('{')]
        result['transport_before_zero_control'] = rows[-1]
        if args.zero_control:
            w.run([rf.rmw32(path_reg, 2, 2)])
            time.sleep(0.5)
            result['zero_path1'] = record(args.out, 'zero-path1', shared, 0.03, True)
            w.run([rf.w32(path_reg, old_path & ~2)])
            time.sleep(0.5)
            result['restored'] = record(args.out, 'restored', shared, 0.03, True)
        result['producer'] = asdict(c.stop())
        result['checks'] = checks(result)
        result['passed'] = all(result['checks'].values())
    finally:
        try:
            if saved is not None and c.packing:
                c.stop()
            if old_path is not None:
                c._wm().run([rf.w32(path_reg, old_path)])
            if saved is not None:
                band, cfg, rfs = saved
                c.configure(args.band, cfg.sample_rate, cfg.lo_hz, cfg.capture_rate)
                c.filter(rfs['filter_mhz'])
                c.gain(rfs['agc'], rfs['lna'], rfs['lpf'])
                c.configure(band)
        finally:
            if receiver is not None:
                receiver.send_signal(signal.SIGINT)
                try:
                    receiver.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    receiver.kill()
                    receiver.wait()
            log.close()
            c.close()
            (args.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit('dual RX checks failed; inspect result.json')


if __name__ == '__main__':
    main()
