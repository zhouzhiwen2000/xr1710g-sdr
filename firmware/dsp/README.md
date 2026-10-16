# MT7996 DSP NCO kernels

[`nco_pack_q15.S`](nco_pack_q15.S) fuses rotation and 14-bit IQ packing.
The standalone [RAM probe](probe/) uses two buffers per DSP to overlap input
DMA, computation and PCIe output. On XR1710G, the three-core probe measured
122.1–123.6 MS/s with exact 10 GbE payload checks. The optional continuous
receiver path supports 40/80 MS/s with online frequency commands; see
[usage and limits](../../docs/dsp-nco.md).

## Fused kernel

`nco_pack_asm(phase, step, count, input, packed)` accepts its arguments in
`a0` through `a4` and returns the number of saturated I/Q components in `a0`.
The count must be 16 through 2048, divisible by 16. Input/output addresses must
be word aligned, non-overlapping, initialized DSP-local memory; the input is
`count * 4` bytes and output is `count * 7 / 2` bytes. The coefficient table
is fixed at `0x201000` and must not overlap either buffer.

Input words contain signed 14-bit I at bits 29:16 and Q at bits 13:0. Output
matches `stream/pack.S`: for each eight quantized words `w0..w7`, output word
`k` (0 through 6) is `wk | ((w7 << (14 - 2*k)) & 0xc000c000)`. The caller
advances the 32-bit phase by `count * step` modulo 2^32. Processing sample 7
first within each group does not alter its phase or the output sample order.

The function preserves integer callee-saved registers and uses 48 bytes of
stack. Both custom MAC accumulators are clobbered; interrupts must remain
disabled and the caller must own the accumulators throughout execution.
Retain `.text` and `.text.slow` together when linking. The function does not
validate arguments; the enclosing mailbox firmware performs the checks.

The fused local benchmark measured 14.8975 cycles per complex sample including
packing, or 42.96 MS/s per core. A deliberately saturated input measured
27.95 MS/s per core. Sixty-six exact cases covered both banks on all three
cores, full-range inputs, clipping, phase wrap and 16/32/64/256/2048-sample
blocks. The network tests covered 251,658,240 complex samples and 840 MiB with
exact payloads and contiguous sequences. Frequent clipping reduced complete
three-core throughput to 81.63 MS/s; see the [measurements](../../docs/dsp-nco.md).

## Standalone RAM probe

Build from the repository root with Python 3, clang, ld.lld and llvm-objcopy:

```sh
python3 firmware/dsp/probe/build.py --output runtime/dsp-nco-build
```

The builder produces `dsp-nco.bin`, `pipeline_async.bin`, ELF files and a source
and artifact hash manifest. It performs a container round-trip check and rejects
oversize code or writable static sections. Building performs no hardware writes.
The receiver build also emits the DSP image as `w1700k_dsp_nco.bin`, referenced
by the optional `dsp_nco` manifest entry. It is loaded between WM and WA only
when the host enables this receive path. The standalone WM probe remains a
separate diagnostic; the continuous producer is `stream/dsp_producer.c`.

The DSP-local map is the same on all three cores:

| Address | Content |
| --- | --- |
| `0x201000..0x204fff` | 4096 Q15 coefficient words, cosine high and sine low |
| `0x205e00` | Initial stack pointer, stack grows down |
| `0x205f00` | Control mailbox |
| `0x205f80` | Control guard |
| `0x206000`, `0x208000` | Two 8192-byte input buffers |
| `0x20a000`, `0x20c000` | Two 7168-byte packed output buffers |
| `0x20bc00`, `0x20dc00` | Output guards |

The MCU view adds `0x86000000 + core * 0x400000` to DSP-local addresses.
The diagnostic explicitly initializes DSP memory windows and disables interrupts
before accessing them. Never access an uninitialized or reset DSP's DTCM by DMA.

Mailbox offsets below are 32-bit word indices from `0x205f00`:

| Word | Meaning |
| --- | --- |
| 0, 1 | Magic `0x4e434f31`, idle heartbeat |
| 2, 3 | Request sequence, completed sequence |
| 4 | Mode: 0 separate NCO, 1 fused NCO/packing, 2 packing only |
| 5–8 | Count, phase, step, repeat count (1–4096) |
| 9–11 | Cycle count, ending phase, saturated component count |
| 12–15 | Fatal fault flag, `mcause`, `mepc`, `mtval` |
| 16–19 | Reserved zero; used by earlier ISA probes |
| 20 | Buffer bank 0 or 1 for fused mode; other modes use fixed buffers |

