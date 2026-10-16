# SPDX-License-Identifier: MIT
"""Start and stop the IQ stream: ICAP0 ring capture published by our WM's producer, received by
mt7996-stream into the SoC's DMA ring and exported over 10 GbE.

Start: one STREAM command to our WM (wm.py): it arms the capture route and ICAP0, starts the WA
for packing, and runs the producer it carries, which waits for the go word. mt7996-stream then
writes the go word and pumps until signalled; whatever ends the pump, it writes the stop word,
so the producer always winds down. The stream runs detached on the unit.

Stop: SIGINT to mt7996-stream; the pump writes the stop word and drains, the producer drains TX0,
stops ICAP and writes its results; our WM releases the route and acknowledges the command, and
the host reads the results through it. Streams start and stop without a reload.
"""
import json
import shlex
import struct
import time
from dataclasses import dataclass

from . import wm as wm_mod
from .unit import DEVICE

# firmware/stream/layout.h
PRODUCER_RETURN_SECONDS = 6.0     # the producer's hold after a WA run (layout.h HOLD_CYCLES) plus margin
DONE_AT = 0x0090F000
DONE_MAGIC = 0x50524F42
GO_ADDR, GO_MAGIC, STOP_MAGIC = 0x0090F008, 0x474F474F, 0x504F5453

STREAM_SECONDS = 604800           # the pump's own limit (7 days); a stream normally ends by stop
IDLE_MS = 300                     # the pump ends once data has stopped arriving this long
DRAIN_MS = 3000
MIN_MTU = 8236                    # one 8 KiB slice per datagram plus its headers
EXPORT_CPU, PUMP_CPU = 2, 3       # the export worker and the pump each busy-poll one CPU

OUT_FILE = '/tmp/w1700k-stream.json'
DONE_FILE = '/tmp/w1700k-stream.done'

STATUS = {-1: 'no result page', 0: 'not reached', 2: 'go word never came', 3: 'overrun', 4: 'TX0 busy at start',
          5: 'firmware ring adapter out of step', 6: 'TX0 did not drain', 7: 'stopped',
          8: 'WA did not start', 9: 'WA did not exit', 10: 'WA dispatch not at rest',
          11: 'DSP fault or invalid result', 12: 'MEM_DMA fault or busy',
          13: 'DSP pipeline stalled', 14: 'DSP buffer ownership error'}
RES32 = ('status', 'cpu_end', 'tx_dma_end', 'cr9_end', 'cr0_end', 'sent_after', 'done_after', 'err_status',
         'go_wait_cycles', 'first_cr9', 'wa_status', 'pack_max_cycles', 'max_lag', 'wa_pack_max', 'wa_sp', 'wa_ra',
         'wa_exit_cycles', 'ctl_served')
RES64 = ('chunks', 'cycles', 'written', 'credit_waits', 'wa_waits', 'wm_waits', 'wm_packs', 'wm_pack_cycles',
         'wa_packs', 'wa_pack_cycles', 'wa_idle')
RESULT_WORDS = 4 + len(RES32) + 2 * len(RES64)   # the result page from DONE_AT
CORE_HZ = 720e6
CHUNK = 8192


class StreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    status: int
    chunks: int
    seconds: float
    overrun: bool
    reader: dict | None           # mt7996-stream's summary line

    @property
    def text(self):
        return STATUS.get(self.status, f'status {self.status}')

    @property
    def ok(self):
        return self.status == 7


def wa_exited(page):
    """Whether the result page says the WA left the packer (wa_status bit 8)."""
    return bool(struct.unpack_from('<I', page, 16 + 4 * RES32.index('wa_status'))[0] >> 8 & 1)


def parse_result(page, reader=None):
    """The producer's result page (DONE_AT onwards)."""
    if len(page) < 16 + 4 * (len(RES32) + 2 * len(RES64)):
        return Result(-1, 0, 0.0, False, reader)
    magic, status = struct.unpack_from('<2I', page, 0)
    if magic != DONE_MAGIC:
        return Result(0, 0, 0.0, False, reader)
    raw = struct.unpack_from(f'<{len(RES32) + 2 * len(RES64)}I', page, 16)
    res = dict(zip(RES32, raw))
    res.update((k, raw[len(RES32) + 2 * i] | raw[len(RES32) + 2 * i + 1] << 32) for i, k in enumerate(RES64))
    return Result(status, res['chunks'], round(res['cycles'] / CORE_HZ, 3), status == 3, reader)


