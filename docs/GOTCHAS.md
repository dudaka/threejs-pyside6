# Gotchas

Full prose for each numbered gotcha. `CLAUDE.md` has the one-line summaries pointing here; this is where you read when something is broken.

Format for each: **symptom** → **cause** → **fix**. Numbered to stay stable across edits.

---

## #1. Chromium blocks ES modules from `file://`

**Symptom.** You load `web/index.html` with `QUrl.fromLocalFile(...)`. The view says `loadFinished=True`. The page is blank. No JS errors in the console (assuming you wired one up).

**Cause.** Chromium enforces a non-`null` origin for ES module imports, per the CORS spec. `file://` URLs have `null` origin, so the `<script type="module">` body silently refuses to run. There's no error event because the import was never attempted in a way that surfaces.

**Fix.** Serve `web/` over a loopback HTTP server. `main.py`'s `start_static_server` uses `http.server.ThreadingHTTPServer` on an OS-assigned port; `QUrl(f"http://127.0.0.1:{port}/...")` works. `zone.py` reuses the same pattern.

**Where it bit.** Initial scaffold. Took ~30 minutes of "module imports must be silently failing" before the realization.

---

## #2. `three.module.js` is a re-export shell

**Symptom.** Page loads, canvas is present, but the scene is blank. No errors. You vendored `three.module.js` from `unpkg`/`npm` and put it under `web/vendor/three/build/`.

**Cause.** In Three.js r150+, `three.module.js` is a thin ~650 KB file that `import`s everything from `./three.core.js`. If you only vendor `three.module.js`, the network tab shows a 404 on `three.core.js` but the rest of the page (HTML, CSS, the importmap) still loads — so nothing visible breaks except the actual Three.js exports are `undefined`.

**Fix.** Vendor both files together. `web/vendor/three/build/` must contain `three.module.js` AND `three.core.js`. Also `examples/jsm/controls/OrbitControls.js` for our orbit controls. See `web/index.html`'s importmap for the import paths.

**Where it bit.** Initial scaffold. Caught by checking the network tab.

---

## #3. `QByteArray` over `QWebChannel` is lossy as a JS string

**Symptom.** `Float32Array` constructor throws `"byte length must be a multiple of 4"`, or `atob` throws `"characters outside Latin1 range"`. The Python side is sending `QByteArray` of valid little-endian floats; the JS side receives garbage.

**Cause.** Qt 6's `QWebChannel` transports `QByteArray` by JSON-stringifying the bytes. Bytes `>= 0x80` are not safe JSON-string characters — Qt's JSON serializer mangles them into multi-codepoint UTF-16 fragments, dropping or duplicating bytes in the process. The round-trip is lossy whenever a payload has any non-ASCII bytes (which is always, for float data).

**Fix.** Encode to base64 on the Python side. Declare the signal as `Signal(str)`, not `Signal(QByteArray)`. JS does `atob(base64str)` → `Uint8Array` → `Float32Array(buf.buffer)`. ~33% wire overhead vs raw bytes, but reliable.

If wire size becomes critical, switch transport: `qwebchannel.js` does NOT support binary frames natively. A WebSocket sidecar with `binaryType = 'arraybuffer'` works.

**Where it bit.** `bridge.py` initial design. See the `Signal(str)` declaration on the Bridge class for the workaround.

---

## #4. `QWebEnginePage.javaScriptConsoleMessage` level is not an int

**Symptom.** You override `javaScriptConsoleMessage` to forward `console.log` from JS to Python's stderr. `int(level)` raises `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'JavaScriptConsoleMessageLevel'`.

**Cause.** In PySide6 6.11, the `level` parameter is a `QWebEnginePage.JavaScriptConsoleMessageLevel` enum that does NOT auto-convert via `int()`. (In some older bindings it did.) The enum is what you get; treat it as an enum.

**Fix.** `str(level).rsplit('.', 1)[-1]` to get a readable label (`"InfoMessageLevel"`, `"WarningMessageLevel"`, etc). Or `level.value` if numeric ordering is what you want.

