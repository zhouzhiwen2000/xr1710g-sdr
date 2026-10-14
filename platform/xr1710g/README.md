# XR1710G receiver

This profile boots the SDR receiver on an XR1710G through its existing
chainloaded U-Boot. It does not build or install another bootloader, expose
NAND to Linux, or run the W1700K installer. The original Secure Boot
bootloader and the unit's DSD/calibration remain outside the test kernel.

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
