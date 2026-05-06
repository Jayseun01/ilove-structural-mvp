import streamlit as st
import ezdxf
import tempfile
import os
import math
import datetime
import pandas as pd


# =========================================================
# STREAMLIT CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - Architectural Wall Centerline Agent",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ iLoveStructural")
st.subheader("Tool 4: Architectural Wall Centerline Agent")
st.caption(
    "Upload architectural DXF → detect accurate wall centerlines → clean overlaps → suggest 225x225 columns → export structural review DXF."
)


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
        doc = ezdxf.readfile(tmp_path)
        return doc

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
        return 1  # red
    if t == 150:
        return 3  # green

    return 7


# =========================================================
# GEOMETRY HELPERS
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
    """
    Wall face line format.

    Horizontal:
        orientation = H
        c = y
        a/b = x range

    Vertical:
        orientation = V
        c = x
        a/b = y range
    """

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
):
    """
    Centerline segment format.

    Horizontal:
        orientation = H
        c = y
        a/b = x range

    Vertical:
        orientation = V
        c = x
        a/b = y range

    Important:
    The center coordinate is calculated exactly as midpoint between two wall faces.
    """

    if actual_thickness is None:
        actual_thickness = thickness

    if thickness_error is None:
        thickness_error = abs(float(actual_thickness) - float(thickness))

    if overlap_length is None:
        overlap_length = abs(float(b) - float(a))

    length = abs(float(b) - float(a))

    # Higher is better.
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
    }


def segment_length(seg):
    return abs(float(seg["b"]) - float(seg["a"]))


def filter_short_segments(segments, min_length):
    if min_length <= 0:
        return list(segments)

    return [
        s for s in segments
        if segment_length(s) >= min_length
    ]


def filter_segments_by_thickness(segments, allowed_thicknesses):
    if not allowed_thicknesses:
        return []

    allowed = set(int(x) for x in allowed_thicknesses)

    return [
        s for s in segments
        if int(s.get("thickness", 0)) in allowed
    ]


def overlap_range(a1, b1, a2, b2):
    start = max(min(a1, b1), min(a2, b2))
    end = min(max(a1, b1), max(a2, b2))
    return start, end


def interval_overlap_length(a1, b1, a2, b2):
    start, end = overlap_range(a1, b1, a2, b2)
    return max(0.0, end - start)


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
    }


def entity_handle(entity):
    try:
        return str(entity.dxf.handle)
    except Exception:
        return ""


def point_distance(p1, p2):
    return math.dist((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))


# =========================================================
# DXF WALL FACE EXTRACTION
# =========================================================