Also: the default `QWebEnginePage` only surfaces UNCAUGHT errors. `console.log` is silent by default — you MUST override `javaScriptConsoleMessage` to see it. `LoggingPage` in `bridge.py` is the reference.

**Where it bit.** `bridge.py` log forwarding.

---

## #5. `runJavaScript` callback can silently return `None`

**Symptom.** You call `page.runJavaScript("({fps: 60, n: 1000})", callback)` and the Python callback receives `None` instead of `{"fps": 60, "n": 1000}`.

**Cause.** PySide6 6.11's `runJavaScript` marshals return values through Qt's variant system. For some JS return types (plain objects, in particular), the marshaling is unreliable. Documented vaguely; reproducible in practice.

**Fix.** Always `JSON.stringify(...)` on the JS side; always `json.loads(...)` on the Python side. The round-trip through a string is reliable. `bench.py`'s polling code uses this pattern.

**Where it bit.** `bench.py` measurement loop.

---

## #6. SRO is LH Y-up; Three.js is RH Y-up — fix with per-vertex Z-flip

**Symptom A.** Buildings and terrain render, but everything looks mirrored. Walking east leaves the city, walking west enters it (wrong). North/south are swapped or X-flipped.

**Symptom B.** Buildings render but the lighting is "inside-out" — fronts are dark, backs are lit. Normal-mapped textures look broken.

**Cause.** SRO stores coordinates in left-handed Y-up (X right, Y up, Z forward INTO the screen). Three.js uses right-handed Y-up (Z OUT of the screen). Naively pasting SRO `(x, y, z)` into Three.js positions gives mirrored geometry AND CW-wound triangles, which Three.js's default back-face culling renders as inside-out.

**Fix.** Per-vertex Z-flip: `(sroX, sroY, -sroZ)`. The Z negation does two jobs at once:
1. Converts handedness (LH → RH).
2. Reverses winding order (CW → CCW), so Three.js's default front-face-CCW renders the correct side.

Apply the same flip to sector offsets:
```js
group.position.set(dx * 1920, 0, -dz * 1920)
```

so the vertex frame and group frame agree on which way is north.

**Don't** also reverse face indices or set `material.side = BackSide` to "fix" the inversion — that puts you back at mirrored geometry. The Z-flip handles both axis and winding in one operation.

**Where it bit.** Initial `web/zone.html` cut. Sanity-checked against silk-nav's Blender pipeline (which does Y/Z swap → Z-up instead; same handedness flip, different axis names).

**Related.** [`docs/PIPELINE.md`](PIPELINE.md) § "Coord conversion" for the full math. Silk-nav's gotcha #59 (its Blender-side counterpart).

---

## #7. UV V must be flipped (`1 - v`)

**Symptom.** Terrain renders with the baked PNG visible but vertically mirrored: paths and tile borders go upside-down. Building textures show wood grain or stone patterns flipped.

**Cause.** `.bms` UVs are stored in DirectX top-down convention (`v=0` = top of texture). Three.js's `TextureLoader.load(...)` uploads PNGs with `texture.flipY = true` by default, producing a GPU texture equivalent to OpenGL bottom-up (`v=0` = bottom of texture). The two conventions don't agree.

**Fix.** Per-vertex `(u, 1 - v)` transform. Applies to:
- Building submesh UVs (carried in scene JSON, originally DirectX top-down).
- Synthesized terrain UVs: `(x / 1920, 1 - z / 1920)`.

Same transform silk-nav's `render_zone_in_blender.py` uses (`uvl.data[li].uv = (u, 1.0 - v)`).

Alternative: set `texture.flipY = false` on every texture you load, then leave UVs untouched. We chose `(u, 1-v)` to match silk-nav's pipeline exactly.

**Where it bit.** Initial `web/zone.html` cut. Easy to spot on terrain (path tiles obviously upside-down); easy to miss on plain wood-grain wall textures.

---

## #8. Material flag `0x200` means alpha cutout

