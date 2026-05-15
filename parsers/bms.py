"""Parser for SRO ``.bms`` visual meshes (JMXVBMS 0110, slots 0/2).

Ported from silk-nav. Reads the *visual* (renderable) mesh -- vertex
buffer at slot 0 + face buffer at slot 2 -- not the navmesh section
(slot 7). For 5c87 placement rendering we want the geometry the game
client draws; the navmesh section is left for future increments.

Per-vertex stride:
  12 position + 12 normal + 8 UV0 + 12 trailer = 44 base
  +8  if vertex_flag & 0x400  (UV1)
  +36 if vertex_flag & 0x800  (morph)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

BMS_SIG_PREFIX = b"JMXVBMS"
SIG_LENGTH = 12
HEADER_OFFSET_COUNT = 12

VERTEX_OFFSET_SLOT = 0
FACE_OFFSET_SLOT = 2
NAVMESH_OFFSET_SLOT = 7
NAV_FLAG_SLOT = 11

NAV_FLAG_EDGE_EVENT_ZONE = 0x01
NAV_FLAG_CELL_EVENT_ZONE = 0x02

_SUBPRIM_OFFSET = SIG_LENGTH + HEADER_OFFSET_COUNT * 4
_VERTEX_FLAG_OFFSET = _SUBPRIM_OFFSET + 4

_VISUAL_STRIDE_BASE = 44
_VERTEX_FLAG_HAS_UV1 = 0x400
_VERTEX_FLAG_HAS_MORPH = 0x800


class BmsParseError(ValueError):
    """Raised when a .bms file is malformed or has wrong signature."""


@dataclass
class BmsVisualMesh:
    """Visual (renderable) mesh from a ``.bms``."""

    vertices: NDArray[np.float32]  # (N, 3)
    faces: NDArray[np.uint16]      # (M, 3)


@dataclass
class BmsNavMesh:
    """Navmesh-section content from a ``.bms`` (slot 7).

    ``vertices`` is shape ``(N, 3)`` XYZ in the object's local frame.
    ``global_edges`` / ``internal_edges`` are stride-3 ``(K, 3)`` int32
    arrays ``(v0, v1, flag)`` -- the srcCell / dstCell columns are
    available in the wire format but dropped here since we only need
    the endpoints + flag for line-segment viz.
    """

    vertices: NDArray[np.float32]
    global_edges: NDArray[np.int32]
    internal_edges: NDArray[np.int32]


def parse_bms(data: bytes) -> BmsNavMesh:
    """Parse the navmesh section (slot 7) of a ``.bms`` blob.

    Returns vertices + (global_edges, internal_edges) as ``(v0, v1, flag)``
    int32 arrays. Files with no navmesh section (decorative props) come
    back with empty arrays -- safe to iterate without a special case.

    NavFlag bits (slot 11):
        0x01 = each edge record has a trailing EventZone byte
        0x02 = each cell record has a trailing EventZone byte
        0x04 = Events section follows (not parsed here)
    """
    if len(data) < SIG_LENGTH + HEADER_OFFSET_COUNT * 4:
        raise BmsParseError(f".bms too small ({len(data)} bytes)")
    if not data[:SIG_LENGTH].startswith(BMS_SIG_PREFIX):
        raise BmsParseError(f".bms bad signature: {data[:SIG_LENGTH]!r}")

    offsets = struct.unpack_from(f"<{HEADER_OFFSET_COUNT}i", data, SIG_LENGTH)
    nav_offset = offsets[NAVMESH_OFFSET_SLOT]
    nav_flag = offsets[NAV_FLAG_SLOT]

    empty = BmsNavMesh(
        vertices=np.zeros((0, 3), dtype=np.float32),
        global_edges=np.zeros((0, 3), dtype=np.int32),
        internal_edges=np.zeros((0, 3), dtype=np.int32),
    )
    if nav_offset <= 0:
        return empty

    edge_has_event_zone = bool(nav_flag & NAV_FLAG_EDGE_EVENT_ZONE)
    cell_has_event_zone = bool(nav_flag & NAV_FLAG_CELL_EVENT_ZONE)

    vertices, after_verts = _read_nav_vertices(data, nav_offset)
    after_cells = _skip_nav_cells(data, after_verts, cell_has_event_zone)
    global_edges, after_global = _read_nav_edges(data, after_cells, edge_has_event_zone)
    internal_edges, _ = _read_nav_edges(data, after_global, edge_has_event_zone)

    return BmsNavMesh(
        vertices=vertices,
        global_edges=global_edges,
        internal_edges=internal_edges,
    )


def _read_nav_vertices(data: bytes, offset: int) -> tuple[NDArray[np.float32], int]:
    """Read ``i32 count`` then ``count * (3f + u8 normal)`` records."""
    if offset + 4 > len(data):
        raise BmsParseError(f"navmesh vertex offset {offset} runs past EOF")
    (count,) = struct.unpack_from("<i", data, offset)
    if count < 0 or count > 1_000_000:
        raise BmsParseError(f"implausible navmesh vertex count {count}")
    stride = 13  # 3 floats + 1 byte normal index
    table_start = offset + 4
    table_end = table_start + count * stride
    if table_end > len(data):
        raise BmsParseError(f"navmesh vertex buffer (count={count}) runs past EOF")
    if count == 0:
        return np.zeros((0, 3), dtype=np.float32), table_end

    out = np.empty((count, 3), dtype=np.float32)
    cursor = table_start
    for i in range(count):
        out[i] = struct.unpack_from("<3f", data, cursor)
        cursor += stride
    return out, table_end


def _skip_nav_cells(data: bytes, offset: int, cell_has_event_zone: bool) -> int:
    """Skip the cell table; return the cursor after it.

    Cells are triangle vertex indices + flag; not needed for edge viz.
    """
    if offset + 4 > len(data):
        raise BmsParseError(f"navmesh cell offset {offset} runs past EOF")
    (count,) = struct.unpack_from("<i", data, offset)
    if count < 0 or count > 1_000_000:
        raise BmsParseError(f"implausible navmesh cell count {count}")
    cell_size = 8 + (1 if cell_has_event_zone else 0)
    end = offset + 4 + count * cell_size
    if end > len(data):
        raise BmsParseError(f"navmesh cell buffer (count={count}) runs past EOF")
    return end


def _read_nav_edges(
    data: bytes, offset: int, edge_has_event_zone: bool
) -> tuple[NDArray[np.int32], int]:
    """Read ``i32 count`` then per-edge records: 4 i16 + u8 flag (+ u8 ev?).

    Returns stride-3 ``(K, 3)`` of ``(v0, v1, flag)``. srcCell / dstCell
    are skipped -- we only need endpoints + flag for line viz.
    """
    if offset + 4 > len(data):
        raise BmsParseError(f"navmesh edge offset {offset} runs past EOF")
    (count,) = struct.unpack_from("<i", data, offset)
    if count < 0 or count > 1_000_000:
        raise BmsParseError(f"implausible navmesh edge count {count}")
    edge_size = 9 + (1 if edge_has_event_zone else 0)  # 4*2 + 1, +1 if event zone
    table_start = offset + 4
    table_end = table_start + count * edge_size
    if table_end > len(data):
        raise BmsParseError(f"navmesh edge buffer (count={count}) runs past EOF")
    if count == 0:
        return np.zeros((0, 3), dtype=np.int32), table_end

    out = np.empty((count, 3), dtype=np.int32)
    cursor = table_start
    for i in range(count):
        v0, v1, _src, _dst = struct.unpack_from("<4h", data, cursor)
        flag = data[cursor + 8]
        out[i, 0] = v0 & 0xFFFF
        out[i, 1] = v1 & 0xFFFF
        out[i, 2] = flag
        cursor += edge_size
    return out, table_end


def parse_bms_visual(data: bytes) -> BmsVisualMesh | None:
    """Parse the visual mesh; return ``None`` if the file has none."""
    if len(data) < SIG_LENGTH + HEADER_OFFSET_COUNT * 4:
        raise BmsParseError(f".bms too small ({len(data)} bytes)")
    if not data[:SIG_LENGTH].startswith(BMS_SIG_PREFIX):
        raise BmsParseError(f".bms bad signature: {data[:SIG_LENGTH]!r}")

    offsets = struct.unpack_from(f"<{HEADER_OFFSET_COUNT}i", data, SIG_LENGTH)
    vertex_offset = offsets[VERTEX_OFFSET_SLOT]
    face_offset = offsets[FACE_OFFSET_SLOT]
    if vertex_offset <= 0 or face_offset <= 0:
        return None

    (vertex_flag,) = struct.unpack_from("<i", data, _VERTEX_FLAG_OFFSET)
    stride = _VISUAL_STRIDE_BASE
    if vertex_flag & _VERTEX_FLAG_HAS_UV1:
        stride += 8
    if vertex_flag & _VERTEX_FLAG_HAS_MORPH:
        stride += 36

    vertices = _read_visual_vertices(data, vertex_offset, stride)
    faces = _read_visual_faces(data, face_offset)
    if faces.size and int(faces.max()) >= len(vertices):
        raise BmsParseError(
            f"visual face index out of range (max={int(faces.max())}, "
            f"vertex_count={len(vertices)})"
        )
    return BmsVisualMesh(vertices=vertices, faces=faces)


def _read_visual_vertices(data: bytes, offset: int, stride: int) -> NDArray[np.float32]:
    """Read ``i32 count`` then ``count * stride`` vertex records, returning XYZ positions."""
    if offset + 4 > len(data):
        raise BmsParseError(f"visual vertex offset {offset} runs past EOF")
    (count,) = struct.unpack_from("<i", data, offset)
    if count < 0 or count > 1_000_000:
        raise BmsParseError(f"implausible visual vertex count {count}")
    table_start = offset + 4
    table_end = table_start + count * stride
    if table_end > len(data):
        raise BmsParseError(f"visual vertex buffer (count={count}) runs past EOF")
    if count == 0:
        return np.zeros((0, 3), dtype=np.float32)

    # Positions are the first 12 bytes of each record; the rest of the stride is
    # normal/UVs/trailer that we don't need for v1. Slice the byte view of the
    # vertex table into a strided float32 read.
    positions = np.empty((count, 3), dtype=np.float32)
    cursor = table_start
    for i in range(count):
        positions[i] = struct.unpack_from("<3f", data, cursor)
        cursor += stride
    return positions


def _read_visual_faces(data: bytes, offset: int) -> NDArray[np.uint16]:
    """Read ``i32 count`` then ``count * 3 * u16`` face indices."""
    if offset + 4 > len(data):
        raise BmsParseError(f"visual face offset {offset} runs past EOF")
    (count,) = struct.unpack_from("<i", data, offset)
    if count < 0 or count > 1_000_000:
        raise BmsParseError(f"implausible visual face count {count}")
    table_start = offset + 4
    table_end = table_start + count * 6
    if table_end > len(data):
        raise BmsParseError(f"visual face buffer (count={count}) runs past EOF")
    if count == 0:
        return np.zeros((0, 3), dtype=np.uint16)
    arr = np.frombuffer(data, dtype=np.uint16, count=count * 3, offset=table_start)
    return arr.reshape(count, 3).copy()