def extract_line_entities(doc, selected_layers, use_all_layers, ortho_tol, min_line_length):
    """
    Extract horizontal and vertical wall face segments.

    Supported:
    - LINE
    - LWPOLYLINE
    - POLYLINE

    Ignored:
    - arcs
    - splines
    - hatches
    - solids
    - non-orthogonal segments
    """

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

            horizontal.append(
                make_face_line(
                    orientation="H",
                    c=y,
                    a=x1,
                    b=x2,
                    layer=layer,
                    source_type=source_type,
                    handle=handle,
                )
            )

        elif is_vertical_segment(x1, y1, x2, y2, ortho_tol):
            x = (x1 + x2) / 2.0

            vertical.append(
                make_face_line(
                    orientation="V",
                    c=x,
                    a=y1,
                    b=y2,
                    layer=layer,
                    source_type=source_type,
                    handle=handle,
                )
            )

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
                    layer=layer,
                    source_type="LINE",
                    handle=entity_handle(e),
                )

            elif dxftype == "LWPOLYLINE":
                pts = []

                try:
                    for p in e.get_points():
                        pts.append((float(p[0]), float(p[1])))
                except Exception:
                    pts = []

                if len(pts) >= 2:
                    for i in range(len(pts) - 1):
                        x1, y1 = pts[i]
                        x2, y2 = pts[i + 1]

                        add_segment(
                            x1,
                            y1,
                            x2,
                            y2,
                            layer=layer,
                            source_type="LWPOLYLINE",
                            handle=entity_handle(e),
                        )

                    try:
                        if e.closed:
                            x1, y1 = pts[-1]
                            x2, y2 = pts[0]

                            add_segment(
                                x1,
                                y1,
                                x2,
                                y2,
                                layer=layer,
                                source_type="LWPOLYLINE_CLOSED",
                                handle=entity_handle(e),
                            )
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

                if len(pts) >= 2:
                    for i in range(len(pts) - 1):
                        x1, y1 = pts[i]
                        x2, y2 = pts[i + 1]

                        add_segment(
                            x1,
                            y1,
                            x2,
                            y2,
                            layer=layer,
                            source_type="POLYLINE",
                            handle=entity_handle(e),
                        )

                    try:
                        if e.is_closed:
                            x1, y1 = pts[-1]
                            x2, y2 = pts[0]

                            add_segment(
                                x1,
                                y1,
                                x2,
                                y2,
                                layer=layer,
                                source_type="POLYLINE_CLOSED",
                                handle=entity_handle(e),
                            )
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
# RAW CENTERLINE DETECTION
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

    # Horizontal wall faces generate horizontal centerlines.
    for thickness in wall_thicknesses:
        for i, l1 in enumerate(horizontal_faces):
            for l2 in horizontal_faces[i + 1:]:
                actual_thickness = abs(l1["c"] - l2["c"])
                thickness_error = abs(actual_thickness - thickness)

                if thickness_error > thickness_tol:
                    continue

                start, end = overlap_range(
                    l1["a"],
                    l1["b"],
                    l2["a"],
                    l2["b"],
                )

                face_overlap = end - start

                if face_overlap < min_overlap:
                    continue

                center_y = (l1["c"] + l2["c"]) / 2.0

                raw_segments.append(
                    make_centerline_segment(
                        orientation="H",
                        c=center_y,
                        a=start,
                        b=end,
                        thickness=thickness,
                        actual_thickness=actual_thickness,
                        thickness_error=thickness_error,
                        overlap_length=face_overlap,
                    )
                )

    # Vertical wall faces generate vertical centerlines.
    for thickness in wall_thicknesses:
        for i, l1 in enumerate(vertical_faces):
            for l2 in vertical_faces[i + 1:]:
                actual_thickness = abs(l1["c"] - l2["c"])
                thickness_error = abs(actual_thickness - thickness)

                if thickness_error > thickness_tol:
                    continue

                start, end = overlap_range(
                    l1["a"],
                    l1["b"],
                    l2["a"],
                    l2["b"],
                )

                face_overlap = end - start

                if face_overlap < min_overlap:
                    continue

                center_x = (l1["c"] + l2["c"]) / 2.0

                raw_segments.append(
                    make_centerline_segment(
                        orientation="V",
                        c=center_x,
                        a=start,
                        b=end,
                        thickness=thickness,
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
    """
    Merge same-axis segments if they overlap or have a small gap.
    This bridges centerlines across doors/windows/openings.
    """

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
                key=lambda s: float(s.get("thickness_error", 0.0))
            )[0].get("actual_thickness", thickness)

            total_overlap = sum(float(s.get("overlap_length", segment_length(s))) for s in cur_members)
            source_count = sum(int(s.get("source_count", 1)) for s in cur_members)

            return make_centerline_segment(
                orientation=orientation,
                c=avg_c,
                a=cur_a,
                b=cur_b,
                thickness=thickness,
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
    """
    Extend horizontal/vertical centerlines to nearby intersections.

    Extension changes endpoint length, not the centerline axis position.
    """

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
    """
    Higher priority segment wins when overlaps are detected.

    Priority:
    1. Better thickness match
    2. Larger original face overlap
    3. Longer final segment
    4. Prefer 225 over 150 if still tied
    """

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
    """
    Remove duplicate/overlapping centerlines on nearly the same axis.

    This mainly fixes cases where 225 and 150 detections create overlapping
    red/green lines in the same location.
    """

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
            removed.append({
                **candidate,
                **duplicate_info,
            })
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
# COLUMN SUGGESTION MODULE
# =========================================================

def segment_contains_axis_value(seg, value, tol):
    return (seg["a"] - tol) <= value <= (seg["b"] + tol)


def segment_side_lengths_at_point(seg, point_coord):
    """
    Returns available segment length on both sides of an intersection point.

    For H segment:
        point_coord = x

    For V segment:
        point_coord = y
    """

    left_or_down = max(0.0, float(point_coord) - float(seg["a"]))
    right_or_up = max(0.0, float(seg["b"]) - float(point_coord))

    return left_or_down, right_or_up


def classify_column_junction(h_seg, v_seg, x, y, min_leg_length):
    """
    Classify intersection as:
    - cross
    - tee
    - corner
    - weak

    Based on whether there is enough centerline length in each direction.
    """

    h_left, h_right = segment_side_lengths_at_point(h_seg, x)
    v_down, v_up = segment_side_lengths_at_point(v_seg, y)

    directions = {
        "left": h_left >= min_leg_length,
        "right": h_right >= min_leg_length,
        "down": v_down >= min_leg_length,
        "up": v_up >= min_leg_length,
    }

    direction_count = sum(1 for v in directions.values() if v)

    if direction_count >= 4:
        junction_type = "cross"
    elif direction_count == 3:
        junction_type = "tee"
    elif direction_count == 2:
        junction_type = "corner"
    else:
        junction_type = "weak"

    return junction_type, direction_count, directions


def column_candidate_score(
    h_seg,
    v_seg,
    junction_type,
    direction_count,
):
    """
    Score a possible column location.

    Higher score means more likely to keep after proximity filtering.

    Priority:
    - 225/225 intersection strongest
    - 225/150 mixed next
    - 150/150 lower
    - cross/tee better than weak endpoint
    - longer connected walls better
    """

    h_t = int(h_seg.get("thickness", 0))
    v_t = int(v_seg.get("thickness", 0))

    thickness_pair = tuple(sorted([h_t, v_t], reverse=True))

    if h_t == 225 and v_t == 225:
        thickness_score = 1000
    elif h_t == 225 or v_t == 225:
        thickness_score = 700
    elif h_t == 150 and v_t == 150:
        thickness_score = 300
    else:
        thickness_score = 100

    if junction_type == "cross":
        junction_score = 400
    elif junction_type == "tee":
        junction_score = 300
    elif junction_type == "corner":
        junction_score = 200
    else:
        junction_score = 50

    length_score = min(segment_length(h_seg), segment_length(v_seg)) * 0.05
    total_length_score = (segment_length(h_seg) + segment_length(v_seg)) * 0.01

    thickness_error_penalty = (
        float(h_seg.get("thickness_error", 0.0))
        + float(v_seg.get("thickness_error", 0.0))
    ) * 50.0

    score = (
        thickness_score
        + junction_score
        + direction_count * 50
        + length_score
        + total_length_score
        - thickness_error_penalty
    )

    return score, thickness_pair


def detect_column_candidates(
    segments,
    axis_tol,
    column_candidate_thicknesses,
    allow_150_150=False,
    allow_mixed_225_150=True,
    min_leg_length=450.0,
):
    """
    Detect candidate column locations from final wall centerline intersections.

    Columns are snapped to the exact intersection of a horizontal and vertical
    wall centerline axis.

    Review-only structural heuristic:
    - Not final structural design.
    - Engineer must review.
    """

    allowed = set(int(x) for x in column_candidate_thicknesses or [])

    h_segments, v_segments = split_hv_segments(segments)

    candidates = []

    for h in h_segments:
        if allowed and int(h.get("thickness", 0)) not in allowed:
            continue

        hy = h["c"]

        for v in v_segments:
            if allowed and int(v.get("thickness", 0)) not in allowed:
                continue

            vx = v["c"]

            # Intersection candidate is (vx, hy).
            if not segment_contains_axis_value(h, vx, axis_tol):
                continue

            if not segment_contains_axis_value(v, hy, axis_tol):
                continue

            h_t = int(h.get("thickness", 0))
            v_t = int(v.get("thickness", 0))

            if h_t == 150 and v_t == 150 and not allow_150_150:
                continue

            if {h_t, v_t} == {225, 150} and not allow_mixed_225_150:
                continue

            junction_type, direction_count, directions = classify_column_junction(
                h,
                v,
                vx,
                hy,
                min_leg_length=min_leg_length,
            )

            if junction_type == "weak":
                continue

            score, thickness_pair = column_candidate_score(
                h,
                v,
                junction_type,
                direction_count,
            )

            candidates.append({
                "x": float(vx),
                "y": float(hy),
                "score": float(score),
                "junction_type": junction_type,
                "direction_count": int(direction_count),
                "h_thickness": h_t,
                "v_thickness": v_t,
                "thickness_pair": f"{thickness_pair[0]}/{thickness_pair[1]}",
                "h_length": segment_length(h),
                "v_length": segment_length(v),
                "left": directions["left"],
                "right": directions["right"],
                "down": directions["down"],
                "up": directions["up"],
            })

    candidates = dedupe_column_candidates(
        candidates,
        point_tol=axis_tol,
    )

    return candidates


def dedupe_column_candidates(candidates, point_tol):
    """
    Merge duplicate candidate points.
    Keeps highest scoring candidate within point_tol.
    """

    if not candidates:
        return []

    sorted_candidates = sorted(
        candidates,
        key=lambda c: c["score"],
        reverse=True,
    )

    kept = []

    for cand in sorted_candidates:
        exists = False

        for existing in kept:
            if point_distance((cand["x"], cand["y"]), (existing["x"], existing["y"])) <= point_tol:
                exists = True
                break

        if not exists:
            kept.append(cand)

    return sorted(
        kept,
        key=lambda c: (c["x"], c["y"]),
    )


def apply_column_proximity_filter(
    candidates,
    min_column_spacing,
):
    """
    Keep stronger column candidates and remove weaker candidates nearby.

    This is the key rule:
    Do not place columns at every junction.
    """

    if not candidates:
        return [], []

    sorted_candidates = sorted(
        candidates,
        key=lambda c: c["score"],
        reverse=True,
    )

    accepted = []
    rejected = []

    for cand in sorted_candidates:
        too_close_to = None
        too_close_distance = None

        for acc in accepted:
            d = point_distance(
                (cand["x"], cand["y"]),
                (acc["x"], acc["y"]),
            )

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
                "reject_reason": "too_close_to_stronger_column_candidate",
                "nearest_accepted_x": round(too_close_to["x"], 3),
                "nearest_accepted_y": round(too_close_to["y"], 3),
                "nearest_accepted_distance": round(too_close_distance, 3),
            })

    accepted = sorted(
        accepted,
        key=lambda c: (c["x"], c["y"]),
    )

    rejected = sorted(
        rejected,
        key=lambda c: (c["x"], c["y"]),
    )

    return accepted, rejected


