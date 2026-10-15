# X310 to XR1710G over-the-air validation

On 2026-10-15, an X310 with a UBX-160 v2 daughterboard transmitted OpenISAC
downlink frames through its RF A TX/RX antenna port to an XR1710G. Both ends
used **40 Msps**, with the X310 master clock at 200 MHz. The XR1710G fed the
CPU UE through its existing shared-memory IQ service. Uplink and ARQ were off.

The selected operating point was **2.350 GHz**, XR band 0, 40 MHz filter,
manual LNA 3 / LPF 8, and X310 TX gain 31.5 dB. This is below the usual 2.4 GHz
Wi-Fi band. The setting is an experimental operating point on this pair of
devices, not a calibrated sensitivity or usable-bandwidth specification.

The later [160 MHz master-clock experiment](x310-160mhz.md) raised the live
link to **80 Msps**, with a 50 MHz payload span and 30 Mb/s video. The 40 Msps
measurements below document the initial validation.

## Rate and RF checks

- UHD returned exactly 40,000,000 samples/s for X310 TX and RX. Its odd-factor
  interpolation warning for the 200/40 ratio remains applicable.
- The XR backend read 120,061,952 samples in 3.001135 seconds in a separate
  short probe, with zero metadata errors, timeouts, overflows, broken chains
  or bad packets. This checks the rate class, not absolute sample-clock ppm.
- A switched X310 tone was visible in XR captures. OpenISAC subsequently
  delivered uniquely tagged, pseudorandom 512-byte UDP payloads over RF;
  each received payload was compared byte for byte against the transmitted
  sequence. The host networking did not relay these payloads between endpoints.
- At 4.750 GHz the OFDM receiver did not acquire sync. At 5.050 GHz it delivered
  valid payloads and recovered after a transmitter restart. With the same
  tone settings on band 1, the received tone was about 22.8 dB stronger at
  5.050 GHz than at 4.750 GHz. This includes both radios and antennas, so it
  does not isolate the XR front-end filter loss.

The 40 Msps choice requires no resampling. The earlier attempts to obtain
50/100 Msps directly are described in [sample-rates.md](sample-rates.md).

## Frequency correction interface

The live X310 provided a reference for UHD RX tuning semantics: with manual
RF 2.442 GHz and DSP +100 kHz, the effective center reported by UHD was the
actual RF value **minus** the DSP value. The XR backend now uses the same
sign convention. Its direct physical frequency-correction method still uses
positive values to raise the LO; the UHD-compatible manual DSP request and
readback have the opposite sign.

The RF runs used the existing PLL fine-tuning helper. Backend unit tests
cover both frequency-offset signs, automatic/manual tuning, preserved offsets
and buffered samples. The UE's frequency-observation ordering, stale-update
checks and reacquisition logic were unchanged. PLL tuning corrects carrier
frequency; it does not correct the sampling clock. See
[pll-fine-tune.md](pll-fine-tune.md).

## Transmit Linux host realtime configuration

An early 2.350 GHz run logged one TX asynchronous UNDERFLOW event. The associated
profile showed a maximum send call of about 251.5 ms. The log's event, profile
and recovery messages refer to this same event, not three independent events.

The X310 Linux host had two concrete scheduling problems: system.slice and user.slice
allowed only CPUs 0-1,12-23 while the BS requested CPUs 2,4,6,8,10, and its
ordinary user launch could not set realtime priority. The governor was
powersave. Both affinity and scheduling warnings appeared in the BS log.

The OpenISAC repository's `scripts/set_performance.bash` and
`scripts/isolate_cpus.py` were then applied on the **X310 Linux host**. Its active
10 GbE USRP interface was `enp2s0f0`; replace this name on another Linux host.
The test used the following configuration:

| Item | Applied setting |
| --- | --- |
| CPU governor | performance on all 24 logical CPUs |
| BS downlink cores | `[2, 6, 8, 12]`; main CPU 10 |
| Reserved realtime CPUs | 2 and 10; SMT siblings 3 and 11 shielded |
| NIC IRQ | CPU 4; its physical core, CPUs 4-5, excluded from applications |
| NIC queues | Existing 24 combined queues preserved |
| NIC RX/TX ring | 4096 descriptors each, previously 512 |
| MTU | 9000 |
| Kernel socket defaults/maxima | 250,000,000 bytes |
| UHD requested send/receive buffers | 25,000,000 bytes each |
| Scheduler feature | RT_RUNTIME_SHARE enabled |

