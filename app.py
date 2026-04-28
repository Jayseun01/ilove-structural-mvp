import streamlit as st
import ezdxf
import os
import tempfile
import math
import re

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# =========================================================
# SIDEBAR / HEADER
# =========================================================
st.sidebar.title("Navigation")
tool_choice = st.sidebar.radio(
    "Select a Tool:",
    ["1. DXF Smart Purger", "2. Grid Label Sync"]
)
st.sidebar.markdown("---")
st.sidebar.info("Developed by James Oluwaseun Emmanuel")

st.title("🏗️ iLoveStructural")

# =========================================================
# FILE HELPERS
# =========================================================
def save_uploaded_to_temp(uploaded_file):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp.write(uploaded_file.getvalue())
    tmp.close()
    return tmp.name


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def write_doc_to_temp_bytes(doc):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp_path = tmp.name
    tmp.close()
    try:
        doc.saveas(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        safe_remove_file(tmp_path)


# =========================================================
# GENERAL HELPERS
# =========================================================
def clean_text(value):
    if value is None:
        return ""
    txt = str(value).replace("\\P", " ").replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()


def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))


def is_vertical(x1, y1, x2, y2, tol=2.0):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def is_horizontal(x1, y1, x2, y2, tol=2.0):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


def probable_grid_label(text):
    text = clean_text(text)
    patterns = [
        r"^[A-Z]{1,3}$",
        r"^\d{1,3}$",
        r"^[A-Z]{1,3}'$",
        r"^\d{1,3}[A-Z]?$",
    ]
    return any(re.match(p, text) for p in patterns)


def is_numeric_label(text):
    text = clean_text(text)
    return bool(re.match(r"^\d{1,3}[A-Z]?$", text))


def is_alpha_label(text):
    text = clean_text(text)
    return bool(re.match(r"^[A-Z]{1,3}'?$", text))


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def pick_default_layer(layers, candidates):
    upper_map = {layer.upper(): layer for layer in layers}
    for c in candidates:
        if c.upper() in upper_map:
            return upper_map[c.upper()]
    return layers[0] if layers else None


def median(values):
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    m = n // 2
    if n % 2 == 1:
        return float(vals[m])
    return float((vals[m - 1] + vals[m]) / 2.0)


def ratio_safe(a, b):
    if b == 0:
        return 0.0
    return float(a) / float(b)


# =========================================================
# TOOL 1 HELPERS
# =========================================================
def purge_layers_from_modelspace(doc, layers_to_keep):
    all_layers = get_layer_names(doc)
    layers_to_delete = set(all_layers) - set(layers_to_keep)
    msp = doc.modelspace()
    deleted_count = 0

    for entity in list(msp):
        try:
            if entity.dxf.layer in layers_to_delete:
                msp.delete_entity(entity)
                deleted_count += 1
        except Exception:
            continue

    return deleted_count


# =========================================================
# BLOCK / GEOMETRY HELPERS
# =========================================================
def get_insert_transform(insert_entity):
    try:
        ins = insert_entity.dxf.insert
        sx = float(getattr(insert_entity.dxf, "xscale", 1.0) or 1.0)
        sy = float(getattr(insert_entity.dxf, "yscale", 1.0) or 1.0)
        rot = float(getattr(insert_entity.dxf, "rotation", 0.0) or 0.0)
        return (float(ins.x), float(ins.y), sx, sy, rot)
    except Exception:
        return (0.0, 0.0, 1.0, 1.0, 0.0)


def transform_block_point(local_point, insert_entity):
    x, y = local_point
    ix, iy, sx, sy, rot = get_insert_transform(insert_entity)

    x *= sx
    y *= sy

    ang = math.radians(rot)
    xr = x * math.cos(ang) - y * math.sin(ang)
    yr = x * math.sin(ang) + y * math.cos(ang)

    return (xr + ix, yr + iy)


def transform_block_radius(local_radius, insert_entity):
    _, _, sx, sy, _ = get_insert_transform(insert_entity)
    avg_scale = (abs(sx) + abs(sy)) / 2.0
    return float(local_radius) * avg_scale


def get_text_point(entity):
    try:
        if entity.dxftype() == "TEXT":
            if hasattr(entity.dxf, "align_point"):
                ap = entity.dxf.align_point
                if ap is not None and (float(ap.x) != 0.0 or float(ap.y) != 0.0):
                    return (float(ap.x), float(ap.y))
            ins = entity.dxf.insert
            return (float(ins.x), float(ins.y))

        elif entity.dxftype() == "MTEXT":
            ins = entity.dxf.insert
            return (float(ins.x), float(ins.y))

        elif entity.dxftype() == "ATTRIB":
            ins = entity.dxf.insert
            return (float(ins.x), float(ins.y))
    except Exception:
        pass
    return (0.0, 0.0)


def get_text_value(entity):
    try:
        if entity.dxftype() == "TEXT":
            return clean_text(entity.dxf.text)
        elif entity.dxftype() == "MTEXT":
            return clean_text(entity.text)
        elif entity.dxftype() == "ATTRIB":
            return clean_text(entity.dxf.text)
    except Exception:
        pass
    return ""


def set_text_value(entity, new_value):
    if entity.dxftype() == "TEXT":
        entity.dxf.text = new_value
    elif entity.dxftype() == "MTEXT":
        entity.text = new_value
    elif entity.dxftype() == "ATTRIB":
        entity.dxf.text = new_value


# =========================================================
# TEXT / CIRCLE / LINE EXTRACTION
# =========================================================
def extract_texts(doc, layer_name):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxf.layer != layer_name:
                continue

            if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
                texts.append({
                    "entity": e,
                    "text": get_text_value(e),
                    "point": get_text_point(e),
                    "layer": e.dxf.layer,
                    "type": e.dxftype(),
                    "source": "modelspace",
                })

            elif e.dxftype() == "INSERT":
                for att in e.attribs:
                    try:
                        texts.append({
                            "entity": att,
                            "text": get_text_value(att),
                            "point": get_text_point(att),
                            "layer": e.dxf.layer,
                            "type": "ATTRIB",
                            "source": "insert_attrib",
                            "parent_insert": e,
                        })
                    except Exception:
                        continue

                if e.dxf.name in doc.blocks:
                    block = doc.blocks[e.dxf.name]
                    for be in block:
                        try:
                            if be.dxftype() in ("TEXT", "MTEXT"):
                                local_point = get_text_point(be)
                                world_point = transform_block_point(local_point, e)
                                texts.append({
                                    "entity": be,
                                    "text": get_text_value(be),
                                    "point": world_point,
                                    "layer": e.dxf.layer,
                                    "type": be.dxftype(),
                                    "source": "block_text",
                                    "parent_insert": e,
                                })
                        except Exception:
                            continue
        except Exception:
            continue

    return texts