def columns_to_dataframe(columns):
    rows = []

    for idx, c in enumerate(columns, start=1):
        rows.append({
            "column_id": idx,
            "x": round(float(c["x"]), 3),
            "y": round(float(c["y"]), 3),
            "score": round(float(c["score"]), 3),
            "junction_type": c.get("junction_type", ""),
            "direction_count": c.get("direction_count", ""),
            "h_thickness": c.get("h_thickness", ""),
            "v_thickness": c.get("v_thickness", ""),
            "thickness_pair": c.get("thickness_pair", ""),
            "h_length": round(float(c.get("h_length", 0.0)), 3),
            "v_length": round(float(c.get("v_length", 0.0)), 3),
            "left": c.get("left", ""),
            "right": c.get("right", ""),
            "down": c.get("down", ""),
            "up": c.get("up", ""),
            "accepted": c.get("accepted", ""),
            "reject_reason": c.get("reject_reason", ""),
            "nearest_accepted_x": c.get("nearest_accepted_x", ""),
            "nearest_accepted_y": c.get("nearest_accepted_y", ""),
            "nearest_accepted_distance": c.get("nearest_accepted_distance", ""),
        })

    return pd.DataFrame(rows)


# =========================================================
# PANEL DETECTION - REVIEW ONLY
# =========================================================

