# XR1710G receiver

This profile boots the SDR receiver on an XR1710G through its existing
chainloaded U-Boot. It does not build or install another bootloader, expose
NAND to Linux, or run the W1700K installer. The original Secure Boot
bootloader and the unit's DSD/calibration remain outside the test kernel.

The separate [GDM2 recovery Ethernet patch](uboot-gdm2/README.md) supplies
the validated driver and board changes for the chainloaded U-Boot, with its
source revision, test scope and installation boundary documented.

Board wiring is based on the XR1710G device tree at
`naoki66/ImmortalWrt-for-Gemtek-brightspeed`, commit
`04cc62e8928308598cf2e88bddee37ede658b814`: AN7581, 2 GiB RAM,
MT7996 on merged PCIe0/1, PHY8/GPIO31 on GDM2, PHY5/GPIO46 on GDM4,
and switch ports lan3/lan4. Pinned upstream SDR software is retained.

On Linux, after creating a dedicated SSH key pair outside the source tree:

```sh
make toolchain
python3 platform/xr1710g/build.py all \
  --unit-dir /path/to/private-unit \
  --authorized-key /path/to/test-key.pub
make firmware host check
```

The result is `platform/build/xr1710g/xr1710g-sdr-ram.itb`; its manifest records
the SHA256 of the FIT and each payload. This is a RAM boot image, not an image
for the recovery page's flash button. The builder reuses upstream build output
directories, so do not run upstream Linux/rootfs builds concurrently.

The management address is 192.168.255.1/24 on eth2 (GDM4, LAN2), MTU 1500.
New builds configure the other high-speed port, eth1 (GDM2/PHY8), for IQ export
at 10.77.0.1/24 with MTU 9216. The host needs 10.77.0.10/24 and jumbo frames on
its directly connected interface. Identify the actual 10G NIC before changing
host settings. The initially tested and installed image used eth2 for both paths
at MTU 1500; the separate-port configuration still needs a hardware boot test.
Gigabit links should use finite local captures followed by file transfer.
Continuous 40 Msps raw IQ requires 1.28 Gbit/s before framing overhead and
requires a 2.5G or faster link and an MTU capable of the upstream 8 KiB datagrams.

Supply the tested unit's own 7680-byte EEPROM to the host controller. A donor
unit's EEPROM is not a replacement for this unit's radio calibration. Hardware
SKU and A-die checks remain active; an unsupported radio must fail safely.

Finite hardware validation, after booting the FIT and configuring the host's
SSH key, verified host key and this unit's EEPROM:

```sh
python3 platform/xr1710g/validate.py --config /path/to/unit-config.json \
  --output /path/to/captures
```

This initializes all three bands at 40 Msps, captures each for 0.25 seconds,
rejects producer overruns and malformed/lapped buffers, and verifies the SHA256
of files retrieved over SSH. It records receive/DMA behavior, not calibrated
RF sensitivity or continuous UDP delivery.

## Hardware validation

The final RAM image was booted and tested on an AN7581GT XR1710G with 2 GiB RAM
and MT7996. The host link negotiated 2500 Mb/s on eth2 with MTU 1500. The test
kernel ran Linux 6.18.52; `verify-storage` passed, with no MTD devices or UBI
volumes exposed.

| Band | LO | Sample rate | Capture duration | Bytes consumed |
| --- | --- | --- | --- | --- |
| 2.4 GHz | 2442 MHz | 40 Msps | 0.25 s | 40,001,536 |
| 5 GHz | 5250 MHz | 40 Msps | 0.25 s | 40,001,536 |
| 6 GHz | 6105 MHz | 40 Msps | 0.25 s | 40,001,536 |

All three synthesizers locked. Each capture stopped with firmware status 7,
no producer overrun, zero malformed buffers, zero lapped buffers and a maximum
reader backlog of one buffer. An 8 MiB raw sample file and the metadata file
from each band were retrieved over SSH and verified against the router's
SHA256. Original bootloader, DSD and installed shim CRCs matched the saved
values after testing.

