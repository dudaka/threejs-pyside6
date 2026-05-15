"""Parser for Object.ifo (asset_id -> resource path manifest).

Ported from silk-nav. Format::

    Line 1: JMXVOBJI1000
    Line 2: <count>
    Line N: <id> 0x<flag-8-hex> "<path>"

`<path>` is the BSR/CPD/BMS file under the Data tree (mostly under
``res\\...``). silknav's resolver walks the suffix chain to terminal
``.bms``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OBJECT_IFO_MAGIC = "JMXVOBJI1000"

_ENTRY_RE = re.compile(r'^\s*(\d+)\s+0x([0-9a-fA-F]+)\s+"(.+)"\s*$')


class IfoParseError(ValueError):
    """Raised when an .ifo manifest can't be parsed."""


@dataclass
class ObjectIfoEntry:
    """One asset entry in Object.ifo."""

    asset_id: int
    flag: int
    path: str


def parse_object_ifo(text: str) -> dict[int, ObjectIfoEntry]:
    """Parse Object.ifo text; return ``{asset_id: entry}``."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != OBJECT_IFO_MAGIC:
        head = lines[0].strip() if lines else "<empty>"
        raise IfoParseError(f"expected magic {OBJECT_IFO_MAGIC!r} at line 1; got {head!r}")
    if len(lines) < 2:
        raise IfoParseError("Object.ifo must have at least magic + count lines")
    try:
        int(lines[1].strip())
    except ValueError as exc:
        raise IfoParseError(f"line 2 must be an integer count, got {lines[1]!r}") from exc

    out: dict[int, ObjectIfoEntry] = {}
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
        m = _ENTRY_RE.match(line)
        if m is None:
            raise IfoParseError(f"malformed Object.ifo entry: {line!r}")
        asset_id = int(m.group(1))
        flag = int(m.group(2), 16)
        path = m.group(3)
        out[asset_id] = ObjectIfoEntry(asset_id=asset_id, flag=flag, path=path)
    return out
