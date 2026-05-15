# SRO Navmesh Viewer

`navmesh.py` + `web/navmesh.html` + `parsers/` form a desktop viewer for SRO region navmesh data. Renders region-level (`.nvm`) and per-BMS-object (slot 7) edges as colored line segments in a Three.js scene with a native PySide6 control panel on the left.

## How to run

```bash
uv run python navmesh.py
# override the region:
uv run python navmesh.py --nvm /path/to/nv_XXXX.nvm
# override asset roots (default /Users/hodung/Workspace/silkroad/sro-data):
uv run python navmesh.py --map-root /path/to/Map --data-root /path/to/Data
```

Env vars: `NAVMESH_NVM_PATH`, `NAVMESH_MAP_ROOT`, `NAVMESH_DATA_ROOT`.

Default loads region 5c87 (Hotan Kingdom castle area).

## File map

```
parsers/
  __init__.py        re-exports
  nvm.py             RTNavMeshTerrain (.nvm) — full file:
                       objects / cells / edges / tilemap / heightmap / planes
  ifo.py             Object.ifo text manifest: asset_id -> resource path
  bsr.py             JMXVRES (.bsr) + JMXVCPD (.cpd) container readers
                       parse_bsr(blob)             -> collision .bms path  (slot 7)
                       parse_bsr_mesh_paths(blob)  -> list[str]            (slot 1, full visual list)
                       parse_bsr_name(blob)        -> "oas_hot_c1_castle"
                       resolve_bms_path(source, p) -> walks .cpd -> .bsr -> .bms chain
  bms.py             JMXVBMS (.bms):
                       parse_bms(blob)         -> BmsNavMesh (slot 7 navmesh section)
                       parse_bms_visual(blob)  -> BmsVisualMesh (slots 0/2; ported, unused)
  filesystem.py      FilesystemDataSource: backslash-path lookup, case-insensitive fallback
                                          UTF-8 with latin-1 fallback for ISO-8859 Object.ifo

navmesh.py           the app (loader + window + tree + JS bridge)
web/navmesh.html     Three.js scene + JS interactivity API
```

The other top-level scripts (`main.py`, `bench.py`, `bridge.py`, `sweep.py`) are unrelated benchmarks and demos; see `CLAUDE.md`.

## Data flow

