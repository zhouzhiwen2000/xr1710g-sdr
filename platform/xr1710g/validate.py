#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify a running XR1710G RAM trial and retrieve finite 40 Msps captures.

Uses the host controller with an explicit per-unit configuration. No UDP
export is started, so a gigabit control link is sufficient. Each band is
captured locally for 0.25 seconds; 8 MiB is then retrieved through SSH.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'host'))
from w1700k.config import load
from w1700k.controller import Controller
from w1700k.stream import GO_ADDR, GO_MAGIC, STOP_MAGIC, OUT_FILE
from w1700k.unit import DEVICE


def validate(config, output, bands):
    output.mkdir(parents=True, exist_ok=True)
    controller = Controller(load(config))
    results = []
    armed = False
    try:
        controller.unit.check('/usr/sbin/verify-storage')
        command_line = controller.unit.check('cat /proc/cmdline').strip()
        if 'ubi.' in command_line:
            raise RuntimeError('this is not the RAM trial boot configuration')
        controller.attach()
        if not controller.loaded:
            controller.bands = {b: replace(cfg, sample_rate=40_000_000, capture_rate=40_000_000)
                                for b, cfg in controller.bands.items()}
            controller.rf = {b: dict(rf, filter_mhz=20) for b, rf in controller.rf.items()}
            controller.load(band=bands[0])
        for band in bands:
            controller.configure(band, 40_000_000)
            controller.filter(20)
            wm = controller._wm()
            controller.stream.install_own(wm, 'raw')
            armed = True
            base = f'/work/xr1710g-band{band}'
            argv = [controller.unit.tool('mt7996-stream'), DEVICE,
                    '--seconds', '0.25', '--idle-ms', '1500',
                    '--go', hex(GO_ADDR), hex(GO_MAGIC), '--stop', hex(GO_ADDR), hex(STOP_MAGIC),
                    '--threads', '2', '--touch', '--cpu', '1',
                    '--data', '8388608', base + '.raw', '--meta', base + '.meta']
            response = controller.unit.run(shlex.join(argv) + f' > {OUT_FILE} 2>&1', timeout=30)
            summary = controller.stream.collect_own(wm, 'raw')
            armed = False
            reader = summary.reader or {}
            if response.returncode or not summary.ok or summary.overrun or \
                    reader.get('malformed', 1) or reader.get('lapped', 1) or not reader.get('bytes'):
                raise RuntimeError(f'band {band} capture failed: {asdict(summary)}')
            files = []
            for extension in ('raw', 'meta'):
                remote = base + '.' + extension
                data = controller.unit.read(remote)
                digest = hashlib.sha256(data).hexdigest()
                expected = controller.unit.check('sha256sum ' + shlex.quote(remote)).split()[0]
                if digest != expected:
                    raise RuntimeError(f'band {band}: {extension} SSH transfer hash mismatch')
                name = f'xr1710g-band{band}.{extension}'
                (output / name).write_bytes(data)
                files.append({'file': name, 'bytes': len(data), 'sha256': digest,
                              'router_sha256_verified': True})
            record = {'band': band, 'sample_rate': 40_000_000, 'reader_exit': response.returncode,
                      'summary': asdict(summary), 'state': controller.state(), 'files': files}
            results.append(record)
            # Persist each completed band even if a later band fails.
            (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
            print(f'band {band}: {reader["bytes"]} bytes, status={summary.status}, '
                  f'malformed={reader["malformed"]}, lapped={reader["lapped"]}', flush=True)
    finally:
        if armed:
            controller.stream.signal_stop()
        controller.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--bands', type=int, nargs='+', choices=(0, 1, 2), default=[0, 1, 2])
    args = parser.parse_args()
    validate(args.config, args.output, args.bands)


if __name__ == '__main__':
    main()
