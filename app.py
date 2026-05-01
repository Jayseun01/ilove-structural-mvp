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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
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
    txt = txt.replace("′", "'")
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
        r"[A-Z]{1,3}",
        r"[A-Z]{1,3}'",
        r"\d{1,3}",
        r"\d{1,3}[A-Z]?",
    ]

    return any(re.fullmatch(p, text) for p in patterns)


def is_numeric_label(text):
    text = clean_text(text)
    return bool(re.fullmatch(r"\d{1,3}[A-Z]?", text))


def is_alpha_label(text):
    text = clean_text(text)
    return bool(re.fullmatch(r"[A-Z]{1,3}'?", text))


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def pick_default_layer(layers, candidates):
    upper_map = {layer.upper(): layer for layer in layers}

    for c in candidates:
        if c.upper() in upper_map:
            return upper_map[c.upper()]

    return layers[0] if layers else None


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
        return float(ins.x), float(ins.y), sx, sy, rot
    except Exception:
        return 0.0, 0.0, 1.0, 1.0, 0.0


def transform_block_point(local_point, insert_entity):
    x, y = local_point
    ix, iy, sx, sy, rot = get_insert_transform(insert_entity)

    x *= sx
    y *= sy

    ang = math.radians(rot)
    xr = x * math.cos(ang) - y * math.sin(ang)
    yr = x * math.sin(ang) + y * math.cos(ang)

    return xr + ix, yr + iy


def transform_block_radius(local_radius, insert_entity):
    _, _, sx, sy, _ = get_insert_transform(insert_entity)
    avg_scale = (abs(sx) + abs(sy)) / 2.0
    return float(local_radius) * avg_scale


def get_text_point(entity):
    try:
        if entity.dxftype() == "TEXT":
            try:
                ap = entity.dxf.align_point
                if ap is not None and (float(ap.x) != 0.0 or float(ap.y) != 0.0):
                    return float(ap.x), float(ap.y)
            except Exception:
                pass

            ins = entity.dxf.insert
            return float(ins.x), float(ins.y)

        if entity.dxftype() in ("MTEXT", "ATTRIB"):
            ins = entity.dxf.insert
            return float(ins.x), float(ins.y)

    except Exception:
        pass

    return 0.0, 0.0


def get_text_value(entity):
    try:
        if entity.dxftype() == "TEXT":
            return clean_text(entity.dxf.text)

        if entity.dxftype() == "MTEXT":
            return clean_text(entity.text)

        if entity.dxftype() == "ATTRIB":
            return clean_text(entity.dxf.text)

    except Exception:
        pass

    return ""


def set_text_value(entity, new_value):
    try:
        if entity.dxftype() == "TEXT":
            entity.dxf.text = new_value
        elif entity.dxftype() == "MTEXT":
            entity.text = new_value
        elif entity.dxftype() == "ATTRIB":
            entity.dxf.text = new_value
    except Exception:
        pass


def is_entity_writable(marker):
    return marker.get("text_source", "") in ("modelspace", "insert_attrib")


# =========================================================
# TEXT / CIRCLE / LINE EXTRACTION
# =========================================================

def extract_texts(doc, layer_name):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
                if e.dxf.layer != layer_name:
                    continue

                texts.append({
                    "entity": e,
                    "text": get_text_value(e),
                    "point": get_text_point(e),
                    "layer": e.dxf.layer,
                    "type": e.dxftype(),
                    "source": "modelspace",
                })

            elif e.dxftype() == "INSERT":
                parent_layer_matches = e.dxf.layer == layer_name

                try:
                    for att in e.attribs:
                        if parent_layer_matches or att.dxf.layer == layer_name:
                            texts.append({
                                "entity": att,
                                "text": get_text_value(att),
                                "point": get_text_point(att),
                                "layer": att.dxf.layer,
                                "type": "ATTRIB",
                                "source": "insert_attrib",
                                "parent_insert": e,
                            })
                except Exception:
                    pass

                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]

                        for be in block:
                            if be.dxftype() in ("TEXT", "MTEXT"):
                                if not parent_layer_matches and be.dxf.layer not in (layer_name, "0"):
                                    continue

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
                    pass

        except Exception:
            continue

    return texts


def extract_circles(doc, layer_name):
    circles = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "CIRCLE":
                if e.dxf.layer != layer_name:
                    continue

                c = e.dxf.center

                circles.append({
                    "entity": e,
                    "center": (float(c.x), float(c.y)),
                    "radius": float(e.dxf.radius),
                    "layer": e.dxf.layer,
                    "source": "modelspace",
                })

            elif e.dxftype() == "INSERT":
                parent_layer_matches = e.dxf.layer == layer_name

                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]

                        for be in block:
                            if be.dxftype() == "CIRCLE":
                                if not parent_layer_matches and be.dxf.layer not in (layer_name, "0"):
                                    continue

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
                    pass

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


def resolve_line_group(group):
    best = max(group, key=lambda x: x["length"])
    out = dict(best)
    out["coord"] = round(sum(g["coord"] for g in group) / len(group), 3)
    return out


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


# =========================================================
# MARKER DETECTION
# =========================================================

def text_inside_circle(text_point, circle_center, circle_radius, extra_gap=180.0):
    return euclidean(text_point, circle_center) <= circle_radius + extra_gap


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

    elif line["orientation"] == "horizontal":
        if abs(line["coord"] - cy) > attach_gap:
            return False

        if not point_projects_on_segment(cx, cy, x1, y1, x2, y2, pad=circle_radius + attach_gap):
            return False

    else:
        return False

    d_seg = point_to_segment_distance(cx, cy, x1, y1, x2, y2)
    d1 = abs(euclidean((x1, y1), circle_center) - circle_radius)
    d2 = abs(euclidean((x2, y2), circle_center) - circle_radius)

    return min(abs(d_seg), d1, d2) <= attach_gap


