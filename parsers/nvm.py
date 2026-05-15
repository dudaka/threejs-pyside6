"""Parser for SRO ``.nvm`` (RTNavMeshTerrain) files.

Wire spec: ``SilkroadDoc.wiki/JMXVNVM`` (signature ``JMXVNVM 1000``).
Ported from ``silk-nav`` (parsers/nvm.py); bit-exact vs the C# extractor
in that project's audit harness.

Layout (little-endian throughout):
    char[12]  signature                       -- "JMXVNVM 1000"
    u16       object_count
    NvmObject objects[object_count]           -- with variable link_edges
    i32       cell_total_count
    i32       cell_open_count
    NvmCell   cells[cell_total_count]         -- with variable object_indices
    i32       global_edge_count
    NvmGlobalEdge global_edges[global_edge_count]
    i32       internal_edge_count
    NvmInternalEdge internal_edges[internal_edge_count]
    Tile      tiles[96 * 96]                  -- {i32 cell_id, u16 flag, u16 texture_id}
    float     height_map[97 * 97]
    u8        plane_type_map[6 * 6]
    float     plane_height_map[6 * 6]
    EOF

Heightmap indexing: ``hm[z_row, x_col]`` (row index is local Z, column
is local X). Y is height.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

NVM_MAGIC: bytes = b"JMXVNVM 1000"
TILE_GRID_SIZE: int = 96
HEIGHTMAP_SIZE: int = 97
PLANE_GRID_SIZE: int = 6

_TILE_DTYPE = np.dtype([("cell_id", "<i4"), ("flag", "<u2"), ("texture_id", "<u2")])

# asset_id(i32), pos(3f), type(i16), yaw(f32), local_uid(i16), short0(i16),
# is_big(bool), is_struct(bool), region_id(u16)
_OBJECT_HEADER_FMT = "<i3fhfhh??H"
_OBJECT_HEADER_SIZE = struct.calcsize(_OBJECT_HEADER_FMT)
_LINK_EDGE_FMT = "<3h"
_LINK_EDGE_SIZE = struct.calcsize(_LINK_EDGE_FMT)
_CELL_RECT_FMT = "<4f"
_CELL_RECT_SIZE = struct.calcsize(_CELL_RECT_FMT)
# min(2f), max(2f), flag(u8), assoc_dir[2](i8), assoc_cell[2](i16), assoc_region[2](i16)
_GLOBAL_EDGE_FMT = "<4fB2b2h2h"
_GLOBAL_EDGE_SIZE = struct.calcsize(_GLOBAL_EDGE_FMT)
# Same as global but without assoc_region.
_INTERNAL_EDGE_FMT = "<4fB2b2h"
_INTERNAL_EDGE_SIZE = struct.calcsize(_INTERNAL_EDGE_FMT)


@dataclass
class LinkEdge:
    """Link between an edge of this object and an edge of another object in the same region."""

    linked_obj_id: int
    linked_obj_edge_id: int
    edge_id: int


@dataclass
class NvmObject:
    """Placement of an RTNavMeshObj (.bms) instance within a region."""

    asset_id: int
    local_position: NDArray[np.float32]  # (3,), (x, y, z) with y = height
    type: int  # -1 = Static, 0 = SkinedNavMesh
    yaw: float
    local_uid: int
    short0: int
    is_big: bool
    is_struct: bool
    region_id: int  # owning region; instance is "owned" if equals file's region
    link_edges: list[LinkEdge] = field(default_factory=list)


@dataclass
class NvmCell:
    """Quadtree-merged walkable cell."""

    min: NDArray[np.float32]  # (2,), 2D X-Z corner
    max: NDArray[np.float32]
    object_indices: NDArray[np.uint16]


@dataclass
class NvmGlobalEdge:
    """Edge on the region perimeter, linking two regions bidirectionally."""

    min: NDArray[np.float32]
    max: NDArray[np.float32]
    flag: int
    assoc_direction: tuple[int, int]
    assoc_cell: tuple[int, int]
    assoc_region: tuple[int, int]


@dataclass
class NvmInternalEdge:
    """Edge between two cells within the same region."""

    min: NDArray[np.float32]
    max: NDArray[np.float32]
    flag: int
    assoc_direction: tuple[int, int]
    assoc_cell: tuple[int, int]


@dataclass
class Navmesh:
    """Parsed RTNavMeshTerrain (.nvm) file."""

    objects: list[NvmObject]
    cell_open_count: int
    cells: list[NvmCell]
    global_edges: list[NvmGlobalEdge]
    internal_edges: list[NvmInternalEdge]
    tiles: NDArray  # shape (96, 96), structured: {cell_id i32, flag u2, texture_id u2}
    height_map: NDArray[np.float32]  # (97, 97); index as [z_row, x_col]
    plane_type_map: NDArray[np.uint8]  # (6, 6); 0=None, 1=Water, 2=Ice
    plane_height_map: NDArray[np.float32]  # (6, 6)


class NvmParseError(ValueError):
    """Raised when an .nvm file's bytes don't match the expected layout."""


def parse_nvm(path: Path | str) -> Navmesh:
    """Parse an SRO ``.nvm`` file from disk."""
    data = Path(path).read_bytes()
    cursor = 0

    if len(data) < len(NVM_MAGIC):
        raise NvmParseError(f"file too small to contain magic ({len(data)} bytes)")
    if data[: len(NVM_MAGIC)] != NVM_MAGIC:
        raise NvmParseError(f"bad magic: {data[: len(NVM_MAGIC)]!r}")
    cursor += len(NVM_MAGIC)

    objects, cursor = _read_object_list(data, cursor)
    cells, cell_open_count, cursor = _read_cell_list(data, cursor)
    global_edges, cursor = _read_global_edges(data, cursor)
    internal_edges, cursor = _read_internal_edges(data, cursor)
    tiles, cursor = _read_tile_map(data, cursor)
    height_map, cursor = _read_height_map(data, cursor)
    plane_type_map, plane_height_map, cursor = _read_plane_maps(data, cursor)

    if cursor != len(data):
        raise NvmParseError(
            f"trailing bytes: parsed {cursor} of {len(data)} ({len(data) - cursor} unread)"
        )

    return Navmesh(
        objects=objects,
        cell_open_count=cell_open_count,
        cells=cells,
        global_edges=global_edges,
        internal_edges=internal_edges,
        tiles=tiles,
        height_map=height_map,
        plane_type_map=plane_type_map,
        plane_height_map=plane_height_map,
    )


def _read_object_list(data: bytes, cursor: int) -> tuple[list[NvmObject], int]:
    (object_count,) = struct.unpack_from("<H", data, cursor)
    cursor += 2
    objects: list[NvmObject] = []
    for _ in range(object_count):
        (
            asset_id,
            x,
            y,
            z,
            obj_type,
            yaw,
            local_uid,
            short0,
            is_big,
            is_struct,
            region_id,
        ) = struct.unpack_from(_OBJECT_HEADER_FMT, data, cursor)
        cursor += _OBJECT_HEADER_SIZE

        (link_edge_count,) = struct.unpack_from("<H", data, cursor)
        cursor += 2
        link_edges: list[LinkEdge] = []
        for _ in range(link_edge_count):
            link_edges.append(LinkEdge(*struct.unpack_from(_LINK_EDGE_FMT, data, cursor)))
            cursor += _LINK_EDGE_SIZE

        objects.append(
            NvmObject(
                asset_id=asset_id,
                local_position=np.array([x, y, z], dtype=np.float32),
                type=obj_type,
                yaw=yaw,
                local_uid=local_uid,
                short0=short0,
                is_big=is_big,
                is_struct=is_struct,
                region_id=region_id,
                link_edges=link_edges,
            )
        )
    return objects, cursor


def _read_cell_list(data: bytes, cursor: int) -> tuple[list[NvmCell], int, int]:
    cell_total_count, cell_open_count = struct.unpack_from("<2i", data, cursor)
    cursor += 8
    cells: list[NvmCell] = []
    for _ in range(cell_total_count):
        min_x, min_z, max_x, max_z = struct.unpack_from(_CELL_RECT_FMT, data, cursor)
        cursor += _CELL_RECT_SIZE
        (obj_count,) = struct.unpack_from("<B", data, cursor)
        cursor += 1
        if obj_count:
            indices = np.frombuffer(data, dtype="<u2", count=obj_count, offset=cursor).copy()
            cursor += 2 * obj_count
        else:
            indices = np.empty(0, dtype=np.uint16)
        cells.append(
            NvmCell(
                min=np.array([min_x, min_z], dtype=np.float32),
                max=np.array([max_x, max_z], dtype=np.float32),
                object_indices=indices,
            )
        )
    return cells, cell_open_count, cursor


def _read_global_edges(data: bytes, cursor: int) -> tuple[list[NvmGlobalEdge], int]:
    (count,) = struct.unpack_from("<i", data, cursor)
    cursor += 4
    edges: list[NvmGlobalEdge] = []
    for _ in range(count):
        min_x, min_z, max_x, max_z, flag, d0, d1, c0, c1, r0, r1 = struct.unpack_from(
            _GLOBAL_EDGE_FMT, data, cursor
        )
        cursor += _GLOBAL_EDGE_SIZE
        edges.append(
            NvmGlobalEdge(
                min=np.array([min_x, min_z], dtype=np.float32),
                max=np.array([max_x, max_z], dtype=np.float32),
                flag=flag,
                assoc_direction=(d0, d1),
                assoc_cell=(c0, c1),
                assoc_region=(r0, r1),
            )
        )
    return edges, cursor


def _read_internal_edges(data: bytes, cursor: int) -> tuple[list[NvmInternalEdge], int]:
    (count,) = struct.unpack_from("<i", data, cursor)
    cursor += 4
    edges: list[NvmInternalEdge] = []
    for _ in range(count):
        min_x, min_z, max_x, max_z, flag, d0, d1, c0, c1 = struct.unpack_from(
            _INTERNAL_EDGE_FMT, data, cursor
        )
        cursor += _INTERNAL_EDGE_SIZE
        edges.append(
            NvmInternalEdge(
                min=np.array([min_x, min_z], dtype=np.float32),
                max=np.array([max_x, max_z], dtype=np.float32),
                flag=flag,
                assoc_direction=(d0, d1),
                assoc_cell=(c0, c1),
            )
        )
    return edges, cursor


def _read_tile_map(data: bytes, cursor: int) -> tuple[NDArray, int]:
    n = TILE_GRID_SIZE * TILE_GRID_SIZE
    size = n * _TILE_DTYPE.itemsize
    tiles = (
        np.frombuffer(data, dtype=_TILE_DTYPE, count=n, offset=cursor)
        .reshape((TILE_GRID_SIZE, TILE_GRID_SIZE))
        .copy()
    )
    return tiles, cursor + size


def _read_height_map(data: bytes, cursor: int) -> tuple[NDArray[np.float32], int]:
    n = HEIGHTMAP_SIZE * HEIGHTMAP_SIZE
    size = n * 4
    hm = (
        np.frombuffer(data, dtype="<f4", count=n, offset=cursor)
        .reshape((HEIGHTMAP_SIZE, HEIGHTMAP_SIZE))
        .copy()
    )
    return hm, cursor + size


def _read_plane_maps(
    data: bytes, cursor: int
) -> tuple[NDArray[np.uint8], NDArray[np.float32], int]:
    n = PLANE_GRID_SIZE * PLANE_GRID_SIZE
    plane_type = (
        np.frombuffer(data, dtype=np.uint8, count=n, offset=cursor)
        .reshape((PLANE_GRID_SIZE, PLANE_GRID_SIZE))
        .copy()
    )
    cursor += n
    plane_height = (
        np.frombuffer(data, dtype="<f4", count=n, offset=cursor)
        .reshape((PLANE_GRID_SIZE, PLANE_GRID_SIZE))
        .copy()
    )
    cursor += n * 4
    return plane_type, plane_height, cursor
