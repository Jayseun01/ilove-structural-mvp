import datetime
import io
import json
import math
import os
import tempfile
import zipfile

import ezdxf
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# =========================================================
# STREAMLIT CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - 3D Structural Model Builder",
    page_icon=":building_construction:",
    layout="wide",
)

st.title("iLoveStructural")
st.subheader("3D Structural Model Builder")
st.caption(
    "Upload ordered floor DXFs -> stack walls/slabs in 3D -> edit columns and beams -> review continuity/cantilevers -> export clean model data and DXF."
)

st.info(
    "This is a modelling and review assistant. It is not a final structural analysis, design check, or stamped engineering output."
)


# =========================================================
# CONSTANTS
# =========================================================

WALL_LAYER_KEYWORDS = [
    "wall",
    "walls",
    "a-wall",
    "a_wall",
    "arch-wall",
    "partition",
    "blockwork",
    "masonry",
    "external",
    "internal",
]

NON_WALL_LAYER_KEYWORDS = [
    "door",
    "window",
    "grid",
    "axis",
    "dim",
    "dimension",
    "text",
    "anno",
    "annotation",
    "furn",
    "furniture",
    "fixture",
    "toilet",
    "stair",
    "hatch",
    "room",
    "label",
    "title",
    "column",
    "beam",
    "slab",
    "roof",
    "plumb",
    "elect",
    "light",
    "section",
    "elevation",
]

COLUMN_COLORS = {
    "continuous": "#22c55e",
    "partial": "#f59e0b",
    "starts_above": "#f97316",
    "terminates_early": "#f97316",
    "misaligned_review": "#ef4444",
    "user_added": "#3b82f6",
    "unconfirmed": "#9ca3af",
}

BEAM_COLORS = {
    "primary": "#1d4ed8",
    "secondary": "#38bdf8",
    "transfer_review": "#f97316",
    "warning": "#ef4444",
    "user_added": "#8b5cf6",
}


# =========================================================
# FILE + DXF HELPERS
# =========================================================

