# Roadmap

Where the project has been and where it's going. Source of truth for status numbers; everything else cross-references back here.

## Status snapshot (2026-05-14)

| Phase | Status | Notes |
|---|---|---|
| 0 — Render-stack scaffolding | ✅ shipped | `main.py`, `bench.py`, `bridge.py`, `sweep.py`; bridge throughput characterized; 5 foundational gotchas captured |
| 1 — Hotan Kingdom zone visual | ✅ shipped 2026-05-13 | 7 regions, ~458 building submeshes, ~196 .o2 decorations, ~330 unique textures, ~140 materials, ~666 draw calls; verified visually against silk-nav's Blender render |
| 1.5 — Standalone navmesh edge viewer | ✅ shipped 2026-05-14 | `navmesh.py` + `parsers/`. Region 5c87, 57 edge groups (3 NVM + 54 BMS), interactive PySide6 left panel with tristate visibility / click-to-zoom / highlight+fade / endpoint markers / Reset. See [`NAVMESH.md`](NAVMESH.md). Establishes the parser path that Phase 2 will reuse. |
| 2 — Navmesh overlays in zone view | 🔜 planned | See [`NAVMESH_PLAN.md`](NAVMESH_PLAN.md). Layer the navmesh data the standalone viewer already parses on top of `web/zone.html`. Includes multi-region load + walkable surface + CC color-coding. |
| 3 — Click-to-pathfind | ⬜ later | Mouse-pick → silk-nav `find_path` + `smooth_path` → polyline overlay + agent dot. Reuses the Phase 2 polygon graph data. |
| 4 — Entity layer | ⬜ later | NPCs / monsters / players via `bridge.py`-style pattern. Free-running QWebChannel ticks, `InstancedMesh` for all entities. Static zone stays as-is; this adds a dynamic layer on top. |
| 5 — Additional zones | ⬜ later | Jangan, Hotan Town, Bandit Fortress, BoI. The pipeline generalizes; the renderer needs URL routing or a zone selector. See [`PIPELINE.md`](PIPELINE.md) § "Adding another zone". |
| v1.0 — Stabilization | ⬜ later | API freeze, performance pass, public-facing docs polish. |

## Goals

1. **A reference Three.js renderer for SRO data.** Treat silk-nav as the upstream "engine" for SRO parsing + nav math; we focus on rendering. (The in-repo `parsers/` package is a direct port of silk-nav's parsers, vendored for the standalone navmesh viewer's needs.)
2. **PySide6-hosted, single-window desktop app.** No browser-only path; the QWebEngineView wrapping is load-bearing because Python owns the data layer.
3. **Validated incrementally.** Each phase ends with a visual or measurable check, not "looks right".

## Non-goals (v1.0)

- Game-client integration (packets, network). Lives in `vtc-sro-bot` or similar.
- Re-implementing SRO parsing as a long-term project. silk-nav owns that; we vendor its output for the zone demo. The ported `parsers/` exist only because the navmesh viewer reads the live PK2 dump tree directly.
- Cross-platform packaging beyond `uv sync`.
- Browser-standalone deployment (no PySide6). The bridge model needs Python.

## Cross-cutting principles

- **Layering (zone demo).** silk-nav (SRO data + nav math) → `data/silknav_export/` → `scripts/build_zone.py` (preprocess) → `web/zones/` → `web/zone.html` (render). Each layer is independent and tested at its boundary.
- **Layering (navmesh viewer).** sro-data PK2 dump (outside repo) → `parsers/` → in-process `EdgeGroup` objects + binary blob → loopback HTTP → `web/navmesh.html`.
- **Three demos, three purposes.** `main.py` is the procedural-scaffolding demo; `zone.py` is the real-SRO textured demo; `navmesh.py` is the real-SRO nav-data demo. Keep all three. Procedural lets us iterate on the renderer stack without large data; the two SRO demos cover orthogonal concerns until Phase 2 unifies them.
- **Static zone vs dynamic entities.** The zone is uploaded once; entities tick. Apply optimization recs to the layer they belong to (see CLAUDE.md "Design recommendations").
- **Pillow for DDS→PNG only.** Don't grow the Python image-processing footprint beyond what `build_zone.py` needs.

---

## Phase 0 — Render-stack scaffolding ✅

**Shipped 2026-05-13 (pre-existing).**