def stream_argv(tool, export, dst_mac):
    """The pump."""
    return [tool, DEVICE, '--seconds', str(STREAM_SECONDS), '--idle-ms', str(IDLE_MS),
            '--stop', f'{GO_ADDR:#010x}', f'{STOP_MAGIC:#010x}', '--threads', '0',
            '--go', f'{GO_ADDR:#010x}', f'{GO_MAGIC:#010x}'] + [
            '--export', export.interface, export.src_ip, export.dst_ip, dst_mac, str(export.port),
            '--ports', str(export.ports), '--drain-ms', str(DRAIN_MS),
            '--export-cpu', str(EXPORT_CPU), '--cpu', str(PUMP_CPU)]


class Stream:
    def __init__(self, unit):
        self.unit = unit

    def install_own(self, w, packing, step=0, run=0):
        """Our own firmware: one STREAM command; our WM arms the route and ICAP0, starts the WA for
        packing, and runs the producer it carries, which waits for the pump's go word. The command
        is acknowledged when the stream has ended (collect_own)."""
        args = (wm_mod.RADIO_PACKING[packing], step, run) if packing == 'dsp' else (wm_mod.RADIO_PACKING[packing],)
        w.radio_post('stream', *args)

    def collect_own(self, w, packing):
        """After the pump ended on our own WM: its STREAM command acknowledged (after the producer's
        hold, HOLD_CYCLES, 3 s after a WA run), then the result page through our WM."""
        text = self.unit.read(OUT_FILE, missing_ok=True).decode(errors='replace')
        reader = next((json.loads(line) for line in text.splitlines() if line.startswith('{"at":"stream"')), None)
        self.unit.run(f'rm -f {OUT_FILE} {DONE_FILE}')
        # The STREAM command is the one posted (another process may have posted it: the CLI).
        w.radio_wait(w.status()['radio_req'], 'stream', timeout=PRODUCER_RETURN_SECONDS)
        page = struct.pack(f'<{RESULT_WORDS}I', *w.session.ctl(read=(DONE_AT, RESULT_WORDS))[0])
        result = parse_result(page, reader)
        if packing == 'half' and result.status > 0 and not wa_exited(page):
            raise StreamError('the WA did not leave the packer; reload the firmware')
        return result

    def check_interface(self, interface):
        """The export sends one 8 KiB slice per datagram straight to the NIC: it needs the link up
        and a jumbo MTU (9216 provisioned)."""
        name = shlex.quote(interface)
        text = self.unit.run(f'cat /sys/class/net/{name}/mtu /sys/class/net/{name}/operstate 2>/dev/null').stdout
        fields = text.decode(errors='replace').split()
        if len(fields) != 2 or not fields[0].isdigit():
            raise StreamError(f'the unit has no interface {interface}')
        if int(fields[0]) < MIN_MTU:
            raise StreamError(f'{interface} MTU is {fields[0]}; the export needs at least {MIN_MTU} (9216)')
        if fields[1] == 'down':
            raise StreamError(f'{interface} is down')

    def launch(self, export, dst_mac):
        """Start mt7996-stream detached from this ssh session; it runs until stop()."""
        stream = shlex.join(stream_argv(self.unit.tool('mt7996-stream'), export, dst_mac))
        job = f'{stream} > {OUT_FILE} 2>&1; echo done > {DONE_FILE}'
        self.unit.check(f'rm -f {OUT_FILE} {DONE_FILE}; setsid sh -c {shlex.quote(job)} < /dev/null > /dev/null 2>&1 &')

    def finished(self):
        return self.unit.run(f'test -e {DONE_FILE}').returncode == 0

    def signal_stop(self):
        self.unit.run('pids=$(pidof mt7996-stream) && kill -INT $pids; true')

    def wait_finished(self, timeout=30.0):
        """Until the job has written its done file, or neither the stream nor its result reader
        runs (a stream whose files were already collected, or one that never started)."""
        deadline = time.monotonic() + timeout
        while not self.finished():
            if not self._running():
                return
            if time.monotonic() > deadline:
                raise StreamError(f'the stream did not wind down in {timeout:.0f} s')
            time.sleep(0.2)

    def _running(self):
        return self.unit.run('pidof mt7996-stream > /dev/null').returncode == 0
