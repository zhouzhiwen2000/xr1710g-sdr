# SPDX-License-Identifier: MIT
"""Verify raw IQ orientation and corrupted-sample rejection in the viewer."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'finite-capture viewer requires NumPy')
class Spectrum(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / 'platform/xr1710g/preview.py'
        spec = importlib.util.spec_from_file_location('xr1710g_preview', source)
        cls.preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.preview)

    def test_positive_frequency_is_not_mirrored(self):
        rate = self.preview.RATE
        n = np.arange(2048 * 64)
        tone = 3_906_250
        i = np.round(4000 * np.cos(2*np.pi*tone*n/rate)).astype(np.int32)
        q = np.round(4000 * np.sin(2*np.pi*tone*n/rate)).astype(np.int32)
        words = (((i & 0x3fff) << 16) | (q & 0x3fff)).astype('<u4')
        data = self.preview.spectrum(words.tobytes(), 5_250_000_000)
        peak = data['frequency_mhz'][np.argmax(data['power'])]
        self.assertAlmostEqual(peak, 5250+tone/1e6, delta=0.08)
        self.assertEqual(len(data['waterfall']), 64)
        self.assertEqual(len(data['waterfall'][0]), 256)

    def test_invalid_spare_bits_are_rejected(self):
        data = np.zeros(2048*64, dtype='<u4')
        data[0] = 0x80000000
        with self.assertRaisesRegex(ValueError, 'invalid raw IQ'):
            self.preview.spectrum(data.tobytes(), 5_250_000_000)

    def test_short_capture_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'too short'):
            self.preview.spectrum(bytes(4096), 5_250_000_000)

    def receiver(self, reported_boot):
        old, fresh = Mock(boot_id='old-boot', packing=None), Mock(packing=None)
        old.unit.check.return_value = ('XR1710G_RAM_STORAGE_OK\n'
                                       'console=ttyS0 rdinit=/init\n' + reported_boot + '\n')
        factory = patch.object(self.preview, 'Controller', side_effect=[old, fresh])
        factory.start()
        self.addCleanup(factory.stop)
        with patch.object(self.preview, 'load', return_value=object()):
            receiver = self.preview.Receiver('test-config')
        return receiver, old, fresh

    def test_reboot_discards_loaded_radio_and_old_control_session(self):
        receiver, old, fresh = self.receiver('new-boot')
        old.loaded = True
        receiver.last = {'captured_at': 123}
        self.assertIs(receiver._attach(), fresh)
        old.close.assert_called_once()
        old.attach.assert_not_called()
        fresh.attach.assert_called_once()
        self.assertIsNone(receiver.last)

    def test_same_boot_retains_control_session(self):
        receiver, old, _ = self.receiver('old-boot')
        self.assertIs(receiver._attach(), old)
        old.close.assert_not_called()
        old.attach.assert_called_once()

    def test_storage_guard_stops_before_radio_control(self):
        receiver, old, _ = self.receiver('new-boot')
        old.unit.check.return_value = 'XR1710G_RAM_STORAGE_OK\nubi.block=0,fit\nnew-boot\n'
        with self.assertRaisesRegex(RuntimeError, 'guarded RAM root'):
            receiver._attach()
        old.attach.assert_not_called()
