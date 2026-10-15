# Fine receiver LO control on XR1710G

The receiver PLL provides a much finer carrier-frequency actuator than crystal
trim, without per-sample software mixing. The existing fractional-divider model
uses an even 24-bit FRAC word (bit 0 is ignored):

`LO step = 2 * f_PFD * ratio / 2^24`

| Radio / mode | Nominal LO step |
| --- | ---: |
| MT7976, 2.4 GHz, ratio 5/16 | 2.980232 Hz |
| MT7977, below 5.926 GHz, ratio 5/8 | 11.920929 Hz |
| MT7977, from 5.926 GHz, ratio 3/4 | 14.305115 Hz |

The model and upstream tone measurements are documented in
[`include/hw/adie/sx.h`](../include/hw/adie/sx.h), also available in the
[upstream source](https://github.com/h0m3us3r/w1700k-sdr/blob/main/include/hw/adie/sx.h).
These are frequency-setting increments, not absolute frequency accuracy.
Crystal-reference error scales the actual frequencies.

## XR1710G measurement, 2026-10-15

The live setup was band 2, nominal LO 6,100,000,005 Hz, 320 MS/s, half packing,
crystal code 88. The baseline SX bank was die 2, bank 4:

- INT `0xa4b = 0x03205f51`.
- FRAC `0xa4a` lower 24 bits `0xd55556`.
- CFG `0xa40 = 0x00100015`.
- VCO band `0xa01 = 0xa3010cff`.

Each adjustment wrote the saved INT word, then `0x80000000 | (saved_frac + delta)`
to FRAC. The LOAD bit is a strobe and reads back clear; whole-word readback is
not a valid frequency or lock test. No VCO bank search or full TUNE command ran.
No crystal, sample-rate, gain or filter settings were changed.

FRAC offsets followed `0,+140,0,-140,0,+14,0,-14,0,+2,0,-2,0`. Each setting was
allowed 150 ms before checking stream progress and PLL status, then captured
twice. Each capture contained 4,194,304 contiguous SC16 samples, with slice tags
checked before and after copying. FFT length was 4,194,304 (76.294 Hz/bin),
with a Hann window and log-power peak interpolation. An additional phase-slope
estimate mixed each feature to near DC, averaged blocks of 65,536 samples and
fit unwrapped phase over the resulting 64 points.

Two incidental spectral features were tracked. The feature near -101.57 MHz
had lower phase residuals (0.07–0.14 rad) than the feature near -6.32 MHz
(roughly 0.4–0.6 rad). The table uses the former, subtracting the mean of the
immediately preceding and following zero-offset measurements:

| Nominal LO change | Expected baseband change | Observed phase-slope change | Observed FFT change |
| ---: | ---: | ---: | ---: |
| +1001.358 Hz | -1001.358 Hz | -999.9 Hz | -1000.0 Hz |
| -1001.358 Hz | +1001.358 Hz | +994.0 Hz | +994.0 Hz |
| +100.136 Hz | -100.136 Hz | -97.1 Hz | -97.2 Hz |
| -100.136 Hz | +100.136 Hz | +111.0 Hz | +109.2 Hz |
| +14.305 Hz | -14.305 Hz | -21.2 Hz | -21.3 Hz |
| -14.305 Hz | +14.305 Hz | +19.7 Hz | +17.5 Hz |

This establishes reversible hardware fine tuning at approximately 100 Hz and
1 kHz on this board. The smallest-step trials show movement in the expected
direction, but do not accurately verify a 14.305 Hz step: the uncalibrated
feature drifted by approximately 58 and 90 Hz between the bracketing baselines
for those trials. A stable reference and faster alternating measurements are
needed to separate single-step response from drift. No stable transmitter was
available; neither spectral feature is an absolute frequency reference.

The two-write command reported **15.66–24.98 us of WM execution**. Host round
trip took 3.67–7.05 ms. WM time is not PLL analog settling time. This experiment
waited 150 ms and did not capture a known coherent tone across the switching
instant, so it does not establish phase continuity, a usable settling guard,
or glitch-free OFDM reception during an update.

All three synthesizer status checks remained locked and WM traps remained zero.
The stream run stayed 3, but the host's lost counter increased by 71,045 during
the sweep. The analyzed windows were individually verified contiguous; this
does not prove uninterrupted, lossless 320 MS/s streaming. The experiment did
not isolate the cause of those losses.

The original INT/FRAC fields were restored. Both crystal words remained
`0x58402a2a`; all PLL status checks were locked, traps were zero and the source
continued advancing. Raw captures are retained on the Linux receiver host under
`xr1710g-pll-fine-20261015/`; the probe, result JSON, analysis and restoration
evidence are in the ignored local `runtime/pll-fine-20261015/` directory.

## Receiver integration

Fine LO control can replace most steady carrier correction arithmetic when
updates are slow enough for the receiver to handle their transient. It changes
the analog receiver center frequency; it does not correct sample-clock error.
Crystal trim remains a separate, coarse shared-reference actuator. A finer
crystal or ADC-clock actuator has not been established on this setup.

The normal `rf.tune` path runs full tuning, including presets and VCO search.
The later OpenISAC backend integration uses a separate PLL fine-tuning helper
and preserves buffered samples and the UE's existing stale-update checks;
see the [OTA frequency-correction validation](x310-ota.md#frequency-correction-interface).
That integration and its RF tests are separate from the bounded sweep above.
The sweep does not establish phase continuity or analog settling during an
update. Stable-source measurements across the switching instant are still
needed to characterize those properties.
