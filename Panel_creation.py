def run():
    """Main function that runs this tool"""
    import datetime
    import math
    import os
    import tempfile
    
    import ezdxf
    import pandas as pd
    import streamlit as st
    
    # ============================================================
    # TOOL CONTENT STARTS HERE
    # ============================================================
    
    st.title("iLoveStructural - Structural Layout Agent")
    st.subheader("Tool 4: Architectural Wall Centerline and Structural Layout Agent")
    st.caption(
        "Architectural DXF -> accurate wall centerlines -> structural grid -> economical columns -> slab panel review."
    )
    
    # YOUR REST OF THE TOOL CODE CONTINUES HERE
    # (all indented inside the function)


# =========================================================
# IMPORTANT SCOPE NOTE
# =========================================================

st.info(
    "This assistant produces code-aware preliminary structural layout geometry. "
    "It is not a substitute for a signed structural design, load take-off, foundation design, or local authority review."
)

st.warning(
    "Columns, beam/grid lines, and slab panels are review geometry. "
    "A qualified engineer must verify member sizes, loads, detailing, drift, punching shear, foundations, and code compliance."
)


# =========================================================
# DESIGN CODE / PRELIMINARY PROFILE DATA
# =========================================================

DESIGN_CODE_PROFILES = {
    "Eurocode 2 (EN 1992) - preliminary": {
        "target_column_spacing": 4000.0,
        "max_column_spacing": 6000.0,
        "min_column_spacing": 2200.0,
        "max_panel_span": 5500.0,
        "preferred_column_family": "metric_rc",
    },
    "ACI 318 - preliminary": {
        "target_column_spacing": 4200.0,
        "max_column_spacing": 6100.0,
        "min_column_spacing": 2400.0,
        "max_panel_span": 5600.0,
        "preferred_column_family": "metric_rc",
    },
    "BS 8110 - preliminary": {
        "target_column_spacing": 4000.0,
        "max_column_spacing": 6000.0,
        "min_column_spacing": 2200.0,
        "max_panel_span": 5500.0,
        "preferred_column_family": "metric_rc",
    },
    "IS 456 - preliminary": {
        "target_column_spacing": 3800.0,
        "max_column_spacing": 5750.0,
        "min_column_spacing": 2200.0,
        "max_panel_span": 5250.0,
        "preferred_column_family": "metric_rc",
    },
    "Custom / office standard": {
        "target_column_spacing": 4000.0,
        "max_column_spacing": 6000.0,
        "min_column_spacing": 2200.0,
        "max_panel_span": 5500.0,
        "preferred_column_family": "metric_rc",
    },
}


def preliminary_column_size_options(floor_count, building_use, column_shape):
    """
    Practical starting sizes only. Final sizes require real loading and design checks.
    """

    floors = max(1, int(floor_count))
    use_factor = {
        "Residential": 1.0,
        "Office / commercial": 1.10,
        "School / assembly": 1.15,
        "Retail": 1.15,
        "Storage / heavy loading": 1.35,
    }.get(building_use, 1.0)

    demand = floors * use_factor

    if column_shape == "Circular":
        if demand <= 1.2:
            return [(300.0, 300.0), (350.0, 350.0)]
        if demand <= 2.5:
            return [(350.0, 350.0), (400.0, 400.0)]
        if demand <= 4.5:
            return [(450.0, 450.0), (500.0, 500.0)]
        if demand <= 7.0:
            return [(550.0, 550.0), (600.0, 600.0)]
        return [(650.0, 650.0), (750.0, 750.0)]

    if column_shape == "Rectangular":
        if demand <= 1.2:
            return [(225.0, 300.0), (300.0, 300.0)]
        if demand <= 2.5:
            return [(300.0, 450.0), (300.0, 500.0)]
        if demand <= 4.5:
            return [(300.0, 600.0), (450.0, 450.0)]
        if demand <= 7.0:
            return [(450.0, 600.0), (500.0, 650.0)]
        return [(600.0, 750.0), (750.0, 750.0)]

    if demand <= 1.2:
        return [(225.0, 225.0), (225.0, 300.0)]
    if demand <= 2.5:
        return [(300.0, 300.0), (300.0, 450.0)]
    if demand <= 4.5:
        return [(300.0, 450.0), (450.0, 450.0)]
    if demand <= 7.0:
        return [(450.0, 450.0), (450.0, 600.0)]
    return [(600.0, 600.0), (600.0, 750.0)]


def column_size_label(shape, width, depth):
    if shape == "Circular":
        return f"DIA {int(round(width))}"
    if abs(width - depth) <= 1e-9:
        return f"{int(round(width))}x{int(round(depth))}"
    return f"{int(round(width))}x{int(round(depth))}"


# =========================================================
# FILE HELPERS
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


def get_layer_names(doc):
    try:
        return sorted([layer.dxf.name for layer in doc.layers])
    except Exception:
        return []


def safe_layer(doc, name, color=7):
    try:
        existing = [layer.dxf.name for layer in doc.layers]
        if name not in existing:
            doc.layers.new(name=name, dxfattribs={"color": color})
    except Exception:
        pass


def layer_color_for_thickness(thickness):
    try:
        t = int(thickness)
    except Exception:
        t = 0

    if t == 225:
        return 1
    if t == 150:
        return 3
    return 7


# =========================================================
# BASIC GEOMETRY
# =========================================================

def is_allowed_layer(layer_name, selected_layers, use_all_layers):
    if use_all_layers:
        return True
    return layer_name in selected_layers


def is_horizontal_segment(x1, y1, x2, y2, ortho_tol):
    return abs(y1 - y2) <= ortho_tol and abs(x2 - x1) > ortho_tol


def is_vertical_segment(x1, y1, x2, y2, ortho_tol):
    return abs(x1 - x2) <= ortho_tol and abs(y2 - y1) > ortho_tol


def make_face_line(orientation, c, a, b, layer, source_type, handle):
    return {
        "orientation": orientation,
        "c": float(c),
        "a": float(min(a, b)),
        "b": float(max(a, b)),
        "layer": layer,
        "source_type": source_type,
        "handle": handle,
    }


def make_centerline_segment(
    orientation,
    c,
    a,
    b,
    thickness,
    actual_thickness=None,
    thickness_error=None,
    overlap_length=None,
    source_count=1,
    region_id=None,
):
    if actual_thickness is None:
        actual_thickness = thickness

    if thickness_error is None:
        thickness_error = abs(float(actual_thickness) - float(thickness))

    if overlap_length is None:
        overlap_length = abs(float(b) - float(a))

    length = abs(float(b) - float(a))

    quality_score = (
        float(overlap_length)
        + length * 0.25
        - float(thickness_error) * 100.0
    )

    return {
        "orientation": orientation,
        "c": float(c),
        "a": float(min(a, b)),
        "b": float(max(a, b)),
        "thickness": int(thickness),
        "actual_thickness": float(actual_thickness),
        "thickness_error": float(thickness_error),
        "overlap_length": float(overlap_length),
        "source_count": int(source_count),
        "quality_score": float(quality_score),
        "region_id": region_id,
    }


def segment_length(seg):
    return abs(float(seg["b"]) - float(seg["a"]))


def filter_short_segments(segments, min_length):
    if min_length <= 0:
        return list(segments)
    return [s for s in segments if segment_length(s) >= min_length]


def filter_segments_by_thickness(segments, allowed_thicknesses):
    if not allowed_thicknesses:
        return []
    allowed = set(int(x) for x in allowed_thicknesses)
    return [s for s in segments if int(s.get("thickness", 0)) in allowed]


def overlap_range(a1, b1, a2, b2):
    start = max(min(a1, b1), min(a2, b2))
    end = min(max(a1, b1), max(a2, b2))
    return start, end


def interval_overlap_length(a1, b1, a2, b2):
    start, end = overlap_range(a1, b1, a2, b2)
    return max(0.0, end - start)


def interval_gap(a1, b1, a2, b2):
    a1, b1 = min(a1, b1), max(a1, b1)
    a2, b2 = min(a2, b2), max(a2, b2)

    if b1 < a2:
        return a2 - b1
    if b2 < a1:
        return a1 - b2
    return 0.0


def point_distance(p1, p2):
    return math.dist((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))


def copy_segment(seg):
    return {
        "orientation": seg["orientation"],
        "c": float(seg["c"]),
        "a": float(seg["a"]),
        "b": float(seg["b"]),
        "thickness": int(seg["thickness"]),
        "actual_thickness": float(seg.get("actual_thickness", seg["thickness"])),
        "thickness_error": float(seg.get("thickness_error", 0.0)),
        "overlap_length": float(seg.get("overlap_length", segment_length(seg))),
        "source_count": int(seg.get("source_count", 1)),
        "quality_score": float(seg.get("quality_score", 0.0)),
        "region_id": seg.get("region_id", None),
    }


def entity_handle(entity):
    try:
        return str(entity.dxf.handle)
    except Exception:
        return ""


def merge_intervals(intervals, gap_tol=0.0):
    clean = []
    for a, b in intervals:
        aa, bb = min(float(a), float(b)), max(float(a), float(b))
        if bb > aa:
            clean.append((aa, bb))

    if not clean:
        return []

    clean = sorted(clean, key=lambda x: x[0])
    merged = [clean[0]]

    for a, b in clean[1:]:
        cur_a, cur_b = merged[-1]
        if a <= cur_b + gap_tol:
            merged[-1] = (cur_a, max(cur_b, b))
        else:
            merged.append((a, b))

    return merged


def total_interval_length(intervals):
    return sum(max(0.0, b - a) for a, b in intervals)


def point_on_intervals(intervals, value, tol):
    for a, b in intervals:
        if min(a, b) - tol <= value <= max(a, b) + tol:
            return True
    return False


def closest_value(value, values):
    if not values:
        return None
    return min(values, key=lambda v: abs(float(v) - float(value)))


def value_in_list(value, values, tol):
    best = closest_value(value, values)
    return best is not None and abs(best - value) <= tol


def is_near_any(value, values, tol):
    return value_in_list(value, values, tol)


# =========================================================
# DXF WALL FACE EXTRACTION
# =========================================================

def extract_line_entities(doc, selected_layers, use_all_layers, ortho_tol, min_line_length):
    msp = doc.modelspace()

    horizontal = []
    vertical = []
    ignored = 0

    def add_segment(x1, y1, x2, y2, layer, source_type, handle):
        nonlocal ignored

        length = math.hypot(x2 - x1, y2 - y1)

        if length < min_line_length:
            ignored += 1
            return

        if is_horizontal_segment(x1, y1, x2, y2, ortho_tol):
            y = (y1 + y2) / 2.0
            horizontal.append(make_face_line("H", y, x1, x2, layer, source_type, handle))
        elif is_vertical_segment(x1, y1, x2, y2, ortho_tol):
            x = (x1 + x2) / 2.0
            vertical.append(make_face_line("V", x, y1, y2, layer, source_type, handle))
        else:
            ignored += 1

    for e in msp:
        try:
            dxftype = e.dxftype()
            layer = e.dxf.layer

            if not is_allowed_layer(layer, selected_layers, use_all_layers):
                continue

            if dxftype == "LINE":
                s = e.dxf.start
                en = e.dxf.end
                add_segment(
                    float(s.x),
                    float(s.y),
                    float(en.x),
                    float(en.y),
                    layer,
                    "LINE",
                    entity_handle(e),
                )

            elif dxftype == "LWPOLYLINE":
                pts = []
                try:
                    for p in e.get_points():
                        pts.append((float(p[0]), float(p[1])))
                except Exception:
                    pts = []

                for i in range(len(pts) - 1):
                    x1, y1 = pts[i]
                    x2, y2 = pts[i + 1]
                    add_segment(x1, y1, x2, y2, layer, "LWPOLYLINE", entity_handle(e))

                try:
                    if e.closed and len(pts) >= 2:
                        x1, y1 = pts[-1]
                        x2, y2 = pts[0]
                        add_segment(x1, y1, x2, y2, layer, "LWPOLYLINE_CLOSED", entity_handle(e))
                except Exception:
                    pass

            elif dxftype == "POLYLINE":
                pts = []
                try:
                    for v in e.vertices:
                        loc = v.dxf.location
                        pts.append((float(loc.x), float(loc.y)))
                except Exception:
                    pts = []

                for i in range(len(pts) - 1):
                    x1, y1 = pts[i]
                    x2, y2 = pts[i + 1]
                    add_segment(x1, y1, x2, y2, layer, "POLYLINE", entity_handle(e))

                try:
                    if e.is_closed and len(pts) >= 2:
                        x1, y1 = pts[-1]
                        x2, y2 = pts[0]
                        add_segment(x1, y1, x2, y2, layer, "POLYLINE_CLOSED", entity_handle(e))
                except Exception:
                    pass

        except Exception:
            ignored += 1
            continue

    return {
        "horizontal": horizontal,
        "vertical": vertical,
        "ignored": ignored,
    }


