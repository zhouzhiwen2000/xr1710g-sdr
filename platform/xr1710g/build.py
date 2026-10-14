#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build an XR1710G RAM-only SDR trial, without replacing either bootloader.

Uses the upstream pinned kernel, toolchain, SDR module and root packages.
The kernel deliberately has no SPI, MTD, UBI or BMT support. This is not a
sysupgrade or installation image; load the FIT through the existing shim.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

UNIT = Path(__file__).resolve().parent
PLATFORM = UNIT.parent
sys.path[:0] = [str(PLATFORM), str(PLATFORM / 'linux'), str(PLATFORM.parent / 'init')]
from buildlib import (BUILD, BUILDROOT_OUT, CROSS_COMPILE, KERNEL_IMAGE, LINUX_OUT,
                      ROOTFS_IMAGE, build_env, kconfig, main_guard, require, run,
                      write_if_changed)
import board_check
import fdt
import images
from initkit import provision


def load_builder(name, directory):
    spec = importlib.util.spec_from_file_location(name, PLATFORM / directory / 'build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


linux = load_builder('xr_linux_builder', 'linux')
rootfs = load_builder('xr_rootfs_builder', 'rootfs')
OUT = BUILD / 'xr1710g'
DTB = OUT / 'an7581-xr1710g-sdr-ram.dtb'
FIT = OUT / 'xr1710g-sdr-ram.itb'
BOOTARGS = 'console=ttyS0,115200 earlycon panic=5 ro rdinit=/init'
NO_FLASH = ('CONFIG_SPI', 'CONFIG_MTD', 'CONFIG_MTD_UBI', 'CONFIG_UBIFS_FS',
            'CONFIG_AIROHA_SDR_BMT', 'CONFIG_AIROHA_SDR_BMT_WRITE')
UNLINKED = ('airoha_snand_probe', 'spinand_probe', 'ubi_attach_mtd_dev',
            'ubifs_read_superblock', 'sdr_bmt_attach', 'mtk_bmt_attach',
            'airoha_bmt_transaction_recover', 'airoha_bmt_publish')


def receiver_config():
    """Keep upstream receiver settings, except the entire flash storage stack."""
    lines = (PLATFORM / 'linux/receiver.config').read_text().splitlines()
    prefixes = ('CONFIG_SPI', 'CONFIG_MTD', 'CONFIG_UBIFS', 'CONFIG_AIROHA_SDR_BMT')
    lines = [line for line in lines
             if not line.lstrip('# ').startswith(prefixes)]
    return '\n'.join(lines) + '\n' + ''.join(f'# {key} is not set\n' for key in NO_FLASH)


def check_tree(data):
    tree = fdt.nodes(data)
    symbols = tree['/__symbols__']

    def node(label):
        return tree[fdt.string(symbols[label])]

    require(fdt.strings(tree['/']['compatible'])[0] == 'gemtek,xr1710g',
            'XR1710G DTB: wrong board compatible')
    require(fdt.string(tree['/chosen']['bootargs']) == BOOTARGS,
            'XR1710G DTB: RAM boot arguments changed')
    for label in set(board_check.ENABLED) - {'snfi'}:
        require(fdt.string(node(label).get('status', b'okay\0')) == 'okay',
                f'XR1710G DTB: {label} is disabled')
    for label in (*board_check.DISABLED, 'snfi', 'spi_nand'):
        require(fdt.string(node(label).get('status', b'okay\0')) == 'disabled',
                f'XR1710G DTB: {label} must be disabled')
    nand_path = fdt.string(symbols['spi_nand'])
    require(not any(path.startswith(nand_path + '/partitions') for path in tree),
            'XR1710G DTB: NAND partitions must be absent')
    for mac, pcs, phy, address, reset in board_check.TEN_GIGABIT:
        require(node(mac)['phy-mode'] == b'usxgmii\0' and
                node(mac)['phy-handle'] == node(phy)['phandle'] and
                node(mac)['pcs-handle'] == node(pcs)['phandle'] and
                fdt.cells(node(phy)['reg']) == [address] and
                fdt.cells(node(phy)['reset-gpios'])[1:] == [reset, 1],
                f'XR1710G DTB: incorrect {mac} PHY/reset wiring')
    require(fdt.cells(node('en7581_pinctrl')['gpio-ranges'])[1:] == [0, 13, 47],
            'XR1710G DTB: incorrect GPIO range')
    for name, (base, size) in board_check.RESERVED.items():
        require(fdt.cells(node(name)['reg']) == [0, base, 0, size] and 'no-map' in node(name),
                f'XR1710G DTB: reserved region {name} changed')


def check_kernel(config, system_map):
    for key in NO_FLASH:
        require(config.get(key, 'n') == 'n', f'XR1710G kernel: {key} must be disabled')
    names = {line.split()[-1] for line in system_map.splitlines() if line.strip()}
    forbidden = [name for name in UNLINKED if name in names]
    require(not forbidden, 'XR1710G kernel: flash driver linked: ' + ', '.join(forbidden))
    for name in ('airoha_probe', 'mtk_pcie_probe', 'rtl826x_probe',
                 'rtl8261x_config_init', '_fw_rtl8261n_bin_bin',
                 '_fw_rtl_nic_rtl8261c_bin_bin'):
        require(name in names, f'XR1710G kernel: missing {name}')


def kernel():
    require(Path(CROSS_COMPILE + 'gcc').is_file(), 'build the toolchain first')
    openwrt = linux.prepare_source()
    linux.phy_firmware(openwrt)
    linux.ce_firmware()
    requested = OUT / 'receiver.config'
    firmware = os.path.relpath(linux.FIRMWARE_DIR, linux.LINUX_SRC)
    write_if_changed(requested, receiver_config() + f'CONFIG_EXTRA_FIRMWARE_DIR="{firmware}"\n')
    LINUX_OUT.mkdir(parents=True, exist_ok=True)
    linux.kernel_make(f'KCONFIG_ALLCONFIG={requested}', 'allnoconfig')
    resolved = kconfig(LINUX_OUT / '.config')
    dropped = [f'{key}={value}' for key, value in kconfig(requested).items()
               if resolved.get(key, 'n') != value]
    require(not dropped, 'XR1710G Kconfig dropped settings: ' + ', '.join(dropped))
    linux.kernel_make('Image', 'modules', 'scripts_dtc')
    preprocessed = OUT / 'xr1710g.dts.tmp'
    run([CROSS_COMPILE + 'gcc', '-E', '-nostdinc', '-undef', '-D__DTS__',
         '-x', 'assembler-with-cpp', '-I', linux.LINUX_SRC / 'include',
         '-I', openwrt / linux.OPENWRT_DTS, UNIT / 'an7581-xr1710g-sdr-ram.dts',
         '-o', preprocessed], env=build_env(), log=linux.LOG)
    run([LINUX_OUT / 'scripts/dtc/dtc', '-@', '-I', 'dts', '-O', 'dtb',
         '-o', DTB, preprocessed], log=linux.LOG)
    check_tree(DTB.read_bytes())
    check_kernel(resolved, (LINUX_OUT / 'System.map').read_text())
    run(['make', '-C', PLATFORM / 'linux', 'module', 'reader'], env=build_env(), log=linux.LOG)


def root_image():
    """Replace the staged overlay only after the toolchain build has finished."""
    rootfs.configure()
    files = rootfs.overlay_files(with_sdr=True)
    files = {name: value for name, value in files.items()
             if name != 'etc/fw_env.config' and not name.startswith('usr/sbin/durable-')}
    for path in (UNIT / 'overlay').rglob('*'):
        if path.is_file():
            name = path.relative_to(UNIT / 'overlay').as_posix()
            files[name] = (path.read_bytes(), 0o755 if name.startswith(('usr/sbin/', 'etc/init.d/'))
                           else 0o644)
    rootfs.stage_overlay(files)
    rootfs.buildroot_make()
    rootfs.check_image()


def bootstrap(root):
    entries = [(name, stat.S_IFDIR | 0o755, b'', (0, 0))
               for name in ('bin', 'dev', 'lower', 'newroot', 'proc', 'ram', 'sbin', 'sys')]
    entries += [('init', stat.S_IFREG | 0o755, (UNIT / 'bootstrap-init').read_bytes(), (0, 0)),
                ('bin/busybox', stat.S_IFREG | 0o755, images.BUSYBOX.read_bytes(), (0, 0)),
                ('bin/sh', stat.S_IFLNK | 0o777, b'busybox', (0, 0)),
                ('dev/console', stat.S_IFCHR | 0o600, b'', (5, 1)),
                ('rootfs.squashfs', stat.S_IFREG | 0o444, root, (0, 0))]
    archive = bytearray()
    for inode, (name, mode, data, rdev) in enumerate(sorted(entries), 1):
        archive += images.newc(name, mode, data, inode, rdev)
    archive += images.newc('TRAILER!!!', stat.S_IFREG, inode=len(entries) + 1)
    archive += bytes(-len(archive) % 512)
    return gzip.compress(bytes(archive), compresslevel=9, mtime=0)


def package(unit_dir, authorized_key):
    require(unit_dir is not None and authorized_key is not None,
            '--unit-dir and --authorized-key are required for the test SSH image')
    unit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = provision.host_key(unit_dir)
    root = provision.build(unit_dir, provision.authorized_key(authorized_key), key,
                           generic=ROOTFS_IMAGE).read_bytes()
    public = provision.host_public_key(key).decode()
    (unit_dir / 'known_hosts').write_text(f'192.168.255.1 {public}\n')
    initrd = OUT / 'ram-root.cpio.gz'
    write_if_changed(initrd, bootstrap(root))
    its = OUT / 'ram.its'
    write_if_changed(its, images.linux_fit('XR1710G SDR RAM trial (no NAND)', KERNEL_IMAGE, DTB, initrd))
    mkimage = shutil.which('mkimage')
    require(mkimage is not None, 'mkimage is needed (u-boot-tools on Debian/Ubuntu)')
    env = build_env(extra_path=[LINUX_OUT / 'scripts/dtc'])
    run([mkimage, '-f', its, FIT], env=env, log=images.LOG)
    payloads = {'kernel': KERNEL_IMAGE.read_bytes(), 'fdt': DTB.read_bytes(),
                'ramdisk': initrd.read_bytes()}
    images.check_fit(FIT, payloads)
    images.check_layout(FIT, payloads['kernel'], len(payloads['ramdisk']))
    tree = fdt.nodes(FIT.read_bytes())
    for name, data in payloads.items():
        require(tree[f'/images/{name}/hash-1']['value'] == hashlib.sha256(data).digest(),
                f'XR1710G FIT: bad {name} SHA256')
    check_tree(payloads['fdt'])
    check_kernel(kconfig(LINUX_OUT / '.config'), (LINUX_OUT / 'System.map').read_text())
    manifest = {'board': 'XR1710G', 'mode': 'RAM only', 'nand_enabled': False,
                'existing_shim_required': True, 'bytes': FIT.stat().st_size,
                'sha256': hashlib.sha256(FIT.read_bytes()).hexdigest(),
                'payload_sha256': {k: hashlib.sha256(v).hexdigest() for k, v in payloads.items()}}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))
    print(FIT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('kernel', 'rootfs', 'image', 'all'))
    parser.add_argument('--unit-dir', type=Path)
    parser.add_argument('--authorized-key', type=Path)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.step in ('kernel', 'all'):
        kernel()
    if args.step in ('rootfs', 'all'):
        root_image()
    if args.step in ('image', 'all'):
        package(args.unit_dir, args.authorized_key)
    return 0


if __name__ == '__main__':
    main_guard(main)