def closest_grid_line_to_circle(circle_center, lines, attach_gap):
    if not lines:
        return None, None, None

    cx, cy = circle_center
    best = None

    for ln in lines:
        x1, y1 = ln["start"]
        x2, y2 = ln["end"]

        if ln["orientation"] == "vertical":
            perp = abs(ln["coord"] - cx)
            span_min = min(y1, y2)
            span_max = max(y1, y2)

            if span_min - attach_gap * 8 <= cy <= span_max + attach_gap * 8:
                span_penalty = 0.0
            else:
                span_penalty = min(abs(cy - span_min), abs(cy - span_max))

        else:
            perp = abs(ln["coord"] - cy)
            span_min = min(x1, x2)
            span_max = max(x1, x2)

            if span_min - attach_gap * 8 <= cx <= span_max + attach_gap * 8:
                span_penalty = 0.0
            else:
                span_penalty = min(abs(cx - span_min), abs(cx - span_max))

        score = perp + 0.10 * span_penalty

        if best is None or score < best["score"]:
            best = {
                "line": ln,
                "score": score,
                "perp": perp,
            }

    return best["line"], best["score"], best["perp"]


def infer_orientation_from_same_label(bubble, bubbles, text_gap):
    label = bubble["label"]
    cx, cy = bubble["circle_center"]

    align_tol = max(text_gap * 4.0, 750.0)
    min_span = 1000.0

    vertical_candidates = []
    horizontal_candidates = []

    for other in bubbles:
        if other is bubble:
            continue

        if other["label"] != label:
            continue

        ox, oy = other["circle_center"]
        dx = abs(cx - ox)
        dy = abs(cy - oy)

        if dx <= align_tol and dy >= min_span:
            vertical_candidates.append((dx, dy, (cx + ox) / 2.0))

        if dy <= align_tol and dx >= min_span:
            horizontal_candidates.append((dy, dx, (cy + oy) / 2.0))

    if vertical_candidates and not horizontal_candidates:
        vertical_candidates.sort(key=lambda x: (x[0], -x[1]))
        return "vertical", round(float(vertical_candidates[0][2]), 3)

    if horizontal_candidates and not vertical_candidates:
        horizontal_candidates.sort(key=lambda x: (x[0], -x[1]))
        return "horizontal", round(float(horizontal_candidates[0][2]), 3)

    if vertical_candidates and horizontal_candidates:
        vertical_candidates.sort(key=lambda x: (x[0], -x[1]))
        horizontal_candidates.sort(key=lambda x: (x[0], -x[1]))

        if vertical_candidates[0][0] <= horizontal_candidates[0][0]:
            return "vertical", round(float(vertical_candidates[0][2]), 3)

        return "horizontal", round(float(horizontal_candidates[0][2]), 3)

    return None, None


def make_virtual_axis(circle_center, orientation, coord, line_layer):
    cx, cy = circle_center

    if orientation == "vertical":
        return {
            "entity": None,
            "orientation": "vertical",
            "coord": round(float(coord), 3),
            "start": (float(coord), float(cy - 1000.0)),
            "end": (float(coord), float(cy + 1000.0)),
            "length": 1000.0,
            "layer": line_layer,
            "virtual": True,
        }

    return {
        "entity": None,
        "orientation": "horizontal",
        "coord": round(float(coord), 3),
        "start": (float(cx - 1000.0), float(coord)),
        "end": (float(cx + 1000.0), float(coord)),
        "length": 1000.0,
        "layer": line_layer,
        "virtual": True,
    }


def build_trusted_markers(doc, line_layer, text_layer, circle_layer, min_grid_length, text_gap, attach_gap):
    texts = extract_texts(doc, text_layer)
    circles = extract_circles(doc, circle_layer)
    lines = extract_axis_lines(doc, line_layer, min_length=min_grid_length)

    bubbles = []
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

        if len(candidate_texts) >= 1:
            # Important fix:
            # If overlapping bubbles put A/B or other close texts inside one circle,
            # do not reject the marker. Pick the text closest to the circle center.
            candidate_texts = sorted(
                candidate_texts,
                key=lambda t: euclidean(t["point"], circle_center)
            )

            t = candidate_texts[0]

            bubbles.append({
                "label": clean_text(t["text"]),
                "text_entity": t["entity"],
                "text_point": t["point"],
                "text_source": t.get("source", "unknown"),
                "parent_insert": t.get("parent_insert"),
                "circle_entity": c["entity"],
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "circle_source": c.get("source", "unknown"),
                "text_matches_before_closest_pick": len(candidate_texts),
                "candidate_texts": [ct["text"] for ct in candidate_texts[:10]],
            })

        else:
            rejected.append({
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "text_matches": 0,
                "line_matches": 0,
                "candidate_texts": [],
                "circle_source": c.get("source", "unknown"),
                "reason": "No valid text in marker",
            })

    trusted_markers = []

    for b in bubbles:
        circle_center = b["circle_center"]
        circle_radius = b["circle_radius"]
        attached_candidates = []

        for line in lines:
            if line_attached_to_circle(line, circle_center, circle_radius, attach_gap=attach_gap):
                attached_candidates.append(line)

        selected_line = None
        detection_mode = ""

        if attached_candidates:
            selected_line = max(attached_candidates, key=lambda ln: ln["length"])
            detection_mode = "attached_gridline"

        else:
            closest_line, score, perp = closest_grid_line_to_circle(circle_center, lines, attach_gap)

            # Safer than the old 2500 / attach_gap*12 setting.
            relaxed_limit = max(attach_gap * 6.0, 1200.0)

            if closest_line is not None and perp is not None and perp <= relaxed_limit:
                selected_line = closest_line
                detection_mode = "closest_gridline"
            else:
                orient, coord = infer_orientation_from_same_label(b, bubbles, text_gap)

                if orient is not None:
                    selected_line = make_virtual_axis(circle_center, orient, coord, line_layer)
                    detection_mode = "same_label_virtual_axis"

        if selected_line is None:
            rejected.append({
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "text_matches": b.get("text_matches_before_closest_pick", 1),
                "line_matches": 0,
                "candidate_texts": b.get("candidate_texts", [b["label"]]),
                "circle_source": b.get("circle_source", "unknown"),
                "reason": "Text found, but no attached/closest/same-label gridline could be inferred",
            })
            continue

        trusted_markers.append({
            "label": b["label"],
            "text_entity": b["text_entity"],
            "text_point": b["text_point"],
            "text_source": b.get("text_source", "unknown"),
            "parent_insert": b.get("parent_insert"),
            "circle_entity": b["circle_entity"],
            "circle_center": b["circle_center"],
            "circle_radius": b["circle_radius"],
            "circle_source": b.get("circle_source", "unknown"),
            "line_entity": selected_line.get("entity"),
            "orientation": selected_line["orientation"],
            "coord": selected_line["coord"],
            "line_start": selected_line["start"],
            "line_end": selected_line["end"],
            "line_length": selected_line["length"],
            "detection_mode": detection_mode,
            "text_matches_before_closest_pick": b.get("text_matches_before_closest_pick", 1),
            "candidate_texts": b.get("candidate_texts", [b["label"]]),
        })

    return {
        "texts": texts,
        "circles": circles,
        "lines": lines,
        "trusted_markers": trusted_markers,
        "rejected_markers": rejected,
    }