# =========================================================
# CENTERLINE DETECTION
# =========================================================

def parse_wall_thicknesses(text):
    values = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(float(part)))
        except Exception:
            pass

    clean = []
    for v in values:
        if v not in clean:
            clean.append(v)
    return clean


def detect_centerlines_from_face_pairs(
    horizontal_faces,
    vertical_faces,
    wall_thicknesses,
    thickness_tol,
    min_overlap,
):
    raw_segments = []

    for thickness in wall_thicknesses:
        for i, l1 in enumerate(horizontal_faces):
            for l2 in horizontal_faces[i + 1:]:
                actual_thickness = abs(l1["c"] - l2["c"])
                thickness_error = abs(actual_thickness - thickness)

                if thickness_error > thickness_tol:
                    continue

                start, end = overlap_range(l1["a"], l1["b"], l2["a"], l2["b"])
                face_overlap = end - start

                if face_overlap < min_overlap:
                    continue

                center_y = (l1["c"] + l2["c"]) / 2.0

                raw_segments.append(
                    make_centerline_segment(
                        "H",
                        center_y,
                        start,
                        end,
                        thickness,
                        actual_thickness=actual_thickness,
                        thickness_error=thickness_error,
                        overlap_length=face_overlap,
                    )
                )

    for thickness in wall_thicknesses:
        for i, l1 in enumerate(vertical_faces):
            for l2 in vertical_faces[i + 1:]:
                actual_thickness = abs(l1["c"] - l2["c"])
                thickness_error = abs(actual_thickness - thickness)

                if thickness_error > thickness_tol:
                    continue

                start, end = overlap_range(l1["a"], l1["b"], l2["a"], l2["b"])
                face_overlap = end - start

                if face_overlap < min_overlap:
                    continue

                center_x = (l1["c"] + l2["c"]) / 2.0

                raw_segments.append(
                    make_centerline_segment(
                        "V",
                        center_x,
                        start,
                        end,
                        thickness,
                        actual_thickness=actual_thickness,
                        thickness_error=thickness_error,
                        overlap_length=face_overlap,
                    )
                )

    return raw_segments


# =========================================================
# CENTERLINE HEALING
# =========================================================

def group_segments_by_axis(segments, axis_tol):
    groups = []

    sorted_segments = sorted(
        segments,
        key=lambda s: (
            s["orientation"],
            s["thickness"],
            s["c"],
            s["a"],
            s["b"],
        ),
    )

    for seg in sorted_segments:
        placed = False

        for group in groups:
            g0 = group[0]

            if seg["orientation"] != g0["orientation"]:
                continue
            if seg["thickness"] != g0["thickness"]:
                continue

            group_c = sum(x["c"] for x in group) / len(group)

            if abs(seg["c"] - group_c) <= axis_tol:
                group.append(seg)
                placed = True
                break

        if not placed:
            groups.append([seg])

    return groups


def merge_collinear_segments(segments, axis_tol, bridge_gap):
    if not segments:
        return []

    groups = group_segments_by_axis(segments, axis_tol)
    merged = []

    for group in groups:
        if not group:
            continue

        orientation = group[0]["orientation"]
        thickness = group[0]["thickness"]
        avg_c = sum(s["c"] for s in group) / len(group)

        intervals = sorted(
            [(s["a"], s["b"], s) for s in group],
            key=lambda x: x[0],
        )

        cur_a, cur_b, cur_seg = intervals[0]
        cur_members = [cur_seg]

        def flush_current():
            best_error = min(float(s.get("thickness_error", 0.0)) for s in cur_members)
            best_actual = sorted(
                cur_members,
                key=lambda s: float(s.get("thickness_error", 0.0)),
            )[0].get("actual_thickness", thickness)
            total_overlap = sum(float(s.get("overlap_length", segment_length(s))) for s in cur_members)
            source_count = sum(int(s.get("source_count", 1)) for s in cur_members)

            return make_centerline_segment(
                orientation,
                avg_c,
                cur_a,
                cur_b,
                thickness,
                actual_thickness=best_actual,
                thickness_error=best_error,
                overlap_length=total_overlap,
                source_count=source_count,
            )

        for a, b, seg in intervals[1:]:
            if a <= cur_b + bridge_gap:
                cur_b = max(cur_b, b)
                cur_members.append(seg)
            else:
                merged.append(flush_current())
                cur_a, cur_b = a, b
                cur_members = [seg]

        merged.append(flush_current())

    return merged


def split_hv_segments(segments):
    h = [s for s in segments if s["orientation"] == "H"]
    v = [s for s in segments if s["orientation"] == "V"]
    return h, v


def extend_to_nearby_intersections(segments, axis_tol, extend_tol, iterations=3):
    if extend_tol <= 0:
        return [copy_segment(s) for s in segments]

    working = [copy_segment(s) for s in segments]

    for _ in range(iterations):
        h_segments, v_segments = split_hv_segments(working)

        for h in h_segments:
            hy = h["c"]

            for v in v_segments:
                vx = v["c"]

                vx_near_h = (h["a"] - extend_tol) <= vx <= (h["b"] + extend_tol)
                hy_near_v = (v["a"] - extend_tol) <= hy <= (v["b"] + extend_tol)

                if not (vx_near_h and hy_near_v):
                    continue

                if vx < h["a"]:
                    h["a"] = vx
                if vx > h["b"]:
                    h["b"] = vx
                if hy < v["a"]:
                    v["a"] = hy
                if hy > v["b"]:
                    v["b"] = hy

        working = merge_collinear_segments(
            working,
            axis_tol=axis_tol,
            bridge_gap=axis_tol,
        )

    return working


# =========================================================
# OVERLAP CLEANUP
# =========================================================

def segment_priority(seg):
    thickness_error = float(seg.get("thickness_error", 0.0))
    face_overlap = float(seg.get("overlap_length", segment_length(seg)))
    length = segment_length(seg)
    thickness = int(seg.get("thickness", 0))
    prefer_225 = 1 if thickness == 225 else 0

    return (
        -thickness_error,
        face_overlap,
        length,
        prefer_225,
        float(seg.get("quality_score", 0.0)),
    )


def resolve_overlapping_centerlines(
    segments,
    axis_tol,
    min_overlap_length=100.0,
    min_overlap_ratio=0.60,
):
    if not segments:
        return [], []

    sorted_segments = sorted(
        [copy_segment(s) for s in segments],
        key=segment_priority,
        reverse=True,
    )

    kept = []
    removed = []

    for candidate in sorted_segments:
        cand_len = segment_length(candidate)

        if cand_len <= 0:
            removed.append({
                **candidate,
                "removed_reason": "zero_length",
                "overlap_with_thickness": "",
                "overlap_amount": 0.0,
                "overlap_ratio": 0.0,
            })
            continue

        duplicate_found = False
        duplicate_info = None

        for existing in kept:
            if candidate["orientation"] != existing["orientation"]:
                continue
            if abs(candidate["c"] - existing["c"]) > axis_tol:
                continue

            overlap = interval_overlap_length(
                candidate["a"],
                candidate["b"],
                existing["a"],
                existing["b"],
            )

            if overlap < min_overlap_length:
                continue

            overlap_ratio = overlap / max(cand_len, 1e-9)

            if overlap_ratio >= min_overlap_ratio:
                duplicate_found = True
                duplicate_info = {
                    "removed_reason": "overlapped_by_better_centerline",
                    "overlap_with_thickness": existing.get("thickness", ""),
                    "overlap_amount": round(overlap, 3),
                    "overlap_ratio": round(overlap_ratio, 3),
                }
                break

        if duplicate_found:
            removed.append({**candidate, **duplicate_info})
        else:
            kept.append(candidate)

    kept = sorted(
        kept,
        key=lambda s: (
            s["orientation"],
            s["thickness"],
            s["c"],
            s["a"],
            s["b"],
        ),
    )

    return kept, removed


# =========================================================
# MULTIPLE PLAN REGION SEPARATION
# =========================================================

class DSU:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def segments_connected_for_region(s1, s2, axis_tol, region_gap):
    if s1["orientation"] == s2["orientation"]:
        if abs(s1["c"] - s2["c"]) > axis_tol:
            return False

        gap = interval_gap(s1["a"], s1["b"], s2["a"], s2["b"])
        return gap <= region_gap

    if s1["orientation"] == "H":
        h = s1
        v = s2
    else:
        h = s2
        v = s1

    x = v["c"]
    y = h["c"]

    x_near_h = (h["a"] - region_gap) <= x <= (h["b"] + region_gap)
    y_near_v = (v["a"] - region_gap) <= y <= (v["b"] + region_gap)

    return x_near_h and y_near_v


def split_segments_into_regions(segments, axis_tol, region_gap):
    if not segments:
        return [], []

    n = len(segments)
    dsu = DSU(n)

    for i in range(n):
        for j in range(i + 1, n):
            if segments_connected_for_region(
                segments[i],
                segments[j],
                axis_tol=axis_tol,
                region_gap=region_gap,
            ):
                dsu.union(i, j)

    groups = {}

    for i, s in enumerate(segments):
        root = dsu.find(i)
        groups.setdefault(root, []).append(copy_segment(s))

    regions = []

    for idx, group in enumerate(groups.values(), start=1):
        xs = []
        ys = []

        for s in group:
            if s["orientation"] == "H":
                xs.extend([s["a"], s["b"]])
                ys.append(s["c"])
            else:
                xs.append(s["c"])
                ys.extend([s["a"], s["b"]])

        if not xs or not ys:
            continue

        for s in group:
            s["region_id"] = idx

        regions.append({
            "region_id": idx,
            "segments": group,
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
            "segment_count": len(group),
        })

    regions = sorted(regions, key=lambda r: (r["min_x"], r["min_y"]))

    all_region_segments = []
    for new_id, r in enumerate(regions, start=1):
        r["region_id"] = new_id
        for s in r["segments"]:
            s["region_id"] = new_id
        all_region_segments.extend(r["segments"])

    return regions, all_region_segments


# =========================================================
# GRAPH-BASED SLAB PANEL DETECTION
# =========================================================

def cluster_values(values, tol):
    if not values:
        return []

    values = sorted(values)
    clusters = []
    cur = [values[0]]

    for v in values[1:]:
        avg = sum(cur) / len(cur)

        if abs(v - avg) <= tol:
            cur.append(v)
        else:
            clusters.append(sum(cur) / len(cur))
            cur = [v]

    clusters.append(sum(cur) / len(cur))
    return clusters


def nearest_axis(value, axes, tol):
    if not axes:
        return value

    best = min(axes, key=lambda a: abs(a - value))

    if abs(best - value) <= tol:
        return best

    return value


def intervals_cover_range(intervals, start, end, closure_tol):
    if end < start:
        start, end = end, start

    if end - start <= 0:
        return False

    relevant = []

    for a, b in intervals:
        a, b = min(a, b), max(a, b)

        if b < start - closure_tol:
            continue
        if a > end + closure_tol:
            continue

        relevant.append((max(a, start), min(b, end)))

    if not relevant:
        return False

    relevant = sorted(relevant, key=lambda x: x[0])
    current_end = start

    for a, b in relevant:
        if a > current_end + closure_tol:
            return False
        current_end = max(current_end, b)

        if current_end >= end - closure_tol:
            return True

    return current_end >= end - closure_tol


