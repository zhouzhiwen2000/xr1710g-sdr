# SPDX-License-Identifier: MIT
"""Known antenna/time markers catch a plausible but wrong alternating-channel decoder."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'numpy is required by the experimental dual RX decoder')
class DualRxDecodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'tools/dual_rx_probe.py'
        spec = importlib.util.spec_from_file_location('dual_rx_probe', path)
        cls.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.probe)

    def test_distinct_antennas_keep_time_order_and_signed_iq(self):
        a = np.array([[10, -10], [11, -11], [12, -12], [13, -13]], dtype='<i2')
        b = np.array([[-8192, 8191], [-101, 101], [-102, 102], [-103, 103]], dtype='<i2')
        wire = np.concatenate((a[:2], b[:2], a[2:], b[2:]))
        got_a, got_b = self.probe.split_paths(wire)
        np.testing.assert_array_equal(got_a, a)
        np.testing.assert_array_equal(got_b, b)

    def test_rejects_partial_beats(self):
        with self.assertRaises(ValueError):
            self.probe.split_paths(np.zeros((6, 2), dtype='<i2'))

    def test_zeroing_second_path_does_not_zero_first(self):
        wire = np.array([[1, -1], [2, -2], [0, 0], [0, 0]], dtype='<i2')
        a, b = self.probe.split_paths(wire)
        self.assertEqual(np.count_nonzero(a), 4)
        self.assertEqual(np.count_nonzero(b), 0)

    def test_rejecting_active_receiver_keeps_existing_stream_running(self):
        from w1700k.config import Config
        controller = mock.Mock(loaded=True, packing='half')
        with tempfile.TemporaryDirectory() as tmp:
            argv = ['dual_rx_probe.py', '--config', 'unused.json', '--out', str(Path(tmp)/'result')]
            with mock.patch.object(sys, 'argv', argv), \
                    mock.patch.object(self.probe, 'load', return_value=Config()), \
                    mock.patch.object(self.probe, 'Controller', return_value=controller), \
                    mock.patch.object(self.probe.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(RuntimeError, 'streaming stopped'):
                    self.probe.main()
        controller.stop.assert_not_called()
        controller.configure.assert_not_called()
        controller.close.assert_called_once()
        popen.assert_not_called()

    def test_rejects_udp_ranges_overlapping_normal_receiver(self):
        from w1700k.config import Config
        for port in (7693, 7707):
            with self.subTest(port=port), tempfile.TemporaryDirectory() as tmp:
                argv = ['dual_rx_probe.py', '--config', 'unused.json', '--out', str(Path(tmp)/'result'),
                        '--port', str(port)]
                with mock.patch.object(sys, 'argv', argv), \
                        mock.patch.object(self.probe, 'load', return_value=Config()), \
                        mock.patch.object(self.probe, 'Controller') as controller:
                    with self.assertRaisesRegex(ValueError, 'separate valid UDP port'):
                        self.probe.main()
                controller.assert_not_called()


if __name__ == '__main__':
    unittest.main()