`kernel.sched_rt_runtime_us` remained 950000. The performance script's message
about disabling throttling refers to enabling runtime sharing; it did not set
that sysctl to -1. `cpufreq-set` was absent, so the installed `cpupower` applied
the performance governor after the script's governor step was skipped.

Example commands, executed on the X310 Linux host from the directory containing the
test `BS.yaml`:

```sh
sudo env OPENISAC_TARGET_IFACES=enp2s0f0 bash ~/UHD_OFDM/scripts/set_performance.bash
sudo cpupower -c all frequency-set -g performance

# The ring change can briefly renegotiate the link; confirm 10000 before setup.
cat /sys/class/net/enp2s0f0/speed
sudo python3 ~/UHD_OFDM/scripts/isolate_cpus.py --role bs --yaml "$PWD/BS.yaml" \
  --smt-siblings isolate --nic-irq isolate --nic-irq-cpu-count 1 \
  --nic-ifaces enp2s0f0

sudo python3 ~/UHD_OFDM/scripts/isolate_cpus.py run /absolute/path/to/BS
```

The test harness itself was launched through `isolate_cpus.py run`, so its BS
child inherited the correct root service and CPU mask. Live `/proc` and `ps -L`
checks found TX on CPU 2 at **SCHED_RR priority 99**, OFDM on CPU 6 at RR 59,
LDPC on CPU 8 at RR 20, and main on CPU 10. The process ran under
`rt-workload.slice`; affinity and priority failure warnings disappeared.

The settings are retained for subsequent tests. Before/after snapshots and the
original systemd drop-ins were saved. Governor, NIC and sysctl settings and the
saved `/tmp` isolation plan must be reapplied after a reboot. The documented
`isolate_cpus.py reset` removes isolation; it does not reconstruct the original
restricted CPU mask or restore every performance setting.

## Payload results and limits

Run 9 preceded the realtime changes; run 10 used them. Both offered 2,000
512-byte packets/s, or **8.192 Mb/s of application payload**, at 2.350 GHz and
40 Msps. Both included a five-second transmitter stop, while the UE stayed
running with uplink disabled.

| Run | Sent | Received | Exact payloads | Corrupt payloads | TX underflow events |
| --- | ---: | ---: | ---: | ---: | ---: |
| 9, before realtime setup | 64,565 | 44,403 | 44,268 | 135 | 0 |
| 10, after realtime setup | 64,568 | 46,507 | 46,360 | 147 | 0 |

Run 10 received 31,013 exact packets before the stop and 15,347 after restart.
Its exact-payload delivery ratio was **71.80% of all packets sent**, including
startup and reacquisition. Missing packets are not counted as successful.
Both processes exited normally. The UE log contained no backend metadata
errors, but did contain sync-queue drop warnings during acquisition/search;
zero backend metadata errors therefore does not imply a lossless UE pipeline.

A further **120-second continuous BS run** (run 11, no intentional gap) used
the same RF and realtime settings. Payload input ran at 2,000 packets/s for
113 seconds after the initial startup wait. It sent **226,000** packets,
received **199,090**, and delivered **198,596** exact payloads, with **494**
corrupt payloads. Exact delivery was **87.87% of all packets sent**. The BS
logged **zero underflow events, zero affinity failures and zero priority
failures**. The UE logged one sync acquisition and zero backend metadata
errors; both processes exited normally. This is a bounded non-reproduction
of underflow, not a long-duration reliability result.

These observations verify that the realtime settings took effect and that
the RF link and uplink-disabled recovery work. Run 9 also had zero underflows,
so the comparison does not prove that realtime tuning eliminates every
intermittent underflow, or that the remaining payload errors have the same
cause. Long-duration reliability, maximum throughput, calibrated RF response
and correction of sampling-frequency offset remain unvalidated.

## Analog filter and payload allocation follow-up