def build_axis_interval_maps(segments, axis_tol):
    h_segments, v_segments = split_hv_segments(segments)

    xs = cluster_values([v["c"] for v in v_segments], axis_tol)
    ys = cluster_values([h["c"] for h in h_segments], axis_tol)

    h_map = {}
    v_map = {}

    for h in h_segments:
        y = nearest_axis(h["c"], ys, axis_tol)
        h_map.setdefault(y, []).append((h["a"], h["b"]))

    for v in v_segments:
        x = nearest_axis(v["c"], xs, axis_tol)
        v_map.setdefault(x, []).append((v["a"], v["b"]))

    return xs, ys, h_map, v_map


def horizontal_edge_exists(h_map, y, x1, x2, axis_tol, closure_tol):
    best_y = None
    best_d = None

    for yy in h_map.keys():
        d = abs(yy - y)
        if d <= axis_tol and (best_d is None or d < best_d):
            best_y = yy
            best_d = d

    if best_y is None:
        return False

    return intervals_cover_range(h_map.get(best_y, []), x1, x2, closure_tol)


def vertical_edge_exists(v_map, x, y1, y2, axis_tol, closure_tol):
    best_x = None
    best_d = None

    for xx in v_map.keys():
        d = abs(xx - x)
        if d <= axis_tol and (best_d is None or d < best_d):
            best_x = xx
            best_d = d

    if best_x is None:
        return False

    return intervals_cover_range(v_map.get(best_x, []), y1, y2, closure_tol)


def detect_graph_slab_panels(
    segments,
    axis_tol,
    closure_tol,
    min_panel_width,
    min_panel_height,
    max_panel_width,
    max_panel_height,
    region_id=None,
):
    xs, ys, h_map, v_map = build_axis_interval_maps(segments, axis_tol)
    panels = []

    if len(xs) < 2 or len(ys) < 2:
        return panels

    for i in range(len(xs) - 1):
        x1 = xs[i]
        x2 = xs[i + 1]
        width = abs(x2 - x1)

        if width < min_panel_width:
            continue
        if max_panel_width > 0 and width > max_panel_width:
            continue

        for j in range(len(ys) - 1):
            y1 = ys[j]
            y2 = ys[j + 1]
            height = abs(y2 - y1)

            if height < min_panel_height:
                continue
            if max_panel_height > 0 and height > max_panel_height:
                continue

            bottom = horizontal_edge_exists(h_map, y1, x1, x2, axis_tol, closure_tol)
            top = horizontal_edge_exists(h_map, y2, x1, x2, axis_tol, closure_tol)
            left = vertical_edge_exists(v_map, x1, y1, y2, axis_tol, closure_tol)
            right = vertical_edge_exists(v_map, x2, y1, y2, axis_tol, closure_tol)

            if bottom and top and left and right:
                panels.append({
                    "region_id": region_id,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "width": width,
                    "height": height,
                    "area": width * height,
                    "method": "wall_graph_cell",
                    "edge_support_count": 4,
                    "corner_support_count": "",
                    "review_status": "wall_bounded",
                })

    return panels


# =========================================================
# CENTERLINE TOPOLOGY / ROOM GRAPH REPAIR
# =========================================================

def region_bounds_from_segments(segments):
    xs = []
    ys = []

    for s in segments:
        if s["orientation"] == "H":
            xs.extend([s["a"], s["b"]])
            ys.append(s["c"])
        else:
            xs.append(s["c"])
            ys.extend([s["a"], s["b"]])

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


def update_region_from_segments(region, segments):
    bounds = region_bounds_from_segments(segments)
    region["segments"] = segments
    region["segment_count"] = len(segments)
    region.update(bounds)
    return region


def copy_regions(regions):
    copied = []

    for region in regions:
        rr = dict(region)
        rr["segments"] = [copy_segment(s) for s in region.get("segments", [])]
        copied.append(rr)

    return copied


def snap_segment_axes(segments, axis_tol):
    h_segments, v_segments = split_hv_segments(segments)
    xs = cluster_values([v["c"] for v in v_segments], axis_tol)
    ys = cluster_values([h["c"] for h in h_segments], axis_tol)

    snapped = []
    for s in segments:
        ss = copy_segment(s)
        if ss["orientation"] == "H":
            ss["c"] = nearest_axis(ss["c"], ys, axis_tol)
        else:
            ss["c"] = nearest_axis(ss["c"], xs, axis_tol)
        snapped.append(ss)

    return snapped


def extend_segments_to_crossing_axes(segments, extend_tol):
    if extend_tol <= 0:
        return [copy_segment(s) for s in segments]

    working = [copy_segment(s) for s in segments]
    h_segments, v_segments = split_hv_segments(working)

    for h in h_segments:
        hy = h["c"]
        for v in v_segments:
            vx = v["c"]

            x_close_to_h = h["a"] - extend_tol <= vx <= h["b"] + extend_tol
            y_close_to_v = v["a"] - extend_tol <= hy <= v["b"] + extend_tol

            if not (x_close_to_h and y_close_to_v):
                continue

            h["a"] = min(h["a"], vx)
            h["b"] = max(h["b"], vx)
            v["a"] = min(v["a"], hy)
            v["b"] = max(v["b"], hy)

    return working


def add_short_connection_segments(segments, axis_tol, connect_tol, default_thickness=225):
    """
    Adds tiny centerline connectors where one wall almost reaches a crossing wall axis.
    This turns a visual near-miss into an actual graph node for panel and room creation.
    """

    if connect_tol <= 0:
        return [copy_segment(s) for s in segments]

    working = [copy_segment(s) for s in segments]
    h_segments, v_segments = split_hv_segments(working)
    xs, ys, h_map, v_map = build_axis_interval_maps(working, axis_tol)
    additions = []

    for h in h_segments:
        hy = nearest_axis(h["c"], ys, axis_tol)
        for vx in xs:
            if point_on_intervals(v_map.get(vx, []), hy, connect_tol):
                if h["b"] < vx and vx - h["b"] <= connect_tol:
                    additions.append(
                        make_centerline_segment(
                            "H",
                            hy,
                            h["b"],
                            vx,
                            h.get("thickness", default_thickness),
                            region_id=h.get("region_id"),
                        )
                    )
                elif vx < h["a"] and h["a"] - vx <= connect_tol:
                    additions.append(
                        make_centerline_segment(
                            "H",
                            hy,
                            vx,
                            h["a"],
                            h.get("thickness", default_thickness),
                            region_id=h.get("region_id"),
                        )
                    )

    for v in v_segments:
        vx = nearest_axis(v["c"], xs, axis_tol)
        for hy in ys:
            if point_on_intervals(h_map.get(hy, []), vx, connect_tol):
                if v["b"] < hy and hy - v["b"] <= connect_tol:
                    additions.append(
                        make_centerline_segment(
                            "V",
                            vx,
                            v["b"],
                            hy,
                            v.get("thickness", default_thickness),
                            region_id=v.get("region_id"),
                        )
                    )
                elif hy < v["a"] and v["a"] - hy <= connect_tol:
                    additions.append(
                        make_centerline_segment(
                            "V",
                            vx,
                            hy,
                            v["a"],
                            v.get("thickness", default_thickness),
                            region_id=v.get("region_id"),
                        )
                    )

    return working + additions


def repair_centerline_topology(segments, axis_tol, gap_tol, connect_tol, iterations=4):
    """
    Converts extracted centerline fragments into a cleaner noded graph:
    snap axes, bridge small wall gaps, extend near-miss endpoints, and merge again.
    """

    if not segments:
        return []

    working = [copy_segment(s) for s in segments]

    for _ in range(max(1, int(iterations))):
        working = snap_segment_axes(working, axis_tol)
        working = merge_collinear_segments(working, axis_tol=axis_tol, bridge_gap=gap_tol)
        working = extend_segments_to_crossing_axes(working, extend_tol=connect_tol)
        working = add_short_connection_segments(
            working,
            axis_tol=axis_tol,
            connect_tol=connect_tol,
            default_thickness=max([int(s.get("thickness", 225)) for s in working] or [225]),
        )
        working = merge_collinear_segments(working, axis_tol=axis_tol, bridge_gap=gap_tol)

    return sorted(
        working,
        key=lambda s: (s.get("region_id", 0), s["orientation"], s["c"], s["a"], s["b"]),
    )


def repair_regions_topology(regions, axis_tol, gap_tol, connect_tol):
    repaired_regions = []
    repaired_segments = []
    stats = []

    for region in regions:
        before = len(region.get("segments", []))
        repaired = repair_centerline_topology(
            region.get("segments", []),
            axis_tol=axis_tol,
            gap_tol=gap_tol,
            connect_tol=connect_tol,
            iterations=4,
        )

        for s in repaired:
            s["region_id"] = region["region_id"]

        updated = update_region_from_segments(dict(region), repaired)
        repaired_regions.append(updated)
        repaired_segments.extend(repaired)
        stats.append({
            "region_id": region["region_id"],
            "segments_before": before,
            "segments_after": len(repaired),
            "added_or_merged_delta": len(repaired) - before,
        })

    return repaired_regions, repaired_segments, stats


# =========================================================
# SMART STRUCTURAL LAYOUT ENGINE
# =========================================================

def axis_weight_from_segments(related_segments):
    if not related_segments:
        return 0.0

    max_thickness = max(int(s.get("thickness", 0)) for s in related_segments)
    count = len(related_segments)
    total_len = sum(segment_length(s) for s in related_segments)
    quality = sum(float(s.get("quality_score", 0.0)) for s in related_segments)

    return total_len * 0.15 + count * 150.0 + max_thickness * 3.0 + quality * 0.01


def build_structural_axis_infos(region, axis_tol):
    segments = region.get("segments", [])
    h_segments, v_segments = split_hv_segments(segments)
    xs, ys, h_map, v_map = build_axis_interval_maps(segments, axis_tol)

    min_x = float(region.get("min_x", 0.0))
    max_x = float(region.get("max_x", 0.0))
    min_y = float(region.get("min_y", 0.0))
    max_y = float(region.get("max_y", 0.0))
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    edge_tol = max(axis_tol * 2.0, 100.0)

    x_infos = []
    y_infos = []

    for x in xs:
        intervals = merge_intervals(v_map.get(x, []), gap_tol=axis_tol)
        related = [s for s in v_segments if abs(nearest_axis(s["c"], xs, axis_tol) - x) <= axis_tol]
        coverage = total_interval_length(intervals)
        coverage_ratio = coverage / height
        max_thickness = max([int(s.get("thickness", 0)) for s in related] or [0])
        is_outer = abs(x - min_x) <= edge_tol or abs(x - max_x) <= edge_tol
        is_major = coverage_ratio >= 0.50 or max_thickness >= 225 or len(related) >= 2

        x_infos.append({
            "axis": "X",
            "value": float(x),
            "coverage": float(coverage),
            "coverage_ratio": float(coverage_ratio),
            "segment_count": len(related),
            "max_thickness": max_thickness,
            "intervals": intervals,
            "is_outer": is_outer,
            "is_major": is_major,
            "score": axis_weight_from_segments(related) + coverage_ratio * 1000.0 + (500.0 if is_outer else 0.0),
            "selected_reason": "",
        })

    for y in ys:
        intervals = merge_intervals(h_map.get(y, []), gap_tol=axis_tol)
        related = [s for s in h_segments if abs(nearest_axis(s["c"], ys, axis_tol) - y) <= axis_tol]
        coverage = total_interval_length(intervals)
        coverage_ratio = coverage / width
        max_thickness = max([int(s.get("thickness", 0)) for s in related] or [0])
        is_outer = abs(y - min_y) <= edge_tol or abs(y - max_y) <= edge_tol
        is_major = coverage_ratio >= 0.50 or max_thickness >= 225 or len(related) >= 2

        y_infos.append({
            "axis": "Y",
            "value": float(y),
            "coverage": float(coverage),
            "coverage_ratio": float(coverage_ratio),
            "segment_count": len(related),
            "max_thickness": max_thickness,
            "intervals": intervals,
            "is_outer": is_outer,
            "is_major": is_major,
            "score": axis_weight_from_segments(related) + coverage_ratio * 1000.0 + (500.0 if is_outer else 0.0),
            "selected_reason": "",
        })

    return sorted(x_infos, key=lambda a: a["value"]), sorted(y_infos, key=lambda a: a["value"]), h_map, v_map


