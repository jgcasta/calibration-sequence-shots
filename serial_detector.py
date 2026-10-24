"""Linux USB serial port detection; pyserial is not required.

Persistent /dev/serial/by-id links are preferred. If several devices are found,
no se selecciona arbitrariamente uno por el mero hecho de ser ttyUSB0.
"""
from dataclasses import dataclass
from pathlib import Path
import glob

@dataclass(frozen=True)
class Port:
    path: str
    target: str
    desc: str
    likely: bool = False


def detect_serial_ports():
    ports = []
    seen = set()
    root = Path('/dev/serial/by-id')
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.exists():
                continue
            target = str(entry.resolve())
            if not (target.startswith('/dev/ttyUSB') or target.startswith('/dev/ttyACM')):
                continue
            name = entry.name.lower()
            likely = any(token in name for token in ('flip', 'optec', '1a86', 'ch340', 'ch341'))
            ports.append(Port(str(entry), target, entry.name, likely))
            seen.add(target)
    for match in sorted(glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')):
        if match not in seen and Path(match).exists():
            ports.append(Port(match, match, Path(match).name))
    return ports


def choose_serial_port(ports, current=''):
    # Respect a user-chosen still-valid device, unless absent or nonexistent.
    if current and Path(current).exists():
        return current, 'existing selection valid'
    if not ports:
        return '', 'no ttyUSB/ttyACM devices found'
    likely = [p for p in ports if p.likely]
    if len(likely) == 1:
        return likely[0].path, 'only candidate matched by USB ID'
    if len(likely) > 1:
        return '', 'multiple USB candidates: select Flip-Flat from the dropdown'
    if len(ports) == 1:
        return ports[0].path, 'only detected serial port (confirm it is Flip-Flat)'
    return '', 'multiple serial ports detected: manual selection required'
