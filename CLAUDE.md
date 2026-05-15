# CLAUDE.md

Operational manual for Claude Code in this repo. Conventions, code map, gotcha quick-ref, current status. Deeper material lives in `docs/`.

---

## Documentation map

| Doc | Audience | Read when |
|---|---|---|
| **`CLAUDE.md`** (this) | Claude / session-resume | Every session. Operational facts, code map, gotcha quick-ref, status. |
| **`docs/PIPELINE.md`** | Humans (pedagogical) | First time understanding the zone pipeline. silk-nav → `data/silknav_export/` → `scripts/build_zone.py` → `web/zones/` → Three.js. Coord math + UV math + material handling. |
| **`docs/NAVMESH.md`** | Both | Working on the SRO navmesh viewer (`navmesh.py` + `web/navmesh.html` + `parsers/`). Architecture, wire format, JS bridge API, extension points. |
| **`docs/SRO_FORMATS.md`** | Both | Touching parsers (`.nvm` / `.bsr` / `.bms` / `Object.ifo`). Wire layouts, EdgeFlag/NavFlag bit reference, coordinate notes. |
| **`docs/GOTCHAS.md`** | Both | Full prose for each numbered gotcha. CLAUDE has 1-line summaries; this is where you read when something's broken. |
| **`docs/NAVMESH_PLAN.md`** | Both | Phase 2 design — integrating navmesh overlays into the zone view. Read alongside `docs/NAVMESH.md` (which documents the shipped standalone viewer). |
| **`docs/ROADMAP.md`** | Both | Source of truth for what shipped + what's next. Status numbers live here. |
| **`README.md`** | Outside readers | Pitch + quickstart. Concise. |

When a decision lands or a gotcha is discovered, **update the doc** rather than answering from conversation memory.

---

## TL;DR for a fresh session

A PySide6 desktop app hosting Three.js scenes via `QWebEngineView`. Foundation for visualizing Silkroad Online's world with pathfinding overlays + many entities. Python owns the data layer (PK2/JMX parsing via [silk-nav](https://github.com/dudaka/silk-nav), pathfinding, agent logic); Three.js owns rendering.

**Three demos:**

1. **`main.py` (procedural).** Procedural terrain scene — testbed for the render stack. Original scaffold.
2. **`zone.py` (Hotan Kingdom).** Real SRO data, textured. Reads vendored silk-nav exports from `data/silknav_export/` → preprocessed to `web/zones/hotan_kingdom/` → rendered by `web/zone.html`. **The headline visual demo.**
3. **`navmesh.py` (SRO navmesh edge viewer).** Real `.nvm` / `.bsr` / `.bms` parsing from disk. PySide6 left tree panel + embedded Three.js scene; renders region-level (`.nvm`) + per-BMS-object (slot 7) global+internal edges as colored `LineSegments`. Click-to-zoom, tristate visibility, highlight+fade, endpoint markers. Default loads region 5c87 (Hotan Kingdom castle area). **Standalone partial implementation of the planned Phase 2 navmesh overlay.**

**Phase status (2026-05-14).** Phase 0 (render-stack scaffolding) shipped. Phase 1 (Hotan Kingdom zone visual) shipped 2026-05-13. Standalone navmesh edge viewer shipped 2026-05-14 — see `docs/NAVMESH.md`. **Phase 2 (navmesh overlays inside the zone view) is next** — see `docs/NAVMESH_PLAN.md`. Test count: none yet (visual validation only). Lint: not configured.

This is a solo project. The user (`dudaka`) is the sole committer; prefers small steps validated visually; strong preference for matching silk-nav's conventions where applicable.

---

## What this project is (and isn't)

**Is:** a Three.js-based renderer for SRO data, hosted in a PySide6 window. The bridge model (`bridge.py`) is load-bearing because Python owns the data layer and entities/pathfinding will be Python-driven.

**Is not:** an SRO format parser owned-from-scratch (`parsers/` is a direct port of silk-nav for the navmesh viewer; silk-nav remains canonical), a game-client integration (no networking), a browser-standalone app (PySide6 wrapping is essential), or a port of Three.js itself.

**Architecture layering (zone demo):**