def clone_axis_info(info, reason):
    out = dict(info)
    out["selected_reason"] = reason
    return out


def add_axis_if_spaced(selected, candidate, min_spacing, axis_tol, reason):
    for current in selected:
        if abs(current["value"] - candidate["value"]) < max(min_spacing, axis_tol):
            return False
    selected.append(clone_axis_info(candidate, reason))
    return True


def select_edge_axis(axis_infos, edge_value):
    if not axis_infos:
        return None
    return min(axis_infos, key=lambda a: abs(a["value"] - edge_value))


def find_best_axis_in_gap(axis_infos, left, right, min_spacing):
    candidates = [
        a for a in axis_infos
        if (left + min_spacing) <= a["value"] <= (right - min_spacing)
    ]

    if not candidates:
        return None

    mid = (left + right) / 2.0
    gap = max(right - left, 1.0)

    return max(
        candidates,
        key=lambda a: (
            a["score"] - abs(a["value"] - mid) / gap * 500.0,
            a["coverage_ratio"],
            -abs(a["value"] - mid),
        ),
    )


def synthetic_axis_info(axis_name, value):
    return {
        "axis": axis_name,
        "value": float(value),
        "coverage": 0.0,
        "coverage_ratio": 0.0,
        "segment_count": 0,
        "max_thickness": 0,
        "intervals": [],
        "is_outer": False,
        "is_major": True,
        "is_synthetic": True,
        "score": 0.0,
        "selected_reason": "",
    }


def merge_axis_infos_unique(axis_infos, selected_infos, axis_tol):
    merged = [dict(a) for a in axis_infos]

    for selected in selected_infos:
        if find_axis_info_by_value(merged, selected["value"], axis_tol) is None:
            merged.append(dict(selected))

    return sorted(merged, key=lambda a: a["value"])


def select_support_axes(axis_infos, low_bound, high_bound, max_spacing, min_spacing, axis_tol, strategy, axis_name):
    if not axis_infos:
        return []

    selected = []
    low_axis = select_edge_axis(axis_infos, low_bound)
    high_axis = select_edge_axis(axis_infos, high_bound)

    if low_axis:
        add_axis_if_spaced(selected, low_axis, 0.0, axis_tol, "outer_edge")
    if high_axis:
        add_axis_if_spaced(selected, high_axis, 0.0, axis_tol, "outer_edge")

    if strategy == "Safety first":
        major_threshold = 0.20
    elif strategy == "Dense review":
        major_threshold = 0.30
    elif strategy == "Balanced":
        major_threshold = 0.48
    else:
        major_threshold = 0.65

    for axis in sorted(axis_infos, key=lambda a: a["score"], reverse=True):
        strong_major_axis = axis["coverage_ratio"] >= major_threshold or (
            strategy != "Economical" and axis["is_major"] and axis["segment_count"] >= 2
        )
        if strong_major_axis:
            add_axis_if_spaced(selected, axis, min_spacing, axis_tol, "major_continuous_wall_axis")

    guard = 0
    while guard < 50:
        guard += 1
        ordered = sorted(selected, key=lambda a: a["value"])
        largest_gap = None
        largest_pair = None

        for a, b in zip(ordered, ordered[1:]):
            gap = b["value"] - a["value"]
            if gap > max_spacing and (largest_gap is None or gap > largest_gap):
                largest_gap = gap
                largest_pair = (a, b)

        if largest_pair is None:
            break

        left, right = largest_pair[0]["value"], largest_pair[1]["value"]
        best = find_best_axis_in_gap(axis_infos, left, right, min_spacing)

        if best is None:
            best = synthetic_axis_info(axis_name, (left + right) / 2.0)

        if not add_axis_if_spaced(selected, best, min_spacing, axis_tol, "span_control"):
            break

    return sorted(selected, key=lambda a: a["value"])


def axis_lookup(axis_infos):
    return {round(float(a["value"]), 6): a for a in axis_infos}


def find_axis_info_by_value(axis_infos, value, axis_tol):
    if not axis_infos:
        return None
    best = min(axis_infos, key=lambda a: abs(a["value"] - value))
    if abs(best["value"] - value) <= axis_tol:
        return best
    return None


def adjacent_spans(selected_values, value):
    ordered = sorted(selected_values)
    if not ordered:
        return 0.0, 0.0

    idx = min(range(len(ordered)), key=lambda i: abs(ordered[i] - value))
    left = ordered[idx] - ordered[idx - 1] if idx > 0 else 0.0
    right = ordered[idx + 1] - ordered[idx] if idx < len(ordered) - 1 else 0.0
    return left, right


def supported_wall_junction(x, y, h_map, v_map, axis_tol, node_tol):
    h_axis = closest_value(y, list(h_map.keys()))
    v_axis = closest_value(x, list(v_map.keys()))

    h_hit = h_axis is not None and abs(h_axis - y) <= axis_tol and point_on_intervals(h_map.get(h_axis, []), x, node_tol)
    v_hit = v_axis is not None and abs(v_axis - x) <= axis_tol and point_on_intervals(v_map.get(v_axis, []), y, node_tol)

    return h_hit and v_hit, h_hit, v_hit


def column_candidate_role(x, y, region, axis_tol):
    edge_tol = max(axis_tol * 2.0, 100.0)
    outer_x = abs(x - region["min_x"]) <= edge_tol or abs(x - region["max_x"]) <= edge_tol
    outer_y = abs(y - region["min_y"]) <= edge_tol or abs(y - region["max_y"]) <= edge_tol

    if outer_x and outer_y:
        return "corner"
    if outer_x or outer_y:
        return "perimeter"
    return "interior"


def score_structural_column_candidate(
    role,
    x_info,
    y_info,
    x_selected,
    y_selected,
    max_selected_span,
    max_spacing,
    has_wall_junction,
    floor_count,
):
    role_score = {
        "corner": 1600.0,
        "perimeter": 1000.0,
        "interior": 450.0,
    }.get(role, 300.0)

    continuity_score = 0.0
    if x_info:
        continuity_score += min(800.0, x_info.get("coverage_ratio", 0.0) * 700.0)
        continuity_score += 300.0 if x_info.get("is_major") else 0.0
        continuity_score += x_info.get("max_thickness", 0) * 0.8
    if y_info:
        continuity_score += min(800.0, y_info.get("coverage_ratio", 0.0) * 700.0)
        continuity_score += 300.0 if y_info.get("is_major") else 0.0
        continuity_score += y_info.get("max_thickness", 0) * 0.8

    grid_score = 500.0 if x_selected and y_selected else -700.0
    junction_score = 300.0 if has_wall_junction else -600.0
    span_pressure = max(0.0, max_selected_span - (max_spacing * 0.70))
    span_score = min(500.0, span_pressure * 0.10)
    floor_score = min(300.0, max(0, floor_count - 1) * 35.0)

    return role_score + continuity_score + grid_score + junction_score + span_score + floor_score


def should_accept_structural_candidate(candidate, strategy):
    if not candidate["on_selected_support_grid"]:
        return False, "not_on_economical_support_grid"

    if strategy == "Safety first":
        if candidate["role"] == "corner":
            return True, "corner_support"
        if candidate["role"] == "perimeter":
            return True, "perimeter_support_grid"
        return True, "safety_span_control_grid"

    if not candidate["has_wall_junction"]:
        return False, "not_confirmed_wall_grid_junction"

    if candidate["role"] == "corner":
        return True, "corner_support"

    if candidate["role"] == "perimeter":
        return True, "perimeter_support_grid"

    x_major = candidate.get("x_axis_major", False)
    y_major = candidate.get("y_axis_major", False)
    x_reason = candidate.get("x_selected_reason", "")
    y_reason = candidate.get("y_selected_reason", "")
    span_control = "span_control" in (x_reason, y_reason)

    if strategy == "Economical":
        if x_major and y_major and span_control:
            return True, "interior_span_control_major_axes"
        if x_major and y_major and candidate["governing_selected_span"] >= candidate["target_spacing"]:
            return True, "interior_major_axis_support"
        return False, "interior_partition_junction_filtered"

    if strategy == "Balanced":
        if x_major or y_major or span_control:
            return True, "balanced_grid_support"
        return False, "minor_partition_junction_filtered"

    return True, "dense_review_grid_support"


def build_region_structural_layout(
    region,
    axis_tol,
    strategy,
    max_spacing,
    target_spacing,
    min_spacing,
    floor_count,
    node_tol,
):
    x_infos, y_infos, h_map, v_map = build_structural_axis_infos(region, axis_tol)

    selected_x_infos = select_support_axes(
        x_infos,
        region["min_x"],
        region["max_x"],
        max_spacing=max_spacing,
        min_spacing=min_spacing,
        axis_tol=axis_tol,
        strategy=strategy,
        axis_name="X",
    )
    selected_y_infos = select_support_axes(
        y_infos,
        region["min_y"],
        region["max_y"],
        max_spacing=max_spacing,
        min_spacing=min_spacing,
        axis_tol=axis_tol,
        strategy=strategy,
        axis_name="Y",
    )

    selected_x_values = [a["value"] for a in selected_x_infos]
    selected_y_values = [a["value"] for a in selected_y_infos]
    candidate_x_infos = merge_axis_infos_unique(x_infos, selected_x_infos, axis_tol)
    candidate_y_infos = merge_axis_infos_unique(y_infos, selected_y_infos, axis_tol)

    candidates = []
    rejected = []

    for x_info in candidate_x_infos:
        x = x_info["value"]
        x_selected_info = find_axis_info_by_value(selected_x_infos, x, axis_tol)
        x_selected = x_selected_info is not None

        for y_info in candidate_y_infos:
            y = y_info["value"]
            y_selected_info = find_axis_info_by_value(selected_y_infos, y, axis_tol)
            y_selected = y_selected_info is not None

            has_junction, h_hit, v_hit = supported_wall_junction(
                x,
                y,
                h_map,
                v_map,
                axis_tol=axis_tol,
                node_tol=node_tol,
            )

            if not has_junction and not (x_selected and y_selected):
                continue

            role = column_candidate_role(x, y, region, axis_tol)
            x_left, x_right = adjacent_spans(selected_x_values, x)
            y_down, y_up = adjacent_spans(selected_y_values, y)
            governing_span = max(x_left, x_right, y_down, y_up)

            candidate = {
                "region_id": region["region_id"],
                "x": float(x),
                "y": float(y),
                "role": role,
                "has_wall_junction": bool(has_junction),
                "h_axis_hit": bool(h_hit),
                "v_axis_hit": bool(v_hit),
                "on_selected_support_grid": bool(x_selected and y_selected),
                "x_axis_major": bool(x_info.get("is_major", False)),
                "y_axis_major": bool(y_info.get("is_major", False)),
                "x_axis_coverage_ratio": float(x_info.get("coverage_ratio", 0.0)),
                "y_axis_coverage_ratio": float(y_info.get("coverage_ratio", 0.0)),
                "x_selected_reason": x_selected_info.get("selected_reason", "") if x_selected_info else "",
                "y_selected_reason": y_selected_info.get("selected_reason", "") if y_selected_info else "",
                "governing_selected_span": float(governing_span),
                "target_spacing": float(target_spacing),
                "score": 0.0,
                "junction_type": role,
                "direction_count": "",
                "h_thickness": int(y_info.get("max_thickness", 0)),
                "v_thickness": int(x_info.get("max_thickness", 0)),
                "thickness_pair": f"{int(y_info.get('max_thickness', 0))}/{int(x_info.get('max_thickness', 0))}",
                "h_length": float(y_info.get("coverage", 0.0)),
                "v_length": float(x_info.get("coverage", 0.0)),
                "left": "",
                "right": "",
                "down": "",
                "up": "",
            }

            candidate["score"] = score_structural_column_candidate(
                role=role,
                x_info=x_info,
                y_info=y_info,
                x_selected=x_selected,
                y_selected=y_selected,
                max_selected_span=governing_span,
                max_spacing=max_spacing,
                has_wall_junction=has_junction,
                floor_count=floor_count,
            )

            accepted, reason = should_accept_structural_candidate(candidate, strategy)

            if accepted:
                candidate["layout_reason"] = reason
                candidates.append(candidate)
            else:
                rejected.append({
                    **candidate,
                    "accepted": False,
                    "reject_reason": reason,
                    "nearest_accepted_x": "",
                    "nearest_accepted_y": "",
                    "nearest_accepted_distance": "",
                })

    warnings = []
    for values, axis_name in [(selected_x_values, "X"), (selected_y_values, "Y")]:
        values = sorted(values)
        for a, b in zip(values, values[1:]):
            gap = b - a
            if gap > max_spacing:
                warnings.append(f"{axis_name}-axis support gap {round(gap, 1)} exceeds selected maximum {round(max_spacing, 1)}")

    return {
        "region_id": region["region_id"],
        "selected_x_infos": selected_x_infos,
        "selected_y_infos": selected_y_infos,
        "selected_x_values": selected_x_values,
        "selected_y_values": selected_y_values,
        "all_x_axis_count": len(x_infos),
        "all_y_axis_count": len(y_infos),
        "accepted_candidates": candidates,
        "rejected_candidates": rejected,
        "warnings": warnings,
        "bounds": {
            "min_x": region["min_x"],
            "max_x": region["max_x"],
            "min_y": region["min_y"],
            "max_y": region["max_y"],
        },
        "h_map": h_map,
        "v_map": v_map,
    }


