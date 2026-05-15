"""SRO binary parsers (.nvm, .bsr, .bms, ...).

Ported from `silk-nav` (https://github.com/dudaka/silk-nav). Wire specs
follow `SilkroadDoc.wiki` (JMXVNVM / JMXVBMS / JMXVRES).
"""

from .bms import BmsNavMesh, BmsParseError, BmsVisualMesh, parse_bms, parse_bms_visual
from .bsr import (
    ResourceParseError,
    parse_bsr,
    parse_bsr_mesh_paths,
    parse_bsr_name,
    parse_cpd,
    resolve_bms_path,
)
from .filesystem import FilesystemDataSource
from .ifo import IfoParseError, ObjectIfoEntry, parse_object_ifo
from .nvm import (
    HEIGHTMAP_SIZE,
    NVM_MAGIC,
    PLANE_GRID_SIZE,
    TILE_GRID_SIZE,
    Navmesh,
    NvmParseError,
    parse_nvm,
)

__all__ = [
    # nvm
    "HEIGHTMAP_SIZE",
    "NVM_MAGIC",
    "PLANE_GRID_SIZE",
    "TILE_GRID_SIZE",
    "Navmesh",
    "NvmParseError",
    "parse_nvm",
    # ifo
    "IfoParseError",
    "ObjectIfoEntry",
    "parse_object_ifo",
    # bsr / resolver
    "ResourceParseError",
    "parse_bsr",
    "parse_bsr_mesh_paths",
    "parse_bsr_name",
    "parse_cpd",
    "resolve_bms_path",
    # bms (visual + navmesh sections)
    "BmsNavMesh",
    "BmsParseError",
    "BmsVisualMesh",
    "parse_bms",
    "parse_bms_visual",
    # filesystem
    "FilesystemDataSource",
]