# =========================================================
# AXIS GROUPING / FAMILY / SYNC
# =========================================================

def sort_axis_groups_for_family(groups, orientation, family):
    """
    Sorting rule:
    - Numeric vertical axes: left-to-right, coord ascending.
    - Numeric horizontal axes: top-to-bottom, coord descending.
    - Alphabetic vertical axes: left-to-right, coord ascending.
    - Alphabetic horizontal axes: bottom-to-top, coord ascending.

    This fixes cases where numeric horizontal grids were syncing as 10,9,8... instead of 1,2,3...
    """
    reverse = False

    if family == "numeric" and orientation == "horizontal":
        reverse = True

    return sorted(groups, key=lambda x: x["coord"], reverse=reverse)


def resolve_axis_group(group, orientation):
    coord = round(sum(m["coord"] for m in group) / len(group), 3)

    labels = [m["label"] for m in group if clean_text(m["label"])]
    label_counts = {}

    for label in labels:
        label_counts[label] = label_counts.get(label, 0) + 1

    label = sorted(label_counts.items(), key=lambda x: (-x[1], x[0]))[0][0] if label_counts else ""
    representative = max(group, key=lambda x: x.get("line_length", 0.0))
    writable_markers = [m for m in group if is_entity_writable(m)]

    xs = [m["circle_center"][0] for m in group]
    ys = [m["circle_center"][1] for m in group]

    return {
        "orientation": orientation,
        "coord": coord,
        "label": label,
        "markers": group,
        "marker_count": len(group),
        "writable_marker_count": len(writable_markers),
        "representative": representative,
        "bbox": {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        },
        "centroid": (sum(xs) / len(xs), sum(ys) / len(ys)),
    }


def group_markers_by_axis(markers, tol=5.0):
    grouped = {"vertical": [], "horizontal": []}

    if not markers:
        return grouped

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

    numeric_arch = sort_axis_groups_for_family(
        arch_groups.get(numeric_orientation, []),
        numeric_orientation,
        "numeric"
    )

    alpha_arch = sort_axis_groups_for_family(
        arch_groups.get(alpha_orientation, []),
        alpha_orientation,
        "alpha"
    )

    return {
        "numeric_orientation": numeric_orientation,
        "alpha_orientation": alpha_orientation,
        "numeric_arch": numeric_arch,
        "alpha_arch": alpha_arch,
        "vertical_counts": {"numeric": v_num, "alpha": v_alpha},
        "horizontal_counts": {"numeric": h_num, "alpha": h_alpha},
    }


def build_axis_mapping_preview(
    source_axis_groups,
    target_axis_groups,
    family_name,
    region_name="Structural",
    source_name="Architecture"
):
    preview = []
    count = min(len(source_axis_groups), len(target_axis_groups))

    for i in range(count):
        preview.append({
            "target_region": region_name,
            "source_reference": source_name,
            "family": family_name,
            "position": i + 1,
            "source_label": source_axis_groups[i]["label"],
            "target_old_label": target_axis_groups[i]["label"],
            "source_coord": source_axis_groups[i]["coord"],
            "target_coord": target_axis_groups[i]["coord"],
            "source_visible_markers": source_axis_groups[i]["marker_count"],
            "target_visible_markers": target_axis_groups[i]["marker_count"],
            "target_writable_markers": target_axis_groups[i]["writable_marker_count"],
        })

    return preview


def build_axis_count_warnings(matched_regions):
    warnings = []

    for region in matched_regions:
        source_num = len(region.get("source_numeric_groups", []))
        target_num = len(region.get("numeric_groups", []))
        source_alpha = len(region.get("source_alpha_groups", []))
        target_alpha = len(region.get("alpha_groups", []))

        if source_num != target_num:
            warnings.append({
                "region": region["name"],
                "family": "numeric",
                "source_reference": region.get("source_reference", ""),
                "source_count": source_num,
                "target_count": target_num,
                "message": f"Numeric axis count mismatch in {region['name']}: source has {source_num}, target has {target_num}.",
            })

        if source_alpha != target_alpha:
            warnings.append({
                "region": region["name"],
                "family": "alphabetic",
                "source_reference": region.get("source_reference", ""),
                "source_count": source_alpha,
                "target_count": target_alpha,
                "message": f"Alphabetic axis count mismatch in {region['name']}: source has {source_alpha}, target has {target_alpha}.",
            })

    return warnings


def validate_matched_regions_before_apply(matched_regions):
    problems = []

    for region in matched_regions:
        source_num = len(region.get("source_numeric_groups", []))
        target_num = len(region.get("numeric_groups", []))

        source_alpha = len(region.get("source_alpha_groups", []))
        target_alpha = len(region.get("alpha_groups", []))

        if source_num != target_num:
            problems.append({
                "region": region.get("name", ""),
                "family": "numeric",
                "source_count": source_num,
                "target_count": target_num,
                "problem": f"Numeric axis mismatch: source has {source_num}, target has {target_num}."
            })

        if source_alpha != target_alpha:
            problems.append({
                "region": region.get("name", ""),
                "family": "alphabetic",
                "source_count": source_alpha,
                "target_count": target_alpha,
                "problem": f"Alphabetic axis mismatch: source has {source_alpha}, target has {target_alpha}."
            })

    return problems


def apply_axis_group_labels(source_axis_groups, target_axis_groups, writable_only=True):
    count = min(len(source_axis_groups), len(target_axis_groups))
    changed = 0
    skipped_non_writable = 0

    for i in range(count):
        new_label = source_axis_groups[i]["label"]
        target_group = target_axis_groups[i]

        for marker in target_group["markers"]:
            if writable_only and not is_entity_writable(marker):
                skipped_non_writable += 1
                continue

            entity = marker["text_entity"]

            try:
                old = get_text_value(entity)

                if old != new_label:
                    set_text_value(entity, new_label)
                    changed += 1

            except Exception:
                continue

    return changed, skipped_non_writable


