# Router-side NCO feasibility on XR1710G

Measured on 2026-10-15. Router-side NCO is feasible in principle on the
AN7581's Cortex-A53 NEON units. The current 320 MS/s transport has insufficient
demonstrated headroom to add it safely. A 40 MS/s integration is the sensible
first target; neither 40 nor 80 MS/s has yet been validated end to end with a
router-side NCO.

For slow carrier-offset correction, [fine LO control](pll-fine-tune.md) is another
hardware option: approximately 100 Hz changes have been measured through direct
PLL divider updates, without processing every IQ sample. Transition settling
and phase continuity still need validation.

## Live configuration

The running router reports four AArch64 Cortex-A53 cores (`CPU part 0xd03`),
with `fp asimd` features. The sample stream was band 2, LO 6,100,000,005 Hz,
nominal 320 MS/s, half packing, exported over the existing 10 GbE link.
The DMA pump is pinned to CPU 3 and the exporter to CPU 2. A two-second
`/proc/stat` observation gave approximately 0.5%, 75.7%, 100% and 100% busy on
CPUs 0 through 3. The standalone benchmark was pinned to CPU 0.

## Bounded benchmark

[`tools/router_nco_bench.c`](../tools/router_nco_bench.c) allocates private
SC16 buffers. It never maps the radio DMA pool or changes radio registers.
Build on an x86 Linux host with the AArch64 cross compiler:

```sh
aarch64-linux-gnu-gcc -O3 -Wall -Wextra -Werror -mcpu=cortex-a53 -static \
  tools/router_nco_bench.c -lm -o router-nco-bench
```

The `full` kernel includes a continuous oscillator, SC16-to-float conversion,
NEON complex multiplication and rounded/clamped 14-bit SC16 output. Eight
oscillator lanes advance recursively, with double-precision phase anchoring
every 2048 samples. Frequency is 25 kHz at nominal 320 MS/s. The built-in
numerical check additionally covers zero, positive and negative frequencies,
clipping and continuity between calls against a double reference. The maximum
observed error was **0.550 output LSB** (pass threshold 2 LSB).

One measurement of each case completed before the stream-health guard stopped
the planned repeat sequence:

| Kernel | Input / output allocation | Wall-clock complex rate | Context |
| --- | --- | ---: | --- |
| Full NCO | 8 KiB / 8 KiB | 109.309 MS/s | 1.500 s; source advanced throughout the observation |
| Full NCO | 16 MiB / 16 MiB | 85.500 MS/s | 1.521 s compute; exporter stopped during this case |
| Precomputed Q15 rotation only | 8 KiB / 8 KiB | 261.575 MS/s | Exporter already stopped; excludes oscillator generation |

The table-only kernel repeats precomputed coefficients every block. It is
an arithmetic comparison, **not** a general phase-continuous NCO benchmark.
The large-buffer result spans a change in transport load, so it is not a
steady-state rate under a healthy 320 MS/s stream. These are single short
measurements, not sustained throughput acceptance or hardware limits.
Neither kernel includes packed-IQ decoding/encoding, DMA cache synchronization,
buffer ownership transfer, packet construction or network transmission.

The large-buffer command used was `router-nco-bench 0 full 4194304 1.5`.
Do not repeat it on an uninterrupted 320 MS/s session expecting the exporter
to tolerate the added memory pressure. The small-buffer command was
`router-nco-bench 0 full 2048 1.5`.

## Export interruption and recovery

During the large-buffer case, the Linux host frontier stopped at 249,308,452
slices. The radio DMA pump kept running and WM traps remained zero. Stopping
the stream collected the exporter result **-105 (`ENOBUFS`)**, with
`max_lag=129472` and `unsent=129473`. In
[`kernel/w1700k_sdr/export.c`](../kernel/w1700k_sdr/export.c), this error means
the oldest unsent slice is approaching overwrite; it is not a report of
failed heap allocation. The same failure mode had occurred in earlier
320 MS/s work. Its coincidence with this workload supports a concern about
headroom, but does not isolate the exact resource or prove a new root cause.

Further pressure tests were cancelled. Restarting the existing stream
restored reception at the same band, LO, sample rate and packing (host run
advanced from 2 to 3). A subsequent five-second check showed 781,616 new
slices and no lost-counter increase; both host services were active and
the spectrum HTTP endpoint returned 200. All three PLLs were locked,
WM traps were zero and both crystal trim words remained `0x58402a2a`.

Raw benchmark output, recovery output and final health checks are retained in
the ignored local directory `runtime/router-nco-20261015/`. No live NCO stage
has been inserted into the data path.

## Integration implications

At 40 MS/s, the measured arithmetic cost leaves enough room to justify an
end-to-end prototype. At 80 MS/s, memory traffic and transport overhead need
careful measurement. This full kernel cannot sustain 160 or 320 MS/s on one
core. Multiple cores and more specialized fixed-point kernels could improve
arithmetic throughput, but do not establish adequate shared-memory bandwidth.

The existing path DMA-writes IQ to a DRAM pool and attaches those pages to
network packets. Userspace mappings are read-only. An NCO needs a defined
input-completion/output-completion protocol, writable output buffers and
correct DMA cache synchronization; it cannot simply overwrite a page already
being transmitted or reused by capture. SC16 read plus write alone adds
2.56 GB/s at 320 MS/s, before coefficient access and packing. Above 160 MS/s,
the current link also requires handling the mixture of raw and packed slices.

The repository's upstream measured NPU characterization in
[`include/hw/an7581/npu.h`](../include/hw/an7581/npu.h) describes eight
800 MHz RV32 cores without F/D/V instructions, sharing the DRAM bottleneck.
Those measurements were not repeated on this XR1710G in this experiment.
It is a programmable network processor, but no demonstrated IQ NCO engine
is available through it. WM/WA already run the producer/packer; using unloaded
radio DSP cores would require separate firmware and data-path bring-up.

A router-side digital NCO corrects carrier offset without changing sample
timing. Crystal trim remains a separate actuator for reference/sample-clock
error. Any receiver control loop needs to preserve that distinction.