def cluster_values(values, tol):
    if not values:
        return []

    values = sorted(values)
    clusters = []
    current = [values[0]]

    for v in values[1:]:
        avg = sum(current) / len(current)

        if abs(v - avg) <= tol:
            current.append(v)
        else:
            clusters.append(sum(current) / len(current))
            current = [v]

    clusters.append(sum(current) / len(current))
    return clusters


def horizontal_covers(h_segments, y, x1, x2, tol):
    for h in h_segments:
        if abs(h["c"] - y) > tol:
            continue

        if h["a"] <= x1 + tol and h["b"] >= x2 - tol:
            return True

    return False


def vertical_covers(v_segments, x, y1, y2, tol):
    for v in v_segments:
        if abs(v["c"] - x) > tol:
            continue

        if v["a"] <= y1 + tol and v["b"] >= y2 - tol:
            return True

    return False


def detect_rectangular_panels(segments, axis_tol, min_panel_width, min_panel_height):
    """
    Review-only rectangular panel detector.
    """

    h_segments, v_segments = split_hv_segments(segments)

    xs = cluster_values([v["c"] for v in v_segments], axis_tol)
    ys = cluster_values([h["c"] for h in h_segments], axis_tol)

    panels = []

    if len(xs) < 2 or len(ys) < 2:
        return panels

    for i in range(len(xs) - 1):
        x1 = xs[i]
        x2 = xs[i + 1]

        if abs(x2 - x1) < min_panel_width:
            continue

        for j in range(len(ys) - 1):
            y1 = ys[j]
            y2 = ys[j + 1]

            if abs(y2 - y1) < min_panel_height:
                continue

            bottom = horizontal_covers(h_segments, y1, x1, x2, axis_tol)
            top = horizontal_covers(h_segments, y2, x1, x2, axis_tol)
            left = vertical_covers(v_segments, x1, y1, y2, axis_tol)
            right = vertical_covers(v_segments, x2, y1, y2, axis_tol)

            if bottom and top and left and right:
                panels.append({
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "width": abs(x2 - x1),
                    "height": abs(y2 - y1),
                    "area": abs(x2 - x1) * abs(y2 - y1),
                })

    return panels


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

        msp.add_line(
            (seg["a"], y),
            (seg["b"], y),
            dxfattribs={"layer": layer},
        )

    elif seg["orientation"] == "V":
        x = seg["c"]

        msp.add_line(
            (x, seg["a"]),
            (x, seg["b"]),
            dxfattribs={"layer": layer},
        )


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

        msp.add_lwpolyline(
            points,
            close=True,
            dxfattribs={"layer": layer},
        )


def draw_nodes(msp, nodes, radius, layer):
    for x, y in nodes:
        msp.add_circle(
            center=(x, y),
            radius=radius,
            dxfattribs={"layer": layer},
        )


def draw_column_square(msp, x, y, size, layer):
    half = float(size) / 2.0

    points = [
        (x - half, y - half),
        (x + half, y - half),
        (x + half, y + half),
        (x - half, y + half),
        (x - half, y - half),
    ]

    msp.add_lwpolyline(
        points,
        close=True,
        dxfattribs={"layer": layer},
    )

    # Small center cross for review.
    cross = min(float(size) * 0.35, 100.0)

    msp.add_line(
        (x - cross, y),
        (x + cross, y),
        dxfattribs={"layer": layer},
    )

    msp.add_line(
        (x, y - cross),
        (x, y + cross),
        dxfattribs={"layer": layer},
    )


def draw_columns(msp, columns, column_size, layer):
    for c in columns:
        draw_column_square(
            msp,
            float(c["x"]),
            float(c["y"]),
            float(column_size),
            layer,
        )