```
silk-nav (separate repo)
  | exports JSON + DDS
  v
data/silknav_export/   <-- vendored ~85 MB; source of truth for the demo
  | scripts/build_zone.py (DDS->PNG, manifest)
  v
web/zones/<zone>/      <-- generated ~102 MB; gitignored
  | http loopback
  v
web/zone.html          <-- Three.js renderer
  | QWebEngineView
  v
PySide6 window
```

**Architecture layering (navmesh viewer):**

```
sro-data/ (user's pre-extracted Pk2 tree, outside the repo)
  | parsers/{nvm,bsr,bms,ifo,filesystem}.py
  v
in-process EdgeGroup objects + binary blob
  | http loopback (binary route, application/octet-stream)
  v
web/navmesh.html       <-- Three.js renderer; LineSegments per group
  | QWebEngineView (+ QSplitter with QTreeWidget on the left)
  v
PySide6 window
```

---

## Hard conventions (do not deviate without sign-off)

### Python
- `uv` for everything. Never `pip`, never bare `python3`. Always `uv run python ...` and `uv add ...`.
- Python `>= 3.11` (`pyproject.toml`); current local resolves 3.13.
- No formatter / linter pinned yet — keep changes minimal. When the codebase grows, add `ruff` matching silk-nav's config (line 100, `select = ["E", "F", "I", "B", "UP", "SIM"]`).
- No emojis in code or output.

