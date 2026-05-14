# Zone rendering pipeline

How a Silkroad Online zone ends up on screen. Pedagogical companion to `CLAUDE.md`; read this first if you're new to the project.

## Big picture

```
SRO game client
  Map.pk2 + Data.pk2 (Blowfish-encrypted archives)
        |
        v
+----------------------------+
| silk-nav (separate repo)   |  parses PK2 + JMX formats; owns SRO knowledge
|                            |
|   export_region_with_      |  per-region: terrain mesh + building submeshes
|     textures.py            |    + .ddj textures
|   bake_terrain_texture.py  |  per-region: tile blend -> PNG
|   export_o2_objects.py     |  per-region: .o2 decoration props
+----------------------------+
        |
        v   (one-time vendor: copy the three top-level out/ dirs)
+----------------------------+
| data/silknav_export/       |  ~85 MB. Source of truth for the demo.
|   zone_hotan_kingdom/      |   buildings: scene.json + textures/*.dds
|   zone_hotan_kingdom_o2/   |   decorations: scene.json + textures/*.dds
|   per_region/              |   baked terrain PNGs
+----------------------------+
        |
        v   uv run python scripts/build_zone.py
+----------------------------+
| web/zones/<zone>/          |  ~102 MB. Web-ready bundle. Gitignored.
|   manifest.json            |   zone-level: regions + sector offsets
|   <region>/scene.json      |   .dds -> .png remap inside
|   <region>/terrain.png     |
|   <region>/textures/*.png  |   Pillow DDS -> PNG
|   o2/<region>/...          |
+----------------------------+
        |
        v   uv run python zone.py   (PySide6 + loopback HTTP)
+----------------------------+
| web/zone.html              |  Three.js scene
|  fetch(manifest.json)      |
|  per region -> Group       |
|    Terrain mesh            |
|    Building submeshes      |
|  per o2 region -> Group    |
|    Decoration submeshes    |
+----------------------------+
        |
        v
   QWebEngineView -> Screen
```

## Layer 1: silk-nav (we don't run this — we consume its output)