The initial software check suite ran 313 tests with 13 skips and no failures,
including five XR1710G storage/device-tree guard tests. Additional tests cover
NAND FIT packaging, raw IQ orientation and viewer state after a reboot. The
skipped tests require upstream images that were not built for this profile. Continuous UDP transport,
long-duration captures, higher sample rates and calibrated RF measurements
have not been validated on XR1710G.

## NAND-loaded receiver

Build the RAM receiver first, then package it for the installed XR1710G shim:

```sh
python3 platform/xr1710g/nand.py
# Or build both with private unit inputs:
make xr1710g-nand UNIT_DIR=/path/to/private-unit AUTHORIZED_KEY=/path/to/test-key.pub
```

The output is `platform/build/xr1710g/xr1710g-sdr-nand.itb` plus a manifest.
The packager retains the guarded kernel, DTB and RAM root, verifies their hashes,
and selects `config-1`, as the existing shim expects. The shim reads it from
`ubi:fit` and must retain these boot arguments in its own UBI environment:

```text
console=ttyS0,115200 earlycon panic=5 ro rdinit=/init
```

The original bootloader, original environment, DSD and installed chainloader
are preserved. Installation adds a static `fit` volume and the shim's dynamic
`ubootenv`/`ubootenv2` volumes to the existing UBI layout. Use the serial console
to inspect the layout and update only these volumes. Read the FIT back and
compare all bytes before saving the shim environment or restarting. Do not run
the upstream W1700K installer: its loader and UBI boundaries differ.

The shim's recovery upload rebuilds UBI and resets the environment, so this FIT
must not be uploaded with that OpenWrt flash flow. It is not a sysupgrade image.
NAND stores the boot image; root changes and captures remain in RAM and are lost
on reboot. Keep any recordings and settings on the host.

On the tested unit, FIT readback matched all 12,209,644 bytes, all three payload
hashes passed, and automatic warm boot loaded the SDR receiver from NAND.
Three-band 40 Msps finite captures passed again. Original bootloader, original
environment, DSD, shim and factory CRCs matched before and after installation.
Physical cold boot and Reset-button entry have not been tested yet.

## Periodic spectrum viewer

This viewer runs on the Linux host with NumPy. It captures briefly, stops the
producer, transfers 2 MiB over SSH and shows a spectrum and a short waterfall.
It supports gigabit management links without a continuous IQ connection:

```sh
python3 platform/xr1710g/preview.py --config /path/to/unit-config.json \
  --listen 127.0.0.1:8074
```

For another computer to open the page, bind to the Linux host's LAN address.
Choose a band and LO, then capture once or enable periodic refresh. The backend
checks the storage guard, validates capture results and transferred SHA256,
and discards stale controller sessions after a receiver reboot. Power is
uncalibrated dBFS/Hz. A real 5 GHz capture at 40 Msps passed backend validation;
the browser flow on the final NAND image still needs testing. Stop continuous
streaming before using this viewer.

The original continuous spectrum interface remains in `host/spectrum/`; follow
its README after validating the direct jumbo-frame IQ path. The portable
[host tools](tools/README.md) cover serial control, read-only TFTP and optional
SSH TCP forwarding. None contains credentials or per-device data.

## Experimental dual receive and ICAP routing probes

Run these tools on the Linux receiving host, from this checkout. They require
an already loaded receiver, exclusive controller access, and streaming stopped.
Stop the spectrum controller service before running them. They use the running
WM's control interpreter; they do not install firmware or write flash.

```sh
python3 tools/dual_rx_probe.py --config /path/to/unit-config.json \
  --out /path/to/new-results-directory --band 0 --seconds 60 --zero-control
```

Build `host/stream` and `host/recv` first and install NumPy. The probe uses a
separate UDP port (7770 by default) and shared-memory object. It disables the
ICAP0 single-path packer after starting a raw stream, excludes the transition,
and records two paths at 40 Msps each. It restores the band's original settings
and leaves streaming stopped. Restart the spectrum controller afterwards.

The intermediate recording contains four complex words per beat:
`ch0[t], ch0[t+1], ch1[t], ch1[t+1]`. The tool writes separate `*-ch0.ci16` and
`*-ch1.ci16` files (little-endian int16 I,Q) and channel metadata, plus transport
and producer results. The ordinary shared-memory ABI and spectrum UI still
describe one channel; these experimental streams must not be shown through
that UI. The DFE zero-path control validates channel separation, not the
physical antenna connector mapping or phase coherence. Fixed LNA/LPF settings
are used during the test. Coherent MIMO still needs same-source RF validation.