1. **Parse** the `.nvm` to get region structure (objects, cells, edges, heightmap).
2. **Walk Object.ifo → BSR/CPD → BMS** for each unique asset_id placed in the region.
3. **Parse BMS slot 7** to get each asset's local navmesh: vertices + global_edges + internal_edges (also captures the BSR's friendly name for tree labels).
4. **Lift NVM 2D edges to 3D** via bilinear heightmap sampling at each endpoint (silk-nav gotcha #24).
5. **Bake BMS edges to world space** with the yaw-negated placement transform ([gotcha #12](GOTCHAS.md#12-bms-placement-yaw-is-negated-in-the-localworld-transform)).
6. **Group by (kind, source, bucket, flag)** — each leaf in the tree is one homogeneous group.
7. **Pack groups into one binary blob** served at `/edges.bin` over the loopback HTTP server.
8. **JS parses the blob** into `Map<group_id, LineSegments>` + `Map<group_id, Points>` and adds them to the scene.
9. **Tree events trigger one-way `runJavaScript` calls** to set visibility / fade / zoom.

## Wire format (`/edges.bin`)

Little-endian throughout. All offsets in floats are 4 bytes.

```
u32 group_count
per group:
    u32 group_id       (assigned by Python at build time; stable for one process lifetime)
    u32 segment_count  (line segments to render = number of edges)
    u32 endpoint_count (= 2 * edge_count; each edge contributes 2 endpoint markers)
    segment_count * 7 * f32   (x0, y0, z0, x1, y1, z1, flag)
    endpoint_count * 3 * f32  (x, y, z)
```

For 5c87: 57 groups, 3,512 edges, ~180 KB total blob.

The `flag` column is float-encoded `int8` (always 0..255 in practice). JS reads it via `f | 0`.

If you need to extend the format later (e.g., per-edge cell IDs for endpoint labels), add new fields *after* the existing ones in each per-group record so old readers ignore them gracefully — or bump the implicit version by adding a magic header at the top.

## JS bridge API (called via `QWebEngineView.runJavaScript`)

All three are no-ops if the scene hasn't loaded yet (defensive `&&` check):

- `window.setGroupVisible(groupId, visible)` — toggle a single group's `LineSegments.visible`.
- `window.setSelectedGroups(groupIds)` — fade non-selected to opacity 0.12. Empty array = no fade. **If exactly one id is passed, also show that group's endpoint marker `Points` cloud.** Multi-id selections (parent rows) hide all markers.
- `window.zoomToGroups(groupIds)` — snap camera to fit the union AABB of the listed groups' visible meshes.

The data flow is one-way Python → JS. There is no `QWebChannel` and no JS → Python signal.

## Tree (`QTreeWidget`) structure

```
[x] Navmesh                                   [edge_count]
   [x] Region 0x5c87 (id 23687)               [edge_count]
       [x] Internal edges                     [edge_count]
           [x] 0x02 Block src->dst            [256]    <- leaf, has group_id
           [x] 0x04 Internal                  [263]    <- leaf
       [x] Global edges                       [64]
           [x] 0x08 Global                    [64]
[x] BMS objects                               [edge_count]
   [x] [01] asset 681 - oas_hot_c1_castle     [...]
       [x] Internal edges
           [x] 0x03 Blocked                   [N]
           [x] 0x04 Internal                  [N]
           [x] 0x07 Blocked + Internal        [N]
       [x] Global edges
           ...
```

Per-item Qt data roles:

- `Qt.UserRole`     → `int group_id` (leaves only)
- `Qt.UserRole + 1` → `list[int] descendant_leaf_ids` (every node)

Tristate cascade: parents have `Qt.ItemIsAutoTristate`; checking/unchecking a parent propagates to children, each fires `itemChanged`. The handler (`MainWindow._on_item_changed`) ignores non-leaves to avoid double-processing.

## Interactivity recipes

**Toggle visibility**: user clicks a checkbox → `_on_item_changed` → for leaves, `setGroupVisible(id, checked)`. For parents, the cascade handles it.

**Click-to-zoom + fade**: user clicks a row → `_on_current_changed` fires → `setSelectedGroups(leaf_ids)` then `zoomToGroups(leaf_ids)`. Selecting a single leaf also reveals its endpoint markers.

**Reset**: button click → clears tree current item + `setSelectedGroups([])` + `zoomToGroups(all_ids)`. Returns to no-fade, no-markers, full view.

## 5c87 baselines (sanity numbers)

When testing changes, these values should hold for `nv_5c87.nvm`:

- 22 BMS placements, 11 unique assets
- NVM: 22 objects, 233 cells (142 open), 519 internal edges, 64 global edges
- NVM heightmap: min 0.0, max 251.7, mean 204.8
- NVM tilemap: 8895 walkable, 321 blocked
- BMS edges: ~2929 total (c1_castle 1848, c2_castle 319, c3_castle 319, palms 7×11=77, etc.)
- 57 edge groups total (3 NVM + 54 BMS)
- Wire blob: ~180 KB
- Notable: zero `0x03` walls in NVM ([gotcha #14](GOTCHAS.md#14-nvm-terrain-has-zero-0x03-walls--all-walls-live-in-bms-files)); all walls live in BMS files. The 1206 wall edges visible in 5c87 all come from BMS placements.

Verify after parser changes:

```bash
uv run python -c "
import sys; sys.argv=['x']
from pathlib import Path
from navmesh import collect_edge_groups, _resolve_bms_navmeshes, pack_groups_blob
from parsers import parse_nvm
p = Path('/Users/hodung/Workspace/silkroad/sro-data/Data/navmesh/nv_5c87.nvm')
nv = parse_nvm(p)
bb = _resolve_bms_navmeshes(nv, Path('/Users/hodung/Workspace/silkroad/sro-data/Map'),
                                Path('/Users/hodung/Workspace/silkroad/sro-data/Data'))
groups, _ = collect_edge_groups(nv, p, bb)
print(f'groups={len(groups)} edges={sum(g.edge_count for g in groups)} '
      f'BMS unique={len(bb)} blob={len(pack_groups_blob(groups))} bytes')
"
# Expected: groups=57 edges=3512 BMS unique=11 blob=183312 bytes
```

## Color palette (kept in sync)

Defined identically in two places: `navmesh.py::flag_color_hex` and `web/navmesh.html::edgeColor`. **Update both together.** Decision tree:

```
block = flag & 0x03
if   block == 0x03:  blocked       #ff3030 (red)
elif block != 0:     one-way       #ff8c1a (orange)
elif flag & 0x10:    underpass     #ffd54f (yellow)
elif flag & 0x08:    global        #40c4ff (cyan)
elif flag & 0x04:    internal      #80c980 (pale green)
else:                other         #ff00ff (magenta)
```

The label text (`edge_flag_label`) decomposes EdgeFlag bits and joins with `+`:
- `0x07` → `Blocked + Internal`
- `0x0a` → `Global + Block dst->src`
- `0x00` → `None`
- (etc.)

## Extension points

- **Multi-region zone**: load N `.nvm` files; for each non-anchor region, offset edge XYZ by `(dx_sector*1920, 0, dz_sector*1920)` from the center region's local frame. Tree gets one `Region 0x...` node per loaded `.nvm`. See deferred work in `docs/ROADMAP.md` and silk-nav's `_common.ZONES` for the canonical filename list.
- **Cell-ID labels**: deferred. Would need: (a) parser change in `parsers/bms.py::_read_nav_edges` to keep srcCell/dstCell columns; (b) wire format extension to send per-edge cell IDs; (c) CSS2DRenderer overlay; (d) cap to small groups (< 50 edges) to avoid DOM blowup. NVM edges already carry `assoc_cell` in the parsed `NvmInternalEdge` / `NvmGlobalEdge` dataclasses.
- **Save/restore state**: write a JSON sidecar next to the `.nvm` storing unchecked group IDs + camera pose. Load on startup before showing the window.
- **Tree search**: filter rows by typed substring. Qt has `QTreeWidget.findItems` + custom hide logic.
- **Region info panel**: sibling widget that shows current selected item's stats (per-flag breakdown, AABB, BSR metadata).

## Known visual quirks

- LineBasicMaterial line width is hardware-clamped to 1px on most GPUs. Lines are crisp but thin. Use `Line2` from addons if you need fat lines (vendored in `web/vendor/three/examples/jsm/lines/`).
- Endpoint marker Points use `sizeAttenuation: false` so they stay readable when zoomed out. Size is fixed at 6px screen-space.
- The 0x00 (None) flag bucket renders magenta. These are BMS edges with no flag bits set; in 5c87 there are 61 of them. They're not errors, just unflagged.
- Direction arrows for one-way (0x01/0x02) edges were tried and intentionally removed — see `docs/ROADMAP.md`.