def dedupe_column_candidates(candidates, point_tol):
    if not candidates:
        return []

    sorted_candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)
    kept = []

    for cand in sorted_candidates:
        exists = False

        for existing in kept:
            if point_distance((cand["x"], cand["y"]), (existing["x"], existing["y"])) <= point_tol:
                exists = True
                break

        if not exists:
            kept.append(cand)

    return sorted(kept, key=lambda c: (c.get("region_id", 0), c["x"], c["y"]))


def apply_column_proximity_filter(candidates, min_column_spacing):
    if not candidates:
        return [], []

    sorted_candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)
    accepted = []
    rejected = []

    for cand in sorted_candidates:
        too_close_to = None
        too_close_distance = None

        for acc in accepted:
            if cand.get("region_id") != acc.get("region_id"):
                continue
            d = point_distance((cand["x"], cand["y"]), (acc["x"], acc["y"]))

            if d < min_column_spacing:
                too_close_to = acc
                too_close_distance = d
                break

        if too_close_to is None:
            accepted.append({
                **cand,
                "accepted": True,
                "reject_reason": "",
                "nearest_accepted_x": "",
                "nearest_accepted_y": "",
                "nearest_accepted_distance": "",
            })
        else:
            rejected.append({
                **cand,
                "accepted": False,
                "reject_reason": "too_close_to_stronger_structural_column",
                "nearest_accepted_x": round(too_close_to["x"], 3),
                "nearest_accepted_y": round(too_close_to["y"], 3),
                "nearest_accepted_distance": round(too_close_distance, 3),
            })

    accepted = sorted(accepted, key=lambda c: (c.get("region_id", 0), c["x"], c["y"]))
    rejected = sorted(rejected, key=lambda c: (c.get("region_id", 0), c["x"], c["y"]))

    return accepted, rejected


def build_structural_layout_for_regions(
    regions,
    axis_tol,
    strategy,
    max_spacing,
    target_spacing,
    min_spacing,
    floor_count,
    min_column_spacing,
    node_tol,
):
    layouts = []
    accepted_candidates = []
    rejected_candidates = []

    for region in regions:
        layout = build_region_structural_layout(
            region=region,
            axis_tol=axis_tol,
            strategy=strategy,
            max_spacing=max_spacing,
            target_spacing=target_spacing,
            min_spacing=min_spacing,
            floor_count=floor_count,
            node_tol=node_tol,
        )
        layouts.append(layout)
        accepted_candidates.extend(layout["accepted_candidates"])
        rejected_candidates.extend(layout["rejected_candidates"])

    accepted_candidates = dedupe_column_candidates(accepted_candidates, point_tol=axis_tol)
    accepted_columns, proximity_rejected = apply_column_proximity_filter(
        accepted_candidates,
        min_column_spacing=min_column_spacing,
    )
    rejected_candidates.extend(proximity_rejected)

    return layouts, accepted_columns, rejected_candidates


def column_exists_at(columns, x, y, tol, region_id=None):
    for c in columns:
        if region_id is not None and c.get("region_id") != region_id:
            continue
        if point_distance((c["x"], c["y"]), (x, y)) <= tol:
            return True
    return False


def detect_structural_grid_panels(layouts, accepted_columns, axis_tol, min_width, min_height, max_width, max_height, closure_tol):
    panels = []

    for layout in layouts:
        region_id = layout["region_id"]
        xs = sorted(layout["selected_x_values"])
        ys = sorted(layout["selected_y_values"])
        h_map = layout["h_map"]
        v_map = layout["v_map"]

        if len(xs) < 2 or len(ys) < 2:
            continue

        for i in range(len(xs) - 1):
            x1 = xs[i]
            x2 = xs[i + 1]
            width = abs(x2 - x1)

            if width < min_width:
                continue
            if max_width > 0 and width > max_width:
                continue

            for j in range(len(ys) - 1):
                y1 = ys[j]
                y2 = ys[j + 1]
                height = abs(y2 - y1)

                if height < min_height:
                    continue
                if max_height > 0 and height > max_height:
                    continue

                bottom = horizontal_edge_exists(h_map, y1, x1, x2, axis_tol, closure_tol)
                top = horizontal_edge_exists(h_map, y2, x1, x2, axis_tol, closure_tol)
                left = vertical_edge_exists(v_map, x1, y1, y2, axis_tol, closure_tol)
                right = vertical_edge_exists(v_map, x2, y1, y2, axis_tol, closure_tol)
                edge_count = sum([bottom, top, left, right])

                corner_count = sum([
                    column_exists_at(accepted_columns, x1, y1, axis_tol * 2.0, region_id),
                    column_exists_at(accepted_columns, x2, y1, axis_tol * 2.0, region_id),
                    column_exists_at(accepted_columns, x1, y2, axis_tol * 2.0, region_id),
                    column_exists_at(accepted_columns, x2, y2, axis_tol * 2.0, region_id),
                ])

                if edge_count < 2 and corner_count < 3:
                    continue

                if edge_count == 4:
                    review_status = "wall_bounded"
                elif corner_count >= 3:
                    review_status = "column_grid_panel_review"
                else:
                    review_status = "partial_edge_panel_review"

                panels.append({
                    "region_id": region_id,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "width": width,
                    "height": height,
                    "area": width * height,
                    "method": "structural_grid_cell",
                    "edge_support_count": int(edge_count),
                    "corner_support_count": int(corner_count),
                    "review_status": review_status,
                })

    return panels


def dedupe_panels(panels, tol=1.0):
    kept = []
    keys = set()

    for panel in panels:
        x1 = round(min(panel["x1"], panel["x2"]) / max(tol, 1e-9))
        x2 = round(max(panel["x1"], panel["x2"]) / max(tol, 1e-9))
        y1 = round(min(panel["y1"], panel["y2"]) / max(tol, 1e-9))
        y2 = round(max(panel["y1"], panel["y2"]) / max(tol, 1e-9))
        key = (panel.get("region_id"), x1, y1, x2, y2)

        if key in keys:
            continue

        keys.add(key)
        kept.append(panel)

    return kept


# =========================================================
# JUNCTION NODES
# =========================================================

def collect_junction_nodes(segments, axis_tol):
    h_segments, v_segments = split_hv_segments(segments)
    nodes = []

    for h in h_segments:
        hy = h["c"]

        for v in v_segments:
            vx = v["c"]

            on_h = h["a"] - axis_tol <= vx <= h["b"] + axis_tol
            on_v = v["a"] - axis_tol <= hy <= v["b"] + axis_tol

            if on_h and on_v:
                nodes.append((vx, hy))

    deduped = []

    for x, y in nodes:
        exists = False

        for dx, dy in deduped:
            if math.dist((x, y), (dx, dy)) <= axis_tol:
                exists = True
                break

        if not exists:
            deduped.append((x, y))

    return deduped


# =========================================================
# DXF OUTPUT
# =========================================================

def draw_segment(msp, seg, layer):
    if seg["orientation"] == "H":
        y = seg["c"]
        msp.add_line((seg["a"], y), (seg["b"], y), dxfattribs={"layer": layer})
    elif seg["orientation"] == "V":
        x = seg["c"]
        msp.add_line((x, seg["a"]), (x, seg["b"]), dxfattribs={"layer": layer})


def draw_segments_by_thickness(msp, segments, prefix):
    for seg in segments:
        layer = f"{prefix}_{seg['thickness']}"
        draw_segment(msp, seg, layer)


def draw_panels(msp, panels, layer):
    for p in panels:
        x1 = p["x1"]
        y1 = p["y1"]
        x2 = p["x2"]
        y2 = p["y2"]

        points = [
            (x1, y1),
            (x2, y1),
            (x2, y2),
            (x1, y2),
            (x1, y1),
        ]

        msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})


def draw_nodes(msp, nodes, radius, layer):
    for x, y in nodes:
        msp.add_circle(center=(x, y), radius=radius, dxfattribs={"layer": layer})


def draw_column_marker(msp, x, y, width, depth, shape, layer):
    if shape == "Circular":
        radius = float(width) / 2.0
        msp.add_circle(center=(x, y), radius=radius, dxfattribs={"layer": layer})
        cross = min(radius * 0.70, 125.0)
    else:
        half_w = float(width) / 2.0
        half_d = float(depth) / 2.0
        points = [
            (x - half_w, y - half_d),
            (x + half_w, y - half_d),
            (x + half_w, y + half_d),
            (x - half_w, y + half_d),
            (x - half_w, y - half_d),
        ]
        msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})
        cross = min(max(float(width), float(depth)) * 0.35, 125.0)

    msp.add_line((x - cross, y), (x + cross, y), dxfattribs={"layer": layer})
    msp.add_line((x, y - cross), (x, y + cross), dxfattribs={"layer": layer})


def draw_columns(msp, columns, width, depth, shape, layer):
    for c in columns:
        draw_column_marker(
            msp,
            float(c["x"]),
            float(c["y"]),
            float(width),
            float(depth),
            shape,
            layer,
        )


def draw_structural_grid_lines(msp, layouts, columns, axis_tol, layer):
    for layout in layouts:
        region_id = layout["region_id"]
        xs = sorted(layout["selected_x_values"])
        ys = sorted(layout["selected_y_values"])

        for y in ys:
            row_points = sorted(
                [(c["x"], c["y"]) for c in columns if c.get("region_id") == region_id and abs(c["y"] - y) <= axis_tol * 2.0],
                key=lambda p: p[0],
            )
            for p1, p2 in zip(row_points, row_points[1:]):
                msp.add_line(p1, p2, dxfattribs={"layer": layer})

        for x in xs:
            col_points = sorted(
                [(c["x"], c["y"]) for c in columns if c.get("region_id") == region_id and abs(c["x"] - x) <= axis_tol * 2.0],
                key=lambda p: p[1],
            )
            for p1, p2 in zip(col_points, col_points[1:]):
                msp.add_line(p1, p2, dxfattribs={"layer": layer})


