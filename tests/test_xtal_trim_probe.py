# SPDX-License-Identifier: MIT
"""Guard and rollback checks for the bounded crystal-trim experiment."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('xtal_probe', Path(__file__).resolve().parents[1] / 'tools/xtal_trim_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeWm:
    def __init__(self, values=None, strap=0, version=6):
        self.values = list(values or [0x58402a2a, 0x58402a2a])
        self.strap, self.version = strap, version
        self.writes = 0
        self.closed = False
        self.session = object()

    def status(self):
        return {'version': self.version, 'traps': 0}

    def read(self, addresses):
        assert addresses == [0x700056f0]
        return [self.strap]

    def run(self, ops):
        results = []
        for op, register, mask, value in ops:
            i = probe.REGS.index(register)
            if op == probe.rf.ARMW:
                self.values[i] = (self.values[i] & ~mask) | value
                self.writes += 1
            else:
                assert op == probe.rf.AR
            results.append(self.values[i])
        return results

    def close(self):
        self.closed = True


class XtalTrimProbe(unittest.TestCase):
    def test_read_has_no_writes(self):
        w = FakeWm()
        self.assertEqual(probe.read(w), ([0x58402a2a]*2, 88))
        self.assertEqual(w.writes, 0)

    def test_unknown_access_refused(self):
        for w in (FakeWm(strap=1<<18), FakeWm(strap=None), FakeWm(version=5)):
            with self.assertRaises(RuntimeError):
                probe.read(w)
            self.assertEqual(w.writes, 0)

    def test_disagreeing_codes_refused(self):
        with self.assertRaises(RuntimeError):
            probe.read(FakeWm([0x59402a2a, 0x58402a2a]))

    def test_restore_recovers_partial_write(self):
        w = FakeWm([0x59402a2a, 0x58402a2a])
        with patch.object(probe, 'Wm', return_value=w), patch.object(probe.rf, 'synth_locks', return_value=[True]*3):
            result = probe.restore(object(), [0x58402a2a]*2)
        self.assertEqual(result['code'], 88)
        self.assertEqual(w.values, [0x58402a2a]*2)
        self.assertTrue(w.closed)

    def test_restore_keeps_other_bits_and_reports_conflict(self):
        w = FakeWm([0x59402a2b, 0x59402a2a])
        with patch.object(probe, 'Wm', return_value=w):
            with self.assertRaisesRegex(RuntimeError, 'restoration'):
                probe.restore(object(), [0x58402a2a]*2)
        self.assertEqual(w.values, [0x58402a2b, 0x58402a2a])
        self.assertTrue(w.closed)


if __name__ == '__main__':
    unittest.main()
