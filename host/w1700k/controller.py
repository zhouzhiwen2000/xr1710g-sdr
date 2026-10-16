# SPDX-License-Identifier: MIT
"""The receiver state machine: load, configure, start, stop and RF control, and `w1700k serve`.

The MT7996 runs only our firmware (firmware/: our ROM patch, WM and WA). A load downloads it,
hands it the unit's calibration EEPROM and has it bring the radio up from cold (BRINGUP), then
sets each band up at its LO and sample rate (FILTER, SETUP, CAPTURE); the captured band is the one
ICAP listens to (SELECT). There are no channels: a band is an LO in Hz (anywhere its synthesizer
locks), a sample rate and an analog filter, each changed on its own. Streams are one STREAM command each: our WM runs the producer it carries
until the pump's stop word. Every operation goes to our firmware as a command (wm.py, rf.py); the
host writes no radio register.

The radio's state is kept on the unit (STATE_FILE, in RAM: a reboot clears it, and the boot id
is checked), so separate CLI invocations and a `serve` session see the same receiver. Streams
run detached on the unit; a controller attaches to one that is already running.

Rules the hardware imposes:
- RF programs (tune, gain, filter, band switch) run in our WM between stream chunks, or in its
  main loop without a stream.
- The LO is one TUNE, also mid-stream. A new sample rate on the band being streamed stops the stream,
  runs RATE and restarts it; switching to another band is one ICAPCR8 write mid-stream.
- A stream's packing follows its sample rate (packing_for): raw 14-bit IQ up to 160 Msps, half
  above, where raw exceeds one 10 GbE link. Half is lossless (8 samples in 7 words), so it is not a
  setting; a band switch keeps a running half stream as it is, since half carries any rate.
"""
import json
import queue
import secrets
import sys
import threading
import time

from . import config, firmware, rf, wm
from . import dsp_nco as nco_control
from .config import local_mac_for
from dataclasses import replace

from .radio import DEFAULT_BANDS, DEFAULT_FILTER_MHZ, LO_RANGE, BandConfig, Radio, actual_lo, eeprom_image
from .stream import GO_ADDR, Result, Stream, StreamError
from .unit import Unit

STATE_FILE = '/tmp/w1700k-state.json'
BANDS = (0, 1, 2)
DEFAULT_BAND = 1
PACKINGS = ('raw', 'half', 'dsp')
RAW_MAX_RATE = 160_000_000        # raw 14-bit IQ above this exceeds one 10 GbE link: packed half
START_TIMEOUT = 10.0
COMMAND_TIMEOUT = 60.0


class ControlError(RuntimeError):
    pass


LOCK_RESOLUTION_HZ = 100_000            # the lock-edge search's resolution
LOCK_NEAR_HZ = 20_000_000               # the edges lie this close to TUNE's reach: tried first
LOCK_MARGIN_HZ = 1_000_000              # the span kept inside the edges found (they wander ~0.1 MHz)


def packing_for(rate):
    return 'half' if rate > RAW_MAX_RATE else 'raw'


def packing_allows(packing, rate):
    if packing == 'dsp':
        return rate in nco_control.ALLOWED_RATES
    return packing == 'half' or rate <= RAW_MAX_RATE


def default_rf(cfg, filter_mhz=DEFAULT_FILTER_MHZ):
    """A band's RF state after its SETUP. The analog filter is its own setting, independent of the
    sample rate: it is kept across LO and sample-rate changes."""
    return {'lo_hz': cfg.lo_hz, 'agc': True, 'lna': None, 'lpf': None, 'filter_mhz': filter_mhz}


