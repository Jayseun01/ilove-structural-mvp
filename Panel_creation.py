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
    "Upload architectural DXF → detect wall face pairs → generate wall centerlines → bridge openings → detect slab panels → export clean structural DXF."
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


def make_centerline_segment(orientation, c, a, b, thickness):
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
    """

    return {
        "orientation": orientation,
        "c": float(c),
        "a": float(min(a, b)),
        "b": float(max(a, b)),
        "thickness": int(thickness),
    }


def segment_length(seg):
    return abs(float(seg["b"]) - float(seg["a"]))


def filter_short_segments(segments, min_length):
    """
    Remove short centerline fragments from final output.

    Safe:
    - This only affects exported DXF output.
    - It does not change detection/healing calculations.
    """

    if min_length <= 0:
        return list(segments)

    return [
        s for s in segments
        if segment_length(s) >= min_length
    ]


def overlap_range(a1, b1, a2, b2):
    start = max(min(a1, b1), min(a2, b2))
    end = min(max(a1, b1), max(a2, b2))
    return start, end


def copy_segment(seg):
    return {
        "orientation": seg["orientation"],
        "c": float(seg["c"]),
        "a": float(seg["a"]),
        "b": float(seg["b"]),
        "thickness": int(seg["thickness"]),
    }


def entity_handle(entity):
    try:
        return str(entity.dxf.handle)
    except Exception:
        return ""


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

    return values


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
                y_dist = abs(l1["c"] - l2["c"])

                if abs(y_dist - thickness) > thickness_tol:
                    continue

                start, end = overlap_range(
                    l1["a"],
                    l1["b"],
                    l2["a"],
                    l2["b"],
                )

                overlap = end - start

                if overlap < min_overlap:
                    continue

                center_y = (l1["c"] + l2["c"]) / 2.0

                raw_segments.append(
                    make_centerline_segment(
                        orientation="H",
                        c=center_y,
                        a=start,
                        b=end,
                        thickness=thickness,
                    )
                )

    # Vertical wall faces generate vertical centerlines.
    for thickness in wall_thicknesses:
        for i, l1 in enumerate(vertical_faces):
            for l2 in vertical_faces[i + 1:]:
                x_dist = abs(l1["c"] - l2["c"])

                if abs(x_dist - thickness) > thickness_tol:
                    continue

                start, end = overlap_range(
                    l1["a"],
                    l1["b"],
                    l2["a"],
                    l2["b"],
                )

                overlap = end - start

                if overlap < min_overlap:
                    continue

                center_x = (l1["c"] + l2["c"]) / 2.0

                raw_segments.append(
                    make_centerline_segment(
                        orientation="V",
                        c=center_x,
                        a=start,
                        b=end,
                        thickness=thickness,
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
            [(s["a"], s["b"]) for s in group],
            key=lambda x: x[0],
        )

        cur_a, cur_b = intervals[0]

        for a, b in intervals[1:]:
            if a <= cur_b + bridge_gap:
                cur_b = max(cur_b, b)
            else:
                merged.append(
                    make_centerline_segment(
                        orientation=orientation,
                        c=avg_c,
                        a=cur_a,
                        b=cur_b,
                        thickness=thickness,
                    )
                )

                cur_a, cur_b = a, b

        merged.append(
            make_centerline_segment(
                orientation=orientation,
                c=avg_c,
                a=cur_a,
                b=cur_b,
                thickness=thickness,
            )
        )

    return merged


def split_hv_segments(segments):
    h = [s for s in segments if s["orientation"] == "H"]
    v = [s for s in segments if s["orientation"] == "V"]
    return h, v


def extend_to_nearby_intersections(segments, axis_tol, extend_tol, iterations=3):
    """
    Extend horizontal/vertical centerlines to nearby intersections.

    This helps line ends meet at T/L/cross junctions.
    """

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
# PANEL DETECTION
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
    Simple orthogonal rectangular panel detector.

    It checks adjacent X and Y centerline axes and confirms that all four
    rectangle sides exist.
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


def build_output_dxf(
    raw_segments,
    healed_segments,
    panels,
    nodes,
    draw_raw=True,
    draw_healed=True,
    draw_panels_enabled=True,
    draw_nodes_enabled=False,
    min_output_centerline_length=0,
):
    """
    Build exported DXF.

    Review mode:
    - raw centerlines can be included
    - junction nodes can be included

    Clean structural mode:
    - raw centerlines usually off
    - short fragments filtered
    - junction nodes usually off
    """

    new_doc = ezdxf.new()
    new_msp = new_doc.modelspace()

    safe_layer(new_doc, "ILS_RAW_CENTERLINE_225", color=8)
    safe_layer(new_doc, "ILS_RAW_CENTERLINE_150", color=9)
    safe_layer(new_doc, "ILS_HEALED_CENTERLINE_225", color=1)
    safe_layer(new_doc, "ILS_HEALED_CENTERLINE_150", color=3)
    safe_layer(new_doc, "ILS_SLAB_PANEL", color=5)
    safe_layer(new_doc, "ILS_JUNCTION_NODE", color=2)

    clean_healed_segments = filter_short_segments(
        healed_segments,
        min_output_centerline_length,
    )

    if draw_raw:
        draw_segments_by_thickness(
            new_msp,
            raw_segments,
            prefix="ILS_RAW_CENTERLINE",
        )

    if draw_healed:
        draw_segments_by_thickness(
            new_msp,
            clean_healed_segments,
            prefix="ILS_HEALED_CENTERLINE",
        )

    if draw_panels_enabled:
        draw_panels(
            new_msp,
            panels,
            layer="ILS_SLAB_PANEL",
        )

    if draw_nodes_enabled:
        draw_nodes(
            new_msp,
            nodes,
            radius=50,
            layer="ILS_JUNCTION_NODE",
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


# =========================================================
# STREAMLIT UI
# =========================================================

st.info(
    "This MVP works best when architectural walls are drawn as LINE, LWPOLYLINE, or POLYLINE wall faces. "
    "It currently focuses on orthogonal walls: horizontal and vertical."
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
        help="Line ends near intersections are extended to meet.",
    )

    output_mode = c9.selectbox(
        "Output mode",
        [
            "Review mode",
            "Clean structural mode",
        ],
        index=1,
        help="Review mode exports debug geometry. Clean structural mode exports cleaner modelling geometry.",
    )


with st.expander("Output cleanup", expanded=True):
    o1, o2, o3 = st.columns(3)

    min_output_centerline_length = o1.number_input(
        "Minimum output centerline length",
        min_value=0.0,
        value=750.0,
        step=50.0,
        help="In clean mode, healed centerlines shorter than this are not exported.",
    )

    export_slab_panels = o2.checkbox(
        "Export slab panels",
        value=True,
        help="Exports detected rectangular slab panels as closed polylines.",
    )

    export_junction_nodes = o3.checkbox(
        "Export junction nodes",
        value=False,
        help="Usually OFF for clean structural output. Useful for review/debug.",
    )


with st.expander("Slab panel detection", expanded=False):
    p1, p2 = st.columns(2)

    min_panel_width = p1.number_input(
        "Minimum panel width",
        min_value=0.0,
        value=500.0,
        step=100.0,
    )

    min_panel_height = p2.number_input(
        "Minimum panel height",
        min_value=0.0,
        value=500.0,
        step=100.0,
    )


wall_thicknesses = parse_wall_thicknesses(wall_thickness_text)

if not wall_thicknesses:
    st.error("Enter at least one wall thickness, for example: 225,150")
    st.stop()


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


with st.spinner("Detecting raw wall centerlines from wall face pairs..."):
    raw_segments = detect_centerlines_from_face_pairs(
        horizontal_faces=horizontal_faces,
        vertical_faces=vertical_faces,
        wall_thicknesses=wall_thicknesses,
        thickness_tol=thickness_tol,
        min_overlap=min_overlap,
    )


with st.spinner("Bridging openings and healing centerlines..."):
    merged_segments = merge_collinear_segments(
        raw_segments,
        axis_tol=axis_tol,
        bridge_gap=bridge_gap,
    )

    healed_segments = extend_to_nearby_intersections(
        merged_segments,
        axis_tol=axis_tol,
        extend_tol=intersection_extend_tol,
        iterations=3,
    )

    healed_segments = merge_collinear_segments(
        healed_segments,
        axis_tol=axis_tol,
        bridge_gap=axis_tol,
    )


with st.spinner("Detecting rectangular slab panels..."):
    panels = detect_rectangular_panels(
        healed_segments,
        axis_tol=axis_tol,
        min_panel_width=min_panel_width,
        min_panel_height=min_panel_height,
    )

    nodes = collect_junction_nodes(
        healed_segments,
        axis_tol=axis_tol,
    )


if output_mode == "Clean structural mode":
    clean_export_segments = filter_short_segments(
        healed_segments,
        min_output_centerline_length,
    )
else:
    clean_export_segments = filter_short_segments(
        healed_segments,
        0.0,
    )


st.markdown("### 4. Result Summary")

r1, r2, r3, r4 = st.columns(4)

r1.metric("Raw centerline fragments", len(raw_segments))
r2.metric("Healed centerlines", len(healed_segments))
r3.metric("Detected slab panels", len(panels))
r4.metric("Junction nodes", len(nodes))

st.info(
    f"Output mode: **{output_mode}**. "
    f"Clean export centerlines: **{len(clean_export_segments)}** out of **{len(healed_segments)}** healed centerlines."
)

if not raw_segments:
    st.warning(
        "No wall centerlines were detected. Try checking wall thickness, tolerance, layer selection, or minimum overlap."
    )


st.markdown("### 5. Preview Tables")

tab1, tab2, tab3 = st.tabs(
    [
        "All Healed Centerlines",
        "Clean Export Centerlines",
        "Slab Panels",
    ]
)

with tab1:
    healed_df = segments_to_dataframe(healed_segments)

    if healed_df.empty:
        st.info("No healed centerlines to preview.")
    else:
        st.dataframe(healed_df, use_container_width=True)

        st.download_button(
            "📄 Download All Healed Centerlines CSV",
            data=healed_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_ALL_HEALED_CENTERLINES.csv",
            mime="text/csv",
        )

with tab2:
    clean_df = segments_to_dataframe(clean_export_segments)

    if clean_df.empty:
        st.info("No clean export centerlines to preview.")
    else:
        st.dataframe(clean_df, use_container_width=True)

        st.download_button(
            "📄 Download Clean Export Centerlines CSV",
            data=clean_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_CLEAN_EXPORT_CENTERLINES.csv",
            mime="text/csv",
        )

with tab3:
    panels_df = panels_to_dataframe(panels)

    if panels_df.empty:
        st.info("No rectangular slab panels detected yet.")
    else:
        st.dataframe(panels_df, use_container_width=True)

        st.download_button(
            "📄 Download Slab Panels CSV",
            data=panels_df.to_csv(index=False).encode("utf-8"),
            file_name="ILS_SLAB_PANELS.csv",
            mime="text/csv",
        )


st.markdown("### 6. Download Structural DXF")

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
    healed_segments=healed_segments,
    panels=panels,
    nodes=nodes,
    draw_raw=draw_raw_output,
    draw_healed=True,
    draw_panels_enabled=export_slab_panels,
    draw_nodes_enabled=draw_nodes_output,
    min_output_centerline_length=clean_length,
)

output_bytes = write_doc_to_bytes(output_doc)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

if output_mode == "Clean structural mode":
    output_filename = f"ILS_CLEAN_STRUCTURAL_CENTERLINES_{timestamp}.dxf"
else:
    output_filename = f"ILS_REVIEW_CENTERLINES_{timestamp}.dxf"


st.download_button(
    "📥 Download Centerline / Slab Panel DXF",
    data=output_bytes,
    file_name=output_filename,
    mime="application/dxf",
)

st.success("Analysis complete. Download the DXF above and inspect the generated layers in AutoCAD.")
