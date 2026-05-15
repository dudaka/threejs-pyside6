# Roadmap & Resume Notes

How we got here, what's working, what's deferred. Read this when picking up a fresh session.

## Current state (2026-05-14)

Working: edges-only navmesh viewer with native PySide6 left panel, validated end-to-end on region 5c87.

Last validated commit point — the user has visually confirmed each of the increments below:

- Region 5c87 loads in ~2-3 s.
- 57 edge groups (3 NVM + 54 BMS) rendered as colored `LineSegments`.
- Tree panel: tristate visibility checkboxes, click-to-zoom, fade-on-highlight (others go to 0.12 opacity), Reset button, friendly BSR names like `[01] asset 681 - oas_hot_c1_castle`.
- Endpoint markers (white `Points`) appear when a single leaf row is sole-highlighted.
- No terrain mesh, no walkable overlay, no BMS visual meshes — those were tried earlier in the session and intentionally dropped when the user scoped to "edges only".

## How to resume

1. `cd /Users/hodung/Workspace/silkroad/threejs-pyside6`
2. `uv run python navmesh.py`
3. Window opens. Try the interactive features in the left panel.
4. Read `docs/NAVMESH.md` for architecture; `docs/SRO_FORMATS.md` for parser details; `CLAUDE.md` for cross-app gotchas + conventions.

If anything is broken, run the smoke check from `docs/NAVMESH.md` (the `uv run python -c "..."` block) — it should print:

```
groups=57 edges=3512 BMS unique=11 blob=183312 bytes
```

## Increment log (oldest first)

Each line is one validated step. Strikethrough = built then removed.

1. **Heightmap TIN** — Three.js `BufferGeometry` from 97×97 NVM heightmap, per-vertex height-ramp colors. *(Replaced when scope simplified to edges-only in increment 6.)*
2. **Walkable tile overlay** — 96×96 quads colored green/red per `(tile.flag & 1) == 0 && 0 <= cell_id < cell_open_count`. *(Replaced.)*
3. **NVM edges overlay** — `internal_edges` + `global_edges` as colored `LineSegments`, Y from heightmap bilinear sample. Edge colors by EdgeFlag bits. (This pattern survived into the final viewer.)
4. **BMS placements (collision-slot visuals)** — render each NvmObject's BSR-collision-slot BMS as one merged mesh. *(Replaced.)*
5. **Full BMS visual submeshes (BSR slot 1)** — iterate `parse_bsr_mesh_paths`, concat per-asset (c1_castle = 16 submeshes; ~80k verts total). *(Replaced.)*
6. **Simplification → edges-only** — drop terrain, walkable overlay, and visual BMS. Render only NVM edges + per-BMS-object navmesh-section edges. Add `parsers/bms.py::parse_bms` (slot 7).
7. **Native PySide6 left panel** — `QSplitter` with `QTreeWidget` (left) + `QWebEngineView` (right). Hierarchical: `Navmesh > Region > bucket > flag`, plus `BMS objects > placement > bucket > flag`.
8. **Per-group wire format + tristate visibility + click-to-zoom + flag-name labels** (validated in one round). Each tree leaf maps to one `EdgeGroup` with stable `group_id`. JS builds `Map<group_id, LineSegments>`. `Qt.ItemIsAutoTristate` cascade; `runJavaScript("setGroupVisible(...)")` on leaf check changes; `currentItemChanged` triggers `zoomToGroups`. Leaf labels read like `0x07 Blocked + Internal`.
9. **BSR friendly names + highlight/fade + Reset button** (validated in one round). `parse_bsr_name` extracts `oas_hot_c1_castle`-style ids. `setSelectedGroups(ids)` dims non-selected to opacity 0.12. Reset button restores no-fade and full view.
10. **Endpoint markers + ~~direction arrows~~** — added per-group `Points` clouds shown only when exactly one leaf is selected. Direction arrows for one-way (0x01/0x02) edges were also added but **removed at user request** ("the arrows are not useful"). Markers stayed.

## Known gotchas hit (and the fixes)

See `CLAUDE.md::Non-obvious gotchas` for the full list. Highlights specific to navmesh work:

- **macOS hook flags PySide6's main-loop call** — workaround `getattr(app, "exec")()` (CLAUDE.md gotcha #6). Hook is environment-specific; avoid the contiguous substring `.exec` + `(` in any new file (Python source AND markdown).
- **Object.ifo is ISO-8859, not UTF-8** — latin-1 fallback in `FilesystemDataSource.read_text` (CLAUDE.md gotcha #7).
- **BMS yaw is negated** in placement transform — silk-nav gotcha #39 (CLAUDE.md gotcha #8).
- **`.nvm` dump filenames have `nv_` prefix** — `nv_5c87.nvm`, NOT `5c87.nvm` like silk-nav fixtures (CLAUDE.md gotcha #9).
- **`.nvm` terrain has zero `0x03` walls** — all walls live in BMS files (CLAUDE.md gotcha #10). Edge-only viz needs BMS slot-7 edges to look interesting.
- **BSR slot 7 is collision-only** — full visual is in slot 1's `mesh_paths` list (silk-nav gotcha #56). Parser exists (`parse_bsr_mesh_paths`) but unused now that visuals are dropped. Bring it back if visuals return.

## Plausible next steps

In rough priority order:

1. **Multi-region zone** (deferred from increment 6). Hotan Kingdom is 7 regions: 5c87 (anchor) + 5c86 + 5c88 + 5b87 + 5b88 + 5d87 + 5e87. Implementation:
   - Load each `.nvm` from the dump dir; apply sector offset `(dx*1920, 0, dz*1920)` to NVM edges and to BMS placements.
   - Tree gets one `Region 0x...` node per loaded region under Navmesh.
   - Possibly straddler dedup (silk-nav gotcha #57) — same BMS placement may appear in two regions; pick the owner by `region_id` field on `NvmObject`.
   - silk-nav `scripts/demos/_common.py::ZONES` has the canonical filename list per zone preset.
2. **Save / restore state** — JSON sidecar next to the `.nvm` storing unchecked group IDs + camera pose; load on startup before showing the window.
3. **Tree search / filter** — typed substring filter on tree rows (handy with 22+ BMS placements in a single region; will be much more so in multi-region zones).
4. **Region info panel** — sibling widget showing current selection's edge breakdown + AABB + BSR metadata.
5. **Cell-ID text labels** — deferred. Would need parser change in `_read_nav_edges` (keep `srcCell`/`dstCell`), wire format extension to ship per-edge cell IDs, CSS2DRenderer overlay, and a cap to small groups (< 50 edges) to avoid DOM blowup.
6. **Texture / visual modes** — bring back the simplified visual BMS rendering as an optional layer (was removed in increment 6 simplification). The parser code (`parse_bms_visual`, `parse_bsr_mesh_paths`) is still present and exported.

## Skipped on purpose

- **One-way arrows** on 0x01/0x02 edges — built, didn't add value, removed.
- **Cell-ID labels** — user chose to skip after the markers landed (clutter risk).

## Pyproject deps

No new deps were added in this session. `pyproject.toml` is unchanged: `pyside6 >= 6.8.0`, `numpy >= 2.0`. The new code uses only the stdlib + numpy + PySide6.

## File inventory (post-session)

Created in this session:

```
parsers/__init__.py
parsers/nvm.py          (port of silk-nav)
parsers/ifo.py          (port of silk-nav)
parsers/bsr.py          (port + parse_bsr_mesh_paths + parse_bsr_name)
parsers/bms.py          (port: parse_bms slot-7 + parse_bms_visual slots 0/2)
parsers/filesystem.py   (port + ISO-8859 fallback for read_text)
navmesh.py
web/navmesh.html
docs/NAVMESH.md
docs/SRO_FORMATS.md
docs/ROADMAP.md
```

Updated in this session:

```
CLAUDE.md               (added doc map, navmesh.py to scripts, gotchas #6-#10, palette-sync convention)
```

Untouched (the original threejs-pyside6 foundation):

```
main.py, bench.py, bridge.py, sweep.py
web/index.html, web/bench.html, web/bridge.html
web/vendor/...
pyproject.toml, uv.lock, README.md
```