class Controller:
    def __init__(self, config, emit=None, unit=None):
        self.config = config
        self.emit = emit or (lambda event: None)
        self.unit = unit or Unit(config.unit)
        self.radio = Radio(self.unit)
        self.stream = Stream(self.unit)
        self._ctl = None
        self.boot_id = None
        self.loaded = False
        self.dsp_loaded = False
        self.nco_hz = config.dsp_nco.frequency_hz
        self.bands = dict(DEFAULT_BANDS)
        self.bands_ready = []
        self.band = DEFAULT_BAND
        self.packing = None               # the running stream's packing; None: no stream
        self.rf = {b: default_rf(cfg) for b, cfg in self.bands.items()}
        self.synth_locked = [None] * len(BANDS)
        self.lo_lock = {}                 # band -> (lowest, highest) LO its synthesizer locks at, found at load
        self.busy = None
        self.error = None
        self.snapshot = self.state()

    # --- state ---

    def state(self):
        cfg, rfs = self.bands[self.band], self.rf[self.band]
        return {
            'busy': self.busy,
            'radio': {'loaded': self.loaded, 'band': self.band, 'rf_band': cfg.rf_band, 'lo_hz': cfg.lo_hz,
                      'sample_rate': cfg.sample_rate, 'capture_rate': cfg.capture_rate,
                      'bands_ready': sorted(self.bands_ready),
                      'synth_locked': list(self.synth_locked)},
            'stream': {'running': self.packing is not None, 'packing': self.packing,
                       'sample_rate': cfg.sample_rate, 'center_hz': rfs['lo_hz']},
            'rf': dict(rfs, lo_range_hz=list(self.lo_lock.get(self.band, LO_RANGE[self.band]))),
            'error': self.error,
            'dsp_nco': {'enabled': self.packing == 'dsp', 'loaded': self.dsp_loaded,
                        'frequency_hz': self.nco_hz},
        }

    def _changed(self):
        self.snapshot = self.state()
        self.emit({'event': 'state', 'state': self.snapshot})

    def _log(self, level, text):
        self.emit({'event': 'log', 'level': level, 'text': text})

    def _progress(self, text, fraction):
        self.emit({'event': 'progress', 'text': text, 'fraction': round(fraction, 3)})

    def _save(self):
        record = {'boot_id': self.boot_id, 'loaded': self.loaded, 'band': self.band,
                  'lo_lock': {str(b): list(v) for b, v in self.lo_lock.items()},
                  'bands': {str(b): c.as_dict() for b, c in self.bands.items()},
                  'bands_ready': self.bands_ready, 'packing': self.packing,
                  'rf': {str(b): v for b, v in self.rf.items()}, 'synth_locked': self.synth_locked,
                  'dsp_loaded': self.dsp_loaded, 'nco_hz': self.nco_hz}
        self.unit.push(json.dumps(record).encode(), STATE_FILE)

    def attach(self):
        """Adopt the receiver state the unit holds for this boot, and a stream already running."""
        text = self.unit.check(f'cat /proc/sys/kernel/random/boot_id; cat {STATE_FILE} 2>/dev/null; true')
        boot_id, _, saved = text.partition('\n')
        self.boot_id = boot_id.strip()
        try:
            record = json.loads(saved) if saved.strip() else None
            if record and record.get('boot_id') == self.boot_id:
                self._restore(record)
        except (ValueError, KeyError, TypeError):
            self._log('warn', 'ignoring an unreadable receiver state on the unit')
        self.poll()
        self._changed()

    def _restore(self, record):
        bands = {int(b): BandConfig(int(b), **c).validate() for b, c in record['bands'].items()}
        rfs = {int(b): dict(v) for b, v in record['rf'].items()}
        if (sorted(bands) != list(BANDS) or sorted(rfs) != list(BANDS) or record['band'] not in BANDS
                or record['packing'] not in (None, *PACKINGS)
                or any(set(v) != set(default_rf(bands[b])) for b, v in rfs.items())):
            raise ValueError('inconsistent state')
        self.bands, self.band = bands, record['band']
        self.loaded, self.bands_ready = bool(record['loaded']), list(record['bands_ready'])
        self.packing = record['packing']
        self.dsp_loaded = bool(record.get('dsp_loaded', False))
        self.nco_hz = record.get('nco_hz', self.config.dsp_nco.frequency_hz)
        self.rf = rfs
        self.lo_lock = {int(b): tuple(v) for b, v in record.get('lo_lock', {}).items()}
        locks = record.get('synth_locked', [None] * len(BANDS))
        if len(locks) == len(BANDS) and all(v in (None, True, False) for v in locks):
            self.synth_locked = list(locks)

    # --- operations ---

    def run(self, busy, operation, *args):
        """Run one operation with the busy flag set and errors recorded in the state."""
        self.busy, self.error = busy, None
        self._changed()
        try:
            return operation(*args)
        except Exception as exc:
            self.error = str(exc)
            self._log('error', self.error)
            raise
        finally:
            self.busy = None
            self._changed()

    def load(self, band=None):
        """Download our firmware and bring the radio up from cold, every band at its LO and sample rate."""
        return self.run('loading', self._load, band, self.config.dsp_nco.enabled)

    def configure(self, band, sample_rate=None, lo_hz=None, capture_rate=None):
        """Capture `band`, optionally at another sample rate (RATE) and LO (TUNE); what is not given
        stays. A sample rate alone is captured at that rate; band 2's 640/2 is 320 Msps captured
        at 640 (radio.RATE_CODE)."""
        if band not in self.bands:
            raise ValueError('band 0, 1 or 2')
        cur = self.bands[band]
        if sample_rate is None:
            if capture_rate is not None:
                raise ValueError('a capture rate needs its sample rate')
            sample_rate, capture_rate = cur.sample_rate, cur.capture_rate
        cfg = BandConfig(band, cur.lo_hz if lo_hz is None else int(lo_hz), sample_rate, capture_rate).validate()
        if self.packing == 'dsp' and cfg.sample_rate not in nco_control.ALLOWED_RATES:
            raise ValueError('DSP NCO supports 40/80 MS/s; stop and disable it before selecting a higher rate')
        self._check_lock_range(band, cfg.lo_hz)
        return self.run('configuring', self._configure, cfg)

    def start(self, dsp_nco=None):
        return self.run('starting', self._start, dsp_nco)

    def nco(self, hz):
        """Apply a baseband frequency shift at a block boundary without flushing IQ."""
        if self.packing != 'dsp':
            raise ControlError('start the DSP NCO path before changing its frequency')
        result = nco_control.set_frequency(self._session(), hz, self.bands[self.band].sample_rate)
        self.nco_hz = result['frequency_hz']
        self._save()
        self._changed()
        return result

    def stop(self):
        return self.run('stopping', self._stop)

    def tune(self, hz):
        return self.run(None, self._tune, int(hz))

    def gain(self, agc=False, lna=None, lpf=None):
        return self.run(None, self._gain, agc, lna, lpf)

    def filter(self, mhz):
        return self.run(None, self._filter, mhz)

    def trim(self, code):
        self._require_loaded()
        return rf.set_trim(self._session(), self.band, code)

    def rf_status(self):
        self._require_loaded()
        out = rf.status(self._session(), self.band)
        self._check_locks()
        self._changed()
        return out

    def poll(self):
        """Notice a stream that ended on its own (overrun, lost source, killed pump)."""
        if self.packing and self.stream.finished():
            result = self._finish_stream()
            self.error = f'the stream ended: {result.text}'
            self._log('error', self.error)
            self._changed()

    def close(self):
        self._close_ctl()
        self.unit.close()

    # --- implementation ---

    def _require_loaded(self):
        if not self.loaded:
            raise ControlError('the radio is not loaded')

    def _tune(self, hz):
        self._require_loaded()
        self._check_lock_range(self.band, hz)
        self._tune_band(self.band, hz)

    def _check_lock_range(self, band, hz):
        span = self.lo_lock.get(band)
        if span and not span[0] <= hz <= span[1]:
            raise ValueError(f'band {band} locks {span[0] / 1e6:.1f}-{span[1] / 1e6:.1f} MHz on this unit')

    def _locks_at(self, band, hz):
        try:
            rf.tune(self._session(), band, hz)
            return True
        except rf.SynthUnlocked:
            return False

    def _probe_lock(self, band):
        """Band's lockable LO span on this unit: from its LO (locked) towards each end of TUNE's reach,
        bisected on TUNE's lock result to LOCK_RESOLUTION_HZ, LOCK_MARGIN_HZ inside the edges found
        (they differ from die to die by tens of MHz); the band is retuned to its LO afterwards. A
        band whose LO does not lock keeps TUNE's reach."""
        home = self.bands[band].lo_hz
        if not self._locks_at(band, home):
            return tuple(LO_RANGE[band])
        edges = []
        for bound in LO_RANGE[band]:
            inward = 1 if bound < home else -1
            if self._locks_at(band, bound):
                edges.append(bound)
                continue
            near = bound + inward * LOCK_NEAR_HZ
            good = near if self._locks_at(band, near) else home
            bad = bound
            while abs(bad - good) > LOCK_RESOLUTION_HZ:
                mid = (good + bad) // 2
                if self._locks_at(band, mid):
                    good = mid
                else:
                    bad = mid
            edges.append(good + inward * LOCK_MARGIN_HZ)
        self._locks_at(band, home)
        return tuple(edges)

    def _tune_band(self, b, hz):
        """Band b's LO (TUNE), mid-stream too; the LO realised is the band's from now on."""
        try:
            actual = rf.tune(self._session(), b, hz)
        except rf.SynthUnlocked:
            self.synth_locked[b] = False
            self._save()
            raise
        self.rf[b]['lo_hz'] = actual
        self.bands[b] = replace(self.bands[b], lo_hz=actual)
        self.synth_locked[b] = True
        self._save()

    def _check_locks(self):
        """Every band's synthesizer lock; an unlocked band is an error that only a power cycle
        clears, not a reason to reload."""
        self.synth_locked = rf.synth_locks(self._session())
        self._save()
        unlocked = [b for b, ok in enumerate(self.synth_locked) if not ok]
        if unlocked:
            self.error = str(rf.SynthUnlocked(unlocked[0]))
            self._log('error', '; '.join(str(rf.SynthUnlocked(b)) for b in unlocked))

    def _gain(self, agc, lna, lpf):
        self._require_loaded()
        if agc:
            lna = lpf = None
        elif lna is None or lpf is None:
            raise ValueError('gain: agc, or both lna and lpf')
        rf.set_gain(self._session(), self.band, lna, lpf)
        self.rf[self.band].update(agc=bool(agc), lna=lna, lpf=lpf)
        self._save()

    def _filter(self, mhz):
        self._require_loaded()
        if mhz not in rf.FILTER_CODE:
            raise ValueError(f'filter: one of {", ".join(map(str, rf.FILTER_CODE))} MHz')
        rf.set_filter(self._session(), self.band, mhz)
        self.rf[self.band]['filter_mhz'] = mhz
        self._save()

    def _command(self, op, *args, timeout=COMMAND_TIMEOUT):
        """One radio command on our WM; raises ControlError with the firmware's reason."""
        res = self._wm().radio(op, *args, timeout=timeout)
        if res['res']:
            reason = wm.RADIO_RESULTS.get(res['res'], f'result {res["res"]}')
            if res['fails']:
                reason += f' ({wm.RADIO_FAILS.get(res["fail_code"], "failure")} {res["fail_arg"]:#x})'
            raise ControlError(f'{op}: {reason}')
        return res

    def _filter_of(self, band):
        return self.rf.get(band, {}).get('filter_mhz', DEFAULT_FILTER_MHZ)

    def _set_band(self, cfg):
        """A band at its LO and sample rate (SETUP: everything that follows from them, the synthesizer,
        the DC-offset calibration at the band's filter) and its capture path. The analog filter is
        not part of it (FILTER, set at load). The band keeps the LO the synthesizer realises."""
        self._command('setup', cfg.band, cfg.lo_hz & 0xFFFFFFFF, cfg.lo_hz >> 32, cfg.rate_code)
        self._command('capture', cfg.band, cfg.rate_code)
        self.bands[cfg.band] = replace(cfg, lo_hz=actual_lo(cfg.band, cfg.lo_hz))

    def _set_rate(self, cfg):
        """A band's sample rate alone (RATE: no synthesizer, no DCOC; LO and filter stay), then its
        capture path."""
        self._command('rate', cfg.band, cfg.rate_code)
        self._command('capture', cfg.band, cfg.rate_code)

    def _load(self, band=None, dsp_nco=False):
        if band is not None:
            if band not in BANDS:
                raise ValueError('band 0, 1 or 2')
            self.band = band
        if self.packing:
            self._stop()
        self._close_ctl()
        fw = firmware.load(self.config.firmware_dir, dsp_nco=dsp_nco)
        eeprom = self._eeprom()
        self.loaded, self.bands_ready, self.lo_lock = False, [], {}
        self.dsp_loaded = False
        self._save()
        self._progress('loading our firmware', 0.0)
        wm.load(self.radio, fw, self.config.unit.module)
        w = self._wm()
        w.wait_running()
        self._progress('bringing the radio up', 0.3)
        w.put_eeprom(eeprom)
        self._command('bringup')
        for i, b in enumerate([b for b in BANDS if b != self.band] + [self.band]):
            self._progress(f'band {b}', 0.5 + 0.15 * i)
            rf.set_filter(self._session(), b, self._filter_of(b))   # before SETUP's DCOC
            self._set_band(self.bands[b])
            self.bands_ready.append(b)
        self._progress('lock ranges', 0.95)
        self.lo_lock = {b: self._probe_lock(b) for b in BANDS}
        self._command('select', self.band)
        self.loaded = True
        self.dsp_loaded = dsp_nco
        self.rf = {b: default_rf(cfg, self._filter_of(b)) for b, cfg in self.bands.items()}
        self.synth_locked = rf.synth_locks(self._session())
        self._save()
        self._progress('ready', 1.0)

    def _wm(self):
        return wm.Wm(self.unit, session=self._session())

    def _eeprom(self):
        try:
            path = config.eeprom_path(self.config)
            return eeprom_image(path.read_bytes())
        except config.ConfigError as exc:
            raise ControlError(str(exc))
        except OSError as exc:
            raise ControlError(f'cannot read the calibration EEPROM {path}: {exc.strerror}')

    def _configure(self, cfg):
        b = cfg.band
        running = self.packing
        if not self.loaded:
            self.bands[b], self.band = cfg, b
            self._load(dsp_nco=self.config.dsp_nco.enabled)
            return
        old = self.bands[b]
        if cfg.rate_code == old.rate_code and b in self.bands_ready:
            # the band's rate unchanged: switch the capture (mid-stream when the running packing
            # carries that rate, else restarted with the packing it needs), then the LO alone if it
            # moved
            if b != self.band:
                if running and running != 'dsp' and packing_allows(running, cfg.sample_rate):
                    self._switch_mid_stream(b)
                else:
                    if running:
                        self._stop()
                    self._command('select', b)
                    self.band = b
                    self._save()
                    if running:
                        self._start(dsp_nco=(running == 'dsp'))
            if cfg.lo_hz != old.lo_hz:
                self._tune_band(b, cfg.lo_hz)
            return
        # another sample rate: RATE (no synthesizer: the LO, gain and filter stay), then the LO
        if running:
            self._stop()
        self.band = b
        if b in self.bands_ready:                 # not ready until its capture is set again
            self.bands_ready.remove(b)
            self._save()
        self._set_rate(replace(cfg, lo_hz=old.lo_hz))
        self.bands[b] = replace(old, sample_rate=cfg.sample_rate, capture_rate=cfg.capture_rate)
        self.bands_ready.append(b)
        self._command('select', b)
        self._save()
        if cfg.lo_hz != old.lo_hz:
            self._tune_band(b, cfg.lo_hz)
        if running:
            self._start(dsp_nco=(running == 'dsp'))

    def _switch_mid_stream(self, band):
        before, after = rf.select_band(self._session(), band)
        self.band = band
        self._save()
        self._log('info', f'capturing band {band} from ICAP0 write pointer {before:#x}..{after:#x}')
        self._check_locks()

    def _start(self, dsp_nco=None):
        enabled = self.config.dsp_nco.enabled if dsp_nco is None else dsp_nco
        if type(enabled) is not bool:
            raise ValueError('dsp_nco must be boolean')
        if self.packing:
            if dsp_nco is not None and enabled != (self.packing == 'dsp'):
                raise ControlError('stop reception before switching the DSP NCO path')
            return
        rate = self.bands[self.band].sample_rate
        if enabled and rate not in nco_control.ALLOWED_RATES:
            raise ValueError('DSP NCO supports 40/80 MS/s')
        packing = 'dsp' if enabled else packing_for(rate)
        step = nco_control.phase_step(self.config.dsp_nco.frequency_hz, rate) if enabled else 0
        if not self.loaded or (enabled and not self.dsp_loaded):
            self._load(dsp_nco=enabled)
        export = self.config.export
        dst_mac = export.dst_mac or local_mac_for(export.dst_ip)
        self.stream.check_interface(export.interface)
        if enabled:
            self.stream.install_own(self._wm(), packing, step, secrets.randbits(32) or 1)
            self.nco_hz = nco_control.frequency(step, rate)
        else:
            self.stream.install_own(self._wm(), packing)
        self.stream.launch(export, dst_mac)
        self.packing = packing
        self._save()
        try:
            self._wait_producer()
        except Exception:
            self._abort_start()
            raise
        self._check_locks()

    def _wait_producer(self):
        """Running once the pump serves the mailbox and the producer has taken the go word."""
        session = self._session()
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            try:
                values, via_pump = session.ctl(read=(GO_ADDR, 1))
            except rf.CtlTimeout:
                values, via_pump = [None], False          # the stream is still taking the device
            if via_pump and values[0] == 0:
                if self.packing != 'dsp' or nco_control.read(session)[nco_control.RUNNING]:
                    return
            if self.stream.finished():
                raise StreamError('the stream ended at start')
            time.sleep(0.2)
        raise StreamError('the WM producer did not take the go word')

    def _abort_start(self):
        self.stream.signal_stop()
        try:
            self.stream.wait_finished()
            result = self._finish_stream()
            self._log('error', f'stream start failed: {result.text}')
        except StreamError as exc:
            self._log('error', str(exc))
            self.packing = None
            self._save()

    def _stop(self):
        if not self.packing:
            return None
        self.stream.signal_stop()
        self.stream.wait_finished()
        result = self._finish_stream()
        level = 'info' if result.ok else 'warn'
        self._log(level, f'stream {result.text}: {result.chunks} chunks in {result.seconds} s')
        return result

    def _finish_stream(self):
        packing = self.packing
        try:
            result = self.stream.collect_own(self._wm(), packing)
        except (StreamError, wm.WmError) as exc:
            self._log('warn', f'stream clean-up: {exc}')
            result = Result(0, 0, 0.0, False, None)
        self.packing = None
        if packing == 'dsp' and not result.ok:
            self.loaded = self.dsp_loaded = False
            self.bands_ready = []
        self._save()
        return result

    def _session(self):
        if self._ctl is None or self._ctl.process.poll() is not None:
            self._close_ctl()
            self._ctl = rf.CtlSession(self.unit, own_wm=True)
        return self._ctl

    def _close_ctl(self):
        if self._ctl is not None:
            self._ctl.close()
            self._ctl = None