def build_output_dxf(
    raw_segments,
    final_segments,
    source_segments,
    topology_segments,
    panels,
    nodes,
    columns,
    layouts,
    column_width,
    column_depth,
    column_shape,
    axis_tol,
    draw_raw=True,
    draw_source_reference=False,
    draw_topology_audit=False,
    draw_wall_centerlines=True,
    draw_panels_enabled=False,
    draw_nodes_enabled=False,
    draw_columns_enabled=True,
    draw_structural_grid_enabled=True,
    min_output_centerline_length=0,
    export_thicknesses=None,
):
    new_doc = ezdxf.new()
    new_msp = new_doc.modelspace()

    export_thicknesses = export_thicknesses or []

    raw_for_output = filter_segments_by_thickness(raw_segments, export_thicknesses)
    final_for_output = filter_segments_by_thickness(final_segments, export_thicknesses)
    source_for_output = filter_segments_by_thickness(source_segments or [], export_thicknesses)
    topology_for_output = filter_segments_by_thickness(topology_segments or [], export_thicknesses)

    clean_final_segments = filter_short_segments(final_for_output, min_output_centerline_length)

    all_thicknesses = sorted(
        set(
            [int(s["thickness"]) for s in raw_for_output]
            + [int(s["thickness"]) for s in clean_final_segments]
            + [int(s["thickness"]) for s in source_for_output]
            + [int(s["thickness"]) for s in topology_for_output]
        )
    )

    for thickness in all_thicknesses:
        safe_layer(new_doc, f"ILS_RAW_CENTERLINE_{thickness}", color=8)
        safe_layer(new_doc, f"ILS_SOURCE_CENTERLINE_REFERENCE_{thickness}", color=9)
        safe_layer(new_doc, f"ILS_TOPOLOGY_REPAIR_AUDIT_{thickness}", color=4)
        safe_layer(new_doc, f"ILS_WALL_CENTERLINE_{thickness}", color=layer_color_for_thickness(thickness))

    safe_layer(new_doc, "ILS_STRUCTURAL_GRID_REVIEW", color=1)
    safe_layer(new_doc, "ILS_SLAB_PANEL_REVIEW", color=5)
    safe_layer(new_doc, "ILS_JUNCTION_NODE_REVIEW", color=2)

    col_label = column_size_label(column_shape, column_width, column_depth).replace(" ", "_")
    column_layer = f"ILS_COLUMN_{col_label}_REVIEW"
    safe_layer(new_doc, column_layer, color=6)

    if draw_raw:
        draw_segments_by_thickness(new_msp, raw_for_output, prefix="ILS_RAW_CENTERLINE")

    if draw_source_reference:
        draw_segments_by_thickness(new_msp, source_for_output, prefix="ILS_SOURCE_CENTERLINE_REFERENCE")

    if draw_topology_audit:
        draw_segments_by_thickness(new_msp, topology_for_output, prefix="ILS_TOPOLOGY_REPAIR_AUDIT")

    if draw_wall_centerlines:
        draw_segments_by_thickness(new_msp, clean_final_segments, prefix="ILS_WALL_CENTERLINE")

    if draw_structural_grid_enabled:
        draw_structural_grid_lines(
            new_msp,
            layouts,
            columns,
            axis_tol=axis_tol,
            layer="ILS_STRUCTURAL_GRID_REVIEW",
        )

    if draw_panels_enabled:
        draw_panels(new_msp, panels, layer="ILS_SLAB_PANEL_REVIEW")

    if draw_nodes_enabled:
        draw_nodes(new_msp, nodes, radius=50, layer="ILS_JUNCTION_NODE_REVIEW")

    if draw_columns_enabled:
        draw_columns(
            new_msp,
            columns,
            width=column_width,
            depth=column_depth,
            shape=column_shape,
            layer=column_layer,
        )

    return new_doc


# =========================================================
# DATAFRAMES
# =========================================================

def segments_to_dataframe(segments):
    rows = []

    for idx, s in enumerate(segments, start=1):
        if s["orientation"] == "H":
            x1 = s["a"]
            y1 = s["c"]
            x2 = s["b"]
            y2 = s["c"]
        else:
            x1 = s["c"]
            y1 = s["a"]
            x2 = s["c"]
            y2 = s["b"]

        rows.append({
            "id": idx,
            "region_id": s.get("region_id", ""),
            "orientation": s["orientation"],
            "thickness": s["thickness"],
            "actual_thickness": round(float(s.get("actual_thickness", s["thickness"])), 3),
            "thickness_error": round(float(s.get("thickness_error", 0.0)), 3),
            "overlap_length": round(float(s.get("overlap_length", segment_length(s))), 3),
            "source_count": int(s.get("source_count", 1)),
            "quality_score": round(float(s.get("quality_score", 0.0)), 3),
            "x1": round(x1, 3),
            "y1": round(y1, 3),
            "x2": round(x2, 3),
            "y2": round(y2, 3),
            "length": round(segment_length(s), 3),
        })

    return pd.DataFrame(rows)


def removed_segments_to_dataframe(segments):
    df = segments_to_dataframe(segments)

    if df.empty:
        return df

    for col in ["removed_reason", "overlap_with_thickness", "overlap_amount", "overlap_ratio"]:
        df[col] = [s.get(col, "") for s in segments]

    return df


def panels_to_dataframe(panels):
    rows = []

    for idx, p in enumerate(panels, start=1):
        rows.append({
            "panel_id": idx,
            "region_id": p.get("region_id", ""),
            "method": p.get("method", ""),
            "review_status": p.get("review_status", ""),
            "edge_support_count": p.get("edge_support_count", ""),
            "corner_support_count": p.get("corner_support_count", ""),
            "x1": round(p["x1"], 3),
            "y1": round(p["y1"], 3),
            "x2": round(p["x2"], 3),
            "y2": round(p["y2"], 3),
            "width": round(p["width"], 3),
            "height": round(p["height"], 3),
            "area": round(p["area"], 3),
        })

    return pd.DataFrame(rows)


def columns_to_dataframe(columns):
    rows = []

    for idx, c in enumerate(columns, start=1):
        rows.append({
            "column_id": idx,
            "region_id": c.get("region_id", ""),
            "x": round(float(c["x"]), 3),
            "y": round(float(c["y"]), 3),
            "score": round(float(c.get("score", 0.0)), 3),
            "role": c.get("role", ""),
            "layout_reason": c.get("layout_reason", ""),
            "governing_selected_span": round(float(c.get("governing_selected_span", 0.0)), 3),
            "x_selected_reason": c.get("x_selected_reason", ""),
            "y_selected_reason": c.get("y_selected_reason", ""),
            "x_axis_major": c.get("x_axis_major", ""),
            "y_axis_major": c.get("y_axis_major", ""),
            "junction_type": c.get("junction_type", ""),
            "direction_count": c.get("direction_count", ""),
            "h_thickness": c.get("h_thickness", ""),
            "v_thickness": c.get("v_thickness", ""),
            "thickness_pair": c.get("thickness_pair", ""),
            "h_length": round(float(c.get("h_length", 0.0)), 3),
            "v_length": round(float(c.get("v_length", 0.0)), 3),
            "accepted": c.get("accepted", ""),
            "reject_reason": c.get("reject_reason", ""),
            "nearest_accepted_x": c.get("nearest_accepted_x", ""),
            "nearest_accepted_y": c.get("nearest_accepted_y", ""),
            "nearest_accepted_distance": c.get("nearest_accepted_distance", ""),
        })

    return pd.DataFrame(rows)


def regions_to_dataframe(regions):
    rows = []

    for r in regions:
        rows.append({
            "region_id": r["region_id"],
            "segment_count": r["segment_count"],
            "min_x": round(r["min_x"], 3),
            "max_x": round(r["max_x"], 3),
            "min_y": round(r["min_y"], 3),
            "max_y": round(r["max_y"], 3),
            "width": round(r["width"], 3),
            "height": round(r["height"], 3),
        })

    return pd.DataFrame(rows)


def structural_layouts_to_dataframe(layouts):
    rows = []

    for layout in layouts:
        sx = sorted(layout.get("selected_x_values", []))
        sy = sorted(layout.get("selected_y_values", []))
        rows.append({
            "region_id": layout["region_id"],
            "all_x_axis_count": layout.get("all_x_axis_count", 0),
            "all_y_axis_count": layout.get("all_y_axis_count", 0),
            "selected_x_axis_count": len(sx),
            "selected_y_axis_count": len(sy),
            "selected_x_axes": ", ".join(str(round(v, 3)) for v in sx),
            "selected_y_axes": ", ".join(str(round(v, 3)) for v in sy),
            "warnings": " | ".join(layout.get("warnings", [])),
        })

    return pd.DataFrame(rows)


def centerline_accuracy_summary(raw_segments):
    if not raw_segments:
        return {
            "raw_count": 0,
            "avg_thickness_error": 0.0,
            "max_thickness_error": 0.0,
            "perfect_or_near_perfect": 0,
        }

    errors = [abs(float(s.get("thickness_error", 0.0))) for s in raw_segments]
    near_perfect = len([e for e in errors if e <= 1.0])

    return {
        "raw_count": len(raw_segments),
        "avg_thickness_error": sum(errors) / len(errors),
        "max_thickness_error": max(errors),
        "perfect_or_near_perfect": near_perfect,
    }


# =========================================================
# STREAMLIT UI
# =========================================================

st.markdown("### 1. Upload Architectural DXF")

uploaded_dxf = st.file_uploader(
    "Upload architectural plan DXF",
    type=["dxf"],
    key="arch_centerline_upload",
)

if uploaded_dxf is None:
    st.stop()

try:
    doc = read_uploaded_dxf(uploaded_dxf)
    layers = get_layer_names(doc)
    st.success("DXF loaded successfully.")
except Exception as e:
    st.error(f"Could not read DXF: {e}")
    st.stop()


st.markdown("### 2. Detection and Structural Settings")

with st.expander("Design code, floors, and preliminary structural rules", expanded=True):
    d1, d2, d3 = st.columns(3)

    design_code = d1.selectbox(
        "Design code profile",
        list(DESIGN_CODE_PROFILES.keys()),
        index=0,
        help="Used for preliminary layout limits only. Final code design still requires engineering calculations.",
    )

    building_use = d2.selectbox(
        "Building use",
        ["Residential", "Office / commercial", "School / assembly", "Retail", "Storage / heavy loading"],
        index=0,
    )

    floor_count = d3.number_input(
        "Number of floors supported",
        min_value=1,
        value=2,
        step=1,
        help="Used to suggest a practical starting column size and to raise the column selection priority.",
    )

    profile = DESIGN_CODE_PROFILES[design_code]

    d4, d5, d6, d7 = st.columns(4)

    target_column_spacing = d4.number_input(
        "Target column spacing",
        min_value=1000.0,
        value=float(profile["target_column_spacing"]),
        step=250.0,
    )

    max_column_spacing = d5.number_input(
        "Maximum column/support spacing",
        min_value=1000.0,
        value=float(profile["max_column_spacing"]),
        step=250.0,
        help="If selected supports are farther apart than this, the engine tries to add a stronger axis inside the gap.",
    )

    min_structural_axis_spacing = d6.number_input(
        "Minimum structural axis spacing",
        min_value=500.0,
        value=float(profile["min_column_spacing"]),
        step=100.0,
        help="Prevents the engine from accepting many close partition intersections as columns.",
    )

    max_panel_span_from_code = d7.number_input(
        "Maximum slab panel span",
        min_value=1000.0,
        value=float(profile["max_panel_span"]),
        step=250.0,
    )