Publish arguments first, then a new request sequence. Do not change arguments
while a request is pending. Read results only after the completed sequence
matches; rejected arguments return `0xffffffff` in word 11. Mode 0 writes its
unpacked result at `0x208000`, so it must not run concurrently with double-buffered
input use. Mode 2 packs fixed input `0x206000` to `0x20a000`.

The WM probe is linked at `0x0090b000`; its entry arguments are core count
(1–3), number of 2048-sample jobs (at least core count, at most 65536), frequency
step and an unused fourth argument. It repeats the known input block at UMAC
address `0xe0400000`. Load/start the DSP image, initialize the radio and host DMA
consumer, ensure the rings are idle, upload the WM probe, then start the consumer
with GO address `0x0090f008` and value `0x474f474f`. These fixed addresses and
the ROM reclaim entry are specific to the tested MT7996 firmware/driver.

The scheduler retains each output buffer until PCIe completion and orders
publication by absolute job number. Each core receives a fresh request sequence
derived from its preceding acknowledgement, including consecutive probe runs.
It uses immutable MEM_DMA destinations,
one outstanding input transfer, two buffer pairs per core and at most six
output buffers across an eight-entry PCIe ring. Fatal faults or a 100 ms
progress timeout terminate the probe; the host must reset the wireless subsystem
after completion or failure. Reset is also required if the host consumer fails.
The probe does not enable RF transmission or write flash.

## Separate NCO kernel

[`nco_q15.S`](nco_q15.S) is the validated MRV5 assembly kernel used by the
XR1710G DSP NCO probe. It is separate from the normal receiver firmware build.
The enclosing diagnostic image initializes and owns the DSP; this function
must not be inserted into a running vendor DSP image without managing its
memory, interrupts and accumulator ownership.

`nco_asm(phase, step, count)` returns the number of saturated I/Q components.
Its arguments use `a0`, `a1`, `a2`, and its result uses `a0`. The caller must
validate a count of 16 through 2048, divisible by 16. It advances phase by
`count * step` modulo 2^32 after the call. All cores use the same local map:

| DSP-local address | Content |
| --- | --- |
| `0x201000` | 4096 packed Q15 coefficient words: cosine high, sine low |
| `0x206000` | Input, at most 8192 bytes |
| `0x208000` | Quantized output, at most 8192 bytes |

Each input/output word holds signed 14-bit I at bits 29:16 and Q at bits 13:0.
Output spare bits are zero, ready for the existing `stream/pack.S` routine.
The 32-bit phase accumulator and coefficient lookup match the original C
kernel exactly, including wrap, rounding and clipping. The function preserves
integer callee-saved registers and requires 48 bytes of stack, in addition to
the caller's stack. Both custom MAC accumulators are clobbered. Interrupts must
remain disabled throughout execution.

The implementation uses two custom multiply/accumulate units, their rounded
right-shift readout, signed 16-bit saturation for the exceptional clipping
path, a 16-sample unroll, and interleaved preparation of the next sample. These
encodings use `.insn`; they do not require compiler support for a vendor ISA.
Compile as RV32IM with the project's existing bare-metal RISC-V clang flags.
The linker must retain `.text` and `.text.slow` together.

On all three 640 MHz cores, the unclipped local benchmark measured 16.146
cycles per complex sample, or 39.64 MS/s; including the existing packing stage
measured 33.98 MS/s. Thirty-three numerical cases compared every output word,
clipping count and final phase against an independent integer reference.
They included 16/32/64/256/2048-sample blocks, positive/negative frequency
steps, phase wrap and full-range inputs. The three-core DMA/PCIe/10 GbE probe
reached approximately 71.8–71.9 MS/s with exact host payload checks.

The throughput figures use unclipped data. A deliberately clipped input with
one saturated component at every sample measured 24.77 MS/s per core including
packing. Saturation is preserved rather than silently wrapping the output.
See [DSP NCO measurements](../../docs/dsp-nco.md) for scope and integration limits.
