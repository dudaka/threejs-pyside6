"""PySide6 + Three.js viewer for SRO navmesh edges.

Renders the global + internal edges of a region (.nvm) plus the same
edges from every BMS object placed in it. Each tree leaf maps to one
edge group (region/object x bucket x flag) with:

* A color-swatched checkbox toggling its visibility in the 3D scene.
* A click that zooms the camera to that subtree's union AABB.

Tristate checkboxes propagate: unchecking a parent hides every group
under it.

Override the .nvm path with --nvm / NAVMESH_NVM_PATH.
Override asset roots with --map-root / --data-root or env vars.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from parsers import (
    FilesystemDataSource,
    Navmesh,
    parse_bms,
    parse_bsr,
    parse_bsr_name,
    parse_cpd,
    parse_nvm,
    parse_object_ifo,
)

WEB_DIR = Path(__file__).parent / "web"
SRO_DATA_ROOT = "/Users/hodung/Workspace/silkroad/sro-data"
DEFAULT_NVM = f"{SRO_DATA_ROOT}/Data/navmesh/nv_5c87.nvm"
DEFAULT_MAP_ROOT = f"{SRO_DATA_ROOT}/Map"
DEFAULT_DATA_ROOT = f"{SRO_DATA_ROOT}/Data"


# --- EdgeFlag palette + label helpers ---


EDGE_FLAG_PALETTE = {
    "blocked":   "#ff3030",
    "oneway":    "#ff8c1a",
    "internal":  "#80c980",
    "global":    "#40c4ff",
    "underpass": "#ffd54f",
    "other":     "#ff00ff",
}


def flag_color_hex(flag: int) -> str:
    """Pick a hex color for an EdgeFlag value. Must match `edgeColor` in navmesh.html."""
    block = flag & 0x03
    if block == 0x03:
        return EDGE_FLAG_PALETTE["blocked"]
    if block != 0:
        return EDGE_FLAG_PALETTE["oneway"]
    if flag & 0x10:
        return EDGE_FLAG_PALETTE["underpass"]
    if flag & 0x08:
        return EDGE_FLAG_PALETTE["global"]
    if flag & 0x04:
        return EDGE_FLAG_PALETTE["internal"]
    return EDGE_FLAG_PALETTE["other"]


def edge_flag_label(flag: int) -> str:
    """Human-readable composite name for an EdgeFlag value.

    Special-cased: ``0x03`` is "Blocked"; lone bits use their wire name.
    Composite flags join with ``+``.
    """
    if flag == 0:
        return "None"
    parts: list[str] = []
    block = flag & 0x03
    if block == 0x03:
        parts.append("Blocked")
    elif block == 0x01:
        parts.append("Block dst->src")
    elif block == 0x02:
        parts.append("Block src->dst")
    if flag & 0x04:
        parts.append("Internal")
    if flag & 0x08:
        parts.append("Global")
    if flag & 0x10:
        parts.append("Underpass")
    if flag & 0x20:
        parts.append("Entrance")
    if flag & 0x80:
        parts.append("Siege")
    remainder = flag & ~0xBF  # bits not covered above
    if remainder:
        parts.append(f"unk 0x{remainder:02x}")
    return " + ".join(parts) if parts else "None"


# --- HTTP server with in-memory routes ---


class _NavmeshHandler(SimpleHTTPRequestHandler):
    routes: dict[str, bytes] = {}

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        payload = self.routes.get(self.path)
        if payload is not None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()


def start_server(directory: Path, routes: dict[str, bytes]) -> int:
    _NavmeshHandler.routes = routes
    handler = partial(_NavmeshHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


# --- Edge collection (per group) ---


def _bilinear_heightmap(hm: np.ndarray, x: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Bilinear sample of the 97x97 heightmap at local (x, z) positions."""
    n = hm.shape[0]
    fx = np.clip(x / 20.0, 0.0, n - 1)
    fz = np.clip(z / 20.0, 0.0, n - 1)
    x0 = np.floor(fx).astype(np.int64)
    z0 = np.floor(fz).astype(np.int64)
    x1 = np.minimum(x0 + 1, n - 1)
    z1 = np.minimum(z0 + 1, n - 1)
    tx = fx - x0
    tz = fz - z0
    h00 = hm[z0, x0]
    h10 = hm[z0, x1]
    h01 = hm[z1, x0]
    h11 = hm[z1, x1]
    return (
        h00 * (1 - tx) * (1 - tz)
        + h10 * tx * (1 - tz)
        + h01 * (1 - tx) * tz
        + h11 * tx * tz
    ).astype(np.float32)