with st.expander("Column type and sizing", expanded=True):
    cs1, cs2, cs3 = st.columns(3)

    column_shape = cs1.selectbox(
        "Column type",
        ["Square", "Rectangular", "Circular"],
        index=0,
    )

    suggested_sizes = preliminary_column_size_options(floor_count, building_use, column_shape)
    suggested_labels = [column_size_label(column_shape, w, d) for w, d in suggested_sizes]

    column_size_mode = cs2.selectbox(
        "Column size mode",
        ["Use suggested size", "Choose manually"],
        index=0,
    )

    if column_size_mode == "Use suggested size":
        suggested_choice = cs3.selectbox(
            "Suggested preliminary size",
            suggested_labels,
            index=0,
        )
        suggested_index = suggested_labels.index(suggested_choice)
        column_width, column_depth = suggested_sizes[suggested_index]
    else:
        manual1, manual2 = cs3.columns(2)
        column_width = manual1.number_input(
            "Width / diameter",
            min_value=100.0,
            value=300.0,
            step=25.0,
        )
        if column_shape == "Circular":
            column_depth = column_width
        else:
            column_depth = manual2.number_input(
                "Depth",
                min_value=100.0,
                value=300.0,
                step=25.0,
            )

    st.caption(
        f"Selected preliminary column: {column_shape} {column_size_label(column_shape, column_width, column_depth)}. "
        "This is a starting size, not a completed design check."
    )

with st.expander("Wall source layers", expanded=True):
    use_all_layers = st.checkbox(
        "Use all layers",
        value=True,
        help="If checked, all LINE/LWPOLYLINE/POLYLINE entities are scanned.",
    )

    if use_all_layers:
        selected_layers = []
    else:
        selected_layers = st.multiselect(
            "Select wall face layers",
            options=layers,
            default=layers,
        )

with st.expander("Geometry tolerances", expanded=True):
    c1, c2, c3 = st.columns(3)

    wall_thickness_text = c1.text_input("Wall thicknesses to detect", value="225,150")

    thickness_tol = c2.number_input(
        "Wall thickness tolerance",
        min_value=0.0,
        value=10.0,
        step=1.0,
    )

    ortho_tol = c3.number_input(
        "Orthogonal tolerance",
        min_value=0.0,
        value=1.0,
        step=0.5,
    )

    c4, c5, c6 = st.columns(3)

    min_line_length = c4.number_input(
        "Minimum wall face line length",
        min_value=0.0,
        value=100.0,
        step=50.0,
    )

    min_overlap = c5.number_input(
        "Minimum wall face overlap",
        min_value=0.0,
        value=100.0,
        step=50.0,
    )

    axis_tol = c6.number_input(
        "Axis merge tolerance",
        min_value=0.0,
        value=20.0,
        step=5.0,
    )

    c7, c8, c9 = st.columns(3)

    bridge_gap = c7.number_input(
        "Door/window bridge gap",
        min_value=0.0,
        value=1200.0,
        step=100.0,
    )

    intersection_extend_tol = c8.number_input(
        "Intersection extension tolerance",
        min_value=0.0,
        value=300.0,
        step=50.0,
    )

    extension_mode = c9.selectbox(
        "Intersection extension mode",
        ["Off", "Conservative", "Normal", "Aggressive"],
        index=1,
    )

with st.expander("Output mode and cleanup", expanded=True):
    o1, o2, o3 = st.columns(3)

    output_mode = o1.selectbox(
        "Output mode",
        ["Review mode", "Clean structural mode"],
        index=1,
    )

    min_output_centerline_length = o2.number_input(
        "Minimum output centerline length",
        min_value=0.0,
        value=0.0,
        step=50.0,
    )

    overlap_cleanup_enabled = o3.checkbox(
        "Remove overlapping duplicate centerlines",
        value=True,
    )

    o4, o5, o6 = st.columns(3)

    overlap_cleanup_ratio = o4.number_input(
        "Overlap cleanup ratio",
        min_value=0.10,
        max_value=1.00,
        value=0.60,
        step=0.05,
    )

    overlap_cleanup_min_length = o5.number_input(
        "Minimum duplicate overlap length",
        min_value=0.0,
        value=100.0,
        step=50.0,
    )

    export_junction_nodes = o6.checkbox("Export junction nodes", value=False)

wall_thicknesses = parse_wall_thicknesses(wall_thickness_text)

if not wall_thicknesses:
    st.error("Enter at least one wall thickness, for example: 225,150")
    st.stop()

with st.expander("Wall thickness export", expanded=True):
    export_wall_thicknesses = st.multiselect(
        "Export wall thicknesses",
        options=wall_thicknesses,
        default=wall_thicknesses,
    )

if not export_wall_thicknesses:
    st.warning("Select at least one wall thickness to export.")
    st.stop()

with st.expander("Multiple plan / region settings", expanded=True):
    r1, r2 = st.columns(2)

    auto_separate_regions = r1.checkbox(
        "Auto separate disconnected plans",
        value=True,
        help="Recommended when a DXF contains more than one plan/drawing.",
    )

    region_gap = r2.number_input(
        "Plan separation / region connection gap",
        min_value=0.0,
        value=3000.0,
        step=250.0,
        help="Geometry farther apart than this is treated as separate regions.",
    )

with st.expander("Geometry fidelity / source guard", expanded=True):
    gf1, gf2, gf3 = st.columns(3)

    preserve_source_geometry = gf1.checkbox(
        "Preserve source wall geometry in output",
        value=True,
        help="Exports the original detected centerline geometry as the main wall model. Repairs are used for analysis and optional audit only.",
    )

    export_topology_audit = gf2.checkbox(
        "Export topology repair audit layer",
        value=False,
        help="Shows repaired/noded analysis geometry on a separate layer so you can compare it against the source plan.",
    )

    export_source_reference = gf3.checkbox(
        "Export source reference layer",
        value=False,
        help="Adds a faint source-reference copy of detected centerlines for visual QA.",
    )

with st.expander("Centerline graph and room topology", expanded=True):
    g1, g2, g3 = st.columns(3)

    repair_centerline_graph = g1.checkbox(
        "Repair and node centerline graph",
        value=True,
        help="Keeps short connector walls, snaps near axes, bridges small gaps, and makes intersections usable for room/panel creation.",
    )

    graph_gap_tol = g2.number_input(
        "Room/wall closure gap",
        min_value=0.0,
        value=max(1200.0, bridge_gap),
        step=100.0,
        help="Small gaps up to this value are bridged in the centerline graph. Use this for doors, openings, and near-miss wall fragments.",
    )

    graph_connect_tol = g3.number_input(
        "Endpoint connection tolerance",
        min_value=0.0,
        value=max(600.0, intersection_extend_tol),
        step=50.0,
        help="Extends near-miss centerline endpoints to crossing axes so the exported graph is connected.",
    )

with st.expander("Smart column layout settings", expanded=True):
    col1, col2, col3 = st.columns(3)

    generate_columns = col1.checkbox("Generate smart structural columns", value=True)

    column_layout_strategy = col2.selectbox(
        "Column economy strategy",
        ["Safety first", "Balanced", "Economical", "Dense review"],
        index=0,
        help="Safety first adds support-grid intersections to control spans. Economical filters minor partitions hardest.",
    )

    default_column_spacing_filter = 1200.0 if column_layout_strategy == "Safety first" else max(1800.0, float(profile["min_column_spacing"]))

    min_column_spacing = col3.number_input(
        "Minimum column spacing",
        min_value=0.0,
        value=default_column_spacing_filter,
        step=100.0,
    )

    col4, col5, col6 = st.columns(3)

    structural_node_tol = col4.number_input(
        "Wall/grid node tolerance",
        min_value=0.0,
        value=max(250.0, axis_tol * 3.0),
        step=25.0,
        help="Lets a column sit on a near wall-grid intersection after centerline cleanup.",
    )

    export_columns = col5.checkbox("Export columns", value=True)

    export_structural_grid = col6.checkbox(
        "Export structural grid/beam lines",
        value=False,
        help="Draws review grid lines through accepted columns. Keep off when checking source-geometry fidelity.",
    )

with st.expander("Slab panel settings", expanded=True):
    p1, p2, p3 = st.columns(3)

    slab_panel_method = p1.selectbox(
        "Panel creation method",
        ["Wall-bounded graph panels", "Both", "Structural grid panels"],
        index=0,
    )

    export_slab_panels = p2.checkbox(
        "Export closed room/slab polylines",
        value=True,
        help="Exports closed LWPOLYLINE panels so modelling software reads rooms/slabs, not only loose wall lines.",
    )

    panel_closure_tol = p3.number_input(
        "Panel closure tolerance",
        min_value=0.0,
        value=150.0,
        step=25.0,
    )

    p4, p5, p6, p7 = st.columns(4)

    min_panel_width = p4.number_input("Minimum panel width", min_value=0.0, value=1200.0, step=100.0)
    min_panel_height = p5.number_input("Minimum panel height", min_value=0.0, value=1200.0, step=100.0)
    max_panel_width = p6.number_input(
        "Maximum panel width",
        min_value=0.0,
        value=max_panel_span_from_code,
        step=500.0,
        help="0 means no maximum.",
    )
    max_panel_height = p7.number_input(
        "Maximum panel height",
        min_value=0.0,
        value=max_panel_span_from_code,
        step=500.0,
        help="0 means no maximum.",
    )

analyze = st.button("Analyze Architectural Walls", type="primary")

if not analyze:
    st.stop()


# =========================================================
# ANALYSIS RUN
# =========================================================

with st.spinner("Extracting wall face lines..."):
    extracted = extract_line_entities(
        doc=doc,
        selected_layers=selected_layers,
        use_all_layers=use_all_layers,
        ortho_tol=ortho_tol,
        min_line_length=min_line_length,
    )

horizontal_faces = extracted["horizontal"]
vertical_faces = extracted["vertical"]

st.markdown("### 3. Extraction Summary")

e1, e2, e3 = st.columns(3)
e1.metric("Horizontal wall-face lines", len(horizontal_faces))
e2.metric("Vertical wall-face lines", len(vertical_faces))
e3.metric("Ignored / non-orthogonal", extracted["ignored"])

if len(horizontal_faces) + len(vertical_faces) == 0:
    st.error("No horizontal/vertical wall face lines found. Check layer selection or entity types.")
    st.stop()

with st.spinner("Detecting midpoint wall centerlines..."):
    raw_segments = detect_centerlines_from_face_pairs(
        horizontal_faces=horizontal_faces,
        vertical_faces=vertical_faces,
        wall_thicknesses=wall_thicknesses,
        thickness_tol=thickness_tol,
        min_overlap=min_overlap,
    )

accuracy = centerline_accuracy_summary(raw_segments)

with st.spinner("Healing centerlines..."):
    merged_segments = merge_collinear_segments(raw_segments, axis_tol=axis_tol, bridge_gap=bridge_gap)

    if extension_mode == "Off":
        effective_extend_tol = 0.0
    elif extension_mode == "Conservative":
        effective_extend_tol = intersection_extend_tol * 0.5
    elif extension_mode == "Normal":
        effective_extend_tol = intersection_extend_tol
    else:
        effective_extend_tol = intersection_extend_tol * 1.5

    extended_segments = extend_to_nearby_intersections(
        merged_segments,
        axis_tol=axis_tol,
        extend_tol=effective_extend_tol,
        iterations=3,
    )

    extended_segments = merge_collinear_segments(
        extended_segments,
        axis_tol=axis_tol,
        bridge_gap=axis_tol,
    )

with st.spinner("Cleaning duplicate centerlines..."):
    if overlap_cleanup_enabled:
        final_segments, removed_overlap_segments = resolve_overlapping_centerlines(
            extended_segments,
            axis_tol=axis_tol,
            min_overlap_length=overlap_cleanup_min_length,
            min_overlap_ratio=overlap_cleanup_ratio,
        )
    else:
        final_segments = extended_segments
        removed_overlap_segments = []

selected_final_segments = filter_segments_by_thickness(final_segments, export_wall_thicknesses)

# Do not remove short centerline fragments before topology repair.
# Small return walls and short corridor pieces are often the exact segments that close rooms.
analysis_centerline_segments = list(selected_final_segments)

