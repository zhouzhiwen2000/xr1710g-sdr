# Additional sample-rate probe

On 2026-10-15, the running XR1710G band-2 receiver was tested for **50 and
100 Msps** output, to match an existing OpenISAC BS. Neither rate was obtained
through the controls tested. The supported host/backend rates remain **40, 80,
160 and 320 Msps**. This is a result for this capture path and these controls,
not proof that every possible ADC clock configuration has been exhausted.

## Hardware experiment

The source was the ordinary 320 Msps path at nominal LO 6,100,000,005 Hz, with
half packing. No firmware image or flash contents changed. The stream stayed
running while the following words were saved, changed and restored:

| Control | Address / field | Result |
| --- | --- | --- |
| ICAP capture rate | `0x832ad448[17:16]` | Codes 0, 1, 2, 3 measured approximately 320, 160, 80, 40 Msps |
| RXFE manual downsample | `0x8328f114[1:0]` | All four codes tested |
| RXFE AFIFO read | `0x8328f114[2]` | Both values tested |
| RXFE ADC480 mode | `0x8328f114[16]` | Both values tested |
| DLPF manual enable/mode | `0x8328f114[6]`, `[5:3]` | Eight mode requests; enable bit 6 did not remain set in readback on this band |

The downsample, FIFO and ADC480 fields were tested in all 16 combinations at
each of the 160 and 80 Msps capture settings: **32 combinations**. Output stayed
within 159.992–160.002 and 79.994–80.001 Msps respectively. The eight DLPF
requests also retained approximately 160 Msps output; they do not establish
that manual DLPF operation was enabled.

The secondary RXFE word at `0x8329f114` read zero and did not retain a candidate
write. That attempt stopped at the readback check, restored the saved state,
and subsequent tests left this word unchanged.

Rates came from the advancing shared-ring data sequence, subtracting event
entries, against host `CLOCK_MONOTONIC_RAW`; each setting used a 1.5-second
measurement and a contiguous 65,536-sample IQ snapshot. Rate metadata was not
changed, so this test does not merely validate a configured rate label. The
short, packetized measurements distinguish the rate classes, but are not an
absolute sampling-clock calibration. Noise samples remained nonconstant and
adjacent identical-sample fractions stayed below 0.005% in the 32-combination
sweep. A stable external RF reference was unavailable.

No additional source losses were counted inside the completed measurement
windows. Final readback restored the capture/RXFE words exactly to
`0x00200014`, `0x08000000`, and `0`. The LO divider and crystal trim were
unchanged, all three PLLs locked, WM traps remained zero, and the source returned
to approximately 320 Msps in the same stream run. IQ and spectrum services
remained active; the spectrum HTTP endpoint returned 200. These short tests do
not establish long-duration loss-free operation or anti-alias rejection.

## Matching 50 / 100 Msps BS waveforms

A host resampler could use these rational conversions:

| Hardware IQ input | UE output | Rate ratio |
| --- | --- | --- |
| 80 Msps | 50 Msps | 5/8 |
| 160 Msps | 100 Msps | 5/8 |

This requires interpolation/decimation with an anti-alias filter and correct
sample timestamps and gap handling inside the radio backend. Merely relabeling
the samples or periodically dropping them is insufficient. The current
OpenISAC XR1710G backend does not implement this conversion. Its existing PLL
frequency correction changes the RF LO, not the sampling-clock rate.

The X310's standard FPGA/UHD configuration uses a 200 or 184.32 MHz master
clock and integer interpolation/decimation, so it does not provide exact
80 Msps. The stock-clock common rate is 40 Msps at the X310's 200 MHz master clock.
The experimental build described below also passed an 80 Msps OTA test.
See the [Ettus clocking documentation](https://files.ettus.com/manual/page_usrp_x3x0.html)
and the [OTA report](x310-ota.md). Another possible receive path is XR capture
at 80 Msps followed by a host low-pass filter and decimation by two to 40 Msps,
retaining the X310's existing 40 Msps waveform. That backend path is not yet
implemented or validated.

## Higher-rate X310 experiment

A separate UHD build based on commit `2af4ddb9`, with a small
[160 MHz clock-admission patch](../tools/uhd-x310-160mhz.patch), was tested on
the X310. Reference and TX LO locks passed, and UHD returned exact TX/RX rates
of **40, 80 and 160 Msps** at a **160 MHz master clock**. At 80 Msps, a
30-second OTA window delivered 59,990 byte-exact packets across a 59,994-sequence
span, with no corrupt packets. The live link now uses 80 Msps and the XR's
160 MHz analog filter. See [the experiment and video report](x310-160mhz.md).

160 Msps also transported and decoded RF payloads, but sustained UE IQ reads
reported overflows. Eight demodulation workers and deeper frame queues improved
parts of the pipeline without eliminating this remaining loss. This rate is
not the current video profile. The high-rate tests used the existing UE DSP and
YAML controls; no resampler was added.

| OFDM rate | X310 hardware setting | XR capture | Status |
| --- | --- | --- | --- |
| 80 Msps | Experimental MCR 160 MHz, DUC x2 | 80 Msps | OTA and high-bitrate video tested |
| 160 Msps | Experimental MCR 160 MHz, DUC x1 | 160 Msps | RF payloads decoded; sustained receive overflows remain |
| 100 Msps | Standard MCR 200 MHz, DUC x2 | 160 Msps | RX polyphase conversion by 5/8 remains unimplemented |
| 100 Msps | Standard MCR 200 MHz, DUC x2 | 320 Msps | RX conversion by 5/16 remains unimplemented |

The 160 MHz clock uses the existing automatic PLL calculation: LMK VCO
2400 MHz divided by 15, PLL2 N=25, R=4, predivider=2, from the doubled
192 MHz VCXO reference. The DAC controller selects N0=2 and a 1280 MHz VCO;
its internal fourfold interpolation runs at 640 Msps. The patch admits exactly
160 MHz through the argument and automatic-clock guards, retaining the normal
clock programming and checks. It does not change the installed UHD library or
FPGA image. Phase noise, image rejection and timing margin remain uncharacterized.

At 80 and 160 Msps, raw int16 complex IQ needs 2.56 and 5.12 Gb/s before packet
overhead. At 320 Msps it needs 10.24 Gb/s, exceeding the existing 10 GbE link.
Packing half the slices from 32 to 28 bits per IQ pair averages **30 bits**, or
9.60 Gb/s before overhead. Band 2's `640/2` mode exports only every other sample
and can alias out-of-band signals; it is not a full 640 Msps IQ path.

Raising the rate moves the receive-filter edge outward in absolute frequency.
The same +/-320 payload-bin allocation spans 50 MHz at 80 Msps and 100 MHz at
160 Msps. It also shortens symbol and CP durations for unchanged sample counts.
Neither the whole Nyquist band nor all antennas/front-end frequencies have
been validated as flat. Future resampling must retain sample history,
timestamps, gap handling and the existing frequency-update ordering semantics.

## Evidence

The local ignored directory `runtime/sample-rate-20261015/` contains the probe
scripts, early readback failures and restoration records, raw IQ snapshots,
`active-results/results.json`, `dlpf-results/results.json`, `summary.json`, final
service checks and a SHA-256 manifest. The test scope is band 2 on the current
capture tap; other front-end clock configurations remain uncharacterized.