def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def save_uploaded_to_temp(uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name


def read_uploaded_dxf(uploaded_file):
    tmp_path = None
    try:
        tmp_path = save_uploaded_to_temp(uploaded_file)
        return ezdxf.readfile(tmp_path)
    finally:
        safe_remove_file(tmp_path)


def write_doc_to_bytes(doc):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp_path = tmp.name
    tmp.close()

    try:
        doc.saveas(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        safe_remove_file(tmp_path)


def build_zip_bytes(file_items):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in file_items:
            zf.writestr(filename, data)
    buffer.seek(0)
    return buffer.getvalue()


def get_layer_names(doc):
    try:
        return sorted(layer.dxf.name for layer in doc.layers)
    except Exception:
        return []


def layer_wall_likelihood_score(layer_name, usable_count):
    name = str(layer_name).lower()
    score = 0

    for word in WALL_LAYER_KEYWORDS:
        if word in name:
            score += 6

    for word in NON_WALL_LAYER_KEYWORDS:
        if word in name:
            score -= 8

    if usable_count:
        score += min(6, usable_count)

    return score


def get_layer_entity_summary(doc):
    rows = {}
    usable_types = {"LINE", "LWPOLYLINE", "POLYLINE"}

    for layer in get_layer_names(doc):
        rows[layer] = {
            "layer": layer,
            "total_entities": 0,
            "usable_linear_entities": 0,
            "wall_score": 0,
            "suggested_wall_layer": False,
        }

    try:
        entities = list(doc.modelspace())
    except Exception:
        entities = []

    for entity in entities:
        try:
            layer = entity.dxf.layer
            dxftype = entity.dxftype()
        except Exception:
            continue

        rows.setdefault(layer, {
            "layer": layer,
            "total_entities": 0,
            "usable_linear_entities": 0,
            "wall_score": 0,
            "suggested_wall_layer": False,
        })

        rows[layer]["total_entities"] += 1
        if dxftype in usable_types:
            rows[layer]["usable_linear_entities"] += 1

    out = []
    for row in rows.values():
        row["wall_score"] = layer_wall_likelihood_score(row["layer"], row["usable_linear_entities"])
        row["suggested_wall_layer"] = row["usable_linear_entities"] > 0 and row["wall_score"] >= 6
        out.append(row)

    return sorted(out, key=lambda r: (-r["suggested_wall_layer"], -r["wall_score"], r["layer"]))


def suggested_wall_layers_from_summary(summary):
    return [row["layer"] for row in summary if row.get("suggested_wall_layer")]


def parse_wall_thicknesses(text):
    values = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(float(part))
            if value > 0 and value not in values:
                values.append(value)
        except Exception:
            pass
    return values


# =========================================================
# GEOMETRY HELPERS
# =========================================================

def distance(p1, p2):
    return math.hypot(float(p1[0]) - float(p2[0]), float(p1[1]) - float(p2[1]))


def segment_length(seg):
    return distance((seg["x1"], seg["y1"]), (seg["x2"], seg["y2"]))


def is_horizontal(x1, y1, x2, y2, tol):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


def is_vertical(x1, y1, x2, y2, tol):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def overlap_range(a1, b1, a2, b2):
    start = max(min(a1, b1), min(a2, b2))
    end = min(max(a1, b1), max(a2, b2))
    return start, end


def interval_overlap_length(a1, b1, a2, b2):
    start, end = overlap_range(a1, b1, a2, b2)
    return max(0.0, end - start)


def cluster_values(values, tol):
    clean = sorted(float(v) for v in values)
    if not clean:
        return []

    clusters = [[clean[0]]]
    for value in clean[1:]:
        avg = sum(clusters[-1]) / len(clusters[-1])
        if abs(value - avg) <= tol:
            clusters[-1].append(value)
        else:
            clusters.append([value])

    return [sum(c) / len(c) for c in clusters]


def nearest_value(value, values, tol=None):
    if not values:
        return None
    best = min(values, key=lambda v: abs(float(v) - float(value)))
    if tol is not None and abs(best - value) > tol:
        return None
    return best


def make_face_segment(x1, y1, x2, y2, layer, source_type, ortho_tol):
    if is_horizontal(x1, y1, x2, y2, ortho_tol):
        y = (float(y1) + float(y2)) / 2.0
        return {
            "orientation": "H",
            "c": y,
            "a": min(float(x1), float(x2)),
            "b": max(float(x1), float(x2)),
            "x1": float(x1),
            "y1": y,
            "x2": float(x2),
            "y2": y,
            "layer": layer,
            "source_type": source_type,
        }

    if is_vertical(x1, y1, x2, y2, ortho_tol):
        x = (float(x1) + float(x2)) / 2.0
        return {
            "orientation": "V",
            "c": x,
            "a": min(float(y1), float(y2)),
            "b": max(float(y1), float(y2)),
            "x1": x,
            "y1": float(y1),
            "x2": x,
            "y2": float(y2),
            "layer": layer,
            "source_type": source_type,
        }

    return None


def centerline_segment(orientation, c, a, b, thickness, floor_label, source="paired_faces"):
    if orientation == "H":
        x1, y1 = a, c
        x2, y2 = b, c
    else:
        x1, y1 = c, a
        x2, y2 = c, b

    return {
        "id": "",
        "floor": floor_label,
        "orientation": orientation,
        "c": float(c),
        "a": float(min(a, b)),
        "b": float(max(a, b)),
        "x1": float(x1),
        "y1": float(y1),
        "x2": float(x2),
        "y2": float(y2),
        "thickness": float(thickness),
        "source": source,
    }


def merge_collinear_segments(segments, axis_tol, gap_tol):
    if not segments:
        return []

    grouped = {}
    for seg in segments:
        key = (seg["floor"], seg["orientation"], round(seg["c"] / max(axis_tol, 1.0)))
        grouped.setdefault(key, []).append(seg)

    merged = []
    for items in grouped.values():
        if not items:
            continue
        items = sorted(items, key=lambda s: (s["a"], s["b"]))
        cur = dict(items[0])
        members = [items[0]]

        def flush():
            avg_c = sum(s["c"] for s in members) / len(members)
            thickness = max(float(s.get("thickness", cur["thickness"])) for s in members)
            out = centerline_segment(cur["orientation"], avg_c, cur["a"], cur["b"], thickness, cur["floor"], "merged_wall_axis")
            merged.append(out)

        for seg in items[1:]:
            if abs(seg["c"] - cur["c"]) <= axis_tol and seg["a"] <= cur["b"] + gap_tol:
                cur["b"] = max(cur["b"], seg["b"])
                members.append(seg)
            else:
                flush()
                cur = dict(seg)
                members = [seg]
        flush()

    return sorted(merged, key=lambda s: (s["floor"], s["orientation"], s["c"], s["a"]))


def bounds_from_segments(segments):
    xs = []
    ys = []
    for seg in segments:
        xs.extend([seg["x1"], seg["x2"]])
        ys.extend([seg["y1"], seg["y2"]])

    if not xs or not ys:
        return {
            "min_x": 0.0,
            "max_x": 0.0,
            "min_y": 0.0,
            "max_y": 0.0,
            "width": 0.0,
            "height": 0.0,
        }

    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


# =========================================================
# DXF EXTRACTION
# =========================================================

def iter_entity_line_segments(entity):
    dxftype = entity.dxftype()

    if dxftype == "LINE":
        start = entity.dxf.start
        end = entity.dxf.end
        yield float(start.x), float(start.y), float(end.x), float(end.y), "LINE"
        return

    if dxftype == "LWPOLYLINE":
        pts = []
        try:
            for p in entity.get_points("xyseb"):
                pts.append((float(p[0]), float(p[1]), float(p[4]) if len(p) >= 5 else 0.0))
        except Exception:
            for p in entity.get_points():
                pts.append((float(p[0]), float(p[1]), 0.0))

        for i in range(len(pts) - 1):
            x1, y1, bulge = pts[i]
            x2, y2, _ = pts[i + 1]
            if abs(bulge) <= 1e-12:
                yield x1, y1, x2, y2, "LWPOLYLINE"
        try:
            if entity.closed and len(pts) >= 2:
                x1, y1, bulge = pts[-1]
                x2, y2, _ = pts[0]
                if abs(bulge) <= 1e-12:
                    yield x1, y1, x2, y2, "LWPOLYLINE_CLOSED"
        except Exception:
            pass
        return

    if dxftype == "POLYLINE":
        pts = []
        try:
            for vertex in entity.vertices:
                loc = vertex.dxf.location
                pts.append((float(loc.x), float(loc.y), float(getattr(vertex.dxf, "bulge", 0.0))))
        except Exception:
            pts = []

        for i in range(len(pts) - 1):
            x1, y1, bulge = pts[i]
            x2, y2, _ = pts[i + 1]
            if abs(bulge) <= 1e-12:
                yield x1, y1, x2, y2, "POLYLINE"
        try:
            if entity.is_closed and len(pts) >= 2:
                x1, y1, bulge = pts[-1]
                x2, y2, _ = pts[0]
                if abs(bulge) <= 1e-12:
                    yield x1, y1, x2, y2, "POLYLINE_CLOSED"
        except Exception:
            pass


def extract_wall_faces(doc, selected_layers, use_all_layers, ortho_tol, min_line_length):
    faces = []
    ignored = 0
    try:
        entities = list(doc.modelspace())
    except Exception:
        entities = []

    selected = set(selected_layers or [])

    for entity in entities:
        try:
            layer = entity.dxf.layer
            if not use_all_layers and layer not in selected:
                continue

            for x1, y1, x2, y2, source_type in iter_entity_line_segments(entity):
                if math.hypot(x2 - x1, y2 - y1) < min_line_length:
                    ignored += 1
                    continue
                face = make_face_segment(x1, y1, x2, y2, layer, source_type, ortho_tol)
                if face:
                    faces.append(face)
                else:
                    ignored += 1
        except Exception:
            ignored += 1

    return faces, ignored


def detect_wall_axes_from_faces(faces, floor_label, wall_thicknesses, thickness_tol, min_overlap):
    horizontal = [f for f in faces if f["orientation"] == "H"]
    vertical = [f for f in faces if f["orientation"] == "V"]
    axes = []

    for thickness in wall_thicknesses:
        for i, f1 in enumerate(horizontal):
            for f2 in horizontal[i + 1:]:
                actual = abs(f1["c"] - f2["c"])
                if abs(actual - thickness) > thickness_tol:
                    continue
                start, end = overlap_range(f1["a"], f1["b"], f2["a"], f2["b"])
                if end - start < min_overlap:
                    continue
                axes.append(centerline_segment("H", (f1["c"] + f2["c"]) / 2.0, start, end, thickness, floor_label))

        for i, f1 in enumerate(vertical):
            for f2 in vertical[i + 1:]:
                actual = abs(f1["c"] - f2["c"])
                if abs(actual - thickness) > thickness_tol:
                    continue
                start, end = overlap_range(f1["a"], f1["b"], f2["a"], f2["b"])
                if end - start < min_overlap:
                    continue
                axes.append(centerline_segment("V", (f1["c"] + f2["c"]) / 2.0, start, end, thickness, floor_label))

    if not axes:
        default_thickness = float(wall_thicknesses[0] if wall_thicknesses else 225.0)
        for face in faces:
            axes.append(centerline_segment(
                face["orientation"],
                face["c"],
                face["a"],
                face["b"],
                default_thickness,
                floor_label,
                source="face_line_fallback",
            ))

    return axes


# =========================================================
# MODEL GENERATION
# =========================================================

def wall_object_from_axis(axis, floor, idx):
    return {
        "id": f"W_{floor['label']}_{idx:04d}",
        "floor": floor["label"],
        "x1": axis["x1"],
        "y1": axis["y1"],
        "x2": axis["x2"],
        "y2": axis["y2"],
        "z": floor["z"],
        "height": floor["wall_height"],
        "thickness": axis["thickness"],
        "source": axis.get("source", ""),
        "status": "detected",
    }


def slab_from_floor_axes(floor, axes, slab_thickness, overhang):
    bounds = bounds_from_segments(axes)
    return {
        "id": f"S_{floor['label']}",
        "floor": floor["label"],
        "x1": bounds["min_x"] - overhang,
        "y1": bounds["min_y"] - overhang,
        "x2": bounds["max_x"] + overhang,
        "y2": bounds["max_y"] + overhang,
        "z": floor["z"],
        "thickness": slab_thickness,
        "status": "detected_floor_plate",
    }


def wall_intersection_candidates(axes, axis_tol):
    h_axes = [a for a in axes if a["orientation"] == "H"]
    v_axes = [a for a in axes if a["orientation"] == "V"]
    points = []

    for h in h_axes:
        for v in v_axes:
            x = v["c"]
            y = h["c"]
            on_h = h["a"] - axis_tol <= x <= h["b"] + axis_tol
            on_v = v["a"] - axis_tol <= y <= v["b"] + axis_tol
            if on_h and on_v:
                points.append((float(x), float(y)))

    clustered = []
    for x, y in points:
        found = None
        for group in clustered:
            if distance((x, y), (group["x"], group["y"])) <= axis_tol * 2.0:
                found = group
                break
        if found:
            found["points"].append((x, y))
            found["x"] = sum(p[0] for p in found["points"]) / len(found["points"])
            found["y"] = sum(p[1] for p in found["points"]) / len(found["points"])
        else:
            clustered.append({"x": x, "y": y, "points": [(x, y)]})

    return [(g["x"], g["y"]) for g in clustered]


def fallback_corner_columns(slab):
    if not slab_is_valid(slab):
        return []

    return [
        (slab["x1"], slab["y1"]),
        (slab["x2"], slab["y1"]),
        (slab["x1"], slab["y2"]),
        (slab["x2"], slab["y2"]),
    ]


def slab_is_valid(slab, min_size=100.0):
    if not slab:
        return False
    width = abs(float(slab.get("x2", 0.0)) - float(slab.get("x1", 0.0)))
    height = abs(float(slab.get("y2", 0.0)) - float(slab.get("y1", 0.0)))
    return width >= min_size and height >= min_size


def dedupe_points(points, tol):
    out = []
    for x, y in points:
        exists = False
        for px, py in out:
            if distance((x, y), (px, py)) <= tol:
                exists = True
                break
        if not exists:
            out.append((float(x), float(y)))
    return out


def structural_column_seed_points(axes, slab, axis_tol):
    """
    Column seeds are taken from major wall-axis intersections and slab corners.
    This is deliberately structural-model friendly: it prefers stable vertical
    grid lines over tiny partition intersections.
    """

    if not axes:
        return fallback_corner_columns(slab)

    bounds = bounds_from_segments(axes)
    width = max(float(bounds.get("width", 0.0)), 1.0)
    height = max(float(bounds.get("height", 0.0)), 1.0)
    min_v_axis_len = max(1500.0, height * 0.22)
    min_h_axis_len = max(1500.0, width * 0.22)

    h_axes = [axis for axis in axes if axis["orientation"] == "H"]
    v_axes = [axis for axis in axes if axis["orientation"] == "V"]

    major_h = [
        axis for axis in h_axes
        if segment_length(axis) >= min_h_axis_len
        or abs(axis["c"] - bounds["min_y"]) <= axis_tol * 2.0
        or abs(axis["c"] - bounds["max_y"]) <= axis_tol * 2.0
    ]
    major_v = [
        axis for axis in v_axes
        if segment_length(axis) >= min_v_axis_len
        or abs(axis["c"] - bounds["min_x"]) <= axis_tol * 2.0
        or abs(axis["c"] - bounds["max_x"]) <= axis_tol * 2.0
    ]

    if not major_h:
        major_h = h_axes
    if not major_v:
        major_v = v_axes

    points = []
    for h in major_h:
        for v in major_v:
            x = float(v["c"])
            y = float(h["c"])
            on_h = h["a"] - axis_tol <= x <= h["b"] + axis_tol
            on_v = v["a"] - axis_tol <= y <= v["b"] + axis_tol
            if on_h and on_v:
                points.append((x, y))

    # Always include floor-plate corners as editable starter columns. They are
    # easy to delete later and give the 3D model a stable structural frame.
    points.extend(fallback_corner_columns(slab))

    return dedupe_points(points, tol=max(axis_tol * 2.0, 1.0))


def global_axis_values(all_axes_by_floor, slabs, axis_tol):
    x_values = []
    y_values = []

    for axes in all_axes_by_floor.values():
        for axis in axes:
            if axis["orientation"] == "V":
                x_values.append(float(axis["c"]))
            elif axis["orientation"] == "H":
                y_values.append(float(axis["c"]))

    for slab in slabs:
        if slab_is_valid(slab):
            x_values.extend([float(slab["x1"]), float(slab["x2"])])
            y_values.extend([float(slab["y1"]), float(slab["y2"])])

    return cluster_values(x_values, axis_tol * 3.0), cluster_values(y_values, axis_tol * 3.0)


def snap_floor_points_to_global_grid(floor_points, all_axes_by_floor, slabs, axis_tol):
    global_x, global_y = global_axis_values(all_axes_by_floor, slabs, axis_tol)
    snap_tol = max(axis_tol * 5.0, 250.0)
    out = {}

    for label, points in floor_points.items():
        snapped = []
        for x, y in points:
            sx = nearest_value(float(x), global_x, tol=snap_tol)
            sy = nearest_value(float(y), global_y, tol=snap_tol)
            snapped.append((float(sx if sx is not None else x), float(sy if sy is not None else y)))
        out[label] = dedupe_points(snapped, tol=max(axis_tol * 2.0, 1.0))

    return out


def build_column_groups(floor_points, floors, width, depth, grouping_tol, force_full_stack=False):
    groups = []

    for floor in floors:
        label = floor["label"]
        for x, y in floor_points.get(label, []):
            match = None
            for group in groups:
                if distance((x, y), (group["x"], group["y"])) <= grouping_tol:
                    match = group
                    break
            if match is None:
                match = {
                    "x": float(x),
                    "y": float(y),
                    "members": [],
                }
                groups.append(match)
            match["members"].append({"floor": label, "x": float(x), "y": float(y)})
            match["x"] = sum(m["x"] for m in match["members"]) / len(match["members"])
            match["y"] = sum(m["y"] for m in match["members"]) / len(match["members"])

    floor_index = {floor["label"]: i for i, floor in enumerate(floors)}
    columns = []

    for idx, group in enumerate(sorted(groups, key=lambda g: (g["x"], g["y"])), start=1):
        present = sorted(set(m["floor"] for m in group["members"]), key=lambda label: floor_index[label])
        present_indices = [floor_index[label] for label in present]
        first_i = min(present_indices)
        last_i = max(present_indices)
        missing_between = [
            floors[i]["label"]
            for i in range(first_i, last_i + 1)
            if floors[i]["label"] not in present
        ]

        source = "wall_axis_intersections"

        if force_full_stack and floors:
            present = [floor["label"] for floor in floors]
            present_indices = list(range(len(floors)))
            first_i = 0
            last_i = len(floors) - 1
            missing_between = []
            status = "continuous"
            source = "projected_full_stack_model_line"
        elif len(present) == len(floors) and first_i == 0:
            status = "continuous"
        elif missing_between:
            status = "misaligned_review"
        elif first_i > 0:
            status = "starts_above"
        elif last_i < len(floors) - 1:
            status = "terminates_early"
        else:
            status = "partial"

        base_z = floors[first_i]["z"]
        top_z = floors[last_i]["z"] + floors[last_i]["floor_height"]

        columns.append({
            "id": f"C_{idx:03d}",
            "x": round(group["x"], 3),
            "y": round(group["y"], 3),
            "width": float(width),
            "depth": float(depth),
            "base_floor": floors[first_i]["label"],
            "top_floor": floors[last_i]["label"],
            "base_z": float(base_z),
            "top_z": float(top_z),
            "floors_present": ", ".join(present),
            "missing_between": ", ".join(missing_between),
            "status": status,
            "source": source,
        })

    return columns


def columns_active_on_floor(columns, floor):
    z = floor["z"]
    active = []
    for col in columns:
        if float(col["base_z"]) - 1e-6 <= z <= float(col["top_z"]) + 1e-6:
            active.append(col)
    return active


def build_primary_beams(columns, floors, row_tol, max_beam_span, beam_width, beam_depth):
    beams = []
    idx = 1

    for floor in floors:
        active = columns_active_on_floor(columns, floor)
        if len(active) < 2:
            continue

        y_rows = cluster_values([c["y"] for c in active], row_tol)
        x_cols = cluster_values([c["x"] for c in active], row_tol)

        for y in y_rows:
            row_cols = sorted([c for c in active if abs(c["y"] - y) <= row_tol], key=lambda c: c["x"])
            for c1, c2 in zip(row_cols, row_cols[1:]):
                span = abs(c2["x"] - c1["x"])
                if max_beam_span > 0 and span > max_beam_span:
                    continue
                beams.append({
                    "id": f"PB_{floor['label']}_{idx:04d}",
                    "floor": floor["label"],
                    "role": "primary",
                    "x1": float(c1["x"]),
                    "y1": float(y),
                    "x2": float(c2["x"]),
                    "y2": float(y),
                    "z": float(floor["z"] + floor["floor_height"]),
                    "width": float(beam_width),
                    "depth": float(beam_depth),
                    "status": "detected_column_line",
                })
                idx += 1

        for x in x_cols:
            col_cols = sorted([c for c in active if abs(c["x"] - x) <= row_tol], key=lambda c: c["y"])
            for c1, c2 in zip(col_cols, col_cols[1:]):
                span = abs(c2["y"] - c1["y"])
                if max_beam_span > 0 and span > max_beam_span:
                    continue
                beams.append({
                    "id": f"PB_{floor['label']}_{idx:04d}",
                    "floor": floor["label"],
                    "role": "primary",
                    "x1": float(x),
                    "y1": float(c1["y"]),
                    "x2": float(x),
                    "y2": float(c2["y"]),
                    "z": float(floor["z"] + floor["floor_height"]),
                    "width": float(beam_width),
                    "depth": float(beam_depth),
                    "status": "detected_column_line",
                })
                idx += 1

    return beams


def build_secondary_beams(slabs, floors, spacing, beam_width, beam_depth):
    if spacing <= 0:
        return []

    floor_by_label = {floor["label"]: floor for floor in floors}
    beams = []
    idx = 1

    for slab in slabs:
        floor = floor_by_label.get(slab["floor"])
        if not floor:
            continue

        width = abs(float(slab["x2"]) - float(slab["x1"]))
        height = abs(float(slab["y2"]) - float(slab["y1"]))

        if width <= 0 or height <= 0:
            continue

        if width >= height:
            count = max(0, int(math.floor(height / spacing)) - 1)
            for i in range(1, count + 1):
                y = float(slab["y1"]) + i * height / (count + 1)
                beams.append({
                    "id": f"SB_{floor['label']}_{idx:04d}",
                    "floor": floor["label"],
                    "role": "secondary",
                    "x1": float(slab["x1"]),
                    "y1": y,
                    "x2": float(slab["x2"]),
                    "y2": y,
                    "z": float(floor["z"] + floor["floor_height"] - beam_depth),
                    "width": float(beam_width),
                    "depth": float(beam_depth),
                    "status": "spacing_rule",
                })
                idx += 1
        else:
            count = max(0, int(math.floor(width / spacing)) - 1)
            for i in range(1, count + 1):
                x = float(slab["x1"]) + i * width / (count + 1)
                beams.append({
                    "id": f"SB_{floor['label']}_{idx:04d}",
                    "floor": floor["label"],
                    "role": "secondary",
                    "x1": x,
                    "y1": float(slab["y1"]),
                    "x2": x,
                    "y2": float(slab["y2"]),
                    "z": float(floor["z"] + floor["floor_height"] - beam_depth),
                    "width": float(beam_width),
                    "depth": float(beam_depth),
                    "status": "spacing_rule",
                })
                idx += 1

    return beams


def build_cantilevers(slabs, floors, tolerance):
    slab_by_floor = {slab["floor"]: slab for slab in slabs}
    warnings = []

    for i in range(1, len(floors)):
        floor = floors[i]
        below = floors[i - 1]
        slab = slab_by_floor.get(floor["label"])
        below_slab = slab_by_floor.get(below["label"])
        if not slab or not below_slab:
            continue

        z = float(floor["z"])
        if float(slab["x1"]) < float(below_slab["x1"]) - tolerance:
            warnings.append({
                "id": f"CAN_{floor['label']}_LEFT",
                "floor": floor["label"],
                "edge": "left",
                "x1": float(slab["x1"]),
                "y1": float(slab["y1"]),
                "x2": float(below_slab["x1"]),
                "y2": float(slab["y2"]),
                "z": z,
                "projection": round(float(below_slab["x1"]) - float(slab["x1"]), 3),
                "status": "cantilever_review",
            })

        if float(slab["x2"]) > float(below_slab["x2"]) + tolerance:
            warnings.append({
                "id": f"CAN_{floor['label']}_RIGHT",
                "floor": floor["label"],
                "edge": "right",
                "x1": float(below_slab["x2"]),
                "y1": float(slab["y1"]),
                "x2": float(slab["x2"]),
                "y2": float(slab["y2"]),
                "z": z,
                "projection": round(float(slab["x2"]) - float(below_slab["x2"]), 3),
                "status": "cantilever_review",
            })

        if float(slab["y1"]) < float(below_slab["y1"]) - tolerance:
            warnings.append({
                "id": f"CAN_{floor['label']}_BOTTOM",
                "floor": floor["label"],
                "edge": "bottom",
                "x1": float(slab["x1"]),
                "y1": float(slab["y1"]),
                "x2": float(slab["x2"]),
                "y2": float(below_slab["y1"]),
                "z": z,
                "projection": round(float(below_slab["y1"]) - float(slab["y1"]), 3),
                "status": "cantilever_review",
            })

        if float(slab["y2"]) > float(below_slab["y2"]) + tolerance:
            warnings.append({
                "id": f"CAN_{floor['label']}_TOP",
                "floor": floor["label"],
                "edge": "top",
                "x1": float(slab["x1"]),
                "y1": float(below_slab["y2"]),
                "x2": float(slab["x2"]),
                "y2": float(slab["y2"]),
                "z": z,
                "projection": round(float(slab["y2"]) - float(below_slab["y2"]), 3),
                "status": "cantilever_review",
            })

    return warnings


def dataframe_records(df):
    if df is None:
        return []
    cleaned = df.copy()
    cleaned = cleaned.where(pd.notna(cleaned), "")
    return cleaned.to_dict(orient="records")


def records_dataframe(records, columns):
    df = pd.DataFrame(records or [])
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    extra_columns = [column for column in df.columns if column not in columns]
    return df[columns + extra_columns]


def normalize_columns(records):
    out = []
    for idx, item in enumerate(records, start=1):
        try:
            base_z = float(item.get("base_z", 0.0))
            top_z = float(item.get("top_z", base_z + 3000.0))
            if top_z <= base_z:
                top_z = base_z + 3000.0
            out.append({
                "id": str(item.get("id") or f"C_{idx:03d}"),
                "x": float(item.get("x", 0.0)),
                "y": float(item.get("y", 0.0)),
                "width": max(50.0, float(item.get("width", 300.0))),
                "depth": max(50.0, float(item.get("depth", 300.0))),
                "base_floor": str(item.get("base_floor", "")),
                "top_floor": str(item.get("top_floor", "")),
                "base_z": base_z,
                "top_z": top_z,
                "floors_present": str(item.get("floors_present", "")),
                "missing_between": str(item.get("missing_between", "")),
                "status": str(item.get("status", "unconfirmed")),
                "source": str(item.get("source", "user_edit")),
            })
        except Exception:
            continue
    return out


def normalize_beams(records):
    out = []
    for idx, item in enumerate(records, start=1):
        try:
            role = str(item.get("role", "primary")).lower()
            if role not in {"primary", "secondary"}:
                role = "primary"
            out.append({
                "id": str(item.get("id") or f"B_{idx:03d}"),
                "floor": str(item.get("floor", "")),
                "role": role,
                "x1": float(item.get("x1", 0.0)),
                "y1": float(item.get("y1", 0.0)),
                "x2": float(item.get("x2", 0.0)),
                "y2": float(item.get("y2", 0.0)),
                "z": float(item.get("z", 0.0)),
                "width": max(25.0, float(item.get("width", 225.0))),
                "depth": max(25.0, float(item.get("depth", 450.0))),
                "status": str(item.get("status", "user_edit")),
            })
        except Exception:
            continue
    return out


def normalize_slabs(records):
    out = []
    for idx, item in enumerate(records, start=1):
        try:
            x1 = float(item.get("x1", 0.0))
            x2 = float(item.get("x2", 0.0))
            y1 = float(item.get("y1", 0.0))
            y2 = float(item.get("y2", 0.0))
            out.append({
                "id": str(item.get("id") or f"S_{idx:03d}"),
                "floor": str(item.get("floor", "")),
                "x1": min(x1, x2),
                "y1": min(y1, y2),
                "x2": max(x1, x2),
                "y2": max(y1, y2),
                "z": float(item.get("z", 0.0)),
                "thickness": max(25.0, float(item.get("thickness", 150.0))),
                "status": str(item.get("status", "user_edit")),
            })
        except Exception:
            continue
    return out


def build_model_from_uploads(uploaded_floors, settings):
    floors = []
    all_walls = []
    all_axes_by_floor = {}
    floor_points = {}
    slabs = []
    summaries = []

    for index, item in enumerate(uploaded_floors):
        label = item["label"]
        uploaded_file = item["file"]
        doc = read_uploaded_dxf(uploaded_file)
        faces, ignored = extract_wall_faces(
            doc,
            selected_layers=settings["selected_layers"],
            use_all_layers=settings["use_all_layers"],
            ortho_tol=settings["ortho_tol"],
            min_line_length=settings["min_line_length"],
        )
        extraction_mode = "all_layers" if settings["use_all_layers"] else "selected_wall_layers"

        if not faces and not settings["use_all_layers"]:
            fallback_faces, fallback_ignored = extract_wall_faces(
                doc,
                selected_layers=[],
                use_all_layers=True,
                ortho_tol=settings["ortho_tol"],
                min_line_length=settings["min_line_length"],
            )
            if fallback_faces:
                faces = fallback_faces
                ignored = fallback_ignored
                extraction_mode = "all_layers_fallback"

        axes = detect_wall_axes_from_faces(
            faces,
            floor_label=label,
            wall_thicknesses=settings["wall_thicknesses"],
            thickness_tol=settings["thickness_tol"],
            min_overlap=settings["min_overlap"],
        )
        axes = merge_collinear_segments(axes, axis_tol=settings["axis_tol"], gap_tol=settings["bridge_gap"])

        floor = {
            "label": label,
            "z": float(index) * float(settings["floor_height"]),
            "floor_height": float(settings["floor_height"]),
            "wall_height": float(settings["wall_height"]),
        }
        floors.append(floor)
        all_axes_by_floor[label] = axes

        for wall_idx, axis in enumerate(axes, start=1):
            all_walls.append(wall_object_from_axis(axis, floor, wall_idx))

        slab = slab_from_floor_axes(
            floor,
            axes,
            slab_thickness=settings["slab_thickness"],
            overhang=settings["slab_overhang"],
        )
        slabs.append(slab)

        points = structural_column_seed_points(axes, slab, settings["axis_tol"])
        floor_points[label] = points

        summaries.append({
            "floor": label,
            "extraction_mode": extraction_mode,
            "wall_faces": len(faces),
            "wall_axes": len(axes),
            "slab_width": round(abs(float(slab["x2"]) - float(slab["x1"])), 3),
            "slab_height": round(abs(float(slab["y2"]) - float(slab["y1"])), 3),
            "column_seed_points": len(points),
            "ignored_entities": ignored,
        })

    floor_points = snap_floor_points_to_global_grid(
        floor_points,
        all_axes_by_floor,
        slabs,
        axis_tol=settings["axis_tol"],
    )

    columns = build_column_groups(
        floor_points,
        floors,
        width=settings["column_width"],
        depth=settings["column_depth"],
        grouping_tol=settings["column_grouping_tol"],
        force_full_stack=settings["force_full_stack_columns"],
    )
    primary_beams = build_primary_beams(
        columns,
        floors,
        row_tol=settings["beam_axis_tol"],
        max_beam_span=settings["max_primary_beam_span"],
        beam_width=settings["primary_beam_width"],
        beam_depth=settings["primary_beam_depth"],
    )
    secondary_beams = build_secondary_beams(
        slabs,
        floors,
        spacing=settings["secondary_beam_spacing"],
        beam_width=settings["secondary_beam_width"],
        beam_depth=settings["secondary_beam_depth"],
    )
    beams = primary_beams + secondary_beams
    cantilevers = build_cantilevers(slabs, floors, tolerance=settings["cantilever_tolerance"])

    return {
        "version": "3d_model_builder_mvp_3_floor_slots_orbit_colors",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "units": "mm",
        "floors": floors,
        "walls": all_walls,
        "slabs": slabs,
        "columns": columns,
        "beams": beams,
        "cantilevers": cantilevers,
        "summaries": summaries,
    }


def rebuild_warnings(model, cantilever_tolerance):
    model = dict(model)
    model["cantilevers"] = build_cantilevers(
        model.get("slabs", []),
        model.get("floors", []),
        tolerance=cantilever_tolerance,
    )
    return model


# =========================================================
# 3D VIEWER
# =========================================================

def model_bounds(model):
    xs = []
    ys = []
    zs = []

    for wall in model.get("walls", []):
        xs.extend([wall["x1"], wall["x2"]])
        ys.extend([wall["y1"], wall["y2"]])
        zs.extend([wall["z"], wall["z"] + wall["height"]])

    for slab in model.get("slabs", []):
        xs.extend([slab["x1"], slab["x2"]])
        ys.extend([slab["y1"], slab["y2"]])
        zs.extend([slab["z"], slab["z"] + slab["thickness"]])

    for column in model.get("columns", []):
        xs.append(column["x"])
        ys.append(column["y"])
        zs.extend([column["base_z"], column["top_z"]])

    if not xs:
        return {"cx": 0, "cy": 0, "cz": 0, "radius": 10}

    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0 if zs else 0.0
    radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs) if zs else 1.0, 1000.0)
    return {"cx": cx, "cy": cy, "cz": cz, "radius": radius}