[`silk-nav`](https://github.com/dudaka/silk-nav) is the upstream Python library that handles every SRO-specific binary format (`.pk2`, `.nvm`, `.bsr`, `.bms`, `.bmt`, `.ddj`, `.m`, `.o2`, `Object.ifo`, `Tile2D.ifo`). It produces Blender-ready JSON + DDS exports.

For Hotan Kingdom, the three scripts that produced our `data/silknav_export/`:

| Script | Output dir | What it contains |
|---|---|---|
| `export_region_with_textures.py` | `zone_hotan_kingdom/<region>/` | Terrain mesh (97×97 heightmap → 9408 verts / 18432 tris) + every building submesh with UVs + `.dds` textures + a single `<region>_scene.json` |
| `bake_terrain_texture.py` | `per_region/<region>/terrain_<region>.png` | 1536² PNG terrain texture, per-region. NumPy-vectorized 4-corner bilinear blend over `.m` heightmap × `Tile2D.ifo` tile types × `.ddj` tile textures |
| `export_o2_objects.py` | `zone_hotan_kingdom_o2/<region>/` | `.o2` decoration props (palms, weeds, vendor stalls). Same JSON shape as the main zone export. Filtered to LoD 2 + non-seasonal |

silk-nav's docs explain the SRO data model itself — read `silk-nav/docs/SRO_VISUALS.md` if you want to understand `.bsr` containers, material chains, etc.

## Layer 2: scripts/build_zone.py (one-shot preprocessor)

`scripts/build_zone.py` does three jobs:

1. **DDS → PNG conversion** via Pillow. Pillow's DDS reader handles DXT1/3/5; BC6/BC7 fail with `NotImplementedError` and we log a warning + skip. Skipped textures fall back to the submesh's `base_color` (4 seasonal pillars affected in Hotan Kingdom; see [`docs/GOTCHAS.md`](GOTCHAS.md) #9).
2. **JSON texture-name remap.** Each scene JSON has `submesh.texture = "foo.dds"`; we rewrite to `"foo.png"`. The geometry data (vertices, faces, UVs, material flags, base colors) passes through unchanged.
3. **Manifest emission.** A single `web/zones/<zone>/manifest.json` carrying the region list, sector offsets, and presence flags (`terrain`, instance counts, texture counts). The Three.js scene fetches this first to learn what to load.

Idempotent: mtime-checked PNG conversion means re-running is fast and safe. Useful when refreshing data after a silk-nav rerun.

Override defaults with `--zone`, `--regions`, `--center`, `--silknav-out`, `--web-root`, `--no-o2`.

## Layer 3: web/zone.html (Three.js renderer)

The renderer's job is to produce a visual equivalent of silk-nav's `scripts/render_zone_in_blender.py` output. Same data, different renderer.

### Scene assembly

```
THREE.Scene
  HemisphereLight + DirectionalLight (sun)
  Zone_hotan_kingdom (Group)
    Region_5c87 (Group at world origin)
      Terrain_5c87 (Mesh: terrain.png on bilinear-interp 97x97 grid)
      SM_5c87_<asset>_<uid>_<sub> ... (one Mesh per submesh)
    Region_5d87 (Group at (0, 0, -1920))
      Terrain_5d87
      SM_5d87_...
    ... (5 more regions)
  Zone_hotan_kingdom_o2_decorations (Group)
    O2_Region_5c87 (Group at world origin)
      O2_5c87_<asset>_<uid>_<sub> ...
    ... (6 more regions)
```

Each region is its own `Group` so we can toggle visibility per-region without touching geometry. Submeshes within a region are not grouped — they're flat children of the region Group, named for debug pickability.

### Coord conversion: SRO LH Y-up → Three.js RH Y-up

SRO data uses **left-handed Y-up** coordinates:

```
SRO local frame (one region, 0..1920 on each horizontal axis)
       Y (up)
       |
       |
       +------ X (east, right)
      /
     /
    Z (north, forward INTO the screen)   <-- left-handed
```

Three.js uses **right-handed Y-up**:

```
Three.js world
       Y (up)
       |
       |
       +------ X (right)
      /
     /
    Z (OUT of the screen, toward viewer)   <-- right-handed
```

To convert with no axis renaming, **flip Z per vertex**:

```js
threeJsXYZ = (sroX, sroY, -sroZ)
```

This is one operation that does two things:
1. **Converts handedness.** `Z_sro → -Z_three` puts "north" at `-Z` in Three.js space, which matches the conventional "into the screen" intuition.
2. **Reverses face winding.** SRO stores triangles CW; after Z-flip they're CCW. Three.js's default front-face is CCW, so face indices stay as-is and back-face culling works.

silk-nav's Blender pipeline does a Y/Z swap instead (`(x, y, z)_sro → (x, z, y)_blender`), going Z-up. Same handedness flip, different axis names. We stay Y-up because Three.js is Y-up.

### Sector offsets (multi-region stitching)

Regions are 1920 SRO-local units on a side. Adjacent regions stack edge-to-edge. The sector index `(x_sector, z_sector)` is encoded in the region ID:

```
region_id = (z_sector << 8) | x_sector
```

For Hotan Kingdom with center at `5c87` (= z=0x5c, x=0x87):

```
            x_sector
  86  87  88
 +---+---+---+
 |5d86|5d87|5d88|  z=5d  (dz=+1 from center)
 +---+---+---+
 |5c86|5c87|5c88|  z=5c  (dz=0)   <- 5c87 is center
 +---+---+---+
 |5b86|5b87|5b88|  z=5b  (dz=-1)
 +---+---+---+
```

(We don't load `5d86`, `5d88`, `5b86` for Hotan Kingdom — only the 7 listed in the manifest. Northern row 5e87 is also loaded at `dz=+2`.)

Per-region offset in Three.js world coords:

```js
group.position.set(dx * 1920, 0, -dz * 1920)
```

The `-dz` matches the per-vertex Z-flip — both vertex-frame and group-frame use the same flipped axis, so a vertex at `z=0` in region `5d87` ends up at `z=-1920` in Three.js world space (one region north).

**Critical:** sector offsets go on `Group.position` **only**, never baked into vertices. Doing both gives a double-offset bug (silk-nav's gotcha #58 transferred to our codebase). See [`docs/GOTCHAS.md`](GOTCHAS.md) #10.

### UV conventions

`.bms` (SRO visual mesh) UVs are stored in **DirectX top-down** convention: `v=0` is the top of the texture. Three.js with default `texture.flipY = true` (the default for `TextureLoader.load`) results in a GPU texture equivalent to OpenGL bottom-up: `v=0` is the bottom of the texture.

Bridge: per-vertex UV transform `(u, 1 - v)`.

Applied to:
1. Building submesh UVs (carried in scene JSON, stored as DX top-down).
2. Synthesized terrain UVs: `(x / 1920, 1 - z / 1920)` — terrain mesh covers `[0, 1920]²` in region-local XZ, mapped to the `[0, 1]²` UV space of the baked terrain PNG.

Same transform as silk-nav's Blender script (`uvl.data[li].uv = (u, 1.0 - v)`). If V isn't flipped, every texture appears vertically mirrored.

### Material handling

Each submesh in the scene JSON carries:

| Field | Meaning |
|---|---|
| `texture` | PNG filename relative to the region's `textures/` dir (empty if no diffuse texture) |
| `material_name` | The `.bmt` material name selected by the submesh's BMS header |
| `material_flag` | The raw `PrimMtrlFlag` 32-bit integer |
| `base_color` | RGB(A) fallback color from the `.bmt` material |

Material flag bit `0x200` = `PrimMtrlFlag.Alpha` = the diffuse texture's alpha channel is meaningful. Three resulting render modes:

| Condition | Three.js material |
|---|---|
| No texture (or DDS decode failed) | `MeshStandardMaterial({ color: base_color })` |
| Texture, no alpha flag | `MeshStandardMaterial({ map, color: base_color })` |
| Texture, alpha flag set | `MeshStandardMaterial({ map, color: base_color, transparent: true, alphaTest: 0.5 })` |

We use **cutout alpha** (`alphaTest`), not blended alpha. Cutout matches the visual intent for plant fronds, lattice gates, railings, and avoids painter's-algorithm sorting issues. `alphaTest: 0.5` is the standard threshold.

All materials get `side: THREE.DoubleSide` as a safety net — a small fraction of SRO submeshes have inverted winding that the Z-flip alone doesn't repair. Negligible perf hit at this scene scale.

### Caching

- **Texture cache** keyed on URL. Each PNG is uploaded to the GPU exactly once even when referenced by N submeshes across M regions.
- **Material cache** keyed on `(texture URL, alpha-flag)`. Reuses the same `MeshStandardMaterial` across submeshes that share both the texture and the alpha-flag bit.

Typical numbers for Hotan Kingdom: ~330 unique texture uploads, ~140 unique materials, ~666 total draw calls (7 terrain + 458 building submeshes + ~196 .o2 decorations).

### Camera framing

After geometry load, `frameCamera(box)` computes the zone bounding box and positions the camera in a bird's-eye 3/4 perspective. Mirrors `render_zone_in_blender.py:render_zone`'s perspective shot. Near/far clip set generously (1.0 / 30k+) to accommodate full-zoom-out + close inspection.

## Adding another zone

The Hotan Kingdom pipeline generalizes cleanly. To add e.g. Jangan:

1. **silk-nav side (one-time):** run the three exporters with `--zone-regions <jangan-csv>`, producing `out/zone_jangan/`, `out/zone_jangan_o2/`, and `out/per_region/<jangan-regions>/`.
2. **Copy into our repo:** mirror the three dirs into `data/silknav_export/`.
3. **Preprocess:** `uv run python scripts/build_zone.py --zone jangan --regions <csv> --center <hex>`.
4. **Renderer:** today `web/zone.html` hardcodes `ZONE = 'hotan_kingdom'`. Either edit the constant or add URL routing (`?zone=jangan`). The renderer code itself is zone-agnostic — only the manifest URL differs.

## Refreshing Hotan Kingdom data

If silk-nav fixes a bug in the export pipeline and you want the refreshed data:

1. Re-run silk-nav's exporters to refresh `silk-nav/out/`.
2. Re-copy the three top-level dirs into our `data/silknav_export/` (overwriting).
3. `rm -rf web/zones/hotan_kingdom` (optional; build_zone.py's mtime check should detect changed PNGs but a clean rebuild is safest).
4. `uv run python scripts/build_zone.py`.
5. `uv run python zone.py`.

## See also

- [`CLAUDE.md`](../CLAUDE.md) — operational manual for Claude Code (terse).
- [`docs/GOTCHAS.md`](GOTCHAS.md) — full prose for each numbered gotcha.
- [`docs/ROADMAP.md`](ROADMAP.md) — what's shipped, what's next.
- [`docs/NAVMESH_PLAN.md`](NAVMESH_PLAN.md) — the upcoming navmesh overlay phase.
- `silk-nav/docs/SRO_VISUALS.md` — upstream visual-pipeline reference (`.bsr`/`.bmt`/`.bms`/`.ddj`/`.m`/`.o2`).
- `silk-nav/scripts/render_zone_in_blender.py` — the Blender-side renderer we mirror.