def build_output_dxf(
    raw_segments,
    final_segments,
    panels,
    nodes,
    columns,
    column_size,
    draw_raw=True,
    draw_wall_centerlines=True,
    draw_panels_enabled=False,
    draw_nodes_enabled=False,
    draw_columns_enabled=True,
    min_output_centerline_length=0,
    export_thicknesses=None,
):
    """
    Build exported DXF.
    """

    new_doc = ezdxf.new()
    new_msp = new_doc.modelspace()

    export_thicknesses = export_thicknesses or []

    raw_for_output = filter_segments_by_thickness(
        raw_segments,
        export_thicknesses,
    )

    final_for_output = filter_segments_by_thickness(
        final_segments,
        export_thicknesses,
    )

    clean_final_segments = filter_short_segments(
        final_for_output,
        min_output_centerline_length,
    )

    all_thicknesses = sorted(
        set(
            [int(s["thickness"]) for s in raw_for_output]
            + [int(s["thickness"]) for s in clean_final_segments]
        )
    )

    for thickness in all_thicknesses:
        safe_layer(
            new_doc,
            f"ILS_RAW_CENTERLINE_{thickness}",
            color=8,
        )

        safe_layer(
            new_doc,
            f"ILS_WALL_CENTERLINE_{thickness}",
            color=layer_color_for_thickness(thickness),
        )

    safe_layer(new_doc, "ILS_SLAB_PANEL_REVIEW", color=5)
    safe_layer(new_doc, "ILS_JUNCTION_NODE_REVIEW", color=2)
    safe_layer(new_doc, f"ILS_COLUMN_{int(column_size)}X{int(column_size)}_REVIEW", color=6)

    if draw_raw:
        draw_segments_by_thickness(
            new_msp,
            raw_for_output,
            prefix="ILS_RAW_CENTERLINE",
        )

    if draw_wall_centerlines:
        draw_segments_by_thickness(
            new_msp,
            clean_final_segments,
            prefix="ILS_WALL_CENTERLINE",
        )

    if draw_panels_enabled:
        draw_panels(
            new_msp,
            panels,
            layer="ILS_SLAB_PANEL_REVIEW",
        )

    if draw_nodes_enabled:
        draw_nodes(
            new_msp,
            nodes,
            radius=50,
            layer="ILS_JUNCTION_NODE_REVIEW",
        )

    if draw_columns_enabled:
        draw_columns(
            new_msp,
            columns,
            column_size=column_size,
            layer=f"ILS_COLUMN_{int(column_size)}X{int(column_size)}_REVIEW",
        )

    return new_doc


# =========================================================
# DATAFRAME HELPERS
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
            "orientation": s["orientation"],
            "thickness": s["thickness"],
            "actual_thickness": round(float(s.get("actual_thickness", s["thickness"])), 3),
            "thickness_error": round(float(s.get("thickness_error", 0.0)), 3),
            "overlap_length": round(float(s.get("overlap_length", segment_length(s))), 3),
            "quality_score": round(float(s.get("quality_score", 0.0)), 3),
            "removed_reason": s.get("removed_reason", ""),
            "overlap_with_thickness": s.get("overlap_with_thickness", ""),
            "overlap_amount": s.get("overlap_amount", ""),
            "overlap_ratio": s.get("overlap_ratio", ""),
            "x1": round(x1, 3),
            "y1": round(y1, 3),
            "x2": round(x2, 3),
            "y2": round(y2, 3),
            "length": round(segment_length(s), 3),
        })

    return pd.DataFrame(rows)


