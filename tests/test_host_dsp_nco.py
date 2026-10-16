# SPDX-License-Identifier: MIT
"""Optional DSP path selection, control lifetime, quantization and UE protocol."""
import io
import json
import unittest
from dataclasses import replace
from unittest import mock

import host_fake
from w1700k import config, dsp_nco as nco, firmware, radio
from w1700k.controller import Controller, ControlError, handle


class Configuration(unittest.TestCase):
    def test_default_off_and_validation(self):
        self.assertEqual(config.parse({}).dsp_nco, config.DspNcoConfig())
        self.assertEqual(config.parse({'dsp_nco': {'enabled': True, 'frequency_hz': -123}}).dsp_nco,
                         config.DspNcoConfig(True, -123.0))
        for value in [None, [], {'enabled': 1}, {'frequency_hz': True}, {'frequency_hz': float('nan')},
                      {'frequency_hz': float('inf')}, {'unknown': 0}]:
            with self.assertRaises(config.ConfigError): config.parse({'dsp_nco': value})

    def test_grid_and_sign(self):
        for rate in nco.ALLOWED_RATES:
            for hz in (0, 1, -1, 123.456, -100000, 100000):
                self.assertLessEqual(abs(nco.frequency(nco.phase_step(hz, rate), rate) - hz), rate / 2**33)
            for hz in (float('nan'), float('inf'), rate/2, -rate/2, True):
                with self.assertRaises(ValueError): nco.phase_step(hz, rate)

    def test_optional_manifest(self):
        with host_fake.temp_dir() as tmp:
            cfg=host_fake.make_config(tmp)
            manifest=json.loads((cfg.firmware_dir/'firmware.json').read_text())
            with self.assertRaisesRegex(firmware.FirmwareError, 'no DSP NCO'): firmware.parse(manifest,tmp,True)
            entry=dict(manifest['containers'][1],role='dsp',flags=['--dsp'])
            manifest['dsp_nco']=entry
            self.assertEqual([c.role for c in firmware.parse(manifest,tmp).containers], ['patch','wm','wa'])
            self.assertEqual([c.role for c in firmware.parse(manifest,tmp,True).containers], ['patch','wm','dsp','wa'])
            entry['flags']=[]
            with self.assertRaises(firmware.FirmwareError):firmware.parse(manifest,tmp,True)


class Selection(unittest.TestCase):
    def setUp(self):
        self.tmp=host_fake.temp_dir();self.cfg=host_fake.make_config(self.tmp.name)
        self.unit=host_fake.FakeUnit();self.c=Controller(self.cfg,unit=self.unit);self.c.attach()
    def tearDown(self): self.c.close();self.tmp.cleanup()

    def test_rate_rejection_does_not_touch_hardware(self):
        before=list(self.unit.log)
        with self.assertRaisesRegex(ValueError,'40/80'):self.c.start(True)
        self.assertEqual(self.unit.log,before)
        self.assertFalse(self.c.loaded)

    def test_switch_requires_stop_and_existing_path_works(self):
        self.c.configure(1,sample_rate=40_000_000);self.c.start(False)
        self.assertEqual(self.c.packing,'raw')
        with self.assertRaisesRegex(ControlError,'stop reception'):self.c.start(True)
        self.assertEqual(self.c.packing,'raw');self.c.stop()

    def test_firmware_selection_and_start_arguments(self):
        self.c.bands[1]=radio.BandConfig(1,5_250_000_000,80_000_000).validate()
        self.c.config=replace(self.cfg,dsp_nco=config.DspNcoConfig(True,-1500))
        with mock.patch.object(self.c,'_load') as load, mock.patch.object(self.c.stream,'install_own') as install, \
                mock.patch.object(self.c.stream,'launch'), mock.patch.object(self.c,'_wait_producer'), \
                mock.patch.object(self.c,'_check_locks'):
            self.c.start()
        load.assert_called_once_with(dsp_nco=True)
        args=install.call_args.args
        self.assertEqual(args[1:3],('dsp',nco.phase_step(-1500,80_000_000)))
        self.assertNotEqual(args[3],0)
        self.assertEqual(self.c.packing,'dsp')
        with self.assertRaisesRegex(ValueError,'40/80'):self.c.configure(1,sample_rate=160_000_000)
        self.c.packing=None