def build_propagated_axis_groups(source_axis_groups, target_axis_groups):
    propagated = []
    count = min(len(source_axis_groups), len(target_axis_groups))

    for i, g in enumerate(target_axis_groups):
        new_g = dict(g)

        if i < count:
            new_g["label"] = source_axis_groups[i]["label"]

        propagated.append(new_g)

    return propagated


# =========================================================
# STRUCTURAL PLAN SEGMENTATION
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


def marker_center_xy(marker):
    cx, cy = marker["circle_center"]
    return float(cx), float(cy)


def squared_distance(p1, p2):
    return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2


def cluster_markers_by_expected_count(trusted_markers, expected_region_markers, max_iter=80):
    """
    Marker-count-aware spatial clustering.

    Example:
    architectural reference = 17 trusted markers
    structural target = 119 trusted markers
    119 / 17 = 7 regions
    """
    if not trusted_markers:
        return None

    if not expected_region_markers or expected_region_markers <= 0:
        return None

    total = len(trusted_markers)
    estimated_regions_float = total / expected_region_markers
    estimated_regions = int(round(estimated_regions_float))

    if estimated_regions < 2:
        return None

    # Only use this if total markers are close to a whole number of reference plans.
    if abs(estimated_regions_float - estimated_regions) > 0.20:
        return None

    points = [marker_center_xy(m) for m in trusted_markers]

    # Deterministic farthest-point initialization.
    first_index = min(range(len(points)), key=lambda i: (points[i][0] + points[i][1], points[i][0]))
    centers = [points[first_index]]

    while len(centers) < estimated_regions:
        farthest_index = max(
            range(len(points)),
            key=lambda i: min(squared_distance(points[i], c) for c in centers)
        )
        centers.append(points[farthest_index])

    clusters = [[] for _ in range(estimated_regions)]

    for _ in range(max_iter):
        new_clusters = [[] for _ in range(estimated_regions)]

        for marker, point in zip(trusted_markers, points):
            nearest_index = min(
                range(estimated_regions),
                key=lambda i: squared_distance(point, centers[i])
            )
            new_clusters[nearest_index].append(marker)

        if any(len(c) == 0 for c in new_clusters):
            return None

        new_centers = []

        for cluster in new_clusters:
            xs = [marker_center_xy(m)[0] for m in cluster]
            ys = [marker_center_xy(m)[1] for m in cluster]
            new_centers.append((sum(xs) / len(xs), sum(ys) / len(ys)))

        movement = sum(squared_distance(a, b) for a, b in zip(centers, new_centers))
        centers = new_centers
        clusters = new_clusters

        if movement < 1e-6:
            break

    # Require clusters to be reasonably close to the expected marker count.
    allowed_difference = max(3, int(expected_region_markers * 0.30))

    for cluster in clusters:
        if abs(len(cluster) - expected_region_markers) > allowed_difference:
            return None

    return clusters


def build_structural_regions_by_empty_space(
    trusted_markers,
    min_region_markers=6,
    expected_region_markers=None,
):
    if not trusted_markers:
        return [], {}

    # FIRST TRY: marker-count-aware clustering.
    count_based_clusters = cluster_markers_by_expected_count(
        trusted_markers,
        expected_region_markers=expected_region_markers,
    )

    if count_based_clusters:
        regions = []
        sortable_clusters = []

        for cluster in count_based_clusters:
            bbox = bbox_from_markers(cluster)
            cx, cy = bbox["centroid"]
            sortable_clusters.append((cy, cx, cluster, bbox))

        # Top-to-bottom, then left-to-right.
        sortable_clusters.sort(key=lambda item: (-item[0], item[1]))

        for i, (_, _, markers, bbox) in enumerate(sortable_clusters, start=1):
            axis_groups = group_markers_by_axis(markers)

            regions.append({
                "name": f"Region {i}",
                "markers": markers,
                "axis_groups": axis_groups,
                "bbox": bbox,
                "grid_bucket": ("count_cluster", i),
                "marker_count": len(markers),
            })

        return regions, {
            "segmentation_method": "marker_count_spatial_clustering",
            "expected_region_markers": expected_region_markers,
            "total_trusted_markers": len(trusted_markers),
            "estimated_region_count": len(regions),
            "cluster_marker_counts": [len(r["markers"]) for r in regions],
            "regions_kept": len(regions),
            "min_region_markers": min_region_markers,
        }

    # FALLBACK: original empty-space method.
    xs = [m["circle_center"][0] for m in trusted_markers]
    ys = [m["circle_center"][1] for m in trusted_markers]

    x_gap_threshold = compute_major_gap_threshold(xs) or 6000.0
    y_gap_threshold = compute_major_gap_threshold(ys) or 6000.0

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
        if len(markers) < min_region_markers:
            skipped_small += 1
            continue

        axis_groups = group_markers_by_axis(markers)
        bbox = bbox_from_markers(markers)

        regions.append({
            "name": f"Region {n}",
            "markers": markers,
            "axis_groups": axis_groups,
            "bbox": bbox,
            "grid_bucket": key,
            "marker_count": len(markers),
        })

        n += 1

    return regions, {
        "segmentation_method": "empty_space_fallback",
        "expected_region_markers": expected_region_markers,
        "total_trusted_markers": len(trusted_markers),
        "x_gap_threshold": x_gap_threshold,
        "y_gap_threshold": y_gap_threshold,
        "x_group_count": len(x_groups),
        "y_group_count": len(y_groups),
        "raw_bucket_count": len(buckets),
        "regions_kept": len(regions),
        "small_regions_skipped": skipped_small,
        "min_region_markers": min_region_markers,
    }


def get_region_family_groups(region, numeric_orientation, alpha_orientation, strict_label_filter=False):
    """
    Structural target axes are selected by orientation, not by current label type.
    This allows wrong structural labels to still be synced.
    """
    axis_groups = region["axis_groups"]

    numeric_groups = sort_axis_groups_for_family(
        axis_groups.get(numeric_orientation, []),
        numeric_orientation,
        "numeric"
    )

    alpha_groups = sort_axis_groups_for_family(
        axis_groups.get(alpha_orientation, []),
        alpha_orientation,
        "alpha"
    )

    if strict_label_filter:
        numeric_groups = [g for g in numeric_groups if is_numeric_label(g["label"])]
        alpha_groups = [g for g in alpha_groups if is_alpha_label(g["label"])]

    return numeric_groups, alpha_groups


