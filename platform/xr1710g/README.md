# XR1710G RAM trial

This profile boots the SDR receiver on an XR1710G through its existing
chainloaded U-Boot. It does not build or install another bootloader, expose
NAND, create UBI volumes, or run the W1700K installer. The original Secure Boot
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

The test management address is 192.168.255.1/24 on eth2 (GDM4, LAN2). For GDM2/eth1,
move the addresses through the serial console. The default MTU is 1500.
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

The software check suite ran 313 tests with 13 skips and no failures, including
five XR1710G storage/device-tree guard tests. The skipped tests require upstream
images that were not built for this RAM profile. Continuous UDP transport,
long-duration captures, higher sample rates and calibrated RF measurements
have not been validated on XR1710G.