### Three.js
- **r184, vendored** at `web/vendor/three/`. Three files load-bearing: `build/three.module.js`, `build/three.core.js` (the module file re-exports from core — gotcha #2), `examples/jsm/controls/OrbitControls.js`.
- No CDN at runtime.
- ES module imports require non-`null` origin (gotcha #1) — always serve over loopback HTTP, never `file://`.
- `qwebchannel.js` vendored from Qt 6.8 at `web/vendor/qwebchannel/`. Used by `bridge.py`; `zone.py` and `navmesh.py` do not need it.

### Coordinate conventions (zone renderer)
- SRO LH Y-up → Three.js RH Y-up via **per-vertex Z-flip**: `(x, y, -z)`. Also flips winding CW→CCW (so face indices stay as-is). See `docs/PIPELINE.md` § "Coord conversion" for full math.
- UV V-flip: per-vertex `(u, 1 - v)`. `.bms` UVs are DirectX top-down; Three.js PNG upload is OpenGL bottom-up.
- Sector offset on **`Group.position` only**: `group.position.set(dx * 1920, 0, -dz * 1920)`. Never bake into vertices.
- Region constants: `REGION_SIZE = 1920.0` (SRO-local units per region side). `region_id = (z_sector << 8) | x_sector`.

### Coordinate conventions (navmesh viewer — BMS placements)
- BMS placement YAW IS NEGATED when transforming local → region world space (silk-nav gotcha #39):

  ```
  cs = cos(-yaw)
  sn = sin(-yaw)
  world_x = cs * lx + sn * lz + obj.local_position[0]
  world_z = -sn * lx + cs * lz + obj.local_position[2]
  world_y = ly + obj.local_position[1]
  ```

  Do not change the sign without testing against silk-nav for the same region.

### Material conventions (zone renderer)
- `material_flag & 0x200` = alpha cutout. Set `transparent: true; alphaTest: 0.5`.
- All materials use `side: THREE.DoubleSide` as a safety net.
- Texture cache by URL; material cache by `(texture URL, alpha flag)`.
- All textures load with `colorSpace = SRGBColorSpace` (matches Blender's PNG color space).

### EdgeFlag color palette (navmesh viewer)
- **Duplicated by design** in two places: `navmesh.py::flag_color_hex` and `web/navmesh.html::edgeColor`. They MUST stay in sync. Comments in each say so.

### Data layout

```
data/silknav_export/                    # ~85 MB, tracked. Source of truth for the zone demo.
  zone_hotan_kingdom/<region>/          # buildings: scene.json + textures/*.dds
  zone_hotan_kingdom_o2/<region>/       # decorations: scene.json + textures/*.dds
  per_region/<region>/                  # baked terrain PNGs

web/zones/<zone>/                       # ~102 MB, gitignored. Regenerable via build_zone.py.
  manifest.json
  <region>/{scene.json, terrain.png, textures/*.png}
  o2/<region>/{scene.json, textures/*.png}
```

`scripts/build_zone.py` is idempotent (mtime-checked); safe to re-run after a data refresh.

### Default SRO data root (navmesh viewer)

`/Users/hodung/Workspace/silkroad/sro-data` — the user's pre-extracted Pk2 tree. NOT vendored into this repo (too large; SRO-licensed).

- `Map/Object.ifo` — asset_id → resource-path manifest (ISO-8859 text — gotcha #11).
- `Data/navmesh/nv_<hex>.nvm` — region navmeshes (`nv_` prefix; not the un-prefixed silk-nav fixture name — gotcha #13).
- `Data/Res/...` — `.bsr` / `.cpd` resource containers.
- `Data/Prim/Mesh/...` — `.bms` mesh files.

Override with `--map-root` / `--data-root` on `navmesh.py` or with `NAVMESH_MAP_ROOT` / `NAVMESH_DATA_ROOT` env vars.

---

## Code map

```
main.py                    procedural terrain demo (origin scaffold)
bench.py                   uncapped throughput bench (10 scenes; warmup + 2s measure)
bridge.py                  Python<->JS bridge. base64 Float32Array over Signal(str).
sweep.py                   parameter sweep over bridge.py (N x protocol)
zone.py                    Hotan Kingdom zone launcher (mirrors main.py + zone.html)
navmesh.py                 SRO navmesh edge viewer (PySide6 left panel + zone.html-style scene)

parsers/                   direct ports from silk-nav; used by navmesh.py
  __init__.py
  nvm.py                   region navmesh (.nvm)
  ifo.py                   Object.ifo asset manifest
  bsr.py                   .bsr resource (slot 7 collision + parse_bsr_mesh_paths + parse_bsr_name)
  bms.py                   .bms mesh (slot-7 nav edges + visual slots 0/2)
  filesystem.py            FilesystemDataSource with ISO-8859 fallback (gotcha #11)

scripts/
  build_zone.py            preprocessor: DDS->PNG via Pillow, JSON texture remap, manifest

web/
  index.html               procedural terrain scene (main.py renders this)
  bench.html               throughput bench scenes
  bridge.html              bridge demo scene
  zone.html                Hotan Kingdom Three.js scene (the headline)
  navmesh.html             navmesh viewer scene; one LineSegments per EdgeGroup
  zones/<zone>/            generated; gitignored
  vendor/three/            r184: build/{three.module,three.core}.js + examples/jsm/controls/
  vendor/qwebchannel/      from Qt 6.8

data/silknav_export/       vendored silk-nav exports for Hotan Kingdom

docs/                      see "Documentation map" above

.gitignore                 web/zones/ excluded (regenerable); data/ tracked
pyproject.toml             uv-managed; deps: pyside6, numpy, pillow
uv.lock                    committed for reproducibility
```

---

## Run commands

```bash
# Procedural scaffolding (no SRO data needed)
uv sync
uv run python main.py
uv run python bench.py        # uncapped fps bench
uv run python bridge.py --n 10000 --hz 60 --protocol bytes --seconds 5
uv run python sweep.py

# Hotan Kingdom zone (vendored data already in data/silknav_export/)
uv run python scripts/build_zone.py   # one-shot; ~10-15 s; idempotent
uv run python zone.py

# SRO navmesh edge viewer (needs sro-data tree at default path or via --map-root)
uv run python navmesh.py
```

---

## Gotcha quick-ref

One-line summaries. Full prose in `docs/GOTCHAS.md`.

| # | One-liner |
|---|---|
| 1 | Chromium blocks ES modules from `file://` — must serve over loopback HTTP. |
| 2 | `three.module.js` is a re-export shell — vendor `three.core.js` too. |
| 3 | `QByteArray` over `QWebChannel` is lossy as JS string — use base64+`Signal(str)`. |
| 4 | `QWebEnginePage.javaScriptConsoleMessage` level is an enum, not int. |
| 5 | `runJavaScript` callback can silently return `None` — always `JSON.stringify` on the JS side. |
| 6 | SRO LH Y-up → Three.js RH Y-up via per-vertex Z-flip (also flips winding CW→CCW). |
| 7 | UV V must be flipped `(u, 1 - v)` — `.bms` is DirectX top-down. |
| 8 | Material flag `0x200` → `transparent: true; alphaTest: 0.5`. |
| 9 | Pillow DDS reader doesn't handle BC6/BC7 — fallback to `base_color`. |
| 10 | Sector offset on `Group.position` only — never bake into vertices. |
| 11 | `Object.ifo` is ISO-8859, not UTF-8 — `read_text` falls back to latin-1. |
| 12 | BMS placement yaw is negated in the local→world transform (silk-nav #39). |
| 13 | `.nvm` dump filenames are `nv_`-prefixed (`nv_5c87.nvm`, NOT `5c87.nvm`). |
| 14 | `.nvm` terrain has zero `0x03` walls — all walls live in BMS slot-7 edges. |
| 15 | macOS editor hook flags PySide6's main-loop call — use `getattr(app, "exec")()`. |

When the **zone demo** renders blank / mirrored / inside-out, cross-check the four conversions: Z-flip (#6), UV V-flip (#7), alpha flag (#8), sector offset (#10). 90% of new-zone bugs are one of these four.

When the **navmesh viewer** is empty or mis-placed, check: filename prefix (#13), yaw sign (#12), and whether you're loading BMS slot 7 not slot 0 (the latter is visual mesh, not nav — see `docs/SRO_FORMATS.md`).

---

## Performance characteristics (measured 2026-05-13)

Numbers from this machine, not portable promises — re-run `bench.py` / `sweep.py` to verify on other hardware.

**Procedural rendering ceiling** (uncapped, `gl.finish()` per frame):
- 200k-tri terrain, no shadows: ~5,300 fps
- 200k-tri terrain + 2048² soft shadows: ~2,600 fps
- 6M tris (500k `InstancedMesh`): ~3,100 fps

Visible framerate **vsync-capped at ~30 fps** in QWebEngineView regardless. That's the compositor.

**Bridge throughput** (Python → JS via `QWebChannel`, base64 vs JSON, 60 Hz target):
- ≤ 10k entities: ~30 Hz updates, 30 fps render — comfortable.
- 25k entities: still ~30 Hz, base64 (6 MB/s) clearly beats JSON (21 MB/s).
- 50k entities: JSON encoder is Python's bottleneck → 26 Hz.
- 100k entities: JS `setMatrixAt` loop saturates at ~29 ms/tick → render fps collapses to 8.

**Wire format is not the bottleneck. The JS-side update loop is.** Fast path: write to `instanceMatrix.array` directly (12 floats/entity for 4×3 affine view), skip `Object3D.updateMatrix()`.

**Zone demo (Hotan Kingdom):** ~666 draw calls (7 terrain + 458 building + ~196 .o2), ~330 unique textures, ~140 unique materials. Not yet benchmarked — perceived smooth at 30 fps vsync cap. If draw-call cost matters for bigger zones, batch per-asset-id into merged geometry or `InstancedMesh` decorations.

**Navmesh viewer (region 5c87):** ~3.5k edges across 57 `LineSegments` groups (3 NVM + 54 BMS), ~180 KB binary blob, one-shot fetch over loopback HTTP. ~30 fps idle, no observable churn. No `QWebChannel` traffic after load.

---

## Design recommendations for the Silkroad use case

The world splits into two render layers; apply optimization recs to the layer they belong to.

**Static zone geometry** (terrain + buildings + decorations) — uploaded once per zone load. The Hotan Kingdom demo is this layer.
- Protocol: per-region JSON is fine up to a few MB; switch to binary chunk (`.glb` or raw `BufferGeometry`) if startup latency starts mattering.
- For one-shot bulk transfers (the navmesh viewer's pattern), serve raw `application/octet-stream` over loopback HTTP and `fetch().arrayBuffer()` on the JS side. Sidesteps QWebChannel entirely.
- Textures: PNG fine; KTX2/BasisU is the future-step if VRAM matters.
- Optimization target: draw-call count and texture memory.

**Dynamic entities** (NPCs, monsters, players, projectiles) — pushed from Python every tick. `bridge.py` is the prototype.
- Protocol: base64-`Float32Array` over `Signal(str)`. JSON fine up to ~10k entities; past that, base64 wins.
- Update model: **request-response**, not free-running. Python emits a tick only after JS sends `ready`. Prevents QWebChannel queue buildup (observed at n=100k).
- Visibility culling: Python side. Only send entities inside/near the camera frustum.
- JS-side: write `instanceMatrix.array` directly. 2-4× win over `setMatrixAt`-per-entity.

**Navmesh overlays** — middle ground. Built once per zone load (like static), but rebuilt on rare events (asset toggle, region refresh). Use `LineSegments` for walls. The shipped `navmesh.py` uses one `LineSegments` per group for per-group toggling; if you don't need toggles, batch into a single `LineSegments` for fewer draw calls. Phase 2 (`docs/NAVMESH_PLAN.md`) integrates this layer into the zone view.

**Plan for 30 Hz, not 60.** macOS + Windows + `QTimer` + `QWebEngineView` consistently lands at ~30 Hz max in this setup. Designs predicated on 60 Hz updates will disappoint.

---

## Working principles

- **Vendor silk-nav data; don't re-parse SRO formats.** silk-nav is the canonical source. The `parsers/` package in this repo is a direct port for the navmesh viewer's needs; if a new format appears, add it to silk-nav first.
- **Optional features lazy-import.** Pillow is the only non-PySide6-non-numpy Python dep so far. If adding more (`shapely`, `pyarrow`, ...), gate via a `[project.optional-dependencies]` extra.
- **Match existing style; don't refactor adjacent code.** Surgical changes only.
- **Don't assume. Surface tradeoffs.** When two interpretations exist, present them and let the user decide.
- **Update docs when decisions land**, not the conversation buffer.
- **When debugging a black/empty scene**, FIRST probe `document.querySelector('canvas')` and read the QtWebEngine console (override `javaScriptConsoleMessage`) before guessing — most "scene didn't render" issues are silent module-load failures, not Three.js bugs.

---

## Status & next steps

**Last shipped:**
- Phase 1 (✅ 2026-05-13 — Hotan Kingdom zone visual, end-to-end).
- Standalone navmesh edge viewer (✅ 2026-05-14 — `navmesh.py`, region 5c87, 57 edge groups, interactive left panel).

**Phase 2 is next: navmesh overlays *inside the zone view*.** See `docs/NAVMESH_PLAN.md` for the original design, and `docs/NAVMESH.md` / `docs/ROADMAP.md` for what the standalone viewer already establishes (parsers, edge classification, JS bridge pattern).

Quick summary of the integration work:

1. The standalone viewer proves the parser path end-to-end. Phase 2 is layering that data onto the zone scene.
2. Add a silk-nav-side export script that emits `<zone>_navmesh.json` (walkable triangles + walls + CC labels), OR reuse the in-repo `parsers/` (decision pending).
3. Extend `web/zone.html` with overlay layers: walkable surface (translucent green, CC-colored), walls (red `LineSegments`), HUD toggles.
4. Multi-region: load each `.nvm` from the dump dir; apply sector offset `(dx*1920, 0, dz*1920)` to NVM edges and to BMS placements (silk-nav `scripts/demos/_common.py::ZONES` has the canonical filename list per zone preset).
5. Visual validation against silk-nav's PyVista `navmesh_qt_place.py --zone hotan_kingdom` and `render_merged_walkable_in_blender.py`.

**Known open issues / gaps:**

- BC6/BC7 seasonal textures (4 unique) fall back to `base_color`. Acceptable.
- Lighting parity with Blender is approximate. Port sun direction/intensity from silk-nav's `render_zone_in_blender.py:render_zone` if parity matters.
- `web/zone.html` hardcodes `ZONE = 'hotan_kingdom'`. To add another zone, parametrize via URL query or add a zone selector.
- No benchmarks yet on the zone scene specifically. Draw-call cost is the next thing to measure.
- `navmesh.py` only loads region 5c87. Multi-region load is planned (see `docs/ROADMAP.md` § Phase 2).

For deeper context: `docs/PIPELINE.md` (zone pipeline), `docs/NAVMESH.md` (navmesh viewer architecture), `docs/SRO_FORMATS.md` (parser wire formats), `docs/GOTCHAS.md` (prose for each gotcha), `docs/ROADMAP.md` (status snapshot truth), `docs/NAVMESH_PLAN.md` (Phase 2 integration design).
