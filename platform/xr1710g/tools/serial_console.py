#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Serial console controlled by newline-delimited JSON requests on stdin."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='COM port or /dev/ttyUSB device')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--log', type=Path, help='append a private binary transcript outside the repository')
    args = parser.parse_args()
    import serial
    lock, buffer, stop = threading.Lock(), bytearray(), threading.Event()
    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        log = None
        if args.log:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            log = args.log.open('ab', buffering=0)

        def reader():
            try:
                while not stop.is_set():
                    chunk = port.read(4096)
                    if chunk:
                        if log:
                            log.write(chunk)
                        with lock:
                            buffer.extend(chunk)
            except serial.SerialException as error:
                print(json.dumps({'serial_error': str(error)}), flush=True)
                stop.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        print(json.dumps({'ready': True, 'port': args.port}), flush=True)
        try:
            for line in sys.stdin:
                request = json.loads(line)
                if request.get('close') or stop.is_set():
                    break
                if 'command' in request:
                    with lock:
                        buffer.clear()
                    port.write((request['command'] + '\r').encode())
                time.sleep(max(0, min(float(request.get('seconds', 1)), 25)))
                with lock:
                    data = bytes(buffer)
                    buffer.clear()
                print(json.dumps({'serial': data.decode(errors='replace')}), flush=True)
        finally:
            stop.set()
            thread.join(1)
            if log:
                log.close()


if __name__ == '__main__':
    main()
