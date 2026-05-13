# CLAUDE.md

Project knowledge for Claude Code (and humans). The "what" lives in the code; this file captures the "why" and the non-obvious gotchas this project hit while landing.

## What this is

A PySide6 desktop app that hosts a Three.js scene via `QWebEngineView`. Built as a foundation for visualizing the Silkroad Online world map with pathfinding overlays and thousands of moving entities (NPCs / monsters / players). Python is the natural place for asset parsing (PK2/JMX), pathfinding (A* / HPA* / navmesh), and game-data logic; Three.js handles rendering with `InstancedMesh` for entity counts.

## Scripts

- `main.py` — minimal demo: procedural terrain, lit, with `OrbitControls`.
- `bench.py` — uncapped throughput bench (10 scenes; warmup + 2s measure each).
- `bridge.py` — Python↔JS bridge experiment. Numpy-simulated agents pushed via `QWebChannel` to an `InstancedMesh`. Args: `--n`, `--hz`, `--protocol {json,bytes}`, `--seconds`.
- `sweep.py` — parameter sweep over `bridge.py` (N × protocol), aggregates steady-state stats.

Run anything with `uv run python <script>.py`.

## Stack decisions (committed)

- **PySide6 6.11+ via uv.** Pulled from PyPI (`pyside6` meta-package, brings `PySide6-Addons` which contains `QWebEngineWidgets`).
- **Three.js r184, vendored** at `web/vendor/three/`. Three files: `build/three.module.js`, `build/three.core.js` (the module file re-exports from core, both are required), and `examples/jsm/controls/OrbitControls.js`.
- **`qwebchannel.js` vendored** from Qt 6.8 source at `web/vendor/qwebchannel/qwebchannel.js`.
- **No CDN at runtime.** Everything is served locally.

## Non-obvious gotchas

These will bite again if you forget them; they each took meaningful debugging.

### 1. Chromium blocks ES modules from `file://`

Loading `web/index.html` with `QUrl.fromLocalFile(...)` fires `loadFinished=True` but the `<script type="module">` body never runs (no console error, no canvas). Reason: ES modules require a non-`null` origin per CORS. The fix in this project is a tiny in-process loopback HTTP server (`http.server.ThreadingHTTPServer`) and a `QUrl(f"http://127.0.0.1:{port}/...")`. Pattern lives in `main.py`'s `start_static_server`.

### 2. `three.module.js` is a re-export shell

In r150+, `three.module.js` is ~650 KB and `import`s everything from `./three.core.js`. Vendoring only `three.module.js` leaves a hidden 404 inside the module graph; the page loads but nothing renders. Vendor both files in `web/vendor/three/build/`.

### 3. `QByteArray` over `QWebChannel` is lossy as a JS string

Qt 6 transports `QByteArray` through `QWebChannel` by JSON-stringifying the bytes — bytes ≥ 0x80 turn into multi-codepoint JS string fragments and the round-trip drops or duplicates bytes. Symptoms: `Float32Array` "byte length must be a multiple of 4" or `atob` "characters outside Latin1 range".

Workaround used in `bridge.py`: encode to base64 on the Python side and declare the signal as `Signal(str)`. JS does `atob` then constructs a `Uint8Array` then a `Float32Array`. Adds ~33% wire overhead but is the only reliable path through `QWebChannel` for binary payloads. (If wire size matters, switch to a WebSocket — `qwebchannel` does not support binary frames natively.)

### 4. `QWebEnginePage.javaScriptConsoleMessage` level is not an int

In PySide6 6.11, the `level` parameter is a `QWebEnginePage.JavaScriptConsoleMessageLevel` enum that does NOT auto-convert via `int()`. Calling `int(level)` raises `TypeError`. Use `str(level).rsplit('.', 1)[-1]` or `level.value` (if available). Default `QWebEnginePage` only surfaces uncaught errors — for `console.log` you must override `javaScriptConsoleMessage`. See `LoggingPage` in `bridge.py`.

### 5. `runJavaScript` callback can silently fail

`page.runJavaScript(script, callback)` and `page.runJavaScript(script, worldId, callback)` are both valid PySide6 signatures, but in some configurations the result of returning a plain `dict`-like value from JS comes back as `None` to Python. Prefer `JSON.stringify(...)` on the JS side and `json.loads(...)` on the Python side — this round-trips reliably. The `bench.py` polling uses this pattern.

## Performance characteristics (measured 2026-05-13)

Numbers from this machine, not portable promises — re-run `bench.py` and `sweep.py` to verify on other hardware.

### Rendering ceiling (uncapped, `gl.finish()` per frame)

- 200k-tri terrain, no shadows: ~5,300 fps
- 200k-tri terrain + 2048² soft shadows: ~2,600 fps
- 6M tris (500k `InstancedMesh`): ~3,100 fps

Rendering has massive headroom. Visible framerate is **vsync-capped at ~30 fps** in this `QWebEngineView` setup regardless of scene complexity — that's the compositor, not the engine.

### Bridge throughput (Python → JS via `QWebChannel`)

Target 60 Hz, base64 bytes vs JSON list:

- ≤ 10k entities: ~30 Hz updates, 30 fps render — comfortable.
- 25k entities: still ~30 Hz updates, base64 (6 MB/s) clearly beats JSON (21 MB/s) on wire.
- 50k entities: JSON encoder becomes Python's bottleneck (drops to 26 Hz).
- 100k entities: JS-side `setMatrixAt` loop saturates at ~29 ms/tick → render fps collapses to 8.

**The wire format is not the bottleneck.** The JS-side `InstancedMesh` update loop is. `apply ms` scales linearly with N. The known fast path: write directly to `agents.instanceMatrix.array` (12 floats per entity for a 4×3 affine view) and skip `Object3D.updateMatrix()` per entity.

## Design recommendations for the Silkroad use case

These are the conclusions of the bench above — apply when wiring the real asset pipeline.

- **Protocol**: base64-`Float32Array` over `Signal(str)`. JSON is fine up to ~10k entities; past that, base64 wins clearly.
- **Update model**: request-response, not free-running. Python emits a tick only after JS sends a `ready` signal each frame. Prevents queue buildup in `QWebChannel`, which otherwise grows unboundedly when the producer outruns the consumer (observed at n=100k).
- **Visibility culling**: do it on the Python side. Only send entities inside or near the camera frustum, not the full world's NPC list.
- **JS-side**: write to `instanceMatrix.array` directly. Easy 2-4× win over `setMatrixAt`-per-entity.
- **Asset pipeline**: preprocess PK2/JMX into chunked glTF or raw `BufferGeometry` JSON files. Stream chunks based on camera position with a few LOD rings.
- **Navmesh / path overlays**: batch all debug edges into a single `LineSegments` mesh. Rebuild only on navmesh change.
- **Plan for 30 Hz, not 60.** macOS + `QTimer` + `QWebEngineView` consistently lands at ~30 Hz max in this setup. Designs predicated on 60 Hz updates will disappoint.

## Project conventions

- `uv` for everything Python. Never `pip`, never `python3` directly. Always `uv run python ...` and `uv add ...`.
- Vendored web assets live under `web/vendor/<package>/`. Don't reach for CDNs at runtime.
- Match the global style: no emojis in code/output; short, well-named functions; no defensive programming for cases that can't happen; comments are for non-obvious "why" only.
- When debugging a black/empty scene, FIRST probe `document.querySelector('canvas')` and `window.__bridgeStats` via `runJavaScript` before guessing — most "scene didn't render" issues are silent module-load failures, not Three.js bugs.