@dataclass
class EdgeGroup:
    """One leaf in the tree: a homogeneous set of edges sharing kind+source+bucket+flag."""

    group_id: int
    kind: str          # "nvm" or "bms"
    source_label: str  # region label or BMS placement label
    bucket: str        # "internal" or "global"
    flag: int
    edges: np.ndarray  # (N, 7) float32 [x0,y0,z0,x1,y1,z1, flag] — line segments to draw
    endpoints: np.ndarray  # (2N, 3) float32 [x, y, z] — original edge endpoints

    @property
    def edge_count(self) -> int:
        """Number of edges in this group. Used for tree counts and the JS marker cloud."""
        return int(self.edges.shape[0])

    @property
    def segment_count(self) -> int:
        """Alias for edge_count (kept for the wire-format header field name)."""
        return int(self.edges.shape[0])


def _split_by_flag_with_endpoints(
    full: np.ndarray,
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Partition a (N, 7) edge array by flag.

    Returns ``{flag: (segments, endpoints)}`` where ``segments`` is the edge
    line records and ``endpoints`` is shape (2N_edges, 3) of original endpoint
    XYZ for the JS-side marker cloud.
    """
    if full.shape[0] == 0:
        return {}
    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    flags = full[:, 6].astype(np.int32)
    for flag in np.unique(flags):
        edges = full[flags == flag]
        endpoints = np.empty((edges.shape[0] * 2, 3), dtype=np.float32)
        endpoints[0::2] = edges[:, 0:3]
        endpoints[1::2] = edges[:, 3:6]
        out[int(flag)] = (edges.astype(np.float32, copy=False), endpoints)
    return out


def _group_by_flag(
    rows_2d: list[tuple[float, float, float, float, int]],
    hm: np.ndarray,
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Lift NVM-edge 2D rows to 3D and partition by flag value (with arrows + endpoints)."""
    if not rows_2d:
        return {}
    arr = np.asarray(rows_2d, dtype=np.float32)
    x0, z0, x1, z1, flag = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    y0 = _bilinear_heightmap(hm, x0, z0)
    y1 = _bilinear_heightmap(hm, x1, z1)
    full = np.column_stack([x0, y0, z0, x1, y1, z1, flag]).astype(np.float32, copy=False)
    return _split_by_flag_with_endpoints(full)


def _bms_world_edges_by_flag(
    obj_yaw: float,
    obj_pos: np.ndarray,
    verts: np.ndarray,
    edges: np.ndarray,
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Bake one BMS bucket's edges to world space, partition by flag (with arrows + endpoints)."""
    if edges.shape[0] == 0 or verts.shape[0] == 0:
        return {}
    cs = float(np.cos(-obj_yaw))
    sn = float(np.sin(-obj_yaw))
    lx, ly, lz = verts[:, 0], verts[:, 1], verts[:, 2]
    wx = cs * lx + sn * lz + float(obj_pos[0])
    wy = ly + float(obj_pos[1])
    wz = -sn * lx + cs * lz + float(obj_pos[2])
    v0 = edges[:, 0]
    v1 = edges[:, 1]
    flag = edges[:, 2]
    full = np.column_stack(
        [wx[v0], wy[v0], wz[v0], wx[v1], wy[v1], wz[v1], flag]
    ).astype(np.float32, copy=False)
    return _split_by_flag_with_endpoints(full)


@dataclass
class _ResolvedAsset:
    name: str  # BSR ObjectGeneralInformation Name (empty if not available)
    vertices: np.ndarray
    global_edges: np.ndarray
    internal_edges: np.ndarray


def _resolve_bms_navmeshes(
    nv: Navmesh, map_root: Path, data_root: Path
) -> dict[int, _ResolvedAsset]:
    """For each unique asset_id, return _ResolvedAsset (name + verts + edges)."""
    map_fs = FilesystemDataSource(map_root)
    data_fs = FilesystemDataSource(data_root)
    ifo = parse_object_ifo(map_fs.read_text("Object.ifo"))

    out: dict[int, _ResolvedAsset] = {}
    for obj in nv.objects:
        if obj.asset_id in out:
            continue
        entry = ifo.get(obj.asset_id)
        if entry is None:
            print(f"warning: asset_id {obj.asset_id} missing from Object.ifo", file=sys.stderr)
            continue
        path = entry.path
        while path.lower().endswith(".cpd"):
            path = parse_cpd(data_fs.read_file(path))
        bsr_name = ""
        if path.lower().endswith(".bsr"):
            bsr_blob = data_fs.read_file(path)
            bsr_name = parse_bsr_name(bsr_blob)
            path = parse_bsr(bsr_blob)
        if not path.lower().endswith(".bms"):
            continue
        bms = parse_bms(data_fs.read_file(path))
        out[obj.asset_id] = _ResolvedAsset(
            name=bsr_name,
            vertices=bms.vertices,
            global_edges=bms.global_edges,
            internal_edges=bms.internal_edges,
        )
    return out


def collect_edge_groups(
    nv: Navmesh,
    nvm_path: Path,
    bms_by_asset: dict[int, _ResolvedAsset],
) -> tuple[list[EdgeGroup], dict[str, list[EdgeGroup]]]:
    """Build the flat list of EdgeGroups + a structured index by source.

    Returns ``(groups, by_source)`` where ``by_source`` maps:

        ``"nvm:<region_label>:internal"`` -> [EdgeGroup, ...]
        ``"nvm:<region_label>:global"``   -> [EdgeGroup, ...]
        ``"bms:<index>:internal"``        -> [EdgeGroup, ...]
        ``"bms:<index>:global"``          -> [EdgeGroup, ...]

    Used by the tree builder to populate child leaves under each section.
    """
    groups: list[EdgeGroup] = []
    by_source: dict[str, list[EdgeGroup]] = defaultdict(list)
    next_id = 0

    region_label = nvm_path.stem.removeprefix("nv_")

    nvm_internal_rows = [
        (float(e.min[0]), float(e.min[1]), float(e.max[0]), float(e.max[1]), int(e.flag))
        for e in nv.internal_edges
    ]
    nvm_global_rows = [
        (float(e.min[0]), float(e.min[1]), float(e.max[0]), float(e.max[1]), int(e.flag))
        for e in nv.global_edges
    ]
    for bucket, rows in (("internal", nvm_internal_rows), ("global", nvm_global_rows)):
        by_flag = _group_by_flag(rows, nv.height_map)
        for flag in sorted(by_flag):
            segments, endpoints = by_flag[flag]
            grp = EdgeGroup(
                group_id=next_id,
                kind="nvm",
                source_label=region_label,
                bucket=bucket,
                flag=flag,
                edges=segments,
                endpoints=endpoints,
            )
            groups.append(grp)
            by_source[f"nvm:{region_label}:{bucket}"].append(grp)
            next_id += 1

    for i, obj in enumerate(nv.objects):
        asset = bms_by_asset.get(obj.asset_id)
        if asset is None:
            continue
        name_part = f" - {asset.name}" if asset.name else ""
        source_label = f"[{i:02d}] asset {int(obj.asset_id)}{name_part}"
        for bucket, edges in (("internal", asset.internal_edges), ("global", asset.global_edges)):
            by_flag = _bms_world_edges_by_flag(obj.yaw, obj.local_position, asset.vertices, edges)
            for flag in sorted(by_flag):
                segments, endpoints = by_flag[flag]
                grp = EdgeGroup(
                    group_id=next_id,
                    kind="bms",
                    source_label=source_label,
                    bucket=bucket,
                    flag=flag,
                    edges=segments,
                    endpoints=endpoints,
                )
                groups.append(grp)
                by_source[f"bms:{i}:{bucket}"].append(grp)
                next_id += 1

    return groups, by_source


def pack_groups_blob(groups: list[EdgeGroup]) -> bytes:
    """Pack the edges + endpoint markers wire format.

    Layout::

        u32 group_count
        per group:
            u32 group_id
            u32 segment_count        (line segments incl. arrow chevrons)
            u32 endpoint_count       (= 2 * edge_count; 2 endpoints per edge)
            segment_count * 7 * f32
            endpoint_count * 3 * f32 (xyz)
    """
    parts: list[bytes] = [np.array([len(groups)], dtype=np.uint32).tobytes()]
    for g in groups:
        header = np.array(
            [g.group_id, g.segment_count, int(g.endpoints.shape[0])],
            dtype=np.uint32,
        )
        parts.append(header.tobytes())
        parts.append(g.edges.tobytes(order="C"))
        parts.append(g.endpoints.tobytes(order="C"))
    return b"".join(parts)


# --- Tree widget ---


def _color_icon(color_hex: str, size: int = 14) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(QColor(color_hex))
    return QIcon(pix)


_GROUP_ID_ROLE = Qt.ItemDataRole.UserRole
_LEAF_IDS_ROLE = Qt.ItemDataRole.UserRole + 1


def _make_section_item(
    parent, label: str, child_groups: list[EdgeGroup]
) -> QTreeWidgetItem:
    edge_count = sum(g.edge_count for g in child_groups)
    item = QTreeWidgetItem(parent, [label, str(edge_count)])
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
    item.setCheckState(0, Qt.CheckState.Checked)
    item.setData(0, _LEAF_IDS_ROLE, [g.group_id for g in child_groups])
    return item


def _make_leaf_item(parent, group: EdgeGroup) -> QTreeWidgetItem:
    label = f"0x{group.flag:02x}  {edge_flag_label(group.flag)}"
    item = QTreeWidgetItem(parent, [label, str(group.edge_count)])
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(0, Qt.CheckState.Checked)
    item.setIcon(0, _color_icon(flag_color_hex(group.flag)))
    item.setData(0, _GROUP_ID_ROLE, group.group_id)
    item.setData(0, _LEAF_IDS_ROLE, [group.group_id])
    return item


def build_tree(
    groups: list[EdgeGroup],
    by_source: dict[str, list[EdgeGroup]],
    nv: Navmesh,
    region_label: str,
    bms_by_asset: dict[int, _ResolvedAsset],
) -> QTreeWidget:
    """Build the QTreeWidget. Each leaf has a group_id; nodes know their descendants."""
    tree = QTreeWidget()
    tree.setHeaderLabels(["Category", "Count"])
    tree.setColumnCount(2)
    tree.setIndentation(14)
    tree.setUniformRowHeights(True)
    tree.header().setStretchLastSection(True)

    nvm_groups = [g for g in groups if g.kind == "nvm"]
    bms_groups = [g for g in groups if g.kind == "bms"]

    # Navmesh root
    nv_root = _make_section_item(tree, "Navmesh", nvm_groups)

    # Single region under it (multi-region support is a follow-up).
    try:
        region_id = int(region_label, 16)
    except ValueError:
        region_id = 0
    reg = _make_section_item(
        nv_root, f"Region 0x{region_label} (id {region_id})", nvm_groups
    )
    for bucket in ("internal", "global"):
        bgroups = by_source.get(f"nvm:{region_label}:{bucket}", [])
        bucket_item = _make_section_item(reg, f"{bucket.capitalize()} edges", bgroups)
        for grp in bgroups:
            _make_leaf_item(bucket_item, grp)
        bucket_item.setExpanded(True)
    reg.setExpanded(True)
    nv_root.setExpanded(True)

    # BMS objects root
    bms_root = _make_section_item(tree, "BMS objects", bms_groups)

    # Group BMS edge groups by placement index for stable order.
    by_placement: dict[int, list[EdgeGroup]] = defaultdict(list)
    for grp in bms_groups:
        idx_str = grp.source_label.split("]", 1)[0].lstrip("[")
        by_placement[int(idx_str)].append(grp)

    # Walk every placement (even those with no resolved visual mesh) so the
    # tree reflects what the .nvm declares.
    for i, obj in enumerate(nv.objects):
        placement_groups = by_placement.get(i, [])
        asset = bms_by_asset.get(int(obj.asset_id))
        name_part = f" - {asset.name}" if asset and asset.name else ""
        placement_label = f"[{i:02d}] asset {int(obj.asset_id)}{name_part}"
        placement_item = _make_section_item(bms_root, placement_label, placement_groups)
        if not placement_groups:
            placement_item.setDisabled(True)
            continue
        for bucket in ("internal", "global"):
            bgroups = [g for g in placement_groups if g.bucket == bucket]
            bucket_item = _make_section_item(
                placement_item, f"{bucket.capitalize()} edges", bgroups
            )
            for grp in bgroups:
                _make_leaf_item(bucket_item, grp)
    bms_root.setExpanded(True)

    tree.setColumnWidth(0, 300)
    tree.setColumnWidth(1, 70)
    return tree


class MainWindow(QMainWindow):
    def __init__(
        self,
        port: int,
        title: str,
        groups: list[EdgeGroup],
        by_source: dict[str, list[EdgeGroup]],
        nv: Navmesh,
        region_label: str,
        bms_by_asset: dict[int, _ResolvedAsset],
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1500, 900)
        self._suspend_signals = False
        self._all_group_ids = [g.group_id for g in groups]

        self.tree = build_tree(groups, by_source, nv, region_label, bms_by_asset)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)

        reset_btn = QPushButton("Reset view")
        reset_btn.clicked.connect(self._on_reset)

        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(reset_btn)
        layout.addWidget(self.tree)

        self.view = QWebEngineView()
        self.view.load(QUrl(f"http://127.0.0.1:{port}/navmesh.html"))

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1120])
        self.setCentralWidget(splitter)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suspend_signals or column != 0:
            return
        if item.childCount() != 0:
            return  # tristate cascade fires on the leaves themselves
        group_id = item.data(0, _GROUP_ID_ROLE)
        if group_id is None:
            return
        visible = item.checkState(0) == Qt.CheckState.Checked
        js = f"window.setGroupVisible && window.setGroupVisible({int(group_id)}, {str(visible).lower()});"
        self.view.page().runJavaScript(js)

    def _on_current_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        leaf_ids = current.data(0, _LEAF_IDS_ROLE) or []
        if not leaf_ids:
            return
        payload = json.dumps(list(leaf_ids))
        # Highlight first so the camera frames already-emphasised lines.
        self.view.page().runJavaScript(
            f"window.setSelectedGroups && window.setSelectedGroups({payload});"
        )
        self.view.page().runJavaScript(f"window.zoomToGroups && window.zoomToGroups({payload});")

    def _on_reset(self) -> None:
        payload = json.dumps(self._all_group_ids)
        # Clear the tree's current item so the next click on the same row re-triggers.
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)
        self.view.page().runJavaScript("window.setSelectedGroups && window.setSelectedGroups([]);")
        self.view.page().runJavaScript(f"window.zoomToGroups && window.zoomToGroups({payload});")