# --- w1700k serve ---

def _require(args, key, kind, optional=False):
    value = args.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise ValueError(f'{key}: expected {getattr(kind, "__name__", kind)}')
    return value


def handle(controller, request):
    """Run one request; the reply fields beyond id/ok."""
    op = request.get('op')
    if op == 'status':
        return {}
    if op == 'load':
        controller.load(_require(request, 'band', int, optional=True))
    elif op == 'configure':
        controller.configure(_require(request, 'band', int), _require(request, 'sample_rate', int, True),
                             _require(request, 'lo_hz', (int, float), True),
                             _require(request, 'capture_rate', int, True))
    elif op == 'start':
        controller.start(_require(request, 'dsp_nco', bool, True))
    elif op == 'stop':
        controller.stop()
    elif op == 'tune':
        controller.tune(_require(request, 'hz', (int, float)))
    elif op == 'gain':
        controller.gain(bool(request.get('agc')), _require(request, 'lna', int, True), _require(request, 'lpf', int, True))
    elif op == 'filter':
        controller.filter(_require(request, 'mhz', int))
    elif op == 'nco':
        return controller.nco(_require(request, 'hz', (int, float)))
    else:
        raise ValueError(f'unknown op {op!r}')
    return {}


def serve(controller, stdin=None, stdout=None, poll_seconds=2.0):
    """Newline-delimited JSON requests on stdin, one reply each, events in between (see README)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    out_lock = threading.Lock()

    def send(obj):
        with out_lock:
            stdout.write(json.dumps(obj) + '\n')
            stdout.flush()

    controller.emit = send
    requests = queue.Queue()

    def worker():
        try:
            controller.attach()
        except Exception as exc:
            send({'event': 'log', 'level': 'error', 'text': f'cannot reach the unit: {exc}'})
        while True:
            try:
                item = requests.get(timeout=poll_seconds)
            except queue.Empty:
                try:
                    controller.poll()
                except Exception as exc:
                    send({'event': 'log', 'level': 'warn', 'text': f'stream check failed: {exc}'})
                continue
            if item is None:
                return
            try:
                extra = handle(controller, item)
                send({'id': item['id'], 'ok': True, **extra, 'state': controller.snapshot})
            except Exception as exc:
                send({'id': item['id'], 'ok': False, 'error': str(exc)})

    thread = threading.Thread(target=worker, name='w1700k-controller', daemon=True)
    thread.start()
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get('id'), int):
                raise ValueError('a request is an object with an integer id')
        except ValueError as exc:
            send({'event': 'log', 'level': 'error', 'text': f'bad request: {exc}'})
            continue
        if request.get('op') == 'status':
            send({'id': request['id'], 'ok': True, 'state': controller.snapshot})
        else:
            requests.put(request)
    requests.put(None)
    thread.join()
    controller.close()