def extract_circles(doc, layer_name):
    circles = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxf.layer != layer_name:
                continue

            if e.dxftype() == "CIRCLE":
                c = e.dxf.center
                circles.append({
                    "entity": e,
                    "center": (float(c.x), float(c.y)),
                    "radius": float(e.dxf.radius),
                    "layer": e.dxf.layer,
                    "source": "modelspace",
                })

            elif e.dxftype() == "INSERT":
                if e.dxf.name in doc.blocks:
                    block = doc.blocks[e.dxf.name]
                    for be in block:
                        try:
                            if be.dxftype() == "CIRCLE":
                                c = be.dxf.center
                                world_center = transform_block_point((float(c.x), float(c.y)), e)
                                world_radius = transform_block_radius(float(be.dxf.radius), e)
                                circles.append({
                                    "entity": e,
                                    "nested_entity": be,
                                    "center": world_center,
                                    "radius": world_radius,
                                    "layer": e.dxf.layer,
                                    "source": "block_circle",
                                    "parent_insert": e,
                                })
                        except Exception:
                            continue
        except Exception:
            continue

    return circles


def add_axis_segment(lines, entity, x1, y1, x2, y2, layer_name, min_length):
    length = math.dist((x1, y1), (x2, y2))
    if length < min_length:
        return

    if is_vertical(x1, y1, x2, y2):
        lines.append({
            "entity": entity,
            "orientation": "vertical",
            "coord": round(float((x1 + x2) / 2.0), 3),
            "start": (float(x1), float(y1)),
            "end": (float(x2), float(y2)),
            "length": round(float(length), 3),
            "layer": layer_name,
        })
    elif is_horizontal(x1, y1, x2, y2):
        lines.append({
            "entity": entity,
            "orientation": "horizontal",
            "coord": round(float((y1 + y2) / 2.0), 3),
            "start": (float(x1), float(y1)),
            "end": (float(x2), float(y2)),
            "length": round(float(length), 3),
            "layer": layer_name,
        })


def extract_axis_lines(doc, layer_name, min_length=1000.0):
    lines = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxf.layer != layer_name:
                continue

            if e.dxftype() == "LINE":
                x1, y1, _ = e.dxf.start
                x2, y2, _ = e.dxf.end
                add_axis_segment(lines, e, x1, y1, x2, y2, e.dxf.layer, min_length)

            elif e.dxftype() == "LWPOLYLINE":
                pts = list(e.get_points())
                for i in range(len(pts) - 1):
                    x1, y1 = float(pts[i][0]), float(pts[i][1])
                    x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
                    add_axis_segment(lines, e, x1, y1, x2, y2, e.dxf.layer, min_length)

            elif e.dxftype() == "POLYLINE":
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in e.vertices]
                for i in range(len(pts) - 1):
                    x1, y1 = pts[i]
                    x2, y2 = pts[i + 1]
                    add_axis_segment(lines, e, x1, y1, x2, y2, e.dxf.layer, min_length)

        except Exception:
            continue

    return deduplicate_axis_lines(lines)


def deduplicate_axis_lines(lines, tol=5.0):
    if not lines:
        return []

    lines = sorted(lines, key=lambda x: (x["orientation"], x["coord"]))
    result = []
    current = [lines[0]]

    for item in lines[1:]:
        same_band = (
            item["orientation"] == current[-1]["orientation"]
            and abs(item["coord"] - current[-1]["coord"]) <= tol
        )
        if same_band:
            current.append(item)
        else:
            result.append(resolve_line_group(current))
            current = [item]

    result.append(resolve_line_group(current))
    return result


def resolve_line_group(group):
    best = max(group, key=lambda x: x["length"])
    out = dict(best)
    out["coord"] = round(sum(g["coord"] for g in group) / len(group), 3)
    return out


# =========================================================
# PATTERN AUTHENTICATION
# =========================================================
def text_inside_circle(text_point, circle_center, circle_radius, extra_gap=180.0):
    d = euclidean(text_point, circle_center)
    return d <= (circle_radius + extra_gap)