`main.py` (procedural terrain), `bench.py` (uncapped throughput bench), `bridge.py` (Python↔JS Float32Array bridge), `sweep.py` (param sweep over bridge). Three.js r184 vendored. PySide6 6.11+.

**Validation.** Bench output confirms the rendering stack does ~5,300 fps on 200k tris, ~3,100 fps on 6M tris (500k `InstancedMesh`); visible framerate vsync-capped at ~30 Hz in QWebEngineView regardless. Bridge throughput characterized: comfortable to ~25k entities at 30 Hz on base64-Float32Array.

**Risks shipped.** Gotchas #1-#5 (`file://` modules, three.module.js re-export, QByteArray-over-QWebChannel, JS console-message enum, `runJavaScript` callback). See [`GOTCHAS.md`](GOTCHAS.md).

---

## Phase 1 — Hotan Kingdom zone visual ✅

**Shipped 2026-05-13.**

End-to-end render of the 7-region Hotan Kingdom zone with textured terrain, buildings, and decoration props.

**What shipped:**

- `scripts/build_zone.py` — preprocessor: DDS→PNG via Pillow, JSON texture-name remap, manifest emission.
- `web/zone.html` — Three.js scene with per-region Groups, sector-offset stitching, SRO LH→Three.js RH coord conversion (per-vertex Z-flip), UV V-flip, alpha-flag handling (cutout via `alphaTest`), texture+material caching, bird's-eye perspective auto-framing.
- `zone.py` — PySide6 launcher mirroring `main.py`'s pattern but pointing at `/zone.html`.
- `data/silknav_export/` — vendored copy of silk-nav's Blender-pipeline output, ~85 MB. Mirrors silk-nav's `out/{zone_hotan_kingdom, zone_hotan_kingdom_o2, per_region}/` layout. Makes the demo self-contained (no silk-nav working tree dependency at runtime).
- Pillow 12+ added as a Python dependency.

**Validation.**

- `uv run python scripts/build_zone.py` produces `web/zones/hotan_kingdom/` (~102 MB).
- `uv run python zone.py` launches the PySide6 window; loopback HTTP serves all assets (verified via HEAD probes); page loads + renders.
- Scene scale: 7 terrain meshes + ~458 building submeshes + ~196 .o2 decoration submeshes = ~666 draw calls.
- ~330 unique texture uploads, ~140 unique materials.
- 30 fps render (vsync-capped; same as procedural).
- Visual parity check vs silk-nav's `render_zone_in_blender.py` Blender output: shapes, textures, sector positions match. Lighting is approximate (HemisphereLight + DirectionalLight; silk-nav uses Blender EEVEE GTAO).

**Risks shipped.** Gotchas #6-#10 (Z-flip, UV V-flip, alpha 0x200, BC6/BC7 fallback, sector offset via Group.position). See [`GOTCHAS.md`](GOTCHAS.md).

**Open gaps:**

- BC6/BC7 seasonal textures (4 unique) fall back to base_color. Acceptable for now.
- Lighting parity with Blender is approximate. If parity matters, port sun direction + intensity from `render_zone_in_blender.py:render_zone`.
- `web/zone.html` hardcodes `ZONE = 'hotan_kingdom'`. To add another zone we need URL routing or a zone selector UI.
- No benchmarks run yet on the zone scene specifically. Perceived smooth; would be useful to confirm draw-call cost.

---

## Phase 1.5 — Standalone navmesh edge viewer ✅

**Shipped 2026-05-14.**

A separate, self-contained PySide6 + Three.js viewer that parses the live SRO PK2 dump tree (not the vendored `data/silknav_export/`) and renders region-level + per-BMS-object navmesh edges as colored `LineSegments`. Built as a standalone explorer; doubles as a parser + bridge-pattern proof for Phase 2.

**What shipped:**