def score_region_for_anchor(region, numeric_arch_groups, alpha_arch_groups, numeric_orientation, alpha_orientation):
    numeric_groups, alpha_groups = get_region_family_groups(
        region,
        numeric_orientation,
        alpha_orientation,
        strict_label_filter=False
    )

    rn = len(numeric_groups)
    ra = len(alpha_groups)
    an = len(numeric_arch_groups)
    aa = len(alpha_arch_groups)

    num_score = min(rn, an) / max(rn, an) if rn and an else 0.0
    alp_score = min(ra, aa) / max(ra, aa) if ra and aa else 0.0

    expected_marker_count = max(1, an + aa)
    marker_score = min(1.0, region["marker_count"] / expected_marker_count)

    full_count_bonus = 0.2 if (rn == an and ra == aa) else 0.0

    total = round(0.35 * num_score + 0.35 * alp_score + 0.10 * marker_score + full_count_bonus, 3)

    return total, numeric_groups, alpha_groups


def prepare_propagator_sync(
    structural_regions,
    numeric_arch_groups,
    alpha_arch_groups,
    numeric_orientation,
    alpha_orientation,
):
    matched_regions = []
    mapping_preview = []
    region_match_report = []

    if not structural_regions:
        return None, matched_regions, mapping_preview, region_match_report

    scored = []

    for region in structural_regions:
        score, numeric_groups, alpha_groups = score_region_for_anchor(
            region,
            numeric_arch_groups,
            alpha_arch_groups,
            numeric_orientation,
            alpha_orientation,
        )

        scored.append({
            "score": score,
            "region": region,
            "numeric_groups": numeric_groups,
            "alpha_groups": alpha_groups,
            "complete": (
                len(numeric_groups) == len(numeric_arch_groups)
                and len(alpha_groups) == len(alpha_arch_groups)
            )
        })

    # Prefer complete regions as anchor. If none complete, pick best but mark as problematic.
    complete_items = [item for item in scored if item["complete"]]
    candidate_pool = complete_items if complete_items else scored
    candidate_pool.sort(key=lambda x: x["score"], reverse=True)

    anchor_item = candidate_pool[0]
    anchor_region = anchor_item["region"]
    anchor_numeric = anchor_item["numeric_groups"]
    anchor_alpha = anchor_item["alpha_groups"]

    synced_anchor_numeric = build_propagated_axis_groups(numeric_arch_groups, anchor_numeric)
    synced_anchor_alpha = build_propagated_axis_groups(alpha_arch_groups, anchor_alpha)

    anchor_complete = (
        len(anchor_numeric) == len(numeric_arch_groups)
        and len(anchor_alpha) == len(alpha_arch_groups)
    )

    if anchor_complete:
        matched_regions.append({
            "name": anchor_region["name"],
            "bbox": anchor_region["bbox"],
            "numeric_groups": anchor_numeric,
            "alpha_groups": anchor_alpha,
            "source_numeric_groups": numeric_arch_groups,
            "source_alpha_groups": alpha_arch_groups,
            "synced_numeric_groups": synced_anchor_numeric,
            "synced_alpha_groups": synced_anchor_alpha,
            "grid_bucket": anchor_region["grid_bucket"],
            "match_mode": "anchor",
            "source_reference": "Architecture",
            "marker_count": anchor_region["marker_count"],
        })

        mapping_preview.extend(build_axis_mapping_preview(
            numeric_arch_groups,
            anchor_numeric,
            "numeric",
            anchor_region["name"],
            "Architecture"
        ))

        mapping_preview.extend(build_axis_mapping_preview(
            alpha_arch_groups,
            anchor_alpha,
            "alphabetic",
            anchor_region["name"],
            "Architecture"
        ))

    for item in scored:
        region = item["region"]
        numeric_groups = item["numeric_groups"]
        alpha_groups = item["alpha_groups"]
        writable_count = len([m for m in region["markers"] if is_entity_writable(m)])

        if region["name"] == anchor_region["name"]:
            classification = "anchor" if anchor_complete else "rejected"
            reason = (
                "Best complete structural region selected as propagator anchor."
                if anchor_complete
                else "Best anchor candidate is incomplete, so sync is blocked for this anchor."
            )
            will_sync = anchor_complete

            region_match_report.append({
                "region": region["name"],
                "classification": classification,
                "source_reference": "Architecture",
                "trusted_markers": region["marker_count"],
                "writable_markers": writable_count,
                "numeric_axes": len(numeric_groups),
                "alphabetic_axes": len(alpha_groups),
                "expected_numeric_axes": len(numeric_arch_groups),
                "expected_alphabetic_axes": len(alpha_arch_groups),
                "score": item["score"],
                "reason": reason,
                "will_sync": will_sync,
            })
            continue

        usable = (
            anchor_complete
            and len(numeric_groups) == len(synced_anchor_numeric)
            and len(alpha_groups) == len(synced_anchor_alpha)
        )

        if usable:
            matched_regions.append({
                "name": region["name"],
                "bbox": region["bbox"],
                "numeric_groups": numeric_groups,
                "alpha_groups": alpha_groups,
                "source_numeric_groups": synced_anchor_numeric,
                "source_alpha_groups": synced_anchor_alpha,
                "grid_bucket": region["grid_bucket"],
                "match_mode": "propagated",
                "source_reference": anchor_region["name"],
                "marker_count": region["marker_count"],
            })

            mapping_preview.extend(build_axis_mapping_preview(
                synced_anchor_numeric,
                numeric_groups,
                "numeric",
                region["name"],
                anchor_region["name"]
            ))

            mapping_preview.extend(build_axis_mapping_preview(
                synced_anchor_alpha,
                alpha_groups,
                "alphabetic",
                region["name"],
                anchor_region["name"]
            ))

            classification = "propagated"
            reason = f"Complete region synced from propagator {anchor_region['name']}."
            will_sync = True

        else:
            classification = "rejected"
            reason = (
                "Incomplete region or axis count mismatch. "
                f"Expected numeric={len(synced_anchor_numeric)}, alphabetic={len(synced_anchor_alpha)}; "
                f"found numeric={len(numeric_groups)}, alphabetic={len(alpha_groups)}."
            )
            will_sync = False

        region_match_report.append({
            "region": region["name"],
            "classification": classification,
            "source_reference": anchor_region["name"],
            "trusted_markers": region["marker_count"],
            "writable_markers": writable_count,
            "numeric_axes": len(numeric_groups),
            "alphabetic_axes": len(alpha_groups),
            "expected_numeric_axes": len(synced_anchor_numeric),
            "expected_alphabetic_axes": len(synced_anchor_alpha),
            "score": item["score"],
            "reason": reason,
            "will_sync": will_sync,
        })

    return {"region": anchor_region, "score": anchor_item["score"]}, matched_regions, mapping_preview, region_match_report