For bounded ICAP1 routing experiments:

```sh
python3 tools/icap_probe.py --config /path/to/unit-config.json \
  --out /path/to/new-route-results --sweep
```

This probe uses 16 KiB finite captures and sparse sentinels, with ICAP0-to-pool
and ICAP1-to-window positive controls. The optional sweep tests individual
write-path bits and their combined enable, retaining the forbidden SRAM grants
at zero. A small cache-maintenance helper runs from an idle WM pack buffer;
the helper bytes and capture registers are restored afterwards. WM pool reads
are cacheable: an unflushed sentinel is not evidence that capture did not write.
Negative results cover the tested routing settings and destination range only.

On the XR1710G tested on 2026-10-15, both paths passed 60 seconds at 40 Msps
per path on each of bands 0, 1 and 2, with no transport losses, overruns,
invalid spare bits or repeated lines. Zeroing path 1 left path 0 active;
restoring the DFE control restored both. These tests establish two digital
receive paths, not calibrated phase coherence or antenna connector identity.

The routing sweep found no ICAP1 writes in the probed pool locations. WPATH
bit 11 suppressed ICAP1 window writes; six follow-up combinations, with and
without ICAP0's pool enable and with both window-owner values, also left
sentinels unchanged across the 1 MiB pool (one probe per 4 KiB). Repeat that
follow-up with `--pool-select`. ICAP0 positive controls before and after wrote
the pool successfully. An initial run without WM cache maintenance failed its
positive control and must not be used as evidence about pool routing.

For a complete comparison of every 32-bit word, including address-offset
controls, use the idle WM v6 probe:

```sh
python3 tools/icap_address_probe.py --config /path/to/unit-config.json \
  --out /path/to/new-address-results
```

This builds `tools/icap_memory_probe.c` with the firmware's clang/LLVM toolchain.
If the receiving host lacks that toolchain, supply `--helper /path/to/helper.bin`
with the same source compiled at entry address `0x90d100`. The helper fills and
compares all 1 MiB of reserved capture RAM and the 16 KiB window, synchronizing
the WM cache around each operation. It saves and restores its idle pack-buffer
scratch area and capture configuration, and leaves streaming stopped. Use a
freshly loaded WM when changing helper machine code, because this diagnostic
does not invalidate the instruction cache. The tool checks the live partition
before writing and does not reallocate or fill the PLE/PSE regions.
Restart the host stream receiver when resuming normal streaming, then restart
the spectrum controller and verify actual received samples. In this run, the
long-lived host receiver initially classified the resumed stream as late;
restarting that service restored reception without reloading device firmware.

The 2026-10-15 full-word run completed 23 cases: one no-capture control, four
ICAP0 pool controls, three ICAP1 window controls and fifteen ICAP1 candidate
pool-routing cases. Each capture was 1 KiB, with start addresses of `0`,
`0x100` or `0x800` in four-byte units. ICAP0 changed exactly the expected 256
words at pool byte offsets `0`, `0x400` and `0x2000`, confirming
`address = 0xe0400000 + 4 * AddrSTART`. ICAP1 window controls also changed
256 words. All fifteen bit-11 routing cases changed zero words in both the
complete capture pool and window; these included combinations with bit 0,
bit 21, and the permitted fields from the vendor type-0 route.

Read-only inspection of the running ROM's layout interfaces (`0x822a2e` and
`0x822a50`), partition globals and GCI field tables agreed with this layout:

| Region | MCU address range | Size |
| --- | --- | --- |
| ICAP | `0xe0400000..0xe04fffff` | 1 MiB |
| PLE | `0xe0500000..0xe051ffff` | 128 KiB |
| PSE | `0xe0520000..0xe055ffff` | 256 KiB |

