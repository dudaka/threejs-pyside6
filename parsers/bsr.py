"""Resolver for SRO .bsr / .cpd resource containers.

Ported from silk-nav. The .bsr header has 13 i32 offsets; slot 7 is
the collision section, which starts with the embedded .bms path. We
read just enough of the container to resolve ``asset_id -> .bms``.
The full visual mesh list (slot 1) is left for a follow-up increment
(silk-nav gotcha #56: c1_castle ships 16 visual submeshes there).
"""

from __future__ import annotations

import struct
from typing import Protocol

SIG_LENGTH = 12
BSR_SIG_PREFIX = b"JMXVRES"
CPD_SIG_PREFIX = b"JMXVCPD"
BSR_HEADER_COUNT = 13
CPD_HEADER_COUNT = 7
BSR_MESH_LIST_SLOT = 1   # MeshOffset: list of visual .bms paths (full render)
BSR_PRIM_MESH_FLAG_SLOT = 8  # bit 0 = each mesh entry has a trailing u32
BSR_COLLISION_SLOT = 7   # collision .bms path (simplified hull)
CPD_NAVMESH_SLOT = 0


class _DataSource(Protocol):
    def file_exists(self, path: str) -> bool: ...

    def read_file(self, path: str) -> bytes: ...


class ResourceParseError(ValueError):
    """Raised when a .bsr / .cpd container is malformed or unresolvable."""


def _read_length_prefixed_string(data: bytes, pos: int) -> str:
    """Read ``i32 byte_count + ASCII bytes`` (NUL-trimmed) starting at ``pos``."""
    if pos + 4 > len(data):
        raise ResourceParseError(f"length prefix runs past EOF at offset {pos}")
    (count,) = struct.unpack_from("<i", data, pos)
    if count <= 0:
        return ""
    if pos + 4 + count > len(data):
        raise ResourceParseError(f"string of {count} bytes at offset {pos} runs past EOF")
    raw = data[pos + 4 : pos + 4 + count]
    nul = raw.find(b"\x00")
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode("ascii", errors="replace")


def parse_bsr(data: bytes) -> str:
    """Extract the embedded .bms path from a JMXVRES (.bsr) blob (collision slot)."""
    if len(data) < SIG_LENGTH + BSR_HEADER_COUNT * 4:
        raise ResourceParseError(f".bsr too small ({len(data)} bytes)")
    if not data[:SIG_LENGTH].startswith(BSR_SIG_PREFIX):
        raise ResourceParseError(f".bsr bad signature: {data[:SIG_LENGTH]!r}")
    offsets = struct.unpack_from(f"<{BSR_HEADER_COUNT}i", data, SIG_LENGTH)
    collision_off = offsets[BSR_COLLISION_SLOT]
    if collision_off <= 0:
        raise ResourceParseError(".bsr has no collision offset")
    return _read_length_prefixed_string(data, collision_off)


def parse_bsr_name(data: bytes) -> str:
    """Return the BSR's ObjectGeneralInformation Name (the asset's friendly id).

    Layout (per silk-nav `parse_bsr_metadata` / SilkroadDoc.wiki/JMXVRES)::

        char[12] signature
        13 x i32 header offsets
        u32      type_id
        i32 + ASCII  name (length-prefixed)

    Empty string if the file is too small or has no name.
    """
    header_end = SIG_LENGTH + BSR_HEADER_COUNT * 4
    if len(data) < header_end + 4:
        return ""
    if not data[:SIG_LENGTH].startswith(BSR_SIG_PREFIX):
        raise ResourceParseError(f".bsr bad signature: {data[:SIG_LENGTH]!r}")
    name_off = header_end + 4  # skip type_id
    try:
        return _read_length_prefixed_string(data, name_off)
    except ResourceParseError:
        return ""


def parse_bsr_mesh_paths(data: bytes) -> list[str]:
    """Return the visual ``.bms`` paths listed at BSR slot 1 (may be empty).

    Slot 1 layout (per silk-nav `parse_bsr_metadata` / `SilkroadDoc.wiki/JMXVRES`)::

        u32 mesh_count
        for _ in mesh_count:
            i32 path_length + path_length ASCII bytes
            [u32 unk_uint0]           # if header slot 8 (PrimMeshFlag) bit 0 set

    For assets like c1_castle this returns 16 submesh paths; the
    collision-slot path (slot 7, parsed by :func:`parse_bsr`) is just
    one simplified hull.
    """
    if len(data) < SIG_LENGTH + BSR_HEADER_COUNT * 4:
        raise ResourceParseError(f".bsr too small ({len(data)} bytes)")
    if not data[:SIG_LENGTH].startswith(BSR_SIG_PREFIX):
        raise ResourceParseError(f".bsr bad signature: {data[:SIG_LENGTH]!r}")
    offsets = struct.unpack_from(f"<{BSR_HEADER_COUNT}i", data, SIG_LENGTH)
    mesh_list_off = offsets[BSR_MESH_LIST_SLOT]
    prim_mesh_flag = offsets[BSR_PRIM_MESH_FLAG_SLOT]
    if mesh_list_off <= 0 or mesh_list_off + 4 > len(data):
        return []
    (count,) = struct.unpack_from("<I", data, mesh_list_off)
    out: list[str] = []
    cur = mesh_list_off + 4
    for _ in range(min(count, 4096)):  # cap defensively against bad sizes
        path = _read_length_prefixed_string(data, cur)
        (slen,) = struct.unpack_from("<i", data, cur)
        cur += 4 + max(0, slen)
        if prim_mesh_flag & 1:
            cur += 4
        out.append(path)
    return out


def parse_cpd(data: bytes) -> str:
    """Extract the next-hop resource path from a JMXVCPD (.cpd) blob."""
    if len(data) < SIG_LENGTH + CPD_HEADER_COUNT * 4:
        raise ResourceParseError(f".cpd too small ({len(data)} bytes)")
    if not data[:SIG_LENGTH].startswith(CPD_SIG_PREFIX):
        raise ResourceParseError(f".cpd bad signature: {data[:SIG_LENGTH]!r}")
    offsets = struct.unpack_from(f"<{CPD_HEADER_COUNT}i", data, SIG_LENGTH)
    target = offsets[CPD_NAVMESH_SLOT]
    if target <= 0:
        raise ResourceParseError(".cpd has no NavMeshObjOffset")
    return _read_length_prefixed_string(data, target)


def resolve_bms_path(source: _DataSource, path: str, *, max_hops: int = 8) -> str:
    """Walk ``.cpd -> .bsr -> .bms`` (suffix-dispatched). Returns the final .bms path."""
    current = path
    for _ in range(max_hops):
        last = current[-1:].lower()
        if last == "s":  # .bms
            return current
        if not source.file_exists(current):
            raise ResourceParseError(f"resource not in source: {current!r}")
        blob = source.read_file(current)
        if last == "r":  # .bsr
            return parse_bsr(blob)
        if last == "d":  # .cpd
            current = parse_cpd(blob)
            continue
        raise ResourceParseError(
            f"unknown resource extension on {current!r} (expected .bms/.bsr/.cpd)"
        )
    raise ResourceParseError(
        f"resource chain exceeded {max_hops} hops; possible cycle starting at {path!r}"
    )