def prepare_direct_sync(
    structural_regions,
    numeric_arch_groups,
    alpha_arch_groups,
    numeric_orientation,
    alpha_orientation,
):
    matched_regions = []
    mapping_preview = []
    region_match_report = []

    for region in structural_regions:
        numeric_groups, alpha_groups = get_region_family_groups(
            region,
            numeric_orientation,
            alpha_orientation,
            strict_label_filter=False
        )

        writable_count = len([m for m in region["markers"] if is_entity_writable(m)])

        usable = (
            len(numeric_groups) == len(numeric_arch_groups)
            and len(alpha_groups) == len(alpha_arch_groups)
        )

        if usable:
            matched_regions.append({
                "name": region["name"],
                "bbox": region["bbox"],
                "numeric_groups": numeric_groups,
                "alpha_groups": alpha_groups,
                "source_numeric_groups": numeric_arch_groups,
                "source_alpha_groups": alpha_arch_groups,
                "grid_bucket": region["grid_bucket"],
                "match_mode": "direct",
                "source_reference": "Architecture",
                "marker_count": region["marker_count"],
            })

            mapping_preview.extend(build_axis_mapping_preview(
                numeric_arch_groups,
                numeric_groups,
                "numeric",
                region["name"],
                "Architecture"
            ))

            mapping_preview.extend(build_axis_mapping_preview(
                alpha_arch_groups,
                alpha_groups,
                "alphabetic",
                region["name"],
                "Architecture"
            ))

            classification = "direct"
            reason = "Complete region. Direct Architecture to Structural region sync."
            will_sync = True
            score = 1.0

        else:
            classification = "rejected"
            reason = (
                "Incomplete region or axis count mismatch. "
                f"Expected numeric={len(numeric_arch_groups)}, alphabetic={len(alpha_arch_groups)}; "
                f"found numeric={len(numeric_groups)}, alphabetic={len(alpha_groups)}."
            )
            will_sync = False
            score = 0.0

        region_match_report.append({
            "region": region["name"],
            "classification": classification,
            "source_reference": "Architecture",
            "trusted_markers": region["marker_count"],
            "writable_markers": writable_count,
            "numeric_axes": len(numeric_groups),
            "alphabetic_axes": len(alpha_groups),
            "expected_numeric_axes": len(numeric_arch_groups),
            "expected_alphabetic_axes": len(alpha_arch_groups),
            "score": score,
            "reason": reason,
            "will_sync": will_sync,
        })

    return None, matched_regions, mapping_preview, region_match_report


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
        "apply_ready": False,
        "labels_changed_count": 0,
        "arch_sig": None,
        "struc_sig": None,
        "last_apply_message": "",
        "family_summary": {},
        "numeric_arch_groups": [],
        "alpha_arch_groups": [],
        "structural_regions": [],
        "segmentation_summary": {},
        "anchor_region": None,
        "matched_regions": [],
        "region_match_report": [],
        "skipped_non_writable_count": 0,
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
        "apply_ready",
        "labels_changed_count",
        "arch_sig",
        "struc_sig",
        "last_apply_message",
        "family_summary",
        "numeric_arch_groups",
        "alpha_arch_groups",
        "structural_regions",
        "segmentation_summary",
        "anchor_region",
        "matched_regions",
        "region_match_report",
        "skipped_non_writable_count",
    ]

    for key in keys:
        if key in st.session_state:
            del st.session_state[key]


def clear_sync_outputs():
    st.session_state.arch_detection = {}
    st.session_state.struc_detection = {}
    st.session_state.arch_axis_groups = {}
    st.session_state.mapping_preview = []
    st.session_state.apply_ready = False
    st.session_state.labels_changed_count = 0
    st.session_state.last_apply_message = ""
    st.session_state.family_summary = {}
    st.session_state.numeric_arch_groups = []
    st.session_state.alpha_arch_groups = []
    st.session_state.structural_regions = []
    st.session_state.segmentation_summary = {}
    st.session_state.anchor_region = None
    st.session_state.matched_regions = []
    st.session_state.region_match_report = []
    st.session_state.skipped_non_writable_count = 0