def render_three_viewer(model, height=740):
    model_json = json.dumps(model)
    bounds = model_bounds(model)
    bounds_json = json.dumps(bounds)
    column_colors_json = json.dumps(COLUMN_COLORS)
    beam_colors_json = json.dumps(BEAM_COLORS)

    viewer_html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: #0f172a;
    }}
    #viewer {{
      width: 100%;
      height: {int(height)}px;
      position: relative;
      cursor: grab;
    }}
    #viewer:active {{
      cursor: grabbing;
    }}
    #hud {{
      position: absolute;
      top: 12px;
      left: 12px;
      z-index: 10;
      color: #e5e7eb;
      background: rgba(15,23,42,0.78);
      border: 1px solid rgba(148,163,184,0.35);
      border-radius: 8px;
      padding: 10px 12px;
      max-width: 420px;
      line-height: 1.45;
      font-size: 13px;
    }}
    #legend {{
      position: absolute;
      right: 12px;
      top: 12px;
      z-index: 10;
      color: #e5e7eb;
      background: rgba(15,23,42,0.78);
      border: 1px solid rgba(148,163,184,0.35);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 12px;
      line-height: 1.55;
    }}
    .swatch {{
      display: inline-block;
      width: 11px;
      height: 11px;
      border-radius: 2px;
      margin-right: 6px;
      vertical-align: -1px;
    }}
    #selected {{
      color: #bae6fd;
      margin-top: 6px;
    }}
    #viewButtons {{
      position: absolute;
      left: 12px;
      bottom: 12px;
      z-index: 10;
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    #viewButtons button {{
      border: 1px solid rgba(148,163,184,0.45);
      background: rgba(15,23,42,0.82);
      color: #e5e7eb;
      border-radius: 6px;
      padding: 7px 9px;
      font-size: 12px;
      cursor: pointer;
    }}
    #viewButtons button:hover {{
      background: rgba(30,41,59,0.95);
    }}
  </style>
  <script type="importmap">
    {{
      "imports": {{
        "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
        "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
      }}
    }}
  </script>