With sampling held at 40 Msps and the same 2.350 GHz link running, the XR
analog filter was changed from 40 to 80 and then 160 MHz. The 40-to-80 MHz
change retained the same capture run and added no source-loss count. Across
short debug-stream captures, the median channel amplitude in bins
`400 <= |k| <= 489`, relative to bins `40 <= |k| <= 200`, was approximately:

| Analog filter | Edge/middle amplitude |
| --- | ---: |
| 40 MHz | -25.68 dB |
| 80 MHz | -24.70 dB |
| 160 MHz | -23.32 dB |

The remaining attenuation is substantial. These are successive OTA
measurements, not a calibrated filter transfer function. The receiver's
digital decimation response also attenuates the edges; widening the analog
filter alone did not remove the observed constellation contraction.

A subsequent [edge roll-off diagnosis](edge-rolloff.md) also identified the
X310's factor-five, four-section CIC interpolation as a substantial
contributor. It compares the end-to-end captures with the TX model and an
RX reference, and explains the additional constellation contraction from
MMSE equalization.

The 40 Msps follow-up profile retained **80 MHz analog filtering and 40 Msps IQ**,
with matching BS/UE `resource_preview.data_resource_blocks` restricting payload
to FFT bins **1-320 and 704-1023** (`k = +/-1 ... +/-320`, about **25 MHz**
between the outermost payload carriers). The ten retained comb pilots are
`[752, 812, 872, 933, 993, 29, 89, 150, 210, 270]`. Payload symbols are
3-18, 20-38, 40-58, 60-78 and 80-98, giving **57,960 payload RE/frame**.
Sync, CFO training and mid-frame reference symbols keep their existing roles.

This uses the existing resource-map implementation. Unallocated RE still
carry the transmitter's reference fill; the original constellation viewer
also displays them. A full-band constellation can therefore still show weak
edge points even though those carriers no longer carry decoded payload.

At the same 8.192 Mb/s offered load and 80 MHz analog setting, a 20-second
window before the resource change delivered 36,664 exact packets across a
39,998-sequence span, plus 37 corrupt packets. After the change, another
20-second window delivered **39,999 exact packets with consecutive sequence
numbers and zero corrupt packets**. This is a short steady-state observation,
not a long-duration loss-free guarantee. Both live YAML files and their
pre-change backups are retained on their respective Linux hosts; local evidence is
in `runtime/filter-bandwidth-20261015/`.

## Live video and viewer

The supplied `Videos/video.mp4` was copied to the X310 Linux host with matching
SHA-256. The running video sender loops the file, encoding **1280x720 at
30 fps**, H.264 at a 4 Mb/s target and AAC at 128 kb/s, in an approximately
4.5 Mb/s MPEG-TS stream. UDP input goes only to the local BS; the XR UE's
decoded output is forwarded to the requested player computer. The random
payload generator is stopped while video is active.

The receiver measured approximately **4.50 Mb/s** over 101 seconds. Four
datagrams failed MPEG-TS structural checks in that window; those were dropped
by the forwarding helper. A bounded capture beginning mid-stream initially
lacked H.264 parameter sets until the next repeated header. A subsequent
remuxed segment decoded **292 video frames and audio over about 10 seconds
without decoder errors**. This validates a received segment, not the remote
player's rendering or loss-free operation for the full stream.

The live constellation, channel, delay-spectrum and bistatic sensing outputs
remain enabled. Viewer-side frame drops can occur independently of payload
delivery. Video scripts, receive statistics and the bounded transport-stream
capture are in `runtime/video-20261015/`.

## Evidence and final state

Local evidence is in the ignored `runtime/x310-ota-20261015/` directory:
per-run BS/UE YAML and logs, payload sequence/timing results, IQ captures,
`results-summary.json`, realtime before/after/running snapshots, build and
test logs, and the unchanged-UE-source hash check. `SHA256SUMS` covers the
recorded evidence, excluding the dependency environment and the manifest itself.

After the bounded run-11 test, both modems were stopped and the PLL helper
restored the nominal LO. The later interactive viewer/video session restarted
them with the resource allocation described above. The subsequent [higher-rate experiment](x310-160mhz.md) leaves BS, UE, video
and viewer outputs running at band 0 / 2.350 GHz / **80 Msps**, with 160 MHz
analog filtering and eight UE demodulation workers. The host realtime settings
are retained.
