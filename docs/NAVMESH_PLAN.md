# Navmesh overlay plan (Phase 2)

Design doc for the next phase. The user named this as the next work after the Hotan Kingdom zone visual landed. Goal: render silk-nav's polygon-based walkable surface + walls as a togglable overlay on top of the existing zone scene.

## Motivation

The zone visual (Phase 1) shows what Hotan Kingdom *looks like* in textured 3D. It doesn't answer:

- "Where can an agent walk?"
- "What's blocked vs passable?"
- "Are these two areas connected?"

The navmesh overlay answers all three. It's the visualization layer that turns the demo from "pretty render" into "interactive playground for nav decisions". It's also the prerequisite for Phase 3 (click-to-pathfind) — the same polygon data the overlay renders is what A* + funnel consume.

## What we're rendering

Three overlay layers, all toggleable independently from the zone visual:

### Layer A — Walkable surface

Translucent green mesh of every walkable triangle. Two kinds of walkable triangles, one mesh:

- **Terrain triangles** from silk-nav's `merge_walkable_polygons` — per-tile (96×96 of them per region) shapely-subtracted polygons triangulated with walls + closed-loop interiors clipped out.
- **BMS-surface triangles** from `bms_walkable_triangles` — for each elevated building walkable surface (castle walkways, plaza tops), the BMS navmesh-section triangles, with Y clamped to `max(vertex_y, terrain_y) + 1.5` so they sit visibly above terrain.

Together these form the polygon graph (Phase 12 of silk-nav). On Hotan Kingdom: ~104k walkable polygon nodes / 295k edges / ~190 connected components.

### Layer B — Walls

Red `LineSegments` for every `0x03` wall segment from `compute_obstacle_segments`. ~1253 walls on Hotan Kingdom's 5c87 region alone; ~5-10k zone-wide (TBD).

### Layer C — Connected-component coloring (optional)

Color each walkable triangle by its CC id. Largest CCs get distinct colors (e.g. tab10 palette); small CCs get a muted gray. Makes reachability obvious: clicking on a yellow triangle reaches every other yellow triangle, never a blue one.

Hotan Kingdom: CC#0 = 56k nodes (6 regions, most-walkable), CC#1 = 18k nodes (4 regions, castle interior), tail = ~188 smaller CCs. The color coding is the obvious next-cheap insight after the basic overlay works.

## Data flow

```
silk-nav.nav.merge.merge_walkable_polygons (per region)
  + silk-nav.nav.polygon_graph.bms_walkable_triangles
  + silk-nav.parsers.bms_assets.compute_obstacle_segments
  + silk-nav.nav.build_zone_polygon_graph (zone stitching + CCs)
        |
        v   new silk-nav script: scripts/export_zone_navmesh.py
        v   (analogous to render_merged_walkable_in_blender.py's compute phase)
+----------------------------+
| <zone>_navmesh.json        |
|   walkable_triangles {     |
|     vertices, faces,       |
|     cc_ids, kinds          |
|   }                        |
|   walls { segments }       |
|   cc_stats                 |
+----------------------------+
        |
        v   (vendor: copy into our repo)
        v
data/silknav_export/zone_<name>_navmesh.json
        |
        v   scripts/build_zone.py reads + passes through (or no change at all)
        v
web/zones/<zone>/navmesh.json
        |
        v   web/zone.html: fetch + build overlay layers
        v
   Three.js overlay (walkable mesh + wall lines + CC colors)
```

## Proposed JSON shape

```json
{
  "zone": "hotan_kingdom",
  "center_region": "5c87",
  "regions": ["5b87", "5b88", "5c86", "5c87", "5c88", "5d87", "5e87"],
  "walkable_triangles": {
    "vertices": [[x, y, z], ...],     // zone-frame coords, SRO LH Y-up (renderer flips Z)
    "faces": [[i0, i1, i2], ...],
    "cc_ids": [0, 0, 1, 1, ...],      // one per triangle
    "kinds": [0, 1, 0, ...]           // 0=terrain, 1=bms_reachable
  },
  "walls": {
    "segments": [[[x1, y1, z1], [x2, y2, z2]], ...]   // zone-frame; line segments
  },
  "cc_stats": [
    {"id": 0, "size": 56098, "regions": ["5c87", "5d87", ...]},
    {"id": 1, "size": 18432, "regions": ["5c87", "5b87", ...]}
  ]
}
```

