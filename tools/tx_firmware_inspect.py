#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inspect an MT7996 WM image offline; never connects to or programs a device.

Validate its CRC and region bounds, decrypt regions using the documented
container format, list TX-related strings, and optionally disassemble. String
matches and disassembly are leads, not proof of a working RF capability.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'firmware'))
import aes

KEYWORDS = re.compile(r'play.?back|waveform|siggen|signal.?gen|tx.?tone|tone.?tx|'
                      r'sram|i/q|iq.?data|data.?iq|tx.?iq|iq.?tx|pattern|rftest|testmode', re.I)


def unpack(image):
    if len(image) < 76:
        raise ValueError('short RAM container')
    if zlib.crc32(image[:-4]) != struct.unpack_from('<I', image, len(image)-4)[0]:
        raise ValueError('container CRC mismatch')
    trailer = image[-36:]
    count = trailer[2]
    if trailer[3] != 2 or not 1 <= count <= 14:
        raise ValueError('unsupported container format or region count')
    table = len(image)-36-count*40
    if table < 0:
        raise ValueError('region table outside image')
    regions, offset = [], 0
    for i in range(count):
        desc = table+i*40
        if any(struct.unpack_from('<III', image, desc)):
            raise ValueError('compressed regions unsupported')
        address, size, feature = struct.unpack_from('<IIB', image, desc+16)
        if size <= 16 or (size-16) % 16 or offset+size > table or address+size > 2**32:
            raise ValueError(f'region {i}: invalid bounds or AES block length')
        body = image[offset:offset+size-16]
        if feature & 1:
            body = aes.cbc_zero(body, encrypt=False)
        regions.append((address, feature, body))
        offset += size
    return trailer, regions


def inspect(source, out, disassemble=False):
    image = source.read_bytes()
    trailer, regions = unpack(image)
    out.mkdir(parents=True, exist_ok=False)
    rows, all_strings, tx_strings = [], [], []
    for i, (address, feature, body) in enumerate(regions):
        name = f'region-{i}-{address:08x}.bin'
        (out/name).write_bytes(body)
        rows.append({'index': i, 'address': hex(address), 'length': len(body),
                     'feature': feature, 'file': name, 'sha256': hashlib.sha256(body).hexdigest()})
        for match in re.finditer(rb'[\x20-\x7e]{5,}', body):
            value = match.group().decode('ascii')
            line = f'{address+match.start():08x} {value}'
            all_strings.append(line)
            if KEYWORDS.search(value):
                tx_strings.append(line)
    info = {'source_file': source.name, 'source_sha256': hashlib.sha256(image).hexdigest(),
            'chip_id': trailer[0], 'version': trailer[7:17].decode('ascii', errors='replace').rstrip('\0'),
            'build_date': trailer[17:32].decode('ascii', errors='replace').rstrip('\0'),
            'regions': rows,
            'limits': 'Offline inspection only. Data regions are also disassembled; they are not necessarily code. '
                      'LLVM may show unknown Andes instructions. No complete control-flow proof or RF test.'}
    (out/'manifest.json').write_text(json.dumps(info, indent=2)+'\n')
    (out/'strings.txt').write_text('\n'.join(all_strings)+'\n')
    (out/'tx-strings.txt').write_text('\n'.join(tx_strings)+'\n')
    if disassemble:
        asm, linker = [], ['SECTIONS {']
        for row in sorted(rows, key=lambda row: int(row['address'], 16)):
            i = row['index']
            asm.append(f'.section .text.r{i},"ax",@progbits\n.incbin "{row["file"]}"')
            linker.append(f'.r{i} {row["address"]} : {{ *(.text.r{i}) }}')
        linker.append('}')
        (out/'image.S').write_text('\n'.join(asm)+'\n')
        (out/'image.ld').write_text('\n'.join(linker)+'\n')
        subprocess.run(['clang', '--target=riscv32', '-march=rv32imac', '-c', 'image.S', '-o', 'image.o'], cwd=out, check=True)
        subprocess.run(['ld.lld', '-m', 'elf32lriscv', '--entry='+rows[0]['address'], '-T', 'image.ld',
                        'image.o', '-o', 'image.elf'], cwd=out, check=True)
        with (out/'disassembly.txt').open('w') as output:
            subprocess.run(['llvm-objdump', '-d', '--no-show-raw-insn', 'image.elf'], cwd=out, stdout=output, check=True)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='uncompressed MT7996 RAM container')
    parser.add_argument('--out', type=Path, required=True, help='new output directory')
    parser.add_argument('--disassemble', action='store_true', help='also use clang, ld.lld and llvm-objdump')
    args = parser.parse_args()
    print(json.dumps(inspect(args.image, args.out, args.disassemble), indent=2))


if __name__ == '__main__':
    main()
