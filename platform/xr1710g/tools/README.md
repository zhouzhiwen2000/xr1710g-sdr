# Host tools

These scripts accept connection settings as arguments and contain no unit data
or credentials. They work on Windows and Linux with Python 3.10+; the serial
console also needs pyserial. Keep transcripts, images, keys and captures outside
the checkout. Firmware FITs include a unit SSH host key and must not be committed.

```sh
python3 platform/xr1710g/tools/serial_console.py \
  --port /dev/ttyUSB0 --log /path/to/private-data/serial.log
```

Use `--port COM6` on Windows. Send one JSON request per line on stdin:
`{"command":"printenv bootargs","seconds":1}`, `{"seconds":1}` to read pending
output, or `{"close":true}` to release the port. Only one program can own it.

```sh
python3 platform/xr1710g/tools/tftp_serve.py \
  --root /path/to/private-images --bind 192.168.255.2
```

The TFTP server allows reads only and exits after one transfer. On Linux,
binding UDP 69 normally requires root or a port redirect. It does not write the
router: verify the transfer size and FIT hashes at the U-Boot console before
booting or installing an image.

```sh
python3 platform/xr1710g/tools/ssh_relay.py \
  --listen 192.0.2.2:2222 --target 192.0.2.1:22 --peer 192.0.2.3
```

The relay accepts only the named peer. It forwards TCP; the target still checks
the SSH key. It does not carry the UDP IQ stream. Use a direct host-to-router
connection when both control and IQ interfaces are available on the Linux host.