Y values are heightmap Y per vertex (from `triangle_surface_y` for terrain, BMS clamp for buildings). Zone-frame XZ — the silk-nav `build_zone_polygon_graph` already does the per-region translation into zone frame.

Coord convention: same as the zone visual (silk-nav LH Y-up; we flip Z when constructing BufferGeometry). Walls are 2D in silk-nav (only XZ matter for blocking) but we add a Y from heightmap sampling so they render as a vertical strip not a flat line.

## Three.js rendering specifics

### Walkable surface (one mesh, color attribute for CC)

- Single `THREE.BufferGeometry` for the whole zone (~104k tris). One draw call.
- Position attribute: `vertices` array, Z-flipped.
- Color attribute: per-vertex RGB based on `cc_id` (palette-indexed) or per-triangle if we prefer flat colors. Per-vertex is the Three.js-native path.
- Material: `MeshStandardMaterial({ vertexColors: true, transparent: true, opacity: 0.55, side: DoubleSide })`.
- Y-lift: render at `y + 0.5` (or use `polygonOffset: true, polygonOffsetFactor: -1` on the material) to avoid z-fighting with terrain.

### Walls (one LineSegments)

- Single `THREE.LineSegments` for the whole zone.
- Position attribute: `walls.segments` flat-list, Z-flipped. Each segment contributes 2 vertices (one line).
- Material: `LineBasicMaterial({ color: 0xff4444, transparent: true, opacity: 0.85 })`.
- Y-lift: render at `y + 1.0` so walls are visibly above the walkable mesh.

### Toggle UI

Simplest: extend the HUD with checkboxes for "Walkable", "Walls", "CC colors". Toggle bound to `mesh.visible` and to the walkable mesh's material `vertexColors` flag.

Future polish: a sidebar (HTML overlay, not Three.js) with the silk-nav demo's hierarchical asset toggles. Out of scope for v0 of this phase.

## Implementation steps

1. **silk-nav side: add export script.** New `silk-nav/scripts/export_zone_navmesh.py`. Loads the 7 regions, builds `ZonePolygonGraph` via `build_zone_polygon_graph`, runs `compute_obstacle_segments` per region (translated to zone frame), computes CCs via BFS, dumps the JSON shape above. Reference: silk-nav's `scripts/render_merged_walkable_in_blender.py` for the compute-phase shape.
2. **Vendor the JSON.** Copy `<zone>_navmesh.json` into `data/silknav_export/`. Optionally extend `scripts/build_zone.py` to copy it through to `web/zones/<zone>/navmesh.json` (or symlink, or load from `data/` directly).
3. **Three.js renderer extension.** New JS module or in-line in `web/zone.html`:
   - Fetch `navmesh.json` alongside the manifest.
   - Build the walkable `BufferGeometry` + LineSegments (above).
   - Add to `scene` as a separate `Group` (`Navmesh_<zone>`).
   - Wire HUD toggles.
4. **CC color palette.** Use Three.js's `Color` palette or a small predefined tab10-equivalent. Pre-compute per-vertex color attribute at load time from `cc_ids`.
5. **Validation.** Visual against silk-nav's `render_merged_walkable_in_blender.py` Blender output for 5c87 (1253 walls, 17828 terrain tris, 694 BMS tris). Cross-region: confirm 5d87↔5c87 seam stitches cleanly (silk-nav reports 38 cross-region terrain edges at z=1920).

## Open design questions