def point_to_segment_distance(px, py, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.dist((px, py), (x1, y1))
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.dist((px, py), (proj_x, proj_y))


def point_projects_on_segment(px, py, x1, y1, x2, y2, pad=0.0):
    min_x, max_x = min(x1, x2) - pad, max(x1, x2) + pad
    min_y, max_y = min(y1, y2) - pad, max(y1, y2) + pad
    return min_x <= px <= max_x and min_y <= py <= max_y


def line_attached_to_circle(line, circle_center, circle_radius, attach_gap=180.0):
    cx, cy = circle_center
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    if line["orientation"] == "vertical":
        if abs(line["coord"] - cx) > attach_gap:
            return False
        if not point_projects_on_segment(cx, cy, x1, y1, x2, y2, pad=circle_radius + attach_gap):
            return False

        d_seg = point_to_segment_distance(cx, cy, x1, y1, x2, y2)
        d1 = abs(euclidean((x1, y1), circle_center) - circle_radius)
        d2 = abs(euclidean((x2, y2), circle_center) - circle_radius)
        return min(abs(d_seg - 0.0), d1, d2) <= attach_gap

    elif line["orientation"] == "horizontal":
        if abs(line["coord"] - cy) > attach_gap:
            return False
        if not point_projects_on_segment(cx, cy, x1, y1, x2, y2, pad=circle_radius + attach_gap):
            return False

        d_seg = point_to_segment_distance(cx, cy, x1, y1, x2, y2)
        d1 = abs(euclidean((x1, y1), circle_center) - circle_radius)
        d2 = abs(euclidean((x2, y2), circle_center) - circle_radius)
        return min(abs(d_seg - 0.0), d1, d2) <= attach_gap

    return False


def build_trusted_markers(doc, line_layer, text_layer, circle_layer, min_grid_length, text_gap, attach_gap):
    texts = extract_texts(doc, text_layer)
    circles = extract_circles(doc, circle_layer)
    lines = extract_axis_lines(doc, line_layer, min_length=min_grid_length)

    trusted_markers = []
    rejected = []

    for c in circles:
        circle_center = c["center"]
        circle_radius = c["radius"]

        candidate_texts = []
        for t in texts:
            if not probable_grid_label(t["text"]):
                continue
            if text_inside_circle(t["point"], circle_center, circle_radius, extra_gap=text_gap):
                candidate_texts.append(t)

        candidate_lines = []
        for line in lines:
            if line_attached_to_circle(line, circle_center, circle_radius, attach_gap=attach_gap):
                candidate_lines.append(line)

        if len(candidate_texts) == 1 and len(candidate_lines) >= 1:
            best_line = max(candidate_lines, key=lambda ln: ln["length"])
            trusted_markers.append({
                "label": candidate_texts[0]["text"],
                "text_entity": candidate_texts[0]["entity"],
                "text_point": candidate_texts[0]["point"],
                "text_source": candidate_texts[0].get("source", "unknown"),
                "parent_insert": candidate_texts[0].get("parent_insert"),
                "circle_entity": c["entity"],
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "circle_source": c.get("source", "unknown"),
                "line_entity": best_line["entity"],
                "orientation": best_line["orientation"],
                "coord": best_line["coord"],
                "line_start": best_line["start"],
                "line_end": best_line["end"],
                "line_length": best_line["length"],
            })
        else:
            rejected.append({
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "text_matches": len(candidate_texts),
                "line_matches": len(candidate_lines),
                "candidate_texts": [t["text"] for t in candidate_texts[:10]],
                "circle_source": c.get("source", "unknown"),
                "reason": (
                    "No valid text and no valid line"
                    if len(candidate_texts) == 0 and len(candidate_lines) == 0
                    else "Multiple texts in marker"
                    if len(candidate_texts) > 1
                    else "No valid text in marker"
                    if len(candidate_texts) == 0
                    else "No attached line"
                ),
            })

    return {
        "texts": texts,
        "circles": circles,
        "lines": lines,
        "trusted_markers": trusted_markers,
        "rejected_markers": rejected,
    }


# =========================================================
# AXIS GROUPING / FAMILY / QA / APPLY
# =========================================================
def group_markers_by_axis(markers, tol=5.0):
    if not markers:
        return {"vertical": [], "horizontal": []}

    grouped = {"vertical": [], "horizontal": []}

    for orientation in ["vertical", "horizontal"]:
        subset = sorted(
            [m for m in markers if m["orientation"] == orientation],
            key=lambda x: x["coord"]
        )

        if not subset:
            continue

        current = [subset[0]]
        for item in subset[1:]:
            if abs(item["coord"] - current[-1]["coord"]) <= tol:
                current.append(item)
            else:
                grouped[orientation].append(resolve_axis_group(current, orientation))
                current = [item]
        grouped[orientation].append(resolve_axis_group(current, orientation))

    return grouped


def resolve_axis_group(group, orientation):
    coord = round(sum(m["coord"] for m in group) / len(group), 3)
    labels = [m["label"] for m in group if clean_text(m["label"])]
    label = labels[0] if labels else ""
    representative = max(group, key=lambda x: x.get("line_length", 0.0))

    xs = [m["circle_center"][0] for m in group]
    ys = [m["circle_center"][1] for m in group]

    return {
        "orientation": orientation,
        "coord": coord,
        "label": label,
        "markers": group,
        "marker_count": len(group),
        "representative": representative,
        "bbox": {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        },
        "centroid": (
            sum(xs) / len(xs),
            sum(ys) / len(ys),
        ),
    }


def infer_arch_families(arch_groups):
    vertical = arch_groups.get("vertical", [])
    horizontal = arch_groups.get("horizontal", [])

    v_num = len([g for g in vertical if is_numeric_label(g["label"])])
    v_alpha = len([g for g in vertical if is_alpha_label(g["label"])])
    h_num = len([g for g in horizontal if is_numeric_label(g["label"])])
    h_alpha = len([g for g in horizontal if is_alpha_label(g["label"])])

    if v_num >= h_num and h_alpha >= v_alpha:
        numeric_orientation = "vertical"
        alpha_orientation = "horizontal"
    elif h_num >= v_num and v_alpha >= h_alpha:
        numeric_orientation = "horizontal"
        alpha_orientation = "vertical"
    else:
        if (v_num - v_alpha) >= (h_num - h_alpha):
            numeric_orientation = "vertical"
            alpha_orientation = "horizontal"
        else:
            numeric_orientation = "horizontal"
            alpha_orientation = "vertical"

    numeric_arch = sorted(arch_groups.get(numeric_orientation, []), key=lambda x: x["coord"])
    alpha_arch = sorted(arch_groups.get(alpha_orientation, []), key=lambda x: x["coord"])

    return {
        "numeric_orientation": numeric_orientation,
        "alpha_orientation": alpha_orientation,
        "numeric_arch": numeric_arch,
        "alpha_arch": alpha_arch,
        "vertical_counts": {"numeric": v_num, "alpha": v_alpha},
        "horizontal_counts": {"numeric": h_num, "alpha": h_alpha},
    }


def build_spacings(axis_groups, family_name):
    data = []
    if len(axis_groups) < 2:
        return data

    for i in range(len(axis_groups) - 1):
        a = axis_groups[i]
        b = axis_groups[i + 1]
        spacing = round(abs(b["coord"] - a["coord"]), 3)
        data.append({
            "family": family_name,
            "label_a": a["label"],
            "label_b": b["label"],
            "coord_a": a["coord"],
            "coord_b": b["coord"],
            "spacing": spacing,
        })
    return data


def compare_spacings(arch_groups, struc_groups, family_name, tolerance):
    arch_sp = build_spacings(arch_groups, family_name)
    struc_sp = build_spacings(struc_groups, family_name)

    pair_count = min(len(arch_sp), len(struc_sp))
    issues = []

    for i in range(pair_count):
        a = arch_sp[i]
        s = struc_sp[i]
        diff = round(abs(a["spacing"] - s["spacing"]), 3)
        status = "PASS" if diff <= tolerance else "FAIL"

        issues.append({
            "family": family_name,
            "span_position": i + 1,
            "arch_span": f"{a['label_a']}-{a['label_b']}",
            "struc_span": f"{s['label_a']}-{s['label_b']}",
            "arch_spacing": a["spacing"],
            "struc_spacing": s["spacing"],
            "difference": diff,
            "tolerance": tolerance,
            "status": status,
        })

    return issues


def summarize_geometry_issues(all_issues):
    passes = [x for x in all_issues if x["status"] == "PASS"]
    fails = [x for x in all_issues if x["status"] == "FAIL"]
    return passes, fails


def build_axis_mapping_preview(arch_axis_groups, struc_axis_groups, family_name, region_name=None):
    preview = []
    count = min(len(arch_axis_groups), len(struc_axis_groups))

    for i in range(count):
        preview.append({
            "region": region_name if region_name else "Reference",
            "family": family_name,
            "position": i + 1,
            "arch_label": arch_axis_groups[i]["label"],
            "struc_old_label": struc_axis_groups[i]["label"],
            "arch_coord": arch_axis_groups[i]["coord"],
            "struc_coord": struc_axis_groups[i]["coord"],
            "difference": round(abs(arch_axis_groups[i]["coord"] - struc_axis_groups[i]["coord"]), 3),
            "arch_visible_markers": arch_axis_groups[i]["marker_count"],
            "struc_visible_markers": struc_axis_groups[i]["marker_count"],
        })

    return preview


def apply_axis_group_labels(arch_axis_groups, struc_axis_groups):
    count = min(len(arch_axis_groups), len(struc_axis_groups))
    changed = 0

    for i in range(count):
        new_label = arch_axis_groups[i]["label"]
        struc_group = struc_axis_groups[i]

        for marker in struc_group["markers"]:
            entity = marker["text_entity"]
            try:
                old = get_text_value(entity)
                if old != new_label:
                    set_text_value(entity, new_label)
                    changed += 1
            except Exception:
                continue

    return changed


# =========================================================
# EMPTY-SPACE PLAN SEGMENTATION
# =========================================================
def bbox_from_markers(markers):
    xs = [m["circle_center"][0] for m in markers]
    ys = [m["circle_center"][1] for m in markers]
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
        "centroid": (sum(xs) / len(xs), sum(ys) / len(ys)),
    }