The actual PLE/PSE PBUF registers were `0x820c0004 = 0x04000800` and
`0x820c8004 = 0x84800400`; their offset fields decode to banks 512 and 576
in 2 KiB units. These partition interfaces describe one ICAP allocation,
without an engine argument. They do not establish that ICAP1 can access it.
The result rules out missed writes between the earlier sparse sentinels for
the tested settings. Other routing configurations or destinations remain
unproven; PLE/PSE and other memory were not searched for capture data.

## TX waveform investigation

The 2026-10-15 investigation located test-transmit call paths in a vendor WM
test image, but did not identify an arbitrary-IQ upload or replay interface.
The receiver firmware still implements RX only. The findings below are offline
code evidence, with no RF-transmit validation.

Two containers were inspected: the receiving host's `mt7996_wm.bin`, build
`20240809121753`, SHA-256
`2a681abee705f544ce143992ae950c96fedb548d9402b38ad660329c049b7659`,
and `mt7996_wm_tm.bin`, build `20250904203046`, SHA-256
`d151b55245f35f8df26ca76e1d1f74148cc1d3a84e351b649086842cad5de6f8`.
The test image and MediaTek-authored driver sources came from the
[feed mirror at commit 8513a267](https://github.com/tagatac/mtk-openwrt-feeds/tree/8513a267adf5a1ca4b0272a77bfd18de3806626d),
under `autobuild/unified/filogic/mac80211/24.10/files/package/kernel/mt76/src/firmware/mt7996/`.
These are dated snapshots, not a claim about every vendor release.

For that exact test image, the IN_RFTEST dispatcher at `0xe0126946` uses the
20-entry table at `0xe0152608`. It resolves these paths:

| Function index | Wrapper | Register-programming function | Observed behavior |
| --- | --- | --- | --- |
| `0x03` | `0xe00d4eac` | `0xe012a9ec` | Tone-generator setup, including band `+0xfc00..+0xfc0c` |
| `0x04` | `0xe00d4ed4` | `0xe012a86a` | Tone cleanup |
| `0x05` | `0xe00d4e64` | `0xe012a450` | Mode-selected continuous test signal |
| `0x06` | `0xe00d4e94` | `0xe012a3d6` | Continuous-signal stop and register restore |

These are **vendor firmware code addresses**, not MMIO addresses or callable
entries in our running WM. For function `0x05`, the wrapper consumes bytes
0, 1, 2, 3, 6, 7, 8 and the 16-bit field at offset 4. The callee selects a
configuration through the table at `0xe0152828`, writes the band signal
generator's `+0xa008..+0xa04c` words and supporting controls, then writes
`0xc1000001` to `0x8308a004 + (band << 20)`. Stop writes zero there. The traced
path contains no IQ-buffer pointer/count pair or sample-upload loop. Several
Andes instructions remain unknown to LLVM, so this is a bounded inspection,
not a complete firmware control-flow proof.

The [MediaTek testmode patch](https://github.com/tagatac/mtk-openwrt-feeds/blob/8513a267adf5a1ca4b0272a77bfd18de3806626d/autobuild/autobuild_5.4_mac80211_release/mt7988_wifi7_mac80211_mlo/package/kernel/mt76/patches/0036-mtk-mt76-mt7996-add-basic-testmode-support.patch)
also provides a separate RF_AT command route: `mt7996_tm_set_tx_cont` sets
TX mode, rate, antenna mask and `CONT_WAVE_MODE = 3` (OFDM), then command 10.
Its `SET_PAYLOAD = 103` identifier does not establish an IQ sample interface.
The inspected `atenl` continuous-TX handler forwards antenna, PHY mode and
rate parameters to this driver interface.

Reproduce container inspection on a host with Python and OpenSSL; add
`--disassemble` when clang, ld.lld and llvm-objdump are installed:

```sh
python3 tools/tx_firmware_inspect.py /path/to/mt7996_wm_tm.bin \
  --out /path/to/new-offline-results --disassemble
```

The tool validates the container CRC and region bounds, decrypts the regions,
and saves hashes, strings and optional disassembly. It has no device access.
String matches such as `Waveform Sync Fail` refer to DPD synchronization and
are not evidence of arbitrary-waveform replay. A usable IQ transmitter still
requires discovery of a writable sample store or FIFO, its connection to the
TX datapath, sample format/rate and repeat or refill control. The identified
tone/test-signal routes provide starting points for that work.