1. **Where does the export run?** silk-nav's repo, or ours? Recommendation: silk-nav's repo. Mirrors the existing pattern (silk-nav owns SRO+nav; we vendor outputs).
2. **One JSON or one-per-region?** Zone-scale is ~104k tris which is a few MB of JSON. One zone-level JSON is simpler and works. If load latency becomes an issue, switch to a binary `.glb` or raw `BufferGeometry` payload (per silk-nav `docs/ROADMAP.md` Phase 13 perf notes — `build_polygon_graph` shared-edge stitching is the ~22 s pure-Python floor).
3. **CC visualization on by default?** Recommendation: yes, on by default — it's the most informative view, and the HUD toggle lets the user turn it off.
4. **Click-to-pathfind in this phase or next?** Recommendation: NEXT phase. Get the overlay rendering right first. Adding pathfinding requires picking, which adds raycasting + UI complexity.
5. **Render walls as 3D strips or 2D lines?** Recommendation: start with 2D `LineBasicMaterial` lines (cheap; matches silk-nav's PyVista demo). Upgrade to thin extruded quads (`LineSegments2` from `examples/jsm/lines/`) only if visual quality demands it.
6. **Translucent overlay sorting?** With `transparent: true` and `opacity: 0.55`, alpha sorting is needed. For 104k tris this could be slow in WebGL. Mitigation: use `depthWrite: false` so the walkable mesh doesn't occlude itself; pair with rendering-order tweaks. Test before optimizing.

## Acceptance criteria

- [ ] Zone-level navmesh JSON exists for Hotan Kingdom (`data/silknav_export/zone_hotan_kingdom_navmesh.json`).
- [ ] `web/zone.html` loads and renders walkable surface (toggleable).
- [ ] `web/zone.html` loads and renders walls as LineSegments (toggleable).
- [ ] CC color-coding visible (toggleable independently from walkable mesh).
- [ ] HUD shows triangle/wall counts + active CCs.
- [ ] Render fps stays at vsync cap (~30) with overlay enabled.
- [ ] Visual parity check: render compared to silk-nav's PyVista `Merged walkable surface` toggle in `scripts/demos/navmesh_qt_place.py --zone hotan_kingdom`.

## Gotchas to watch for

(Pre-emptively documenting what's likely to bite during implementation. Move to `GOTCHAS.md` when they actually bite.)

- **Z-fighting** between terrain and walkable overlay. The +0.5 Y-lift OR `polygonOffset` settings need to be tuned. Watch for shimmering on the terrain texture under the green overlay.
- **Wall Y from heightmap sampling.** silk-nav's wall segments are 2D (XZ only). We synthesize Y. If we use a single Y per segment endpoint, walls along sloped terrain will float or sink. Sample heightmap at each endpoint independently.
- **Cross-region wall positions.** Walls are extracted per-region in silk-nav. When we stitch zone-wide, each region's walls need their region offset applied. Watch for walls floating at sector seams.
- **Per-vertex color attribute size.** 104k tris × 3 verts × 3 floats = ~1.2M floats just for color. Acceptable but not free; sanity-check GPU memory.
- **Connected-component count.** ~190 CCs means the palette needs to wrap. Recommendation: top 10 CCs get distinct colors; everything else shares a gray.

## See also

- silk-nav: `docs/NAVIGATION_DESIGN.md` § 7.6 (multi-region polygon graph), `docs/SRO_NAVMESH.md` (polygon graph data model), `scripts/render_merged_walkable_in_blender.py` (the reference renderer + JSON shape we'll mirror).
- silk-nav source: `silknav.nav.merge` (`merge_walkable_polygons`), `silknav.nav.polygon_graph` (`build_polygon_graph`, `bms_walkable_triangles`), `silknav.nav.zone_polygon_graph` (`build_zone_polygon_graph`).
- [`ROADMAP.md`](ROADMAP.md) — Phase 2 entry pointing here.
- [`PIPELINE.md`](PIPELINE.md) — current zone pipeline (Phase 1). Navmesh overlay extends the renderer, not the pipeline.
