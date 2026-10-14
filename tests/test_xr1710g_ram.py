# SPDX-License-Identifier: MIT
"""Reject a RAM trial that could expose NAND, or miswire a high-speed port."""
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('xr1710g_builder', ROOT / 'platform/xr1710g/build.py')
xr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xr)


class KernelGuard(unittest.TestCase):
    def setUp(self):
        self.symbols = '\n'.join('ffff0000 T ' + name for name in
                                ('airoha_probe', 'mtk_pcie_probe', 'rtl826x_probe',
                                 'rtl8261x_config_init', '_fw_rtl8261n_bin_bin',
                                 '_fw_rtl_nic_rtl8261c_bin_bin'))

    def test_flash_enabled_or_linked_is_rejected(self):
        xr.check_kernel({}, self.symbols)
        for name in xr.NO_FLASH:
            with self.subTest(config=name), self.assertRaisesRegex(Exception, 'disabled'):
                xr.check_kernel({name: 'y'}, self.symbols)
        for name in xr.UNLINKED:
            with self.subTest(symbol=name), self.assertRaisesRegex(Exception, 'flash driver'):
                xr.check_kernel({}, self.symbols + '\nffff0000 T ' + name)


@unittest.skipUnless(xr.DTB.is_file(), 'requires the compiled XR1710G DTB')
class CompiledBoard(unittest.TestCase):
    def setUp(self):
        self.tree = xr.fdt.nodes(xr.DTB.read_bytes())

    def node(self, label):
        return self.tree[xr.fdt.string(self.tree['/__symbols__'][label])]

    def check_modified(self):
        with mock.patch.object(xr.fdt, 'nodes', return_value=self.tree):
            xr.check_tree(b'')

    def test_actual_board_passes(self):
        xr.check_tree(xr.DTB.read_bytes())

    def test_enabled_flash_is_rejected(self):
        self.node('snfi')['status'] = b'okay\0'
        with self.assertRaisesRegex(Exception, 'snfi'):
            self.check_modified()

    def test_wrong_phy_reset_is_rejected(self):
        gpio = self.node('phy5')['reset-gpios']
        self.node('phy5')['reset-gpios'] = gpio[:4] + struct.pack('>II', 31, 1)
        with self.assertRaisesRegex(Exception, 'PHY/reset'):
            self.check_modified()

    def test_flash_bootargs_are_rejected(self):
        self.tree['/chosen']['bootargs'] = xr.BOOTARGS.encode() + b' ubi.mtd=system\0'
        with self.assertRaisesRegex(Exception, 'boot arguments'):
            self.check_modified()