def panels_to_dataframe(panels):
    rows = []

    for idx, p in enumerate(panels, start=1):
        rows.append({
            "panel_id": idx,
            "x1": round(p["x1"], 3),
            "y1": round(p["y1"], 3),
            "x2": round(p["x2"], 3),
            "y2": round(p["y2"], 3),
            "width": round(p["width"], 3),
            "height": round(p["height"], 3),
            "area": round(p["area"], 3),
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

    near_perfect = len([
        e for e in errors
        if e <= 1.0
    ])

    return {
        "raw_count": len(raw_segments),
        "avg_thickness_error": sum(errors) / len(errors),
        "max_thickness_error": max(errors),
        "perfect_or_near_perfect": near_perfect,
    }


# =========================================================
# STREAMLIT UI
# =========================================================

st.info(
    "This MVP works best when architectural walls are drawn as LINE, LWPOLYLINE, or POLYLINE wall faces. "
    "It currently focuses on orthogonal walls: horizontal and vertical."
)

st.warning(
    "Column suggestions are preliminary review geometry. They are not final structural design. "
    "An engineer must review column locations, spans, loads, stability, and code requirements."
)

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


st.markdown("### 2. Detection Settings")

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

    wall_thickness_text = c1.text_input(
        "Wall thicknesses to detect",
        value="225,150",
        help="Comma-separated wall thicknesses in drawing units, usually mm.",
    )

    thickness_tol = c2.number_input(
        "Wall thickness tolerance",
        min_value=0.0,
        value=10.0,
        step=1.0,
        help="Allowed drafting error when checking wall face spacing.",
    )

    ortho_tol = c3.number_input(
        "Orthogonal tolerance",
        min_value=0.0,
        value=1.0,
        step=0.5,
        help="How much deviation is allowed for a line to count as horizontal/vertical.",
    )

    c4, c5, c6 = st.columns(3)

    min_line_length = c4.number_input(
        "Minimum wall face line length",
        min_value=0.0,
        value=100.0,
        step=50.0,
        help="Shorter wall face line pieces are ignored.",
    )

    min_overlap = c5.number_input(
        "Minimum wall face overlap",
        min_value=0.0,
        value=100.0,
        step=50.0,
        help="Two wall faces must overlap by at least this amount to form a centerline.",
    )

    axis_tol = c6.number_input(
        "Axis merge tolerance",
        min_value=0.0,
        value=20.0,
        step=5.0,
        help="Centerlines with nearly same X/Y coordinate are treated as same axis.",
    )

    c7, c8, c9 = st.columns(3)

    bridge_gap = c7.number_input(
        "Door/window bridge gap",
        min_value=0.0,
        value=1200.0,
        step=100.0,
        help="Collinear centerline pieces separated by less than this gap are joined.",
    )

    intersection_extend_tol = c8.number_input(
        "Intersection extension tolerance",
        min_value=0.0,
        value=300.0,
        step=50.0,
        help="Base tolerance for extending line ends to nearby intersections.",
    )

    extension_mode = c9.selectbox(
        "Intersection extension mode",
        [
            "Off",
            "Conservative",
            "Normal",
            "Aggressive",
        ],
        index=1,
        help="Use Off or Conservative when checking dimensional accuracy. Normal/Aggressive can make modelling lines longer.",
    )


with st.expander("Output mode and cleanup", expanded=True):
    o1, o2, o3 = st.columns(3)

    output_mode = o1.selectbox(
        "Output mode",
        [
            "Review mode",
            "Clean structural mode",
        ],
        index=1,
        help="Review mode exports debug geometry. Clean structural mode exports cleaner modelling geometry.",
    )

    min_output_centerline_length = o2.number_input(
        "Minimum output centerline length",
        min_value=0.0,
        value=750.0,
        step=50.0,
        help="In clean mode, final centerlines shorter than this are not exported.",
    )

    overlap_cleanup_enabled = o3.checkbox(
        "Remove overlapping duplicate centerlines",
        value=True,
        help="Recommended. Removes weaker red/green duplicate overlaps.",
    )

    o4, o5, o6 = st.columns(3)

    overlap_cleanup_ratio = o4.number_input(
        "Overlap cleanup ratio",
        min_value=0.10,
        max_value=1.00,
        value=0.60,
        step=0.05,
        help="A weaker line is removed when this fraction of its length overlaps a better line.",
    )

    overlap_cleanup_min_length = o5.number_input(
        "Minimum duplicate overlap length",
        min_value=0.0,
        value=100.0,
        step=50.0,
        help="Minimum overlap length before duplicate cleanup can remove a line.",
    )

    export_junction_nodes = o6.checkbox(
        "Export junction nodes",
        value=False,
        help="Usually OFF for clean structural output. Useful for review/debug.",
    )


wall_thicknesses = parse_wall_thicknesses(wall_thickness_text)

if not wall_thicknesses:
    st.error("Enter at least one wall thickness, for example: 225,150")
    st.stop()


with st.expander("Wall thickness export", expanded=True):
    export_wall_thicknesses = st.multiselect(
        "Export wall thicknesses",
        options=wall_thicknesses,
        default=wall_thicknesses,
        help="Use this to export only 225 walls, only 150 walls, or both.",
    )

if not export_wall_thicknesses:
    st.warning("Select at least one wall thickness to export.")
    st.stop()


with st.expander("Column suggestion settings", expanded=True):
    col1, col2, col3 = st.columns(3)

    generate_columns = col1.checkbox(
        "Generate column suggestions",
        value=True,
        help="Places preliminary review columns at selected wall centerline junctions.",
    )

    column_size = col2.number_input(
        "Column size",
        min_value=100.0,
        value=225.0,
        step=25.0,
        help="Column square size. Default is 225x225.",
    )

    min_column_spacing = col3.number_input(
        "Minimum column spacing",
        min_value=0.0,
        value=1800.0,
        step=100.0,
        help="Proximity rule: weaker column candidates closer than this to stronger candidates are removed.",
    )

    col4, col5, col6 = st.columns(3)

    default_column_thicknesses = [225] if 225 in wall_thicknesses else wall_thicknesses

    column_candidate_thicknesses = col4.multiselect(
        "Use wall thicknesses for column candidates",
        options=wall_thicknesses,
        default=default_column_thicknesses,
        help="Recommended: start with 225 only. Add 150 if you want mixed 225/150 junctions considered.",
    )

    allow_mixed_225_150 = col5.checkbox(
        "Allow 225/150 mixed junctions",
        value=True,
        help="Allows columns at junctions between 225 and 150 walls if both are selected above.",
    )

    allow_150_150_columns = col6.checkbox(
        "Allow 150/150 junctions",
        value=False,
        help="Usually OFF. 150/150 partition intersections can create too many columns.",
    )

    col7, col8 = st.columns(2)

    min_column_leg_length = col7.number_input(
        "Minimum connected wall leg length",
        min_value=0.0,
        value=450.0,
        step=50.0,
        help="A junction direction must have at least this much wall length to count as a real support leg.",
    )

    export_columns = col8.checkbox(
        "Export columns",
        value=True,
        help="Exports accepted column suggestions as 225x225 boxes on a review layer.",
    )


with st.expander("Slab panel review settings", expanded=False):
    p1, p2, p3 = st.columns(3)

    show_panel_preview = p1.checkbox(
        "Run slab panel review detection",
        value=False,
        help="Experimental. Keep OFF while validating wall centerlines and columns.",
    )

    export_slab_panels = p2.checkbox(
        "Export slab panels",
        value=False,
        help="Experimental/review only. OFF is recommended for clean wall centerline export.",
    )

    min_panel_width = p3.number_input(
        "Minimum panel width",
        min_value=0.0,
        value=500.0,
        step=100.0,
    )

    min_panel_height = st.number_input(
        "Minimum panel height",
        min_value=0.0,
        value=500.0,
        step=100.0,
    )


analyze = st.button(
    "🔎 Analyze Architectural Walls",
    type="primary",
)


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
    st.error(
        "No horizontal/vertical wall face lines found. "
        "Check layer selection or confirm that the drawing uses LINE/LWPOLYLINE/POLYLINE entities."
    )
    st.stop()


with st.spinner("Detecting exact midpoint raw wall centerlines from wall face pairs..."):
    raw_segments = detect_centerlines_from_face_pairs(
        horizontal_faces=horizontal_faces,
        vertical_faces=vertical_faces,
        wall_thicknesses=wall_thicknesses,
        thickness_tol=thickness_tol,
        min_overlap=min_overlap,
    )


accuracy = centerline_accuracy_summary(raw_segments)


with st.spinner("Bridging openings and healing centerlines..."):
    merged_segments = merge_collinear_segments(
        raw_segments,
        axis_tol=axis_tol,
        bridge_gap=bridge_gap,
    )

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


with st.spinner("Cleaning overlapping duplicate centerlines..."):
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


selected_final_segments = filter_segments_by_thickness(
    final_segments,
    export_wall_thicknesses,
)

if output_mode == "Clean structural mode":
    clean_export_segments = filter_short_segments(
        selected_final_segments,
        min_output_centerline_length,
    )
else:
    clean_export_segments = filter_short_segments(
        selected_final_segments,
        0.0,
    )


with st.spinner("Generating preliminary column suggestions..."):
    if generate_columns and column_candidate_thicknesses:
        raw_column_candidates = detect_column_candidates(
            clean_export_segments,
            axis_tol=axis_tol,
            column_candidate_thicknesses=column_candidate_thicknesses,
            allow_150_150=allow_150_150_columns,
            allow_mixed_225_150=allow_mixed_225_150,
            min_leg_length=min_column_leg_length,
        )

        accepted_columns, rejected_columns = apply_column_proximity_filter(
            raw_column_candidates,
            min_column_spacing=min_column_spacing,
        )
    else:
        raw_column_candidates = []
        accepted_columns = []
        rejected_columns = []


with st.spinner("Preparing review geometry..."):
    if show_panel_preview:
        panels = detect_rectangular_panels(
            final_segments,
            axis_tol=axis_tol,
            min_panel_width=min_panel_width,
            min_panel_height=min_panel_height,
        )
    else:
        panels = []

    nodes = collect_junction_nodes(
        final_segments,
        axis_tol=axis_tol,
    )


st.markdown("### 4. Centerline Accuracy and Cleanup Summary")

a1, a2, a3, a4 = st.columns(4)

a1.metric("Raw midpoint centerlines", len(raw_segments))
a2.metric("Avg thickness error", round(accuracy["avg_thickness_error"], 3))
a3.metric("Max thickness error", round(accuracy["max_thickness_error"], 3))
a4.metric("Near-perfect ≤ 1mm", accuracy["perfect_or_near_perfect"])

st.caption(
    "The raw centerline coordinate is calculated exactly as the midpoint between two detected wall faces. "
    "Thickness error measures how close the detected wall-face spacing is to the target wall thickness."
)

r1, r2, r3, r4 = st.columns(4)

r1.metric("After gap bridging", len(merged_segments))
r2.metric("After intersection extension", len(extended_segments))
r3.metric("Removed overlaps", len(removed_overlap_segments))
r4.metric("Final export centerlines", len(clean_export_segments))

c1, c2, c3 = st.columns(3)

c1.metric("Raw column candidates", len(raw_column_candidates))
c2.metric("Accepted columns", len(accepted_columns))
c3.metric("Rejected by proximity", len(rejected_columns))

st.info(
    f"Output mode: **{output_mode}**. "
    f"Extension mode: **{extension_mode}** using effective tolerance **{round(effective_extend_tol, 3)}**. "
    f"Exporting wall thicknesses: **{', '.join(str(x) for x in export_wall_thicknesses)}**. "
    f"Column size: **{int(column_size)}x{int(column_size)}**."
)

if not raw_segments:
    st.warning(
        "No wall centerlines were detected. Try checking wall thickness, tolerance, layer selection, or minimum overlap."
    )


st.markdown("### 5. Preview Tables")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "Raw Accurate Centerlines",
        "Final Clean Centerlines",
        "Removed Overlaps",
        "Accepted Columns",
        "Rejected Column Candidates",
        "Slab Panels Review",
    ]
)

