# SRO Binary Formats

Reference for the parsers in `parsers/`. All parsers are direct ports from [silk-nav](https://github.com/dudaka/silk-nav); wire specs cross-checked against the [SilkroadDoc.wiki](https://github.com/DummkopfOfHachtenduden/SilkroadDoc/wiki) entries `JMXVNVM` / `JMXVBMS` / `JMXVRES` / `EdgeFlag`.

## Asset tree on disk

Default location: `/Users/hodung/Workspace/silkroad/sro-data`. The user's pre-extracted Pk2 tree.

```
sro-data/
  Map/
    Object.ifo         <-- asset_id -> resource-path manifest
    Tile2D.ifo         (not yet parsed)
    100/...127/        per-sector terrain mapping (.m / .o / .o2 / .t)
  Data/
    navmesh/
      nv_5c87.nvm      <-- the navmesh dump (note "nv_" prefix; silk-nav gotcha #73)
      object.ifo       (a duplicate; Map/Object.ifo is canonical)
    Res/               .bsr / .cpd resource containers
      Bldg/            buildings
      COS/             special objects
      ...
    Prim/Mesh/         .bms mesh files
      Bldg/...
      nature/...
```

`FilesystemDataSource` (in `parsers/filesystem.py`) accepts the SRO-style backslash paths used by Object.ifo and falls back to case-insensitive lookup at the leaf — the original archives are case-insensitive; macOS APFS is case-preserving so the on-disk name may differ in case.

## Object.ifo (`parsers/ifo.py`)

Plain text. Lines:

```
JMXVOBJI1000           magic
3307                   entry count (informational; not enforced)
00000 0x00000001 "res\bldg\china\cj_ferry\cj_ferry_buil.bsr"
00001 0x00000000 "res\bldg\china\cj_ferry\cj_ferry_warehou.bsr"
...
```

Format per line: `<id> 0x<flag-8-hex> "<path>"`. Path is the BSR/CPD/BMS file under the Data tree (mostly `res\...`). Encoding is ISO-8859 on this user's tree (UTF-8 fallback handled by `FilesystemDataSource.read_text`).

`parse_object_ifo(text) -> dict[asset_id, ObjectIfoEntry]`.

## .bsr / .cpd (`parsers/bsr.py`)

JMXVRES = `.bsr` container; JMXVCPD = `.cpd` container. Both have:

- 12-byte ASCII signature
- N i32 header offsets (BSR=13, CPD=7)
- Length-prefixed strings at the offsets

The asset_id chain is suffix-dispatched: `.cpd → .bsr → .bms`. `resolve_bms_path(source, path)` walks until it hits a `.bms` filename.

### BSR header offsets (slots used by this project)

- **Slot 1** (MeshOffset; "PrimMeshFlag" controls): list of visual `.bms` paths. `parse_bsr_mesh_paths(blob)` reads this. c1_castle has 16 entries here.
- **Slot 7** (CollisionOffset): one collision `.bms` path. `parse_bsr(blob)` reads this. This is a **simplified hull** (silk-nav gotcha #56); the navmesh viewer reads slot 7's BMS for its navmesh-section (slot 7 of the inner BMS, confusingly).
- **ObjectGeneralInformation** (just after the 13-i32 header): `u32 type_id` then `i32 + ASCII` for Name. `parse_bsr_name(blob)` reads this. Used for tree labels (e.g. `oas_hot_c1_castle`).

### CPD

`parse_cpd(blob) -> str` — returns the next-hop path (slot 0 = NavMeshObjOffset).

## .bms (`parsers/bms.py`)

JMXVBMS 0110. 12-byte signature + 12 i32 header offsets.

Each `.bms` packs two distinct mesh datasets:

### Visual mesh (slots 0 + 2)

- Slot 0 = `vertexOffset`: `i32 count`, then per vertex:
  ```
  pos(12) + normal(12) + uv0(8)
  + uv1(8)   if vertex_flag & 0x400
  + morph(36) if vertex_flag & 0x800
  + trailer(12)
  ```
  Base stride = 44 bytes; `vertex_flag` lives at offset 12 + 12*4 + 4 = 64 from the file start.
- Slot 2 = `faceOffset`: `i32 count`, then `count * 3 * u16` indices.

`parse_bms_visual(blob) -> BmsVisualMesh | None`. Currently unused by `navmesh.py` but ported and exported in `parsers/__init__.py`.

### Navmesh section (slot 7)

`parse_bms(blob) -> BmsNavMesh`. Layout starting at `nav_offset`:

```
i32 vertex_count
per vertex: 3 floats (XYZ) + 1 byte normal index   (13 bytes per vertex)
i32 cell_count
per cell:   3 i16 vertex indices + 1 i16 flag      (8 bytes; +1 byte EventZone if NavFlag & 0x02)
i32 global_edge_count
per edge:   4 i16 (v0, v1, srcCell, dstCell) + 1 byte flag  (9 bytes; +1 byte EventZone if NavFlag & 0x01)
i32 internal_edge_count
per edge:   same shape as global
```

The current parser keeps `(v0, v1, flag)` per edge — `srcCell` / `dstCell` are read and dropped. Bring them back if you ever add cell-ID labels (see `docs/NAVMESH.md` extension points).

`NavFlag` is slot 11 of the BMS header (`offsets[11]`); controls trailing EventZone bytes.

## .nvm (`parsers/nvm.py`)

RTNavMeshTerrain. Per-region file. Layout (after `JMXVNVM 1000` magic):

```
u16 object_count
per object: NvmObject (header + variable link_edges)
i32 cell_total_count
i32 cell_open_count
per cell:  NvmCell (rect + variable object_indices)
i32 global_edge_count
per edge:  NvmGlobalEdge (rect + flag + assoc_dir + assoc_cell + assoc_region)
i32 internal_edge_count
per edge:  NvmInternalEdge (same as global, no assoc_region)
Tile tiles[96 * 96]                <-- {i32 cell_id, u16 flag, u16 texture_id}
float height_map[97 * 97]
u8 plane_type_map[6 * 6]           <-- 0=None, 1=Water, 2=Ice
float plane_height_map[6 * 6]
EOF                                <-- parser asserts no trailing bytes
```

Heightmap indexing: `hm[z_row, x_col]`. **Row index is local Z, column is local X.** Not the other way.

`parse_nvm(path) -> Navmesh`. Bit-exact vs the C# extractor in silk-nav's audit harness.

### NvmObject record

Each placed object header is 22 bytes:

```
i32 asset_id
3f  local_position (x, y, z)         y is height
i16 type            (-1 = Static, 0 = SkinedNavMesh)
f32 yaw             radians; NEGATED in placement transform (see CLAUDE.md gotcha #8)
i16 local_uid
i16 short0
?2  is_big, is_struct  (booleans, 1 byte each)
u16 region_id       (owning region; instance is "owned" if equals file's region)
```

Followed by `u16 link_edge_count` and that many `LinkEdge` records.

### NvmInternalEdge / NvmGlobalEdge

Both have 2D X-Z `min` and `max` (4 floats), an `i8` (or `u8`) `flag`, and a 2-element `assoc_direction` (signed bytes). Internal edges add 2-element `assoc_cell` (signed shorts). Global edges add `assoc_cell` AND `assoc_region` (both signed shorts).

`assoc_cell` is the pair of NvmCell indices the edge connects (-1 sentinel = blocked side). For NVM cell-IDs in a future endpoint-label feature, read these.

## EdgeFlag bits (NVM and BMS, same scheme)

```
0x01  BlockDst2Src   blocks edge dst -> src   (one-way passable src -> dst)
0x02  BlockSrc2Dst   blocks edge src -> dst   (one-way passable dst -> src)
0x03  Blocked        both directions blocked  = wall
0x04  Internal       intra-region adjacency marker (passable)
0x08  Global         region/object boundary marker (passable)
0x10  Underpass      bridge underside; passable
0x20  Entrance       dungeon entrance (legacy)
0x80  Siege          siege wall (passable for attackers)
```

Composites are common: `0x07 = Blocked + Internal`, `0x0a = Global + BlockDst2Src`, etc. `edge_flag_label(flag)` in `navmesh.py` decomposes them.

## NavFlag bits (BMS slot 11)

```
0x01  EdgeEventZone   each edge record has a trailing EventZone u8
0x02  CellEventZone   each cell record has a trailing EventZone u8
0x04  EventsFollow    Events section after cells (not parsed)
```

Constants: `NAV_FLAG_EDGE_EVENT_ZONE`, `NAV_FLAG_CELL_EVENT_ZONE` in `parsers/bms.py`.

## Coordinate frames

- **Y is height, Y-up.** X-Z is the horizontal plane.
- **Region size**: 1920 local units per side. Heightmap 97×97 vertices over 96×96 tiles → **20 local units per tile**.
- **RegionID encoding**:
  ```
  region_id = (z_sector << 8) | x_sector       # 16-bit, little-endian
  x_sector  = region_id & 0xFF
  z_sector  = (region_id >> 8) & 0xFF
  is_dungeon = region_id > 32767               # high bit set
  ```
- **World origin sector**: `(X_SECTOR_ORIGIN=135, Z_SECTOR_ORIGIN=92)` per silk-nav. Region 5c87 = sector (0x87, 0x5c) = (135, 92) = exactly the origin sector.
- **Multi-region offset**: each region's vertices are in its own local frame. To stitch in a zone frame, offset every non-anchor region by `(dx_sector*1920, 0, dz_sector*1920)` from the anchor region.

## BMS placement transform (yaw negated!)

```python
cs = math.cos(-yaw)
sn = math.sin(-yaw)
world_x = cs * local_x + sn * local_z + obj.local_position[0]
world_z = -sn * local_x + cs * local_z + obj.local_position[2]
world_y = local_y + obj.local_position[1]
```

Per silk-nav gotcha #39. SRO is left-handed; Three.js is right-handed. **Don't change the sign** without testing against silk-nav's PyVista demo for the same region.

## silk-nav: canonical reference

`/Users/hodung/Workspace/silkroad/silk-nav` — sibling project on this user's machine. The Python parsers in `parsers/` are direct ports; the wire layouts are bit-exact-validated there (4 fixture regions × ~37k field comparisons, 0 mismatches in their CI). silk-nav also has:

- `silknav.viz` (PyVista) demos that this project intentionally does NOT mirror — we render in Three.js instead.
- `docs/SRO_NAVMESH.md`, `docs/SRO_VISUALS.md`, `docs/GOTCHAS.md` — deeper material on the formats and pipelines, including ~80 numbered gotchas.

When confused about a parser, read silk-nav's equivalent (`src/silknav/parsers/*.py`) and the SilkroadDoc.wiki entry, then reconcile.
