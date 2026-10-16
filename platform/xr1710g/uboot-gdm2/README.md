# XR1710G GDM2 recovery Ethernet

This patch adds GDM2 / RTL8261 PHY8 Ethernet recovery to the XR1710G
chainloaded U-Boot. It targets the `http-uboot` source snapshot at commit
`53b73174c0fcb192a355e79710cdef5be61b14af` (U-Boot 2026.07), whose original
`drivers/net/airoha_eth.c` SHA256 is
`82b41aa92dac58cf5a75bb32249cc3398ba7d0bebaf54aab4d77d05a4038a73d`.
It is separate from the upstream U-Boot used by the platform loader builds.

Apply it to a clean copy of that source:

```sh
git apply --check /path/to/0001-xr1710g-gdm2-recovery.patch
git apply /path/to/0001-xr1710g-gdm2-recovery.patch
```

The board device tree enables `airoha,pon-recovery` and provides PON MAC/PHY
resets. The driver initializes the PON PCS, synchronizes PHY8, enables GDM2
channel 0, and includes port 2 in automatic discovery and reply routing.
The GDM4 PCS retains its own state and register mapping.

Recovery port selection uses the existing environment variable:

| `recovery_port` | Selection |
| --- | --- |
| Unset or `auto` | Automatic discovery, including GDM2 |
| `2` or `wan` | GDM2 / PHY8 |
| `4` or `10g` | GDM4 / PHY5 |

`10g` names the GDM4 path; it does not mean either available 10GbE connector.
`recovery_debug=1` prints descriptor and port diagnostics when networking stops.
These variables can be changed in RAM without saving the environment.

Two descriptor fixes support reliable TFTP. RX follows FIFO order, so an even
descriptor awaiting paired recycling cannot be delivered twice. Network
stop/start preserves initialized TX/RX rings and their indices, discarding only
completed RX packets. Disabling DMA alone can leave prefetched descriptors in
the engine; rebuilding those rings caused periodic retransmissions.

## Validation and installation boundary

On XR1710G, RAM builds using both lwIP and the legacy network stack completed
ping and repeated TFTP transfers over the GDM2 / PHY8 10GbE connection. Tests
covered forced port 2, automatic selection and an unset `recovery_port`.
Received 1 MiB payloads passed SHA256; a 7,002,380-byte Linux FIT passed SHA256
and byte comparison. Consecutive transfers measured approximately 20–25 MiB/s
with a Linux host running dnsmasq TFTP. These are file-transfer measurements,
not the physical Ethernet line rate. Initial link setup can take longer.

The RAM test builds disabled flash drivers, persistent environment and recovery
flashing. This patch does not include that test harness and does not itself
disable flashing in an ordinary U-Boot build.

A production build, `U-Boot 2026.07-gdm2-v1`, was installed in the separate
`0x00600000–0x006fffff` chainloader slot. It retains the existing prefix and
handoff shim, with the FIT entry at slot offset `0x2100`. Full-slot readback
matched the new image. The original first 6 MiB, factory volume, both saved
environments and Linux FIT matched their pre-update contents byte for byte.
The original bootloader subsequently loaded the new second stage from NAND;
ping and consecutive TFTP transfers passed at approximately 20 MiB/s.
Automatic boot into the existing Linux FIT also passed, followed by SSH and
9000-byte IP packet checks on the 10GbE link.

The original bootloader and its boot area must remain preserved. This directory
supplies no bootloader flashing procedure; packaging and the target device's
actual layout must be verified separately.

The uncabled GDM4 connector, HTTP recovery, power-on cold boot and simultaneous
use of both connectors have not been validated with this patch.