with tab1:
    raw_df = segments_to_dataframe(raw_segments)

    if raw_df.empty:
        st.info("No raw centerlines to preview.")
    else:
        st.dataframe(raw_df, use_container_width=True)

        st.download_button(
            "📄 Download Raw Accurate Centerlines CSV",
            data=raw_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_RAW_ACCURATE_CENTERLINES.csv",
            mime="text/csv",
        )

with tab2:
    clean_df = segments_to_dataframe(clean_export_segments)

    if clean_df.empty:
        st.info("No final clean centerlines to preview.")
    else:
        st.dataframe(clean_df, use_container_width=True)

        st.download_button(
            "📄 Download Final Clean Centerlines CSV",
            data=clean_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_FINAL_CLEAN_CENTERLINES.csv",
            mime="text/csv",
        )

with tab3:
    removed_df = removed_segments_to_dataframe(removed_overlap_segments)

    if removed_df.empty:
        st.info("No overlapping duplicate centerlines were removed.")
    else:
        st.warning(
            "These centerlines were removed because they overlapped better-quality centerlines on the same axis."
        )

        st.dataframe(removed_df, use_container_width=True)

        st.download_button(
            "📄 Download Removed Overlaps CSV",
            data=removed_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_REMOVED_OVERLAP_CENTERLINES.csv",
            mime="text/csv",
        )