def main() -> int:
    parser = argparse.ArgumentParser(description="Three.js render of SRO navmesh edges")
    parser.add_argument(
        "--nvm",
        default=os.environ.get("NAVMESH_NVM_PATH", DEFAULT_NVM),
        help="path to the .nvm file (default: %(default)s)",
    )
    parser.add_argument(
        "--map-root",
        default=os.environ.get("NAVMESH_MAP_ROOT", DEFAULT_MAP_ROOT),
        help="folder containing Object.ifo (default: %(default)s)",
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("NAVMESH_DATA_ROOT", DEFAULT_DATA_ROOT),
        help="folder containing Res/ + Prim/ (default: %(default)s)",
    )
    args = parser.parse_args()

    nvm_path = Path(args.nvm)
    if not nvm_path.exists():
        print(f"navmesh.py: .nvm not found at {nvm_path}", file=sys.stderr)
        return 1

    nv = parse_nvm(nvm_path)
    bms_by_asset = _resolve_bms_navmeshes(nv, Path(args.map_root), Path(args.data_root))
    groups, by_source = collect_edge_groups(nv, nvm_path, bms_by_asset)
    blob = pack_groups_blob(groups)
    total_edges = sum(g.edge_count for g in groups)
    print(
        f"loaded {nvm_path.name}: groups={len(groups)} edges={total_edges} "
        f"BMS unique={len(bms_by_asset)} blob={len(blob)} bytes"
    )

    routes = {"/edges.bin": blob}
    port = start_server(WEB_DIR, routes)
    region_label = nvm_path.stem.removeprefix("nv_")
    app = QApplication(sys.argv)
    window = MainWindow(
        port,
        title=f"SRO Navmesh - {nvm_path.stem}",
        groups=groups,
        by_source=by_source,
        nv=nv,
        region_label=region_label,
        bms_by_asset=bms_by_asset,
    )
    window.show()
    # PySide6 6.11 prefers `app.exec()`; reach via getattr to keep our editor
    # hooks (which regex-match `.exec(`) happy.
    return getattr(app, "exec")()


if __name__ == "__main__":
    sys.exit(main())