def compute_major_gap_threshold(values):
    if len(values) < 2:
        return None

    vals = sorted(set(round(v, 3) for v in values))
    if len(vals) < 2:
        return None

    gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    pos_gaps = [g for g in gaps if g > 0]
    if not pos_gaps:
        return None

    typical = sorted(pos_gaps)[len(pos_gaps) // 2]
    return max(typical * 3.0, 6000.0)


def build_axis_value_groups(values, major_gap_threshold):
    vals = sorted(set(round(v, 3) for v in values))
    if not vals:
        return []

    groups = [[vals[0]]]
    for v in vals[1:]:
        if abs(v - groups[-1][-1]) > major_gap_threshold:
            groups.append([v])
        else:
            groups[-1].append(v)
    return groups


def assign_value_to_group(value, groups):
    v = round(value, 3)
    for i, grp in enumerate(groups):
        if grp[0] <= v <= grp[-1]:
            return i
    return None


def build_structural_regions_by_empty_space(trusted_markers, min_region_markers=6):
    if not trusted_markers:
        return [], {}

    xs = [m["circle_center"][0] for m in trusted_markers]
    ys = [m["circle_center"][1] for m in trusted_markers]

    x_gap_threshold = compute_major_gap_threshold(xs)
    y_gap_threshold = compute_major_gap_threshold(ys)

    if x_gap_threshold is None:
        x_gap_threshold = 6000.0
    if y_gap_threshold is None:
        y_gap_threshold = 6000.0

    x_groups = build_axis_value_groups(xs, x_gap_threshold)
    y_groups = build_axis_value_groups(ys, y_gap_threshold)

    buckets = {}
    for m in trusted_markers:
        x_idx = assign_value_to_group(m["circle_center"][0], x_groups)
        y_idx = assign_value_to_group(m["circle_center"][1], y_groups)
        key = (x_idx, y_idx)
        buckets.setdefault(key, []).append(m)

    regions = []
    n = 1
    skipped_small = 0

    for key, markers in sorted(buckets.items(), key=lambda item: (item[0][1], item[0][0])):
        if not markers:
            continue
        if len(markers) < min_region_markers:
            skipped_small += 1
            continue

        axis_groups = group_markers_by_axis(markers)
        bbox = bbox_from_markers(markers)

        vertical_count = len(axis_groups.get("vertical", []))
        horizontal_count = len(axis_groups.get("horizontal", []))

        regions.append({
            "name": f"Region {n}",
            "markers": markers,
            "axis_groups": axis_groups,
            "bbox": bbox,
            "grid_bucket": key,
            "marker_count": len(markers),
            "vertical_axes": vertical_count,
            "horizontal_axes": horizontal_count,
            "bbox_area": round(max(1.0, bbox["width"] * bbox["height"]), 3),
        })
        n += 1

    return regions, {
        "x_gap_threshold": x_gap_threshold,
        "y_gap_threshold": y_gap_threshold,
        "x_group_count": len(x_groups),
        "y_group_count": len(y_groups),
        "raw_bucket_count": len(buckets),
        "regions_kept": len(regions),
        "small_regions_skipped": skipped_small,
        "min_region_markers": min_region_markers,
    }


# =========================================================
# SIMILARITY / MATCHING
# =========================================================
def build_spacing_values(axis_groups):
    if len(axis_groups) < 2:
        return []
    coords = [g["coord"] for g in axis_groups]
    return [round(abs(coords[i + 1] - coords[i]), 3) for i in range(len(coords) - 1)]


def best_subsequence_match_score(ref_spacings, cand_spacings, spacing_tol=50.0):
    if not ref_spacings or not cand_spacings:
        return 0.0, 0, 0

    best_match_count = 0
    best_window_len = 0

    if len(cand_spacings) <= len(ref_spacings):
        max_start = len(ref_spacings) - len(cand_spacings)
        for start in range(max_start + 1):
            matched = 0
            for i, val in enumerate(cand_spacings):
                if abs(ref_spacings[start + i] - val) <= spacing_tol:
                    matched += 1
            if matched > best_match_count:
                best_match_count = matched
                best_window_len = len(cand_spacings)
    else:
        max_start = len(cand_spacings) - len(ref_spacings)
        for start in range(max_start + 1):
            matched = 0
            for i, val in enumerate(ref_spacings):
                if abs(cand_spacings[start + i] - val) <= spacing_tol:
                    matched += 1
            if matched > best_match_count:
                best_match_count = matched
                best_window_len = len(ref_spacings)

    denom = max(1, best_window_len)
    return round(best_match_count / denom, 3), best_match_count, best_window_len


def bbox_similarity_score(ref_bbox, cand_bbox):
    ref_w = max(1.0, ref_bbox.get("width", 1.0))
    ref_h = max(1.0, ref_bbox.get("height", 1.0))
    cand_w = max(1.0, cand_bbox.get("width", 1.0))
    cand_h = max(1.0, cand_bbox.get("height", 1.0))

    width_ratio = min(ref_w, cand_w) / max(ref_w, cand_w)
    height_ratio = min(ref_h, cand_h) / max(ref_h, cand_h)
    return round((width_ratio + height_ratio) / 2.0, 3), round(width_ratio, 3), round(height_ratio, 3)


def evaluate_region_match(ref_numeric, ref_alpha, cand_numeric, cand_alpha, ref_marker_count, cand_marker_count, ref_bbox, cand_bbox):
    ref_num_count = len(ref_numeric)
    ref_alpha_count = len(ref_alpha)
    cand_num_count = len(cand_numeric)
    cand_alpha_count = len(cand_alpha)

    ref_num_sp = build_spacing_values(ref_numeric)
    cand_num_sp = build_spacing_values(cand_numeric)
    ref_alp_sp = build_spacing_values(ref_alpha)
    cand_alp_sp = build_spacing_values(cand_alpha)

    num_subseq_score, num_subseq_matches, num_subseq_window = best_subsequence_match_score(ref_num_sp, cand_num_sp, spacing_tol=50.0)
    alp_subseq_score, alp_subseq_matches, alp_subseq_window = best_subsequence_match_score(ref_alp_sp, cand_alp_sp, spacing_tol=50.0)

    num_ratio = ratio_safe(cand_num_count, ref_num_count) if ref_num_count else 0.0
    alp_ratio = ratio_safe(cand_alpha_count, ref_alpha_count) if ref_alpha_count else 0.0
    marker_ratio = ratio_safe(cand_marker_count, ref_marker_count) if ref_marker_count else 0.0

    bbox_score, width_ratio, height_ratio = bbox_similarity_score(ref_bbox, cand_bbox)

    exact_full = (
        cand_num_count == ref_num_count
        and cand_alpha_count == ref_alpha_count
        and num_subseq_score >= 1.0
        and alp_subseq_score >= 1.0
    )

    strong_similar = (
        num_ratio >= 0.75
        and alp_ratio >= 0.75
        and marker_ratio >= 0.70
        and num_subseq_score >= 0.70
        and alp_subseq_score >= 0.70
        and bbox_score >= 0.75
    )

    partial_but_confident = (
        num_ratio >= 0.60
        and alp_ratio >= 0.60
        and marker_ratio >= 0.55
        and (
            (num_subseq_score >= 0.85 and alp_subseq_score >= 0.60)
            or (alp_subseq_score >= 0.85 and num_subseq_score >= 0.60)
            or (num_subseq_score >= 0.75 and alp_subseq_score >= 0.75 and bbox_score >= 0.85)
        )
    )

    reasons = []

    if exact_full:
        reasons.append("Exact full match")
        matched = True
        match_mode = "exact"
    elif strong_similar:
        reasons.append("Strong similar repeated-plan match")
        matched = True
        match_mode = "strong-similar"
    elif partial_but_confident:
        reasons.append("Partial but confident repeated-plan match")
        matched = True
        match_mode = "partial-confident"
    else:
        matched = False
        match_mode = "rejected"

    if not matched:
        if num_ratio < 0.60:
            reasons.append("numeric axis count too low")
        elif num_ratio < 0.75:
            reasons.append("numeric axis count slightly low")

        if alp_ratio < 0.60:
            reasons.append("alphabetic axis count too low")
        elif alp_ratio < 0.75:
            reasons.append("alphabetic axis count slightly low")

        if marker_ratio < 0.55:
            reasons.append("trusted marker count too low")
        elif marker_ratio < 0.70:
            reasons.append("trusted marker count slightly low")

        if num_subseq_score < 0.60:
            reasons.append("numeric spacing signature weak")
        elif num_subseq_score < 0.75:
            reasons.append("numeric spacing signature moderate")

        if alp_subseq_score < 0.60:
            reasons.append("alphabetic spacing signature weak")
        elif alp_subseq_score < 0.75:
            reasons.append("alphabetic spacing signature moderate")

        if bbox_score < 0.75:
            reasons.append("plan bounding shape differs")

    return {
        "matched": matched,
        "match_mode": match_mode,
        "reason": "; ".join(reasons) if reasons else "Not similar enough",
        "num_ratio": round(num_ratio, 3),
        "alp_ratio": round(alp_ratio, 3),
        "marker_ratio": round(marker_ratio, 3),
        "num_score": round(num_subseq_score, 3),
        "alp_score": round(alp_subseq_score, 3),
        "bbox_score": round(bbox_score, 3),
        "width_ratio": width_ratio,
        "height_ratio": height_ratio,
        "num_subseq_matches": num_subseq_matches,
        "alp_subseq_matches": alp_subseq_matches,
        "num_subseq_window": num_subseq_window,
        "alp_subseq_window": alp_subseq_window,
    }


def build_reference_region_like_bbox(numeric_arch_groups, alpha_arch_groups):
    xs = [g["centroid"][0] for g in numeric_arch_groups] if numeric_arch_groups else []
    ys = [g["centroid"][1] for g in alpha_arch_groups] if alpha_arch_groups else []

    if not xs and not ys:
        return {"width": 1.0, "height": 1.0}

    if not xs:
        xs = [0.0, 1.0]
    if not ys:
        ys = [0.0, 1.0]

    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


# =========================================================
# SESSION STATE
# =========================================================
def init_sync_state():
    defaults = {
        "docs_loaded": False,
        "arch_doc": None,
        "struc_doc": None,
        "arch_name": "",
        "struc_name": "",
        "arch_detection": {},
        "struc_detection": {},
        "arch_axis_groups": {},
        "mapping_preview": [],
        "geometry_issues": [],
        "apply_ready": False,
        "labels_changed_count": 0,
        "arch_sig": None,
        "struc_sig": None,
        "last_apply_message": "",
        "family_summary": {},
        "numeric_arch_groups": [],
        "alpha_arch_groups": [],
        "structural_regions": [],
        "matched_regions": [],
        "segmentation_summary": {},
        "region_match_report": [],
        "reference_region_bbox": {},
        "reference_marker_count": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_sync_state():
    keys = [
        "docs_loaded",
        "arch_doc",
        "struc_doc",
        "arch_name",
        "struc_name",
        "arch_detection",
        "struc_detection",
        "arch_axis_groups",
        "mapping_preview",
        "geometry_issues",
        "apply_ready",
        "labels_changed_count",
        "arch_sig",
        "struc_sig",
        "last_apply_message",
        "family_summary",
        "numeric_arch_groups",
        "alpha_arch_groups",
        "structural_regions",
        "matched_regions",
        "segmentation_summary",
        "region_match_report",
        "reference_region_bbox",
        "reference_marker_count",
    ]
    for key in keys:
        if key in st.session_state:
            del st.session_state[key]


def clear_sync_outputs():
    st.session_state.arch_detection = {}
    st.session_state.struc_detection = {}
    st.session_state.arch_axis_groups = {}
    st.session_state.mapping_preview = []
    st.session_state.geometry_issues = []
    st.session_state.apply_ready = False
    st.session_state.labels_changed_count = 0
    st.session_state.last_apply_message = ""
    st.session_state.family_summary = {}
    st.session_state.numeric_arch_groups = []
    st.session_state.alpha_arch_groups = []
    st.session_state.structural_regions = []
    st.session_state.matched_regions = []
    st.session_state.segmentation_summary = {}
    st.session_state.region_match_report = []
    st.session_state.reference_region_bbox = {}
    st.session_state.reference_marker_count = 0


def uploaded_file_signature(uploaded_file):
    if uploaded_file is None:
        return None
    return (uploaded_file.name, len(uploaded_file.getvalue()))


# =========================================================
# TOOL 1
# =========================================================
if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload your .DXF file", type=["dxf"], key="purger")

    if uploaded_file is not None:
        tmp_path = save_uploaded_to_temp(uploaded_file)
        try:
            doc = ezdxf.readfile(tmp_path)
            all_layers = get_layer_names(doc)
            st.success(f"Successfully analyzed {uploaded_file.name}")

            layers_to_keep = st.multiselect(
                "Which layers should remain?",
                options=all_layers,
                default=all_layers
            )

            if st.button("🔥 Purge and Prepare Download"):
                deleted_count = purge_layers_from_modelspace(doc, layers_to_keep)
                dxf_bytes = write_doc_to_temp_bytes(doc)

                st.success(f"Deleted {deleted_count} modelspace entities.")
                st.download_button(
                    "📥 Download Cleaned DXF",
                    data=dxf_bytes,
                    file_name=f"CLEANED_{uploaded_file.name}",
                    mime="application/dxf"
                )

        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            safe_remove_file(tmp_path)


# =========================================================
# TOOL 2
# =========================================================
elif tool_choice == "2. Grid Label Sync":
    st.subheader("Tool 2: Grid Label Sync")
    st.caption(
        "Architectural labels are copied onto trusted Structural markers only. "
        "Two-factor authentication is enforced: selected layers + valid marker pattern."
    )

    init_sync_state()

    c1, c2, c3 = st.columns(3)
    with c1:
        tolerance = st.slider("Tolerance (mm)", 0.0, 500.0, 10.0, 0.5)
    with c2:
        text_gap = st.slider("Text-in-Marker Gap (mm)", 20.0, 1500.0, 180.0, 10.0)
    with c3:
        attach_gap = st.slider("Line-to-Marker Attach Gap (mm)", 20.0, 1500.0, 180.0, 10.0)

    d1, d2, d3 = st.columns(3)
    with d1:
        min_grid_length = st.number_input("Minimum Grid Line Length (mm)", min_value=1.0, value=1000.0, step=100.0)
    with d2:
        min_region_markers = st.number_input("Minimum Markers Per Region", min_value=1, value=6, step=1)
    with d3:
        sync_similar_regions = st.checkbox("Sync structurally similar repeated plan regions", value=True)

    col_a, col_b = st.columns(2)
    with col_a:
        arch_file = st.file_uploader("Reference (Architectural DXF)", type=["dxf"], key="arch")
    with col_b:
        struc_file = st.file_uploader("Target (Structural DXF)", type=["dxf"], key="struc")

    current_arch_sig = uploaded_file_signature(arch_file)
    current_struc_sig = uploaded_file_signature(struc_file)

    if (
        current_arch_sig != st.session_state.arch_sig
        or current_struc_sig != st.session_state.struc_sig
    ):
        reset_sync_state()
        init_sync_state()
        st.session_state.arch_sig = current_arch_sig
        st.session_state.struc_sig = current_struc_sig

    if arch_file and struc_file:
        if not st.session_state.docs_loaded:
            arch_tmp = None
            struc_tmp = None
            try:
                arch_tmp = save_uploaded_to_temp(arch_file)
                struc_tmp = save_uploaded_to_temp(struc_file)

                st.session_state.arch_doc = ezdxf.readfile(arch_tmp)
                st.session_state.struc_doc = ezdxf.readfile(struc_tmp)
                st.session_state.arch_name = arch_file.name
                st.session_state.struc_name = struc_file.name
                st.session_state.docs_loaded = True
            except Exception as e:
                st.error(f"Failed to load DXF files: {e}")
            finally:
                safe_remove_file(arch_tmp)
                safe_remove_file(struc_tmp)

        if st.session_state.arch_doc and st.session_state.struc_doc:
            arch_layers = get_layer_names(st.session_state.arch_doc)
            struc_layers = get_layer_names(st.session_state.struc_doc)

            arch_line_default = pick_default_layer(arch_layers, ["S-GRID"])
            arch_text_default = pick_default_layer(arch_layers, ["S-GRID-IDEN"])
            arch_circle_default = pick_default_layer(arch_layers, ["S-GRID-IDEN"])

            struc_line_default = pick_default_layer(struc_layers, ["GRIDLINELAYER", "S-GRID"])
            struc_text_default = pick_default_layer(struc_layers, ["DEFAULTLAYER", "S-STRS-IDEN", "S-GRID-IDEN"])
            struc_circle_default = pick_default_layer(struc_layers, ["DEFAULTLAYER", "S-GRID-IDEN"])

            st.markdown("### Layer Setup")

            a1, a2, a3 = st.columns(3)
            with a1:
                arch_line_layer = st.selectbox(
                    "Arch Grid Line Layer",
                    arch_layers,
                    index=arch_layers.index(arch_line_default) if arch_line_default in arch_layers else 0
                )
            with a2:
                arch_text_layer = st.selectbox(
                    "Arch Grid Text Layer",
                    arch_layers,
                    index=arch_layers.index(arch_text_default) if arch_text_default in arch_layers else 0
                )
            with a3:
                arch_circle_layer = st.selectbox(
                    "Arch Grid Circle Layer",
                    arch_layers,
                    index=arch_layers.index(arch_circle_default) if arch_circle_default in arch_layers else 0
                )

            s1, s2, s3 = st.columns(3)
            with s1:
                struc_line_layer = st.selectbox(
                    "Struc Grid Line Layer",
                    struc_layers,
                    index=struc_layers.index(struc_line_default) if struc_line_default in struc_layers else 0
                )
            with s2:
                struc_text_layer = st.selectbox(
                    "Struc Grid Text Layer",
                    struc_layers,
                    index=struc_layers.index(struc_text_default) if struc_text_default in struc_layers else 0
                )
            with s3:
                struc_circle_layer = st.selectbox(
                    "Struc Grid Circle Layer",
                    struc_layers,
                    index=struc_layers.index(struc_circle_default) if struc_circle_default in struc_layers else 0
                )

            b1, b2 = st.columns(2)
            with b1:
                if st.button("🔎 Prepare Label Sync"):
                    clear_sync_outputs()
                    try:
                        arch_detection = build_trusted_markers(
                            st.session_state.arch_doc,
                            line_layer=arch_line_layer,
                            text_layer=arch_text_layer,
                            circle_layer=arch_circle_layer,
                            min_grid_length=min_grid_length,
                            text_gap=text_gap,
                            attach_gap=attach_gap,
                        )

                        struc_detection = build_trusted_markers(
                            st.session_state.struc_doc,
                            line_layer=struc_line_layer,
                            text_layer=struc_text_layer,
                            circle_layer=struc_circle_layer,
                            min_grid_length=min_grid_length,
                            text_gap=text_gap,
                            attach_gap=attach_gap,
                        )

                        st.session_state.arch_detection = arch_detection
                        st.session_state.struc_detection = struc_detection

                        arch_axis_groups = group_markers_by_axis(arch_detection["trusted_markers"])
                        st.session_state.arch_axis_groups = arch_axis_groups

                        fam = infer_arch_families(arch_axis_groups)
                        st.session_state.family_summary = fam

                        numeric_orientation = fam["numeric_orientation"]
                        alpha_orientation = fam["alpha_orientation"]

                        numeric_arch_groups = sorted(arch_axis_groups.get(numeric_orientation, []), key=lambda x: x["coord"])
                        alpha_arch_groups = sorted(arch_axis_groups.get(alpha_orientation, []), key=lambda x: x["coord"])

                        st.session_state.numeric_arch_groups = numeric_arch_groups
                        st.session_state.alpha_arch_groups = alpha_arch_groups
                        st.session_state.reference_marker_count = len(arch_detection["trusted_markers"])
                        st.session_state.reference_region_bbox = build_reference_region_like_bbox(
                            numeric_arch_groups, alpha_arch_groups
                        )

                        structural_regions, segmentation_summary = build_structural_regions_by_empty_space(
                            struc_detection["trusted_markers"],
                            min_region_markers=min_region_markers,
                        )
                        st.session_state.structural_regions = structural_regions
                        st.session_state.segmentation_summary = segmentation_summary

                        matched_regions = []
                        mapping_preview = []
                        issues = []
                        region_match_report = []

                        for region in structural_regions:
                            region_axis = region["axis_groups"]
                            numeric_struc_groups = sorted(region_axis.get(numeric_orientation, []), key=lambda x: x["coord"])
                            alpha_struc_groups = sorted(region_axis.get(alpha_orientation, []), key=lambda x: x["coord"])

                            match_eval = evaluate_region_match(
                                numeric_arch_groups,
                                alpha_arch_groups,
                                numeric_struc_groups,
                                alpha_struc_groups,
                                st.session_state.reference_marker_count,
                                len(region["markers"]),
                                st.session_state.reference_region_bbox,
                                region["bbox"],
                            )

                            strict_exact = match_eval["match_mode"] == "exact"
                            similar_allowed = sync_similar_regions and match_eval["matched"]
                            will_sync = strict_exact or similar_allowed

                            region_match_report.append({
                                "region": region["name"],
                                "trusted_markers": len(region["markers"]),
                                "numeric_axes": len(numeric_struc_groups),
                                "alphabetic_axes": len(alpha_struc_groups),
                                "bbox_width": round(region["bbox"]["width"], 3),
                                "bbox_height": round(region["bbox"]["height"], 3),
                                "marker_ratio": match_eval["marker_ratio"],
                                "num_ratio": match_eval["num_ratio"],
                                "alp_ratio": match_eval["alp_ratio"],
                                "num_score": match_eval["num_score"],
                                "alp_score": match_eval["alp_score"],
                                "bbox_score": match_eval["bbox_score"],
                                "match_mode": match_eval["match_mode"],
                                "match_reason": match_eval["reason"],
                                "will_sync": will_sync,
                            })

                            if will_sync:
                                matched_regions.append({
                                    "name": region["name"],
                                    "bbox": region["bbox"],
                                    "numeric_groups": numeric_struc_groups,
                                    "alpha_groups": alpha_struc_groups,
                                    "grid_bucket": region["grid_bucket"],
                                    "match_reason": match_eval["reason"],
                                    "match_mode": match_eval["match_mode"],
                                    "marker_count": len(region["markers"]),
                                })

                                mapping_preview.extend(build_axis_mapping_preview(
                                    numeric_arch_groups, numeric_struc_groups, "numeric", region["name"]
                                ))
                                mapping_preview.extend(build_axis_mapping_preview(
                                    alpha_arch_groups, alpha_struc_groups, "alphabetic", region["name"]
                                ))

                                issues.extend(compare_spacings(
                                    numeric_arch_groups,
                                    numeric_struc_groups,
                                    f"{region['name']} - numeric",
                                    tolerance
                                ))
                                issues.extend(compare_spacings(
                                    alpha_arch_groups,
                                    alpha_struc_groups,
                                    f"{region['name']} - alphabetic",
                                    tolerance
                                ))

                        st.session_state.matched_regions = matched_regions
                        st.session_state.mapping_preview = mapping_preview
                        st.session_state.geometry_issues = issues
                        st.session_state.region_match_report = region_match_report
                        st.session_state.apply_ready = len(matched_regions) > 0

                        if matched_regions:
                            st.success(
                                f"Detected {len(structural_regions)} structural region(s). "
                                f"{len(matched_regions)} region(s) are ready for sync."
                            )
                        else:
                            st.warning(
                                "No structural regions qualified for sync. "
                                "Check region reasons below."
                            )

                    except Exception as e:
                        st.error(f"Label sync preparation failed: {e}")

            with b2:
                if st.button("🧹 Reset Tool 2"):
                    reset_sync_state()
                    st.rerun()

            if st.session_state.family_summary:
                fam = st.session_state.family_summary
                st.info(
                    f"Reference family detection: Numeric family = {fam['numeric_orientation']} axes, "
                    f"Alphabetic family = {fam['alpha_orientation']} axes."
                )

            if st.session_state.segmentation_summary:
                st.markdown("### Empty-Space Segmentation Summary")
                st.write(st.session_state.segmentation_summary)

            if st.session_state.region_match_report:
                st.markdown("### Structural Region Match Report")
                st.dataframe(st.session_state.region_match_report, use_container_width=True)

            st.markdown("---")
            st.markdown("### Sync Preview")

            if st.session_state.mapping_preview:
                st.dataframe(st.session_state.mapping_preview, use_container_width=True)

                if st.session_state.geometry_issues:
                    _, fails = summarize_geometry_issues(st.session_state.geometry_issues)
                    if fails:
                        st.warning(
                            f"{len(fails)} spacing check(s) are outside tolerance. "
                            "You can still apply if you intentionally want to continue."
                        )
            else:
                st.info("No sync preview yet.")

            st.markdown("### Apply Sync")
            if st.session_state.apply_ready:
                if st.button("✍️ Apply Label Sync"):
                    try:
                        changed = 0
                        for region in st.session_state.matched_regions:
                            changed += apply_axis_group_labels(
                                st.session_state.numeric_arch_groups,
                                region["numeric_groups"]
                            )
                            changed += apply_axis_group_labels(
                                st.session_state.alpha_arch_groups,
                                region["alpha_groups"]
                            )

                        st.session_state.labels_changed_count = changed

                        if changed > 0:
                            st.session_state.last_apply_message = (
                                f"Label sync complete. {changed} trusted structural marker text entities updated "
                                f"across {len(st.session_state.matched_regions)} matched structural region(s)."
                            )
                            st.success(st.session_state.last_apply_message)
                        else:
                            st.session_state.last_apply_message = (
                                "Matched structural regions were found, but no text changes were needed. "
                                "The labels may already match."
                            )
                            st.info(st.session_state.last_apply_message)

                    except Exception as e:
                        st.error(f"Failed to apply label sync: {e}")
            else:
                st.info("Prepare Label Sync first.")

            st.markdown("### Download")
            if st.session_state.struc_doc is not None:
                dxf_bytes = write_doc_to_temp_bytes(st.session_state.struc_doc)
                st.download_button(
                    "📥 Download Relabeled Structural DXF",
                    data=dxf_bytes,
                    file_name=f"RELABELED_{st.session_state.struc_name}",
                    mime="application/dxf"
                )

            with st.expander("Detection details"):
                arch_det = st.session_state.arch_detection or {}
                struc_det = st.session_state.struc_detection or {}
                arch_groups = st.session_state.arch_axis_groups or {}

                st.write("#### Architectural Detection")
                st.write({
                    "texts_on_selected_text_layer": len(arch_det.get("texts", [])),
                    "circles_or_block_markers_on_selected_circle_layer": len(arch_det.get("circles", [])),
                    "axis_lines_on_selected_line_layer": len(arch_det.get("lines", [])),
                    "trusted_markers_found": len(arch_det.get("trusted_markers", [])),
                    "rejected_markers": len(arch_det.get("rejected_markers", [])),
                    "vertical_axes_grouped": len(arch_groups.get("vertical", [])),
                    "horizontal_axes_grouped": len(arch_groups.get("horizontal", [])),
                    "reference_marker_count": st.session_state.reference_marker_count,
                    "reference_region_bbox": st.session_state.reference_region_bbox,
                })

                st.write("#### Structural Detection")
                st.write({
                    "texts_on_selected_text_layer": len(struc_det.get("texts", [])),
                    "circles_or_block_markers_on_selected_circle_layer": len(struc_det.get("circles", [])),
                    "axis_lines_on_selected_line_layer": len(struc_det.get("lines", [])),
                    "trusted_markers_found": len(struc_det.get("trusted_markers", [])),
                    "rejected_markers": len(struc_det.get("rejected_markers", [])),
                    "structural_regions_found": len(st.session_state.structural_regions),
                    "matched_regions": len(st.session_state.matched_regions),
                })

                if st.session_state.family_summary:
                    st.write("#### Architectural Family Inference")
                    st.write(st.session_state.family_summary)

                if st.session_state.structural_regions:
                    st.write("#### Structural Region Summary")
                    region_rows = []
                    for r in st.session_state.structural_regions:
                        region_rows.append({
                            "region": r["name"],
                            "trusted_markers": r["marker_count"],
                            "vertical_axes": r["vertical_axes"],
                            "horizontal_axes": r["horizontal_axes"],
                            "bbox_width": round(r["bbox"]["width"], 3),
                            "bbox_height": round(r["bbox"]["height"], 3),
                            "grid_bucket": r["grid_bucket"],
                        })
                    st.dataframe(region_rows, use_container_width=True)

                if struc_det.get("rejected_markers"):
                    st.write("#### Rejected Structural Markers")
                    st.dataframe(struc_det["rejected_markers"], use_container_width=True)

                if st.session_state.geometry_issues:
                    st.write("#### Spacing Check")
                    st.dataframe(st.session_state.geometry_issues, use_container_width=True)

                st.caption(
                    "This version uses empty-space segmentation plus looser repeated-plan matching. "
                    "A structural region can qualify as exact, strong-similar, or partial-but-confident. "
                    "The region match report explains why each region will or will not sync."
                )
    else:
        st.info("Please upload both the Architectural DXF and Structural DXF.")