- `navmesh.py` — PySide6 window with `QSplitter`: `QTreeWidget` (left) + `QWebEngineView` (right). Loads region 5c87 by default; defaults to `/Users/hodung/Workspace/silkroad/sro-data` (override via `--map-root` / `NAVMESH_MAP_ROOT`).
- `web/navmesh.html` — Three.js scene; one `LineSegments` per `EdgeGroup`, indexed by stable `group_id`. `setGroupVisible`, `setSelectedGroups`, `zoomToGroups` JS APIs called fire-and-forget from Python.
- `parsers/` — direct ports from [silk-nav](https://github.com/dudaka/silk-nav):
  - `nvm.py` — region navmesh.
  - `ifo.py` — `Object.ifo` asset manifest.
  - `bsr.py` — `.bsr` slot 7 (collision) + `parse_bsr_mesh_paths` + `parse_bsr_name`.
  - `bms.py` — `.bms` slot 7 (nav edges) + `parse_bms_visual` for slots 0/2 (currently unused; see "Plausible next steps" below).
  - `filesystem.py` — `FilesystemDataSource` with ISO-8859 fallback for `read_text` (gotcha #11).
- `docs/NAVMESH.md` — viewer architecture, wire format, JS bridge API, extension points.
- `docs/SRO_FORMATS.md` — wire layouts for `.nvm` / `.bsr` / `.bms` / `Object.ifo`. EdgeFlag/NavFlag bit reference, coordinate notes.

**Validation.** Visual: region 5c87 loads in ~2-3 s; 57 edge groups render as colored `LineSegments`; tree panel checkboxes toggle visibility (tristate cascade); click-to-zoom frames the AABB of the selected group; sole-leaf selection dims others to 0.12 opacity and shows endpoint markers; Reset button restores defaults. Smoke check (`docs/NAVMESH.md`):

```
groups=57 edges=3512 BMS unique=11 blob=183312 bytes
```

**Risks shipped.** Gotchas #11-#15 (`Object.ifo` ISO-8859, BMS yaw negation, `nv_` prefix, `.nvm` walls live in BMS, macOS `.exec` hook). See [`GOTCHAS.md`](GOTCHAS.md).

**Increment log (oldest first; strikethrough = built then removed).**

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

**Skipped on purpose.**

- One-way arrows on 0x01/0x02 edges — built, didn't add value, removed.
- Cell-ID labels — chose to skip after the markers landed (clutter risk). Would need parser change in `_read_nav_edges` (keep `srcCell`/`dstCell`), wire-format extension, CSS2DRenderer overlay, and a cap to small groups.

**Open gaps (stand-alone viewer):**

- Single-region only (5c87). Multi-region zone load is the first item under Phase 2 below.
- No save/restore of view state across launches (planned: JSON sidecar with unchecked group IDs + camera pose).
- No tree search/filter.

---

## Phase 2 — Navmesh overlays in zone view 🔜

**Status:** planned. Originally named by the user 2026-05-13 as the next phase after the zone visual. Phase 1.5 (standalone viewer) shipped 2026-05-14 to derisk parsers + JS bridge pattern; Phase 2 layers that data into the zone scene.

**Motivation.** The zone visual shows what the world *looks like*. The navmesh overlay shows what the agent *can do* in it — where they can walk, what blocks them, which regions are connected. This is the visualization layer that turns the demo from "pretty render" into "interactive playground for nav decisions". The standalone viewer already proves the parser path; integration is the remaining work.

**Scope (proposed).** Render translucent walkable surface (terrain + reachable BMS triangles) + opaque wall LineSegments on top of the existing zone scene. Toggle on/off. Optional: color triangles by connected-component (CC) so reachability is visible at a glance.

**First sub-task: multi-region load** (deferred from Phase 1.5 increment 6). Hotan Kingdom is 7 regions: 5c87 (anchor) + 5c86 + 5c88 + 5b87 + 5b88 + 5d87 + 5e87.

- Load each `.nvm` from the dump dir; apply sector offset `(dx*1920, 0, dz*1920)` to NVM edges and to BMS placements.
- Tree gets one `Region 0x...` node per loaded region under Navmesh.
- Possibly straddler dedup (silk-nav gotcha #57) — same BMS placement may appear in two regions; pick the owner by `region_id` field on `NvmObject`.
- silk-nav `scripts/demos/_common.py::ZONES` has the canonical filename list per zone preset.

**Open design question.** Reuse the in-repo `parsers/` directly inside `zone.py`, or add a silk-nav-side export script that emits `<zone>_navmesh.json` (walkable triangles + walls + CC labels) and vendor that into `data/silknav_export/`? Standalone viewer chose the former (live PK2 read). Phase 2 leans toward the latter for consistency with the zone pipeline, but reusing the parsers avoids round-tripping through JSON. Decide before starting.

**Detailed plan:** [`NAVMESH_PLAN.md`](NAVMESH_PLAN.md).

**Validation criteria (proposed).**

- Walkable surface visually aligns with terrain (no z-fighting); buildings cast walls visibly on top.
- Wall LineSegments follow building outlines (verifiable against silk-nav's `render_merged_walkable_in_blender.py`).
- 30 fps render maintained even with overlay enabled.
- Toggle layer cleanly hides/shows without rebuild.

**Other plausible items (lower priority).**

- Save / restore state — JSON sidecar storing unchecked group IDs + camera pose; load on startup.
- Tree search / filter — typed substring filter on tree rows (handy with 22+ BMS placements per region; much more so multi-region).
- Region info panel — sibling widget showing current selection's edge breakdown + AABB + BSR metadata.
- Cell-ID text labels — see "Skipped on purpose" under Phase 1.5.
- Bring back optional visual BMS layer — `parse_bms_visual` and `parse_bsr_mesh_paths` are still present.

---

## Phase 3 — Click-to-pathfind ⬜

**Status:** later. Sketch:

Pick the agent + click destinations on the walkable surface. Python side calls silk-nav's `find_path` (A* over the polygon graph) + `smooth_path` (Mononen funnel). Resulting waypoint polyline renders as a cyan path overlay. Agent marker animates along the path tick-by-tick (`bridge.py`-style position updates).

**Open questions:** does pathfinding live in a separate Python process or in `zone.py` directly? (Probably `zone.py` directly via embedded silk-nav import.) Path recomputation on every click should be cheap (silk-nav's bench: A* + funnel < 1 ms on the Hotan pair).

---

## Phase 4 — Entity layer ⬜

**Status:** later. Sketch:

Reuse `bridge.py`'s pattern: numpy-simulated agents at first, then real NPC placements from silk-nav's `npc-data.ts` equivalent. Single `InstancedMesh` for all entities; base64-Float32Array transport; request-response cadence to prevent QWebChannel queue buildup.

Static zone is unchanged; entities are a separate layer that the bridge updates per frame.

---

## Phase 5 — Additional zones ⬜

**Status:** later. Sketch:

- Add URL routing or query parameter (`?zone=jangan`) to `web/zone.html`.
- Build script's `--zone` flag already supports it; just need fresh data exports.
- Sidebar zone selector to switch without page reload (optional polish).

Pre-condition: have silk-nav export at least one more zone.

---

## v1.0 — Stabilization ⬜

API freeze for the JS↔Python contract. Performance benchmarks across the zone scene (draw calls, GPU memory). Public-facing docs polish. Possibly a `pyproject.toml` `[project]` block for distribution (currently we're in "internal demo" mode).

---

## Working principles

- **Vendor silk-nav data; don't re-parse SRO formats** (long-term goal). silk-nav is the canonical source. The ported `parsers/` package is a tactical exception for the standalone navmesh viewer; if a new format appears, add it to silk-nav first.
- **Static-vs-dynamic split.** The zone is geometry-once. Entities are tick-based. Architecture them differently.
- **One demo per concern.** `main.py` proves the renderer stack. `zone.py` proves the SRO data path. `navmesh.py` proves the SRO nav-data path. Future phases get their own entry point or merge concerns into existing demos.
- **Match silk-nav's conventions** where applicable (axis names, region IDs, edge-flag semantics). Cross-reference silk-nav's docs rather than re-asserting facts.
- **Update docs when decisions land**, not the conversation buffer.

---

## See also

- [`../CLAUDE.md`](../CLAUDE.md) — operational manual.
- [`PIPELINE.md`](PIPELINE.md) — how the rendered output is produced end-to-end (zone demo).
- [`NAVMESH.md`](NAVMESH.md) — standalone navmesh viewer architecture + JS bridge API.
- [`SRO_FORMATS.md`](SRO_FORMATS.md) — wire layouts for `.nvm` / `.bsr` / `.bms` / `Object.ifo`.
- [`GOTCHAS.md`](GOTCHAS.md) — full prose for each gotcha.
- [`NAVMESH_PLAN.md`](NAVMESH_PLAN.md) — Phase 2 integration design.
- `silk-nav/docs/ROADMAP.md` — upstream phase tracker (phases 0-14 there map to our Phase 1 data inputs and Phase 1.5 parser ports).