**Symptom.** Plant fronds, lattice gates, railings, fences render as opaque rectangles with the texture visible inside. Looks like sticker-on-glass.

**Cause.** SRO marks these materials with `PrimMtrlFlag` bit 9 (`0x200`) = "diffuse texture has a meaningful alpha channel". Without acting on the bit, Three.js renders the texture without alpha consideration: the rectangular UV patch shows up as opaque.

**Fix.** When `material_flag & 0x200`, set:
```js
material.transparent = true
material.alphaTest = 0.5
```

`alphaTest: 0.5` does hard-edge cutout (texel passes only if alpha >= 0.5). This is the right call for plant-frond style cutouts and avoids alpha-sorting artifacts. `transparent: true` allows soft blending if alpha is between 0 and 1; we leave the option open even though Hotan Kingdom doesn't currently exercise it.

**Don't** set `material.transparent = true` without `alphaTest` — soft-alpha sorting will produce visible artifacts when fronds overlap.

**Where it bit.** Initial cut. Caught by visually comparing against silk-nav's Blender render of the same scene.

---

## #9. Pillow DDS reader doesn't handle BC6/BC7

**Symptom.** `scripts/build_zone.py` raises `NotImplementedError: Unimplemented pixel format <fourcc>` on certain `.dds` files. Currently exactly 4 unique textures (`sum_event_02.dds`, `sum_event_pillar01.dds`, `sum_event_pillar02.dds`, and one more) — all summer-event seasonal pillars in `.o2`.

**Cause.** Pillow's `DdsImagePlugin` handles DXT1, DXT3, DXT5 (the original DirectX 9 block-compressed formats SRO mostly uses). BC6H and BC7 (the DirectX 11 block-compressed formats SRO sometimes uses for HDR/seasonal content) are not implemented.

**Fix in build_zone.py.** Catch the exception, log a `WARN`, leave the destination PNG absent. The submesh's JSON entry still references the missing PNG; Three.js's `TextureLoader.load()` errors silently for missing files, and the `MeshStandardMaterial`'s `color: base_color` shows through. Result: a flat-colored pillar where there should be a textured one. Acceptable for seasonal props.

**If a real material ever needs BC6/BC7.** Integrate a Python BC7 decoder (`bcdec_py`, `compressonatorcli` via subprocess, or `texconv.exe` on Windows) at the conversion step. Don't try to teach Pillow.

**Where it bit.** First `scripts/build_zone.py` run. Mitigated immediately to skip-on-fail. silk-nav has the same gap (gap L5 in its `docs/SRO_VISUALS.md`).

---

## #10. Sector offset belongs on `Group.position`, not in vertex coords

**Symptom.** Regions look correct individually but the multi-region zone shows them drifted away from each other — adjacent regions are double-spaced, or scattered, or stacked on top of each other.

**Cause.** Each scene JSON carries vertices in *region-local* coords (0..1920 on each horizontal axis). To stitch into a zone, you need to translate each region by `(dx * 1920, 0, -dz * 1920)`. If you bake that translation into the vertex arrays AND set `Group.position` to it, the vertices move twice.

**Fix.** Pick one. Our convention: vertices stay region-local; `Group.position` carries the sector offset. This makes per-region toggling cheap (`group.visible = false`) and keeps the JSON region-agnostic.

Silk-nav's Blender-side counterpart is its gotcha #58 — same trap, same fix.

**Where it bit.** Did NOT bite us; we got it right the first time because we'd already read silk-nav's gotcha #58 before writing the renderer. Documented here so we don't regress.

---

## Adding a new gotcha

Format: copy a section above. Keep the symptom-cause-fix structure; humans debug by symptom. Add a "Where it bit" note so future-you knows whether this is theoretical or scar tissue. Cross-reference related gotchas and the relevant pipeline section.

The current numbering goes 1-5 (scaffold/bridge era) and 6-10 (SRO zone era). When the navmesh phase ships, expect 11-15 in the same numbering scheme.