</head>
<body>
  <div id="viewer">
    <div id="hud">
      <strong>3D Structural Model</strong><br/>
      Orbit: left mouse | Pan: right mouse | Zoom: wheel<br/>
      Double-click object to inspect/edit handle. Press W/E/R for move/rotate/scale handles.
      <div id="selected">Selected: none</div>
    </div>
    <div id="legend">
      <strong>Columns</strong><br/>
      <span class="swatch" style="background:#22c55e"></span>continuous<br/>
      <span class="swatch" style="background:#f97316"></span>partial/stops<br/>
      <span class="swatch" style="background:#ef4444"></span>review<br/>
      <span class="swatch" style="background:#3b82f6"></span>user added<br/>
      <br/>
      <strong>Beams</strong><br/>
      <span class="swatch" style="background:#1d4ed8"></span>primary<br/>
      <span class="swatch" style="background:#38bdf8"></span>secondary<br/>
      <span class="swatch" style="background:#ef4444"></span>cantilever review
    </div>
    <div id="viewButtons">
      <button id="isoView" type="button">Iso</button>
      <button id="topView" type="button">Top</button>
      <button id="frontView" type="button">Front</button>
      <button id="sideView" type="button">Side</button>
      <button id="clearPick" type="button">Clear</button>
    </div>
  </div>

  <script type="module">
    import * as THREE from 'three';
    import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
    import {{ TransformControls }} from 'three/addons/controls/TransformControls.js';

    const model = {model_json};
    const bounds = {bounds_json};
    const columnColors = {column_colors_json};
    const beamColors = {beam_colors_json};
    const root = document.getElementById('viewer');
    const selectedText = document.getElementById('selected');
    const scale = 0.001;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f172a);

    const camera = new THREE.PerspectiveCamera(45, root.clientWidth / root.clientHeight, 0.01, 10000);
    const r = Math.max(bounds.radius * scale, 8);
    camera.position.set(bounds.cx * scale + r * 0.75, bounds.cz * scale + r * 0.70, -bounds.cy * scale + r * 1.05);

    const renderer = new THREE.WebGLRenderer({{ antialias: true }});
    renderer.setSize(root.clientWidth, root.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    root.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(bounds.cx * scale, bounds.cz * scale, -bounds.cy * scale);
    controls.enableDamping = true;
    controls.mouseButtons = {{
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN
    }};
    controls.touches = {{
      ONE: THREE.TOUCH.ROTATE,
      TWO: THREE.TOUCH.DOLLY_PAN
    }};
    controls.update();

    const transform = new TransformControls(camera, renderer.domElement);
    transform.addEventListener('dragging-changed', event => controls.enabled = !event.value);
    scene.add(transform);

    const ambient = new THREE.AmbientLight(0xffffff, 0.72);
    scene.add(ambient);
    const sun = new THREE.DirectionalLight(0xffffff, 1.25);
    sun.position.set(15, 25, 20);
    sun.castShadow = true;
    scene.add(sun);

    const grid = new THREE.GridHelper(Math.max(r * 2, 20), 32, 0x64748b, 0x334155);
    grid.position.set(bounds.cx * scale, -0.02, -bounds.cy * scale);
    scene.add(grid);

    function mat(color, opacity = 1.0) {{
      return new THREE.MeshStandardMaterial({{
        color,
        transparent: opacity < 1,
        opacity,
        roughness: 0.72,
        metalness: 0.03
      }});
    }}

    const wallMat = mat(0xe2e8f0, 0.24);
    const slabPalette = [0x0284c7, 0x16a34a, 0xa855f7, 0xf59e0b, 0x14b8a6, 0xf43f5e];
    const cantileverMat = mat(0xef4444, 0.38);
    const edgeMat = new THREE.LineBasicMaterial({{ color: 0xf8fafc, transparent: true, opacity: 0.35 }});
    const pickables = [];

    function addEdges(mesh) {{
      const edges = new THREE.EdgesGeometry(mesh.geometry);
      const line = new THREE.LineSegments(edges, edgeMat);
      mesh.add(line);
    }}

    function addBox(id, type, x, y, z, sx, sy, sz, yaw, material, data) {{
      const geom = new THREE.BoxGeometry(Math.max(sx * scale, 0.001), Math.max(sy * scale, 0.001), Math.max(sz * scale, 0.001));
      const mesh = new THREE.Mesh(geom, material);
      mesh.position.set(x * scale, y * scale, -z * scale);
      mesh.rotation.y = yaw || 0;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData = {{ id, type, ...data }};
      scene.add(mesh);
      addEdges(mesh);
      pickables.push(mesh);
      return mesh;
    }}

    for (const wall of model.walls || []) {{
      const dx = wall.x2 - wall.x1;
      const dy = wall.y2 - wall.y1;
      const len = Math.hypot(dx, dy);
      if (len <= 1) continue;
      const mx = (wall.x1 + wall.x2) / 2;
      const my = (wall.y1 + wall.y2) / 2;
      const yaw = Math.atan2(dy, dx);
      addBox(wall.id, 'wall', mx, wall.z + wall.height / 2, my, len, wall.height, wall.thickness, yaw, wallMat, wall);
    }}

    const floorIndex = new Map((model.floors || []).map((floor, idx) => [floor.label, idx]));

    for (const slab of model.slabs || []) {{
      const w = Math.abs(slab.x2 - slab.x1);
      const d = Math.abs(slab.y2 - slab.y1);
      const mx = (slab.x1 + slab.x2) / 2;
      const my = (slab.y1 + slab.y2) / 2;
      const slabColor = slabPalette[(floorIndex.get(slab.floor) || 0) % slabPalette.length];
      addBox(slab.id, 'slab', mx, slab.z + slab.thickness / 2, my, w, slab.thickness, d, 0, mat(slabColor, 0.36), slab);
    }}

    for (const col of model.columns || []) {{
      const h = Math.max(col.top_z - col.base_z, 1);
      const color = columnColors[col.status] || '#9ca3af';
      addBox(col.id, 'column', col.x, col.base_z + h / 2, col.y, col.width, h, col.depth, 0, mat(color, 0.92), col);
    }}

    for (const beam of model.beams || []) {{
      const dx = beam.x2 - beam.x1;
      const dy = beam.y2 - beam.y1;
      const len = Math.hypot(dx, dy);
      if (len <= 1) continue;
      const mx = (beam.x1 + beam.x2) / 2;
      const my = (beam.y1 + beam.y2) / 2;
      const yaw = Math.atan2(dy, dx);
      const color = beamColors[beam.role] || beamColors.primary;
      addBox(beam.id, 'beam', mx, beam.z - beam.depth / 2, my, len, beam.depth, beam.width, yaw, mat(color, 1.0), beam);
    }}

    for (const cantilever of model.cantilevers || []) {{
      const w = Math.abs(cantilever.x2 - cantilever.x1);
      const d = Math.abs(cantilever.y2 - cantilever.y1);
      const mx = (cantilever.x1 + cantilever.x2) / 2;
      const my = (cantilever.y1 + cantilever.y2) / 2;
      addBox(cantilever.id, 'cantilever', mx, cantilever.z + 80, my, w, 80, d, 0, cantileverMat, cantilever);
    }}

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let selected = null;

    function setSelected(object) {{
      selected = object;
      if (!object) {{
        transform.detach();
        selectedText.textContent = 'Selected: none';
        return;
      }}
      transform.attach(object);
      selectedText.textContent = `Selected: ${{object.userData.type}} / ${{object.userData.id}}`;
    }}

    renderer.domElement.addEventListener('dblclick', event => {{
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(pickables, false);
      if (hits.length) setSelected(hits[0].object);
    }});

    function setCameraView(kind) {{
      const target = new THREE.Vector3(bounds.cx * scale, bounds.cz * scale, -bounds.cy * scale);
      const dist = Math.max(bounds.radius * scale, 8);
      if (kind === 'top') camera.position.set(target.x, target.y + dist * 1.35, target.z + 0.001);
      else if (kind === 'front') camera.position.set(target.x, target.y + dist * 0.25, target.z + dist * 1.35);
      else if (kind === 'side') camera.position.set(target.x + dist * 1.35, target.y + dist * 0.25, target.z);
      else camera.position.set(target.x + dist * 0.75, target.y + dist * 0.70, target.z + dist * 1.05);
      controls.target.copy(target);
      camera.lookAt(target);
      controls.update();
    }}

    document.getElementById('isoView').addEventListener('click', () => setCameraView('iso'));
    document.getElementById('topView').addEventListener('click', () => setCameraView('top'));
    document.getElementById('frontView').addEventListener('click', () => setCameraView('front'));
    document.getElementById('sideView').addEventListener('click', () => setCameraView('side'));
    document.getElementById('clearPick').addEventListener('click', () => setSelected(null));

    window.addEventListener('keydown', event => {{
      if (event.key.toLowerCase() === 'w') transform.setMode('translate');
      if (event.key.toLowerCase() === 'e') transform.setMode('rotate');
      if (event.key.toLowerCase() === 'r') transform.setMode('scale');
      if (event.key === 'Escape') setSelected(null);
    }});

    window.addEventListener('resize', () => {{
      camera.aspect = root.clientWidth / root.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(root.clientWidth, root.clientHeight);
    }});

    function animate() {{
      requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    }}
    animate();
  </script>
</body>
</html>
"""
    components.html(viewer_html, height=height, scrolling=False)


# =========================================================
# EXPORTS
# =========================================================

def safe_layer(doc, name, color=7):
    try:
        if name not in [layer.dxf.name for layer in doc.layers]:
            doc.layers.new(name=name, dxfattribs={"color": color})
    except Exception:
        pass


def add_rect(msp, x1, y1, x2, y2, layer):
    points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})


def build_review_dxf(model):
    doc = ezdxf.new()
    msp = doc.modelspace()

    layers = {
        "ILS_3D_WALL_GUIDES": 8,
        "ILS_3D_COLUMNS_CONTINUOUS": 3,
        "ILS_3D_COLUMNS_REVIEW": 30,
        "ILS_3D_PRIMARY_BEAMS": 5,
        "ILS_3D_SECONDARY_BEAMS": 4,
        "ILS_3D_SLABS": 1,
        "ILS_3D_CANTILEVER_REVIEW": 1,
    }
    for layer, color in layers.items():
        safe_layer(doc, layer, color=color)

    floor_offsets = {floor["label"]: i * 25000.0 for i, floor in enumerate(model.get("floors", []))}

    for slab in model.get("slabs", []):
        off = floor_offsets.get(slab["floor"], 0.0)
        add_rect(msp, slab["x1"] + off, slab["y1"], slab["x2"] + off, slab["y2"], "ILS_3D_SLABS")

    for wall in model.get("walls", []):
        off = floor_offsets.get(wall["floor"], 0.0)
        msp.add_line((wall["x1"] + off, wall["y1"]), (wall["x2"] + off, wall["y2"]), dxfattribs={"layer": "ILS_3D_WALL_GUIDES"})

    for col in model.get("columns", []):
        status = col.get("status", "")
        layer = "ILS_3D_COLUMNS_CONTINUOUS" if status == "continuous" else "ILS_3D_COLUMNS_REVIEW"
        half_w = float(col.get("width", 300.0)) / 2.0
        half_d = float(col.get("depth", 300.0)) / 2.0
        for floor in model.get("floors", []):
            if float(col["base_z"]) - 1e-6 <= float(floor["z"]) <= float(col["top_z"]) + 1e-6:
                off = floor_offsets.get(floor["label"], 0.0)
                add_rect(msp, col["x"] - half_w + off, col["y"] - half_d, col["x"] + half_w + off, col["y"] + half_d, layer)

    for beam in model.get("beams", []):
        off = floor_offsets.get(beam["floor"], 0.0)
        layer = "ILS_3D_PRIMARY_BEAMS" if beam.get("role") == "primary" else "ILS_3D_SECONDARY_BEAMS"
        msp.add_line((beam["x1"] + off, beam["y1"]), (beam["x2"] + off, beam["y2"]), dxfattribs={"layer": layer})

    for cantilever in model.get("cantilevers", []):
        off = floor_offsets.get(cantilever["floor"], 0.0)
        add_rect(
            msp,
            cantilever["x1"] + off,
            cantilever["y1"],
            cantilever["x2"] + off,
            cantilever["y2"],
            "ILS_3D_CANTILEVER_REVIEW",
        )

    return doc


def model_downloads(model):
    model_json = json.dumps(model, indent=2).encode("utf-8")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dxf_bytes = write_doc_to_bytes(build_review_dxf(model))

    column_df = pd.DataFrame(model.get("columns", []))
    beam_df = pd.DataFrame(model.get("beams", []))
    slab_df = pd.DataFrame(model.get("slabs", []))
    cantilever_df = pd.DataFrame(model.get("cantilevers", []))

    file_items = [
        (f"ILS_3D_MODEL_{timestamp}.json", model_json),
        (f"ILS_3D_REVIEW_DXF_{timestamp}.dxf", dxf_bytes),
        (f"ILS_COLUMNS_{timestamp}.csv", column_df.to_csv(index=False).encode("utf-8")),
        (f"ILS_BEAMS_{timestamp}.csv", beam_df.to_csv(index=False).encode("utf-8")),
        (f"ILS_SLABS_{timestamp}.csv", slab_df.to_csv(index=False).encode("utf-8")),
        (f"ILS_CANTILEVERS_{timestamp}.csv", cantilever_df.to_csv(index=False).encode("utf-8")),
    ]

    return timestamp, model_json, dxf_bytes, build_zip_bytes(file_items)


# =========================================================
# UI
# =========================================================

with st.sidebar:
    st.markdown("### Model Setup")

    floor_height = st.number_input("Floor-to-floor height", min_value=1000.0, value=3000.0, step=100.0)
    wall_height = st.number_input("Wall display height", min_value=500.0, value=3000.0, step=100.0)
    slab_thickness = st.number_input("Slab thickness", min_value=50.0, value=150.0, step=25.0)
    slab_overhang = st.number_input("Slab edge overhang from wall bounds", min_value=0.0, value=0.0, step=50.0)

    st.markdown("### Extraction")
    wall_thickness_text = st.text_input("Wall thicknesses to pair", value="225,150")
    wall_thicknesses = parse_wall_thicknesses(wall_thickness_text)
    if not wall_thicknesses:
        wall_thicknesses = [225]
    thickness_tol = st.number_input("Wall thickness tolerance", min_value=0.0, value=15.0, step=1.0)
    ortho_tol = st.number_input("Orthogonal tolerance", min_value=0.0, value=2.0, step=0.5)
    min_line_length = st.number_input("Minimum source line length", min_value=0.0, value=250.0, step=50.0)
    min_overlap = st.number_input("Minimum wall face overlap", min_value=0.0, value=250.0, step=50.0)
    axis_tol = st.number_input("Axis tolerance", min_value=1.0, value=35.0, step=5.0)
    bridge_gap = st.number_input("Bridge wall gaps for display axes", min_value=0.0, value=1200.0, step=100.0)

    st.markdown("### Columns")
    column_width = st.number_input("Column width", min_value=100.0, value=300.0, step=25.0)
    column_depth = st.number_input("Column depth", min_value=100.0, value=300.0, step=25.0)
    column_grouping_tol = st.number_input("Column continuity tolerance", min_value=10.0, value=750.0, step=25.0)
    force_full_stack_columns = st.checkbox(
        "Start with proposed columns continuous through full stack",
        value=True,
        help="Recommended for modelling. It makes detected/proposed column lines green first, then you can edit exceptions manually.",
    )

    st.markdown("### Beams")
    beam_axis_tol = st.number_input("Beam axis grouping tolerance", min_value=10.0, value=350.0, step=25.0)
    max_primary_beam_span = st.number_input("Max auto primary beam span", min_value=0.0, value=7000.0, step=500.0)
    primary_beam_width = st.number_input("Primary beam width", min_value=100.0, value=225.0, step=25.0)
    primary_beam_depth = st.number_input("Primary beam depth", min_value=100.0, value=450.0, step=25.0)
    secondary_beam_spacing = st.number_input("Secondary beam spacing", min_value=0.0, value=0.0, step=500.0)
    secondary_beam_width = st.number_input("Secondary beam width", min_value=100.0, value=150.0, step=25.0)
    secondary_beam_depth = st.number_input("Secondary beam depth", min_value=100.0, value=300.0, step=25.0)

    st.markdown("### Review")
    cantilever_tolerance = st.number_input("Cantilever projection tolerance", min_value=0.0, value=300.0, step=50.0)


st.markdown("### 1. Upload Floors In Real Order")
st.caption("Use fixed floor slots so the model stack is unambiguous: GF, FF, other upper floors, then roof.")

upload_1, upload_2 = st.columns(2)
gf_dxf = upload_1.file_uploader(
    "1. Ground floor (GF) DXF - required",
    type=["dxf"],
    key="gf_dxf",
)
ff_dxf = upload_2.file_uploader(
    "2. First floor (FF) DXF - optional",
    type=["dxf"],
    key="ff_dxf",
)

upload_3, upload_4 = st.columns(2)
other_floor_dxfs = upload_3.file_uploader(
    "3. Other upper floors DXF - optional",
    type=["dxf"],
    accept_multiple_files=True,
    key="other_floor_dxfs",
    help="Upload only floors that are different from FF. They will be stacked after FF.",
)
roof_dxf = upload_4.file_uploader(
    "4. Roof DXF - optional",
    type=["dxf"],
    key="roof_dxf",
)

uploaded_floors = []
if gf_dxf is not None:
    uploaded_floors.append({"label": "GF", "file": gf_dxf, "filename": gf_dxf.name})
if ff_dxf is not None:
    uploaded_floors.append({"label": "FF", "file": ff_dxf, "filename": ff_dxf.name})
if ff_dxf is not None:
    for idx, floor_file in enumerate(other_floor_dxfs or [], start=2):
        uploaded_floors.append({"label": f"F{idx}", "file": floor_file, "filename": floor_file.name})
elif other_floor_dxfs:
    st.warning("Other floors were uploaded without FF. Upload FF first so the stack remains logical.")
if roof_dxf is not None:
    uploaded_floors.append({"label": "ROOF", "file": roof_dxf, "filename": roof_dxf.name})

if not uploaded_floors:
    st.stop()

floor_order_df = pd.DataFrame([
    {"order": idx + 1, "label": item["label"], "filename": item["filename"]}
    for idx, item in enumerate(uploaded_floors)
])

st.dataframe(
    floor_order_df,
    use_container_width=True,
    hide_index=True,
)

first_doc = None
layer_summary = []
layers = []
try:
    first_doc = read_uploaded_dxf(uploaded_floors[0]["file"])
    layers = get_layer_names(first_doc)
    layer_summary = get_layer_entity_summary(first_doc)
except Exception as exc:
    st.error(f"Could not read the first DXF: {exc}")
    st.stop()

suggested_layers = suggested_wall_layers_from_summary(layer_summary)

with st.expander("Layer Isolation", expanded=True):
    show_layers = st.checkbox("Show first-floor layer diagnostics", value=False)
    if show_layers:
        st.dataframe(pd.DataFrame(layer_summary), use_container_width=True)

    use_layer_isolation = st.checkbox(
        "Use selected wall layers only",
        value=True if suggested_layers else False,
    )

    if use_layer_isolation:
        selected_layers = st.multiselect(
            "Wall layers",
            options=layers,
            default=suggested_layers,
        )
        use_all_layers = False
        if not selected_layers:
            st.warning("Select wall layers or disable layer isolation.")
            st.stop()
    else:
        selected_layers = []
        use_all_layers = True
        st.warning("Layer isolation is off. Door/window/furniture/dimension lines may become model geometry.")


build = st.button("Build 3D Structural Model", type="primary")

if build:
    settings = {
        "floor_height": floor_height,
        "wall_height": wall_height,
        "slab_thickness": slab_thickness,
        "slab_overhang": slab_overhang,
        "selected_layers": selected_layers,
        "use_all_layers": use_all_layers,
        "ortho_tol": ortho_tol,
        "min_line_length": min_line_length,
        "wall_thicknesses": wall_thicknesses,
        "thickness_tol": thickness_tol,
        "min_overlap": min_overlap,
        "axis_tol": axis_tol,
        "bridge_gap": bridge_gap,
        "column_width": column_width,
        "column_depth": column_depth,
        "column_grouping_tol": column_grouping_tol,
        "force_full_stack_columns": force_full_stack_columns,
        "beam_axis_tol": beam_axis_tol,
        "max_primary_beam_span": max_primary_beam_span,
        "primary_beam_width": primary_beam_width,
        "primary_beam_depth": primary_beam_depth,
        "secondary_beam_spacing": secondary_beam_spacing,
        "secondary_beam_width": secondary_beam_width,
        "secondary_beam_depth": secondary_beam_depth,
        "cantilever_tolerance": cantilever_tolerance,
    }

    with st.spinner("Building 3D structural model..."):
        st.session_state["ils_3d_model"] = build_model_from_uploads(uploaded_floors, settings)

if "ils_3d_model" not in st.session_state:
    st.stop()

model = st.session_state["ils_3d_model"]

st.markdown("### 2. Model Summary")

col_a, col_b, col_c, col_d, col_e = st.columns(5)
col_a.metric("Floors", len(model.get("floors", [])))
col_b.metric("Walls", len(model.get("walls", [])))
col_c.metric("Columns", len(model.get("columns", [])))
col_d.metric("Beams", len(model.get("beams", [])))
col_e.metric("Cantilever flags", len(model.get("cantilevers", [])))

if model.get("summaries"):
    with st.expander("Extraction Summary", expanded=False):
        st.dataframe(pd.DataFrame(model["summaries"]), use_container_width=True)


st.markdown("### 3. Editable Structural Objects")
st.caption("Edit the tables, then press Apply Edits to update the 3D model.")

tabs = st.tabs(["Columns", "Beams", "Slabs", "Cantilever Review", "3D Viewer", "Exports"])

with tabs[0]:
    st.caption("Column status controls color in 3D. Use `user_added` for manually inserted columns.")
    column_df = st.data_editor(
        records_dataframe(
            model.get("columns", []),
            [
                "id",
                "x",
                "y",
                "width",
                "depth",
                "base_floor",
                "top_floor",
                "base_z",
                "top_z",
                "floors_present",
                "missing_between",
                "status",
                "source",
            ],
        ),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="columns_editor",
    )

with tabs[1]:
    st.caption("Beam role controls color: `primary` is dark blue, `secondary` is light blue.")
    beam_df = st.data_editor(
        records_dataframe(
            model.get("beams", []),
            [
                "id",
                "floor",
                "role",
                "x1",
                "y1",
                "x2",
                "y2",
                "z",
                "width",
                "depth",
                "status",
            ],
        ),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="beams_editor",
    )

with tabs[2]:
    slab_df = st.data_editor(
        records_dataframe(
            model.get("slabs", []),
            [
                "id",
                "floor",
                "x1",
                "y1",
                "x2",
                "y2",
                "z",
                "thickness",
                "status",
            ],
        ),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="slabs_editor",
    )

with tabs[3]:
    if not model.get("cantilevers"):
        st.success("No cantilever projections above the tolerance were detected.")
    else:
        st.warning("Cantilever zones are review geometry. Confirm support conditions manually.")
        st.dataframe(pd.DataFrame(model.get("cantilevers", [])), use_container_width=True)

apply_edits = st.button("Apply Edits To 3D Model", type="secondary")

if apply_edits:
    edited_model = dict(model)
    edited_model["columns"] = normalize_columns(dataframe_records(column_df))
    edited_model["beams"] = normalize_beams(dataframe_records(beam_df))
    edited_model["slabs"] = normalize_slabs(dataframe_records(slab_df))
    edited_model = rebuild_warnings(edited_model, cantilever_tolerance)
    st.session_state["ils_3d_model"] = edited_model
    st.rerun()


with tabs[4]:
    render_three_viewer(model, height=760)

with tabs[5]:
    timestamp, model_json, dxf_bytes, zip_bytes = model_downloads(model)

    d1, d2, d3 = st.columns(3)
    d1.download_button(
        "Download Model JSON",
        data=model_json,
        file_name=f"ILS_3D_MODEL_{timestamp}.json",
        mime="application/json",
    )
    d2.download_button(
        "Download Review DXF",
        data=dxf_bytes,
        file_name=f"ILS_3D_REVIEW_DXF_{timestamp}.dxf",
        mime="application/dxf",
    )
    d3.download_button(
        "Download Full Package ZIP",
        data=zip_bytes,
        file_name=f"ILS_3D_MODEL_PACKAGE_{timestamp}.zip",
        mime="application/zip",
    )

st.success(
    "3D model ready. Next step is turning the viewer into a full two-way drag editor with save-back from Three.js into Streamlit."
)