with st.spinner("Separating plan regions..."):
    if auto_separate_regions:
        regions, region_segments = split_segments_into_regions(
            analysis_centerline_segments,
            axis_tol=axis_tol,
            region_gap=region_gap,
        )
    else:
        region_segments = []
        for s in analysis_centerline_segments:
            ss = copy_segment(s)
            ss["region_id"] = 1
            region_segments.append(ss)

        if region_segments:
            xs = []
            ys = []
            for s in region_segments:
                if s["orientation"] == "H":
                    xs.extend([s["a"], s["b"]])
                    ys.append(s["c"])
                else:
                    xs.append(s["c"])
                    ys.extend([s["a"], s["b"]])
            regions = [{
                "region_id": 1,
                "segments": region_segments,
                "min_x": min(xs),
                "max_x": max(xs),
                "min_y": min(ys),
                "max_y": max(ys),
                "width": max(xs) - min(xs),
                "height": max(ys) - min(ys),
                "segment_count": len(region_segments),
            }]
        else:
            regions = []

if not regions:
    st.error("No usable plan regions were created after filtering. Lower the output length filter or check wall thickness selection.")
    st.stop()

source_regions = copy_regions(regions)
source_region_segments = [copy_segment(s) for s in region_segments]
topology_stats = []

with st.spinner("Repairing centerline graph into connected room topology..."):
    if repair_centerline_graph:
        regions, region_segments, topology_stats = repair_regions_topology(
            regions,
            axis_tol=axis_tol,
            gap_tol=graph_gap_tol,
            connect_tol=graph_connect_tol,
        )
    else:
        topology_stats = [
            {
                "region_id": r["region_id"],
                "segments_before": len(r.get("segments", [])),
                "segments_after": len(r.get("segments", [])),
                "added_or_merged_delta": 0,
            }
            for r in regions
        ]

output_wall_segments = source_region_segments if preserve_source_geometry else region_segments
output_regions_for_preview = source_regions if preserve_source_geometry else regions

with st.spinner("Building smart structural grid and economical columns..."):
    structural_layouts = []
    accepted_columns = []
    rejected_columns = []

    if generate_columns:
        structural_layouts, accepted_columns, rejected_columns = build_structural_layout_for_regions(
            regions=regions,
            axis_tol=axis_tol,
            strategy=column_layout_strategy,
            max_spacing=max_column_spacing,
            target_spacing=target_column_spacing,
            min_spacing=min_structural_axis_spacing,
            floor_count=floor_count,
            min_column_spacing=min_column_spacing,
            node_tol=structural_node_tol,
        )
    else:
        for region in regions:
            x_infos, y_infos, h_map, v_map = build_structural_axis_infos(region, axis_tol)
            structural_layouts.append({
                "region_id": region["region_id"],
                "selected_x_infos": [],
                "selected_y_infos": [],
                "selected_x_values": [],
                "selected_y_values": [],
                "all_x_axis_count": len(x_infos),
                "all_y_axis_count": len(y_infos),
                "accepted_candidates": [],
                "rejected_candidates": [],
                "warnings": [],
                "bounds": {
                    "min_x": region["min_x"],
                    "max_x": region["max_x"],
                    "min_y": region["min_y"],
                    "max_y": region["max_y"],
                },
                "h_map": h_map,
                "v_map": v_map,
            })

with st.spinner("Creating slab panels..."):
    panels = []

    if slab_panel_method in ["Wall-bounded graph panels", "Both"]:
        for region in regions:
            panels.extend(
                detect_graph_slab_panels(
                    region["segments"],
                    axis_tol=axis_tol,
                    closure_tol=panel_closure_tol,
                    min_panel_width=min_panel_width,
                    min_panel_height=min_panel_height,
                    max_panel_width=max_panel_width,
                    max_panel_height=max_panel_height,
                    region_id=region["region_id"],
                )
            )

    if slab_panel_method in ["Structural grid panels", "Both"]:
        panels.extend(
            detect_structural_grid_panels(
                structural_layouts,
                accepted_columns,
                axis_tol=axis_tol,
                min_width=min_panel_width,
                min_height=min_panel_height,
                max_width=max_panel_width,
                max_height=max_panel_height,
                closure_tol=panel_closure_tol,
            )
        )

    panels = dedupe_panels(panels, tol=max(1.0, axis_tol))
    nodes = collect_junction_nodes(output_wall_segments, axis_tol=axis_tol)


# =========================================================
# SUMMARY
# =========================================================

st.markdown("### 4. Accuracy / Structural Layout Summary")

a1, a2, a3, a4 = st.columns(4)
a1.metric("Raw midpoint centerlines", len(raw_segments))
a2.metric("Avg thickness error", round(accuracy["avg_thickness_error"], 3))
a3.metric("Max thickness error", round(accuracy["max_thickness_error"], 3))
a4.metric("Near-perfect <= 1mm", accuracy["perfect_or_near_perfect"])

r1, r2, r3, r4 = st.columns(4)
r1.metric("Detected plan regions", len(output_regions_for_preview))
r2.metric("Output wall centerlines", len(output_wall_segments))
r3.metric("Accepted columns", len(accepted_columns))
r4.metric("Slab panels", len(panels))

c1, c2, c3, c4 = st.columns(4)
c1.metric("After gap bridging", len(merged_segments))
c2.metric("After extension", len(extended_segments))
c3.metric("Removed overlaps", len(removed_overlap_segments))
c4.metric("Rejected column candidates", len(rejected_columns))

t1, t2, t3 = st.columns(3)
t1.metric("Topology graph segments", len(region_segments))
t2.metric("Centerline graph repair", "ON" if repair_centerline_graph else "OFF")
t3.metric("Source geometry lock", "ON" if preserve_source_geometry else "OFF")

warnings = []
for layout in structural_layouts:
    warnings.extend([f"Region {layout['region_id']}: {w}" for w in layout.get("warnings", [])])

if warnings:
    st.warning("Structural layout warnings: " + " | ".join(warnings))

st.info(
    f"Design profile: **{design_code}**. "
    f"Floors: **{floor_count}**. "
    f"Column strategy: **{column_layout_strategy}**. "
    f"Column: **{column_shape} {column_size_label(column_shape, column_width, column_depth)}**. "
    f"Output mode: **{output_mode}**. "
    f"Panel method: **{slab_panel_method}**. "
    f"Source geometry preservation: **{'ON' if preserve_source_geometry else 'OFF'}**."
)


# =========================================================
# PREVIEW TABLES
# =========================================================

st.markdown("### 5. Preview Tables")

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs(
    [
        "Regions",
        "Structural Grid",
        "Topology Repair",
        "Raw Centerlines",
        "Output Wall Centerlines",
        "Removed Overlaps",
        "Accepted Columns",
        "Rejected Columns",
        "Slab Panels",
    ]
)

with tab1:
    regions_df = regions_to_dataframe(output_regions_for_preview)
    if regions_df.empty:
        st.info("No regions.")
    else:
        st.dataframe(regions_df, use_container_width=True)
        st.download_button(
            "Download Regions CSV",
            data=regions_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_PLAN_REGIONS.csv",
            mime="text/csv",
        )

with tab2:
    layout_df = structural_layouts_to_dataframe(structural_layouts)
    if layout_df.empty:
        st.info("No structural grid layout.")
    else:
        st.dataframe(layout_df, use_container_width=True)
        st.download_button(
            "Download Structural Grid CSV",
            data=layout_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_STRUCTURAL_GRID_REVIEW.csv",
            mime="text/csv",
        )

with tab3:
    topology_df = pd.DataFrame(topology_stats)
    if topology_df.empty:
        st.info("No topology repair data.")
    else:
        st.dataframe(topology_df, use_container_width=True)
        st.download_button(
            "Download Topology Repair CSV",
            data=topology_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_TOPOLOGY_REPAIR.csv",
            mime="text/csv",
        )

with tab4:
    raw_df = segments_to_dataframe(raw_segments)
    if raw_df.empty:
        st.info("No raw centerlines.")
    else:
        st.dataframe(raw_df, use_container_width=True)
        st.download_button(
            "Download Raw Centerlines CSV",
            data=raw_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_RAW_ACCURATE_CENTERLINES.csv",
            mime="text/csv",
        )

with tab5:
    final_df = segments_to_dataframe(output_wall_segments)
    if final_df.empty:
        st.info("No output wall centerlines.")
    else:
        st.dataframe(final_df, use_container_width=True)
        st.download_button(
            "Download Output Wall Centerlines CSV",
            data=final_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_OUTPUT_WALL_CENTERLINES.csv",
            mime="text/csv",
        )

with tab6:
    removed_df = removed_segments_to_dataframe(removed_overlap_segments)
    if removed_df.empty:
        st.info("No overlapping centerlines removed.")
    else:
        st.dataframe(removed_df, use_container_width=True)
        st.download_button(
            "Download Removed Overlaps CSV",
            data=removed_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_REMOVED_OVERLAPS.csv",
            mime="text/csv",
        )

with tab7:
    accepted_columns_df = columns_to_dataframe(accepted_columns)
    if accepted_columns_df.empty:
        st.info("No accepted columns.")
    else:
        st.warning("Column suggestions are preliminary review geometry only.")
        st.dataframe(accepted_columns_df, use_container_width=True)
        st.download_button(
            "Download Accepted Columns CSV",
            data=accepted_columns_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_ACCEPTED_COLUMNS_REVIEW.csv",
            mime="text/csv",
        )

with tab8:
    rejected_columns_df = columns_to_dataframe(rejected_columns)
    if rejected_columns_df.empty:
        st.info("No rejected columns.")
    else:
        st.dataframe(rejected_columns_df, use_container_width=True)
        st.download_button(
            "Download Rejected Columns CSV",
            data=rejected_columns_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_REJECTED_COLUMNS.csv",
            mime="text/csv",
        )

with tab9:
    panels_df = panels_to_dataframe(panels)
    if panels_df.empty:
        st.info("No slab panels detected.")
    else:
        st.warning("Slab panels are review geometry. Verify all spans and edge conditions before structural modelling.")
        st.dataframe(panels_df, use_container_width=True)
        st.download_button(
            "Download Slab Panels CSV",
            data=panels_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_SLAB_PANELS_REVIEW.csv",
            mime="text/csv",
        )


# =========================================================
# DOWNLOAD DXF
# =========================================================

st.markdown("### 6. Download Structural Review DXF")

if output_mode == "Review mode":
    draw_raw_output = True
    clean_length = 0.0
else:
    draw_raw_output = False
    clean_length = min_output_centerline_length

output_doc = build_output_dxf(
    raw_segments=raw_segments,
    final_segments=output_wall_segments,
    source_segments=source_region_segments,
    topology_segments=region_segments,
    panels=panels,
    nodes=nodes,
    columns=accepted_columns,
    layouts=structural_layouts,
    column_width=column_width,
    column_depth=column_depth,
    column_shape=column_shape,
    axis_tol=axis_tol,
    draw_raw=draw_raw_output,
    draw_source_reference=export_source_reference,
    draw_topology_audit=export_topology_audit,
    draw_wall_centerlines=True,
    draw_panels_enabled=export_slab_panels,
    draw_nodes_enabled=export_junction_nodes,
    draw_columns_enabled=export_columns,
    draw_structural_grid_enabled=export_structural_grid,
    min_output_centerline_length=clean_length,
    export_thicknesses=export_wall_thicknesses,
)

output_bytes = write_doc_to_bytes(output_doc)
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

if output_mode == "Clean structural mode":
    output_filename = f"ILS_SMART_STRUCTURAL_LAYOUT_{timestamp}.dxf"
else:
    output_filename = f"ILS_REVIEW_STRUCTURAL_LAYOUT_{timestamp}.dxf"

st.download_button(
    "Download Connected Centerlines + Columns + Room/Slab Polylines DXF",
    data=output_bytes,
    file_name=output_filename,
    mime="application/dxf",
)

st.success(
    "Analysis complete. The DXF now exports a repaired centerline graph, support-grid columns, and closed room/slab panel polylines for modelling review."
)

if __name__ == "__main__":
    run()
