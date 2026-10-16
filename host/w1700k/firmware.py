# SPDX-License-Identifier: MIT
"""Our firmware set built by `make -C firmware`, described by its firmware.json: the ROM patch, the
WM and the WA, in download order."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 2
ROLES = ('patch', 'wm', 'wa')


class FirmwareError(RuntimeError):
    pass


@dataclass(frozen=True)
class Image:
    role: str
    path: Path
    size: int
    sha256: str
    flags: tuple

    def data(self):
        data = self.path.read_bytes()
        if len(data) != self.size or hashlib.sha256(data).hexdigest() != self.sha256:
            raise FirmwareError(f'{self.path} does not match firmware.json; rebuild with make -C firmware')
        return data


@dataclass(frozen=True)
class Firmware:
    containers: tuple             # Images in download order


def _image(entry, directory):
    try:
        role, name, size, digest = entry['role'], entry['file'], entry['size'], entry['sha256']
        flags = tuple(entry.get('flags', ()))
    except (KeyError, TypeError) as exc:
        raise FirmwareError(f'firmware.json: malformed entry ({exc})')
    if '/' in name or not isinstance(size, int) or len(digest) != 64:
        raise FirmwareError(f'firmware.json: malformed entry for {role}')
    return Image(role, directory / name, size, digest, flags)


def parse(manifest, directory, dsp_nco=False):
    """A Firmware from a decoded firmware.json whose files live in `directory`."""
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
        raise FirmwareError(f'firmware.json: expected schema {SCHEMA}; rebuild with make -C firmware')
    directory = Path(directory)
    try:
        containers = tuple(_image(e, directory) for e in manifest['containers'])
    except (KeyError, TypeError) as exc:
        raise FirmwareError(f'firmware.json: {exc}')
    if tuple(c.role for c in containers) != ROLES:
        raise FirmwareError(f'firmware.json: containers must be {", ".join(ROLES)} in that order')
    if dsp_nco:
        if 'dsp_nco' not in manifest:
            raise FirmwareError('firmware has no DSP NCO image; rebuild with make -C firmware')
        dsp = _image(manifest['dsp_nco'], directory)
        if dsp.role != 'dsp' or dsp.flags != ('--dsp',):
            raise FirmwareError('firmware.json: invalid optional DSP NCO image')
        containers = containers[:2] + (dsp,) + containers[2:]
    return Firmware(containers)


def load(directory, dsp_nco=False):
    path = Path(directory) / 'firmware.json'
    if not path.is_file():
        raise FirmwareError(f'{path} not found; build the firmware with make -C firmware')
    try:
        manifest = json.loads(path.read_text())
    except ValueError as exc:
        raise FirmwareError(f'{path}: {exc}')
    return parse(manifest, path.parent, dsp_nco=dsp_nco)