class Session:
    def __init__(self, ack=True):
        self.words=[0]*19;self.writes=[];self.auto_ack=ack
        self.words[0]=nco.MAGIC;self.words[nco.RUN]=123;self.words[nco.RUNNING]=1
        self.words[nco.NOW]=0xffff0000
    def ctl(self,writes=(),read=None):
        self.writes.extend(writes)
        for address,value in writes:self.words[(address-nco.BASE)//4]=value
        if writes and self.auto_ack:
            self.words[nco.APPLIED]=self.words[nco.STEP];self.words[nco.ACK]=self.words[nco.REQ]
            self.words[nco.BOUNDARY_LO]=2048;self.words[nco.BOUNDARY_HI]=1;self.words[nco.PHASE]=0x12345678
        return list(self.words),True


class Mailbox(unittest.TestCase):
    def test_publish_request_last_and_report_sample_boundary(self):
        s=Session();r=nco.set_frequency(s,-1500,80_000_000,123)
        self.assertEqual([a for a,_ in s.writes], [nco.BASE+4*i for i in (nco.TARGET,nco.STEP,nco.DEADLINE,nco.REQ)])
        self.assertEqual(s.words[nco.DEADLINE],(0xffff0000+360_000_000)&0xffffffff)
        self.assertEqual((r['sample'],r['phase']),(2**32+2048,0x12345678))
        self.assertLess(abs(r['frequency_hz']+1500),.01)

    def test_old_run_pending_and_stopped_do_not_write(self):
        for index,value,run in [(nco.RUN,124,123),(nco.REQ,1,123),(nco.RUNNING,0,123)]:
            s=Session();s.words[index]=value
            with self.assertRaises(RuntimeError):nco.set_frequency(s,0,40_000_000,run)
            self.assertEqual(s.writes,[])

    def test_timeout_and_firmware_refusal(self):
        s=Session(False)
        with self.assertRaisesRegex(RuntimeError,'timed out'):nco.set_frequency(s,1,40_000_000,timeout=0)
        s=Session();s.words[nco.STATUS]=3
        with self.assertRaisesRegex(RuntimeError,'status 3'):nco.set_frequency(s,1,40_000_000)

    def test_sequence_wrap(self):
        s=Session();s.words[nco.REQ]=s.words[nco.ACK]=0xffffffff
        nco.set_frequency(s,0,40_000_000)
        self.assertEqual(s.words[nco.REQ],0)


class Protocol(unittest.TestCase):
    def control(self):
        c=mock.Mock();c.step=80_000_000/2**32;c.set_offset.side_effect=lambda v:v
        return c
    def test_ordered_protocol_and_close(self):
        c=self.control();out=io.StringIO()
        self.assertEqual(nco.serve(lambda hz,sr:c,io.StringIO('OPEN 1 5250000000 80000000\nSET 2 -123\nCLOSE 3\n'),out),0)
        self.assertEqual([l.split()[:3] for l in out.getvalue().splitlines()],
                         [['OK','1','0'],['OK','2','-123'],['OK','3','0']])
        c.set_offset.assert_called_once_with(-123)
        self.assertTrue(c.close.called)
    def test_stale_id_never_reaches_controller(self):
        c=self.control();out=io.StringIO()
        self.assertEqual(nco.serve(lambda hz,sr:c,io.StringIO('OPEN 2 5250000000 80000000\nSET 1 1000\n'),out),1)
        c.set_offset.assert_not_called();c.close.assert_called_once()
        self.assertIn('ERR 1 stale request id',out.getvalue())
    def test_helper_reverses_lo_correction_sign(self):
        c=object.__new__(nco.BackendControl)
        c.rate=80_000_000;c.run=123;c.boot='boot';c.expected_step=0;c.session=Session()
        c._state=lambda:('boot',{});c._check=lambda saved:None
        hz=c.set_offset(1234)
        self.assertGreater(hz,0);self.assertLess(nco.frequency(c.expected_step,c.rate),0)
        c.session.words[nco.APPLIED]=999
        with self.assertRaisesRegex(RuntimeError,'another controller'):c.set_offset(1000)

if __name__=='__main__':unittest.main()