with tab4:
    accepted_columns_df = columns_to_dataframe(accepted_columns)

    if accepted_columns_df.empty:
        st.info("No accepted columns.")
    else:
        st.warning(
            "Column positions are preliminary review suggestions. "
            "They are snapped to wall centerline intersections and filtered by proximity."
        )

        st.dataframe(accepted_columns_df, use_container_width=True)

        st.download_button(
            "📄 Download Accepted Columns CSV",
            data=accepted_columns_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_ACCEPTED_COLUMNS_REVIEW.csv",
            mime="text/csv",
        )

with tab5:
    rejected_columns_df = columns_to_dataframe(rejected_columns)

    if rejected_columns_df.empty:
        st.info("No column candidates were rejected by proximity.")
    else:
        st.dataframe(rejected_columns_df, use_container_width=True)

        st.download_button(
            "📄 Download Rejected Column Candidates CSV",
            data=rejected_columns_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_REJECTED_COLUMNS_PROXIMITY.csv",
            mime="text/csv",
        )

with tab6:
    panels_df = panels_to_dataframe(panels)

    if panels_df.empty:
        st.info("No rectangular slab panels detected, or slab panel review detection is turned off.")
    else:
        st.warning(
            "Slab panels are review-only at this stage. "
            "For clean structural use, export wall centerlines and column suggestions first."
        )

        st.dataframe(panels_df, use_container_width=True)

        st.download_button(
            "📄 Download Slab Panels Review CSV",
            data=panels_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_SLAB_PANELS_REVIEW.csv",
            mime="text/csv",
        )


st.markdown("### 6. Download Structural Review DXF")

if output_mode == "Review mode":
    draw_raw_output = True
    draw_nodes_output = export_junction_nodes
    clean_length = 0.0
else:
    draw_raw_output = False
    draw_nodes_output = export_junction_nodes
    clean_length = min_output_centerline_length


output_doc = build_output_dxf(
    raw_segments=raw_segments,
    final_segments=final_segments,
    panels=panels,
    nodes=nodes,
    columns=accepted_columns,
    column_size=column_size,
    draw_raw=draw_raw_output,
    draw_wall_centerlines=True,
    draw_panels_enabled=export_slab_panels,
    draw_nodes_enabled=draw_nodes_output,
    draw_columns_enabled=export_columns,
    min_output_centerline_length=clean_length,
    export_thicknesses=export_wall_thicknesses,
)

output_bytes = write_doc_to_bytes(output_doc)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

if output_mode == "Clean structural mode":
    output_filename = f"ILS_CLEAN_WALL_CENTERLINES_COLUMNS_{timestamp}.dxf"
else:
    output_filename = f"ILS_REVIEW_WALL_CENTERLINES_COLUMNS_{timestamp}.dxf"


st.download_button(
    "📥 Download Wall Centerline + Column Review DXF",
    data=output_bytes,
    file_name=output_filename,
    mime="application/dxf",
)

st.success(
    "Analysis complete. The DXF contains clean wall centerlines and preliminary 225x225 column review boxes. "
    "Open the layer ILS_COLUMN_225X225_REVIEW in AutoCAD to inspect suggested columns."
)
