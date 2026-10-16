#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the standalone DSP image and WM pipeline probe; never deploy them."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
FIRMWARE = HERE.parents[1]
sys.path.insert(0, str(FIRMWARE))
import container

FLAGS = [
    "--target=riscv32", "-march=rv32im_zicsr_zifencei", "-mabi=ilp32",
    "-mno-relax", "-msmall-data-limit=0", "-O3", "-ffreestanding",
    "-fno-builtin", "-fno-pic", "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables", "-Wall", "-Werror",
]


def compile_image(out, name, sources, address, entry, limit):
    objects = []
    for index, source in enumerate(sources):
        obj = out / f"{name}-{index}.o"
        subprocess.run(["clang", *FLAGS, "-c", str(source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    script = out / f"{name}.ld"
    script.write_text(
        f"SECTIONS {{ . = {address:#x}; .text : {{ KEEP(*(.text.entry)) "
        "*(.text .text.*) *(.rodata .rodata.*) . = ALIGN(4); } "
        ".unexpected : { *(.data .data.* .sdata .sdata.* .bss .bss.* .sbss .sbss.* COMMON) } "
        'ASSERT(SIZEOF(.unexpected) == 0, "unexpected writable static data") '
        "/DISCARD/ : { *(.comment) *(.riscv.attributes) } }"
    )
    elf, binary = out / f"{name}.elf", out / f"{name}.bin"
    subprocess.run(["ld.lld", "-m", "elf32lriscv", "-T", str(script),
                    f"--entry={entry}", "--no-relax", *objects, "-o", str(elf)], check=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", "-j", ".text", str(elf), str(binary)], check=True)
    raw = binary.read_bytes()
    if not raw or len(raw) >= limit or len(raw) % 4:
        raise RuntimeError(f"invalid {name} size: {len(raw)}")
    return raw


def ram_container(code):
    """The same DSP memory initialization for diagnostics and optional RX use."""
    table = []
    for index in range(4096):
        angle = 2 * math.pi * index / 4096
        co = max(-32768, min(32767, round(math.cos(angle) * 32768)))
        si = max(-32768, min(32767, round(math.sin(angle) * 32768)))
        table.append(((co & 65535) << 16) | (si & 65535))
    regions = []
    for band in range(3):
        data = bytearray(65536)
        struct.pack_into("<4096I", data, 0x1000, *table)
        for offset in (0x5f80, 0xbc00, 0xdc00):
            struct.pack_into("<16I", data, offset, *[0xdead1234] * 16)
        regions.extend([(0x86000000 + band * 0x400000, code, 1),
                        (0x86200000 + band * 0x400000, data, 1)])
    raw = container.ram_container(regions, "DSPNCO0002", "20261016000000")
    decoded = container.read_ram_container(raw)
    if len(decoded) != len(regions):
        raise RuntimeError("container region count mismatch")
    for (address, payload, flags), (actual, data, actual_flags) in zip(regions, decoded):
        if (address, flags, payload) != (actual, actual_flags, data[:len(payload)]):
            raise RuntimeError("container round-trip mismatch")
    return raw


def dsp_sources():
    return [HERE / "start.S", HERE / "main.c", HERE.parent / "nco_q15.S",
            HERE.parent / "nco_pack_q15.S", FIRMWARE / "stream/pack.S"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = dsp_sources()
    code = compile_image(out, "dsp-nco-code", sources, 0, "_start", 0x4000)
    pipeline = compile_image(out, "pipeline_async", [HERE / "pipeline.c"], 0x90b000, "entry", 0x3000)
    raw = ram_container(code)
    (out / "dsp-nco.bin").write_bytes(raw)
    source_paths = [*sources, HERE / "pipeline.c", Path(__file__).resolve(), FIRMWARE / "container.py"]
    manifest = {
        "code_bytes": len(code), "container_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pipeline_bytes": len(pipeline), "pipeline_sha256": hashlib.sha256(pipeline).hexdigest(),
        "sources": {str(path.relative_to(FIRMWARE)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in source_paths},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
