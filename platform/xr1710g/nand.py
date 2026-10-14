#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package the verified RAM receiver for the existing XR1710G shim's fit volume.

This changes the FIT configuration name to config-1. The kernel, DTB and RAM
root are retained byte for byte; it does not create a UBI image or write NAND.
The shim must use the RAM bootargs recorded in the manifest.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

UNIT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('xr1710g_ram_builder', UNIT / 'build.py')
xr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xr)
OUT = xr.OUT
FIT = OUT / 'xr1710g-sdr-nand.itb'


def source_payloads(data):
    tree = xr.fdt.nodes(data)
    config = xr.fdt.string(tree['/configurations']['default'])
    xr.require(config == 'conf-sdr', 'input must be the XR1710G RAM receiver FIT')
    selection = tree['/configurations/' + config]
    payloads = {}
    for name in ('kernel', 'fdt', 'ramdisk'):
        xr.require(selection[name] == name.encode() + b'\0', 'unexpected FIT image selection')
        payload = tree['/images/' + name]['data']
        digest = tree['/images/' + name + '/hash-1']
        xr.require(digest['algo'] == b'sha256\0' and
                   digest['value'] == hashlib.sha256(payload).digest(),
                   'input FIT: invalid ' + name + ' SHA256')
        payloads[name] = payload
    xr.check_tree(payloads['fdt'])
    return payloads


def package(source, destination):
    payloads = source_payloads(source.read_bytes())
    xr.require(payloads['kernel'] == xr.KERNEL_IMAGE.read_bytes(),
               'input kernel does not match the guarded build output')
    xr.check_kernel(xr.kconfig(xr.LINUX_OUT / '.config'),
                    (xr.LINUX_OUT / 'System.map').read_text())
    work = OUT / 'nand-input'
    work.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, data in payloads.items():
        paths[name] = work / name
        xr.write_if_changed(paths[name], data)
    its = work / 'nand.its'
    text = xr.images.linux_fit('XR1710G SDR NAND-loaded RAM root',
                               paths['kernel'], paths['fdt'], paths['ramdisk'])
    xr.write_if_changed(its, text.replace('conf-sdr', 'config-1'))
    mkimage = shutil.which('mkimage')
    xr.require(mkimage is not None, 'mkimage is required')
    destination.parent.mkdir(parents=True, exist_ok=True)
    xr.run([mkimage, '-f', its, destination],
           env=xr.build_env(extra_path=[xr.LINUX_OUT / 'scripts/dtc']), log=xr.images.LOG)
    xr.images.check_fit(destination, payloads)
    xr.images.check_layout(destination, payloads['kernel'], len(payloads['ramdisk']))
    tree = xr.fdt.nodes(destination.read_bytes())
    xr.require(tree['/configurations']['default'] == b'config-1\0', 'wrong shim boot configuration')
    xr.require(tree['/configurations/config-1']['ramdisk'] == b'ramdisk\0', 'RAM root is missing')
    for name, data in payloads.items():
        xr.require(tree['/images/' + name + '/hash-1']['value'] == hashlib.sha256(data).digest(),
                   'output FIT: invalid ' + name + ' SHA256')
    manifest = {'board': 'XR1710G', 'mode': 'NAND-loaded RAM root',
                'bytes': destination.stat().st_size,
                'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                'payload_sha256': {n: hashlib.sha256(d).hexdigest() for n, d in payloads.items()},
                'source_fit_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'kernel_nand_enabled': False, 'target_partition': 'ubi', 'target_volume': 'fit',
                'shim_bootconf': 'config-1', 'shim_bootargs': xr.BOOTARGS,
                'preserve': ['original bootloader', 'original environment', 'DSD', 'chainloader'],
                'requires_existing_shim': True, 'openwrt_sysupgrade': False}
    destination.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=xr.FIT)
    parser.add_argument('--output', type=Path, default=FIT)
    args = parser.parse_args()
    xr.require(args.source.resolve() != args.output.resolve(), 'source and output must differ')
    package(args.source, args.output)
    return 0


if __name__ == '__main__':
    xr.main_guard(main)
