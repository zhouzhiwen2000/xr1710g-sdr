#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Serve one U-Boot TFTP download from an explicit directory; refuse uploads."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bind', required=True, help='address on the directly connected NIC')
    parser.add_argument('--port', type=int, default=69)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error('--root must be a directory')
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'init'))
    from initkit.tftp import serve
    return serve(root, args.bind, args.port, False, False, 2.0, 10, True, 16)


if __name__ == '__main__':
    main()
