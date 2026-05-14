# Roadmap

Where the project has been and where it's going. Source of truth for status numbers; everything else cross-references back here.

## Status snapshot (2026-05-13)

| Phase | Status | Notes |
|---|---|---|
| 0 — Render-stack scaffolding | ✅ shipped | `main.py`, `bench.py`, `bridge.py`, `sweep.py`; bridge throughput characterized; 5 foundational gotchas captured |
| 1 — Hotan Kingdom zone visual | ✅ shipped 2026-05-13 | 7 regions, ~458 building submeshes, ~196 .o2 decorations, ~330 unique textures, ~140 materials, ~666 draw calls; verified visually against silk-nav's Blender render |
| 2 — Navmesh overlays | 🔜 planned | See [`NAVMESH_PLAN.md`](NAVMESH_PLAN.md). Walkable surface mesh + wall LineSegments + (optional) CC color-coding. The user explicitly named this as the next phase. |
| 3 — Click-to-pathfind | ⬜ later | Mouse-pick → silk-nav `find_path` + `smooth_path` → polyline overlay + agent dot. Reuses the Phase 2 polygon graph data. |
| 4 — Entity layer | ⬜ later | NPCs / monsters / players via `bridge.py`-style pattern. Free-running QWebChannel ticks, `InstancedMesh` for all entities. Static zone stays as-is; this adds a dynamic layer on top. |
| 5 — Additional zones | ⬜ later | Jangan, Hotan Town, Bandit Fortress, BoI. The pipeline generalizes; the renderer needs URL routing or a zone selector. See [`PIPELINE.md`](PIPELINE.md) § "Adding another zone". |
| v1.0 — Stabilization | ⬜ later | API freeze, performance pass, public-facing docs polish. |

## Goals

1. **A reference Three.js renderer for SRO data.** Treat silk-nav as the upstream "engine" for SRO parsing + nav math; we focus on rendering.
2. **PySide6-hosted, single-window desktop app.** No browser-only path; the QWebEngineView wrapping is load-bearing because Python owns the data layer.
3. **Validated incrementally.** Each phase ends with a visual or measurable check, not "looks right".

## Non-goals (v1.0)

- Game-client integration (packets, network). Lives in `vtc-sro-bot` or similar.
- Re-implementing SRO parsing. silk-nav owns that; we vendor its output.
- Cross-platform packaging beyond `uv sync`.
- Browser-standalone deployment (no PySide6). The bridge model needs Python.

## Cross-cutting principles

- **Layering.** silk-nav (SRO data + nav math) → `data/silknav_export/` → `scripts/build_zone.py` (preprocess) → `web/zones/` → `web/zone.html` (render). Each layer is independent and tested at its boundary.
- **Two demos, two purposes.** `main.py` is the procedural-scaffolding demo; `zone.py` is the real-SRO demo. Keep them both. Procedural lets us iterate on the renderer stack without large data.
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

## Phase 2 — Navmesh overlays 🔜

**Status:** planned. The user named this as the next phase (2026-05-13).

**Motivation.** The zone visual shows what the world *looks like*. The navmesh overlay shows what the agent *can do* in it — where they can walk, what blocks them, which regions are connected. This is the visualization layer that turns the demo from "pretty render" into "interactive playground for nav decisions".

**Scope (proposed).** Render translucent walkable surface (terrain + reachable BMS triangles) + opaque wall LineSegments on top of the existing zone scene. Toggle on/off. Optional: color triangles by connected-component (CC) so reachability is visible at a glance.

**Detailed plan:** [`NAVMESH_PLAN.md`](NAVMESH_PLAN.md).

**Validation criteria (proposed).**

- Walkable surface visually aligns with terrain (no z-fighting); buildings cast walls visibly on top.
- Wall LineSegments follow building outlines (verifiable against silk-nav's `render_merged_walkable_in_blender.py`).
- 30 fps render maintained even with overlay enabled.
- Toggle layer cleanly hides/shows without rebuild.

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

- **Vendor silk-nav data; don't re-parse SRO formats.** silk-nav is the canonical source. If a new format appears, add it to silk-nav first.
- **Static-vs-dynamic split.** The zone is geometry-once. Entities are tick-based. Architecture them differently.
- **One demo per concern.** `main.py` proves the renderer stack. `zone.py` proves the SRO data path. Future phases get their own entry point or an extended `zone.py`.
- **Match silk-nav's conventions** where applicable (axis names, region IDs, edge-flag semantics). Cross-reference silk-nav's docs rather than re-asserting facts.
- **Update docs when decisions land**, not the conversation buffer.

---

## See also

- [`../CLAUDE.md`](../CLAUDE.md) — operational manual.
- [`PIPELINE.md`](PIPELINE.md) — how the rendered output is produced end-to-end.
- [`GOTCHAS.md`](GOTCHAS.md) — full prose for each gotcha.
- [`NAVMESH_PLAN.md`](NAVMESH_PLAN.md) — next-phase design.
- `silk-nav/docs/ROADMAP.md` — upstream phase tracker (phases 0-14 there map to our Phase 1 data inputs).