def uploaded_file_signature(uploaded_file):
    if uploaded_file is None:
        return None

    return uploaded_file.name, len(uploaded_file.getvalue())


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
        "Hybrid detection: circle + closest valid grid text + attached gridline OR closest gridline. "
        "Incomplete regions are blocked from syncing."
    )

    init_sync_state()

    strategy = st.radio(
        "Sync Strategy",
        [
            "Option B - Propagator Chain Sync",
            "Option A - Direct Multi-Plan Sync",
        ],
        index=0,
    )

    c1, c2, c3 = st.columns(3)

    with c1:
        tolerance = st.slider("Tolerance (mm)", 0.0, 500.0, 10.0, 0.5)

    with c2:
        text_gap = st.slider("Text-in-Marker Gap (mm)", 20.0, 1500.0, 180.0, 10.0)

    with c3:
        attach_gap = st.slider("Attached/Closest Grid Gap (mm)", 20.0, 3000.0, 180.0, 10.0)

    d1, d2, d3 = st.columns(3)

    with d1:
        min_grid_length = st.number_input(
            "Minimum Grid Line Length (mm)",
            min_value=1.0,
            value=1000.0,
            step=100.0
        )

    with d2:
        min_region_markers = st.number_input(
            "Minimum Markers Per Region",
            min_value=1,
            value=6,
            step=1
        )

    with d3:
        writable_only = st.checkbox(
            "Write only to safe writable text entities",
            value=True
        )

    col_a, col_b = st.columns(2)

    with col_a:
        arch_file = st.file_uploader(
            "Reference (Architectural DXF)",
            type=["dxf"],
            key="arch"
        )

    with col_b:
        struc_file = st.file_uploader(
            "Target (Structural DXF)",
            type=["dxf"],
            key="struc"
        )

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

            arch_line_default = pick_default_layer(arch_layers, ["S-GRID", "GRID", "GRIDLINELAYER"])
            arch_text_default = pick_default_layer(arch_layers, ["S-GRID-IDEN", "DEFAULTLAYER", "GRID-ID"])
            arch_circle_default = pick_default_layer(arch_layers, ["S-GRID-IDEN", "DEFAULTLAYER", "GRID-ID"])

            struc_line_default = pick_default_layer(struc_layers, ["GRIDLINELAYER", "S-GRID", "GRID"])
            struc_text_default = pick_default_layer(struc_layers, ["DEFAULTLAYER", "S-STRS-IDEN", "S-GRID-IDEN", "GRID-ID"])
            struc_circle_default = pick_default_layer(struc_layers, ["DEFAULTLAYER", "S-GRID-IDEN", "GRID-ID"])

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

                        if arch_detection["rejected_markers"]:
                            st.warning(
                                f"Reference drawing has {len(arch_detection['rejected_markers'])} rejected marker(s). "
                                "The reference may be incomplete. Review rejected markers before applying sync."
                            )

                        if struc_detection["rejected_markers"]:
                            st.warning(
                                f"Structural drawing has {len(struc_detection['rejected_markers'])} rejected marker(s). "
                                "Some regions may be incomplete."
                            )

                        arch_axis_groups = group_markers_by_axis(
                            arch_detection["trusted_markers"],
                            tol=tolerance
                        )

                        st.session_state.arch_axis_groups = arch_axis_groups

                        fam = infer_arch_families(arch_axis_groups)
                        st.session_state.family_summary = fam

                        numeric_orientation = fam["numeric_orientation"]
                        alpha_orientation = fam["alpha_orientation"]

                        numeric_arch_groups = sort_axis_groups_for_family(
                            arch_axis_groups.get(numeric_orientation, []),
                            numeric_orientation,
                            "numeric"
                        )

                        alpha_arch_groups = sort_axis_groups_for_family(
                            arch_axis_groups.get(alpha_orientation, []),
                            alpha_orientation,
                            "alpha"
                        )

                        st.session_state.numeric_arch_groups = numeric_arch_groups
                        st.session_state.alpha_arch_groups = alpha_arch_groups

                        expected_region_markers = len(arch_detection["trusted_markers"])

                        structural_regions, segmentation_summary = build_structural_regions_by_empty_space(
                            struc_detection["trusted_markers"],
                            min_region_markers=min_region_markers,
                            expected_region_markers=expected_region_markers,
                        )

                        st.session_state.structural_regions = structural_regions
                        st.session_state.segmentation_summary = segmentation_summary

                        if strategy == "Option A - Direct Multi-Plan Sync":
                            anchor_candidate, matched_regions, mapping_preview, region_match_report = prepare_direct_sync(
                                structural_regions,
                                numeric_arch_groups,
                                alpha_arch_groups,
                                numeric_orientation,
                                alpha_orientation,
                            )
                        else:
                            anchor_candidate, matched_regions, mapping_preview, region_match_report = prepare_propagator_sync(
                                structural_regions,
                                numeric_arch_groups,
                                alpha_arch_groups,
                                numeric_orientation,
                                alpha_orientation,
                            )

                        st.session_state.anchor_region = anchor_candidate
                        st.session_state.matched_regions = matched_regions
                        st.session_state.mapping_preview = mapping_preview
                        st.session_state.region_match_report = region_match_report
                        st.session_state.apply_ready = len(matched_regions) > 0

                        if matched_regions:
                            st.success(
                                f"Detected {len(structural_regions)} structural region(s). "
                                f"{len(matched_regions)} complete region(s) are ready for sync."
                            )
                        else:
                            st.warning(
                                f"Detected {len(structural_regions)} structural region(s), "
                                f"but 0 complete region(s) qualified for sync. Check diagnostics below."
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
                    f"Alphabetic family = {fam['alpha_orientation']} axes. "
                    f"Vertical counts = {fam['vertical_counts']}, "
                    f"Horizontal counts = {fam['horizontal_counts']}."
                )

            if st.session_state.anchor_region is not None:
                st.markdown("### Propagator / Anchor Region")

                try:
                    anchor_region = st.session_state.anchor_region["region"]

                    st.write({
                        "anchor_region": anchor_region["name"],
                        "score": st.session_state.anchor_region["score"],
                        "trusted_markers": len(anchor_region["markers"]),
                        "reason": "This structural region is selected as the propagator anchor only if complete.",
                    })

                except Exception:
                    pass

            if st.session_state.segmentation_summary:
                st.markdown("### Segmentation Summary")
                st.write(st.session_state.segmentation_summary)

            if st.session_state.region_match_report:
                st.markdown("### Structural Region Match Report")
                st.dataframe(st.session_state.region_match_report, use_container_width=True)

            st.markdown("---")
            st.markdown("### Sync Preview")

            if st.session_state.mapping_preview:
                st.dataframe(st.session_state.mapping_preview, use_container_width=True)

                axis_count_warnings = build_axis_count_warnings(st.session_state.matched_regions)

                if axis_count_warnings:
                    st.warning(
                        "Some matched regions have different source/target axis counts. "
                        "These will block Apply Sync."
                    )
                    st.dataframe(axis_count_warnings, use_container_width=True)
            else:
                st.info("No sync preview yet, or all regions were rejected as incomplete.")

            st.markdown("### Apply Sync")

            if st.session_state.apply_ready:
                apply_blockers = validate_matched_regions_before_apply(st.session_state.matched_regions)

                if apply_blockers:
                    st.error(
                        "Sync is blocked because at least one matched region has incomplete/mismatched axis counts. "
                        "Fix detection/segmentation first, then prepare again."
                    )
                    st.dataframe(apply_blockers, use_container_width=True)

                else:
                    if st.button("✍️ Apply Label Sync"):
                        try:
                            changed = 0
                            skipped_non_writable = 0

                            for region in st.session_state.matched_regions:
                                c1, s1 = apply_axis_group_labels(
                                    region["source_numeric_groups"],
                                    region["numeric_groups"],
                                    writable_only=writable_only,
                                )

                                c2, s2 = apply_axis_group_labels(
                                    region["source_alpha_groups"],
                                    region["alpha_groups"],
                                    writable_only=writable_only,
                                )

                                changed += c1 + c2
                                skipped_non_writable += s1 + s2

                            st.session_state.labels_changed_count = changed
                            st.session_state.skipped_non_writable_count = skipped_non_writable

                            if changed > 0:
                                st.session_state.last_apply_message = (
                                    f"Label sync complete. {changed} trusted structural marker text entities updated "
                                    f"across {len(st.session_state.matched_regions)} matched region(s)."
                                )
                                st.success(st.session_state.last_apply_message)
                            else:
                                st.info("No text changes were needed, or detected labels already match.")

                            if skipped_non_writable > 0:
                                st.warning(
                                    f"{skipped_non_writable} marker text entities were skipped because they are not safely writable. "
                                    f"If labels are inside block definitions, convert them to attributes or test on a copied DXF."
                                )

                        except Exception as e:
                            st.error(f"Failed to apply label sync: {e}")
            else:
                st.info("Prepare Label Sync first. If no region qualifies, check the Region Match Report.")

            st.markdown("### Download")

            if st.session_state.struc_doc is not None:
                dxf_bytes = write_doc_to_temp_bytes(st.session_state.struc_doc)

                file_label = (
                    "📥 Download Relabeled Structural DXF"
                    if st.session_state.labels_changed_count > 0
                    else "📥 Download Current Structural DXF"
                )

                st.download_button(
                    file_label,
                    data=dxf_bytes,
                    file_name=f"RELABELED_{st.session_state.struc_name}",
                    mime="application/dxf"
                )

            with st.expander("Detection details"):
                arch_det = st.session_state.arch_detection or {}
                struc_det = st.session_state.struc_detection or {}
                arch_groups = st.session_state.arch_axis_groups or {}

                arch_modes = {}

                for m in arch_det.get("trusted_markers", []):
                    mode = m.get("detection_mode", "unknown")
                    arch_modes[mode] = arch_modes.get(mode, 0) + 1

                struc_modes = {}

                for m in struc_det.get("trusted_markers", []):
                    mode = m.get("detection_mode", "unknown")
                    struc_modes[mode] = struc_modes.get(mode, 0) + 1

                st.write("#### Architectural Detection")
                st.write({
                    "texts_on_selected_text_layer": len(arch_det.get("texts", [])),
                    "circles_or_block_markers_on_selected_circle_layer": len(arch_det.get("circles", [])),
                    "axis_lines_on_selected_line_layer": len(arch_det.get("lines", [])),
                    "trusted_markers_found": len(arch_det.get("trusted_markers", [])),
                    "rejected_markers": len(arch_det.get("rejected_markers", [])),
                    "vertical_axes_grouped": len(arch_groups.get("vertical", [])),
                    "horizontal_axes_grouped": len(arch_groups.get("horizontal", [])),
                    "detection_modes": arch_modes,
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
                    "detection_modes": struc_modes,
                })

                if st.session_state.family_summary:
                    st.write("#### Architectural Family Inference")
                    st.write(st.session_state.family_summary)

                if st.session_state.structural_regions:
                    st.write("#### Structural Region Summary")

                    rows = []

                    for r in st.session_state.structural_regions:
                        writable_count = len([m for m in r["markers"] if is_entity_writable(m)])

                        rows.append({
                            "region": r["name"],
                            "trusted_markers": len(r["markers"]),
                            "writable_markers": writable_count,
                            "vertical_axes": len(r["axis_groups"].get("vertical", [])),
                            "horizontal_axes": len(r["axis_groups"].get("horizontal", [])),
                            "bbox_width": round(r["bbox"]["width"], 3),
                            "bbox_height": round(r["bbox"]["height"], 3),
                            "grid_bucket": r["grid_bucket"],
                        })

                    st.dataframe(rows, use_container_width=True)

                if st.session_state.matched_regions:
                    st.write("#### Matched Region Sync Summary")

                    matched_rows = []

                    for r in st.session_state.matched_regions:
                        matched_rows.append({
                            "region": r.get("name"),
                            "match_mode": r.get("match_mode"),
                            "source_reference": r.get("source_reference"),
                            "source_numeric_axes": len(r.get("source_numeric_groups", [])),
                            "target_numeric_axes": len(r.get("numeric_groups", [])),
                            "source_alphabetic_axes": len(r.get("source_alpha_groups", [])),
                            "target_alphabetic_axes": len(r.get("alpha_groups", [])),
                            "target_markers": r.get("marker_count"),
                        })

                    st.dataframe(matched_rows, use_container_width=True)

                if struc_det.get("rejected_markers"):
                    st.write("#### Rejected Structural Markers")
                    st.dataframe(struc_det["rejected_markers"], use_container_width=True)

                if struc_det.get("trusted_markers"):
                    st.write("#### Sample Trusted Structural Markers")

                    sample_rows = []

                    for m in struc_det.get("trusted_markers", [])[:150]:
                        sample_rows.append({
                            "label": m.get("label"),
                            "orientation": m.get("orientation"),
                            "coord": m.get("coord"),
                            "text_source": m.get("text_source"),
                            "detection_mode": m.get("detection_mode"),
                            "text_matches": m.get("text_matches_before_closest_pick", 1),
                            "candidate_texts": m.get("candidate_texts", []),
                            "circle_x": round(m.get("circle_center", (0, 0))[0], 3),
                            "circle_y": round(m.get("circle_center", (0, 0))[1], 3),
                        })

                    st.dataframe(sample_rows, use_container_width=True)

                st.caption(
                    "Hybrid marker authentication: circle + closest valid grid text + attached gridline OR closest gridline. "
                    "Multiple structural plans are segmented by marker count first, then empty space fallback. "
                    "Incomplete regions are blocked from syncing."
                )

    else:
        st.info("Please upload both the Architectural DXF and Structural DXF files.")
