"""Preprocess a silk-nav zone export into web-ready assets.

Reads the output of silk-nav's Blender exporters
(``export_region_with_textures.py``, ``bake_terrain_texture.py``,
``export_o2_objects.py``) and emits:

- ``web/zones/<zone>/manifest.json``
- ``web/zones/<zone>/<region>/scene.json`` (geometry; ``.dds`` filenames
  remapped to ``.png``)
- ``web/zones/<zone>/<region>/textures/*.png`` (converted via Pillow)
- ``web/zones/<zone>/<region>/terrain.png`` (baked terrain, if present)
- ``web/zones/<zone>/o2/<region>/...`` (decoration layer, optional)

Run once per zone; the JS side then loads the manifest at startup.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parent.parent

# Defaults match silk-nav's render_zone_in_blender.py USER CONFIG.
DEFAULT_ZONE_NAME = "hotan_kingdom"
DEFAULT_REGIONS = ["5c88", "5b88", "5e87", "5d87", "5c87", "5b87", "5c86"]
DEFAULT_CENTER = "5c87"
DEFAULT_SILKNAV_OUT = PROJECT_ROOT / "data" / "silknav_export"
REGION_SIZE = 1920.0


def sector_offset(region_hex: str, center_hex: str) -> tuple[int, int]:
    """Return ``(dx, dz)`` in sector units from ``center_hex`` to ``region_hex``."""
    r = int(region_hex, 16)
    c = int(center_hex, 16)
    return ((r & 0xFF) - (c & 0xFF), ((r >> 8) & 0xFF) - ((c >> 8) & 0xFF))


def dds_to_png(src: Path, dst: Path) -> bool:
    """Convert one DDS to PNG via Pillow. Returns True on success.

    Pillow's DDS reader handles DXT1/3/5 but not BC6/BC7 (newer formats).
    On NotImplementedError we leave dst absent and return False so the
    caller can fall back to base_color rendering.
    """
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return True
    try:
        with Image.open(src) as im:
            im.convert("RGBA").save(dst, format="PNG", optimize=False)
        return True
    except NotImplementedError as e:
        print(f"  WARN: cannot decode {src.name}: {e}")
        return False


def remap_scene_textures(scene: dict) -> dict:
    """Rewrite every ``.dds`` reference inside a scene.json blob to ``.png``."""
    for inst in scene.get("instances", []):
        for sub in inst.get("submeshes", []):
            tex = sub.get("texture") or ""
            if tex.lower().endswith(".dds"):
                sub["texture"] = tex[:-4] + ".png"
    for entry in scene.get("texture_files", []):
        f = entry.get("file", "")
        if f.lower().endswith(".dds"):
            entry["file"] = f[:-4] + ".png"
    return scene


def process_region(
    region: str,
    src_zone_dir: Path,
    src_per_region_dir: Path | None,
    dst_region_dir: Path,
) -> dict:
    """Copy scene.json + convert textures + copy terrain.png for one region.

    Returns a small summary dict suitable for the manifest.
    """
    src_scene = src_zone_dir / region / f"{region}_scene.json"
    if not src_scene.exists():
        return {"region": region, "status": "missing"}

    scene = json.loads(src_scene.read_text())
    scene = remap_scene_textures(scene)

    dst_region_dir.mkdir(parents=True, exist_ok=True)
    (dst_region_dir / "scene.json").write_text(json.dumps(scene))

    # Convert all DDS textures.
    src_tex = src_zone_dir / region / "textures"
    dst_tex = dst_region_dir / "textures"
    dst_tex.mkdir(parents=True, exist_ok=True)
    n_tex = 0
    if src_tex.exists():
        for dds in src_tex.glob("*.dds"):
            png = dst_tex / (dds.stem + ".png")
            if dds_to_png(dds, png):
                n_tex += 1

    # Optional baked terrain.
    terrain_present = False
    if src_per_region_dir is not None:
        cand = src_per_region_dir / region / f"terrain_{region}.png"
        if cand.exists():
            shutil.copy2(cand, dst_region_dir / "terrain.png")
            terrain_present = True

    return {
        "region": region,
        "status": "ok",
        "instances": len(scene.get("instances", [])),
        "textures": n_tex,
        "terrain": terrain_present,
    }


def process_o2_region(
    region: str,
    src_o2_dir: Path,
    dst_o2_region_dir: Path,
) -> dict:
    """Like process_region but for the .o2 decoration layer."""
    src_scene = src_o2_dir / region / f"{region}_o2_scene.json"
    if not src_scene.exists():
        return {"region": region, "status": "missing"}

    scene = json.loads(src_scene.read_text())
    scene = remap_scene_textures(scene)

    dst_o2_region_dir.mkdir(parents=True, exist_ok=True)
    (dst_o2_region_dir / "scene.json").write_text(json.dumps(scene))

    src_tex = src_o2_dir / region / "textures"
    dst_tex = dst_o2_region_dir / "textures"
    dst_tex.mkdir(parents=True, exist_ok=True)
    n_tex = 0
    if src_tex.exists():
        for dds in src_tex.glob("*.dds"):
            png = dst_tex / (dds.stem + ".png")
            if dds_to_png(dds, png):
                n_tex += 1

    return {
        "region": region,
        "status": "ok",
        "instances": len(scene.get("instances", [])),
        "textures": n_tex,
    }


def build_zone(
    zone_name: str,
    regions: list[str],
    center: str,
    silknav_out: Path,
    web_root: Path,
    include_o2: bool,
) -> None:
    src_zone_dir = silknav_out / f"zone_{zone_name}"
    src_per_region = silknav_out / "per_region"
    src_o2_dir = silknav_out / f"zone_{zone_name}_o2"

    if not src_zone_dir.exists():
        raise SystemExit(f"missing source zone dir: {src_zone_dir}")

    dst_root = web_root / "zones" / zone_name
    dst_root.mkdir(parents=True, exist_ok=True)

    region_summaries: list[dict] = []
    region_offsets: dict[str, list[int]] = {}
    for region in regions:
        dx, dz = sector_offset(region, center)
        region_offsets[region] = [dx, dz]
        dst_region_dir = dst_root / region
        summary = process_region(region, src_zone_dir, src_per_region, dst_region_dir)
        summary["sector_offset"] = [dx, dz]
        summary["origin"] = [dx * REGION_SIZE, dz * REGION_SIZE]
        region_summaries.append(summary)
        print(
            f"  {region} dx={dx:+d} dz={dz:+d}: {summary.get('instances', 0)} placements, "
            f"{summary.get('textures', 0)} textures"
            + (", terrain.png" if summary.get("terrain") else "")
        )

    o2_summaries: list[dict] = []
    if include_o2 and src_o2_dir.exists():
        dst_o2_root = dst_root / "o2"
        dst_o2_root.mkdir(parents=True, exist_ok=True)
        for region in regions:
            dst_o2_region = dst_o2_root / region
            summary = process_o2_region(region, src_o2_dir, dst_o2_region)
            o2_summaries.append(summary)
            if summary.get("status") == "ok":
                print(
                    f"  o2 {region}: {summary['instances']} placements, "
                    f"{summary['textures']} textures"
                )

    manifest = {
        "zone": zone_name,
        "center_region": center,
        "region_size": REGION_SIZE,
        "regions": region_summaries,
        "o2": o2_summaries if include_o2 else [],
        "axis": {
            "source": "SRO local: X right, Y up, Z forward (left-handed)",
            "target": "Three.js Y-up RH; flip Z per-vertex; UV V flipped",
        },
    }
    manifest_path = dst_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {manifest_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zone", default=DEFAULT_ZONE_NAME)
    ap.add_argument("--regions", default=",".join(DEFAULT_REGIONS))
    ap.add_argument("--center", default=DEFAULT_CENTER)
    ap.add_argument("--silknav-out", type=Path, default=DEFAULT_SILKNAV_OUT)
    ap.add_argument("--web-root", type=Path, default=PROJECT_ROOT / "web")
    ap.add_argument("--no-o2", action="store_true", help="Skip the .o2 decoration layer")
    args = ap.parse_args()

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    build_zone(
        zone_name=args.zone,
        regions=regions,
        center=args.center,
        silknav_out=args.silknav_out,
        web_root=args.web_root,
        include_o2=not args.no_o2,
    )


if __name__ == "__main__":
    main()
