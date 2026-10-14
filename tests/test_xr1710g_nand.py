# SPDX-License-Identifier: MIT
"""Reject damaged input and incompatible boot selections before NAND packaging."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('xr1710g_nand', ROOT / 'platform/xr1710g/nand.py')
nand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nand)


@unittest.skipUnless(nand.xr.FIT.is_file(), 'requires the compiled XR1710G RAM FIT')
class InputGuards(unittest.TestCase):
    def setUp(self):
        self.data = nand.xr.FIT.read_bytes()
        self.tree = nand.xr.fdt.nodes(self.data)

    def test_verified_payloads_are_retained(self):
        payloads = nand.source_payloads(self.data)
        self.assertEqual(payloads['kernel'], nand.xr.KERNEL_IMAGE.read_bytes())
        self.assertEqual(payloads['fdt'], nand.xr.DTB.read_bytes())

    def test_bad_hash_is_rejected(self):
        self.tree['/images/kernel/hash-1']['value'] = bytes(32)
        with mock.patch.object(nand.xr.fdt, 'nodes', return_value=self.tree):
            with self.assertRaisesRegex(Exception, 'invalid kernel SHA256'):
                nand.source_payloads(self.data)

    def test_wrong_ramdisk_selection_is_rejected(self):
        self.tree['/configurations/conf-sdr']['ramdisk'] = b'rootfs\0'
        with mock.patch.object(nand.xr.fdt, 'nodes', return_value=self.tree):
            with self.assertRaisesRegex(Exception, 'image selection'):
                nand.source_payloads(self.data)
