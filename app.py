import streamlit as st
import ezdxf
import os
import tempfile
import math
import re
from statistics import median

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")


# =========================================================
# BASIC HELPERS
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


def clean_text(value):
    if value is None:
        return ""
    txt = str(value).replace("\\P", " ").replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()


def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def pick_default_layer(layers, candidates):
    upper_map = {layer.upper(): layer for layer in layers}
    for c in candidates:
        if c.upper() in upper_map:
            return upper_map[c.upper()]
    return layers[0] if layers else None


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
    return bool(re.match(r"^\d{1,3}[A-Z]?$", clean_text(text)))


def is_alpha_label(text):
    return bool(re.match(r"^[A-Z]{1,3}'?$", clean_text(text)))


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
# DXF TRANSFORM HELPERS
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


# =========================================================
# TEXT HELPERS
# =========================================================
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
# GEOMETRY HELPERS
# =========================================================
def is_vertical(x1, y1, x2, y2, tol=2.0):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def is_horizontal(x1, y1, x2, y2, tol=2.0):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


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
    return (
        min(x1, x2) - pad <= px <= max(x1, x2) + pad
        and min(y1, y2) - pad <= py <= max(y1, y2) + pad
    )


def line_attached_to_circle(line, circle_center, circle_radius, attach_gap=180.0):
    cx, cy = circle_center
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    if line["orientation"] == "vertical":
        if abs(line["coord"] - cx) > attach_gap:
            return False
    elif line["orientation"] == "horizontal":
        if abs(line["coord"] - cy) > attach_gap:
            return False
    else:
        return False

    if not point_projects_on_segment(cx, cy, x1, y1, x2, y2, pad=circle_radius + attach_gap):
        return False

    d_seg = point_to_segment_distance(cx, cy, x1, y1, x2, y2)
    d1 = abs(euclidean((x1, y1), circle_center) - circle_radius)
    d2 = abs(euclidean((x2, y2), circle_center) - circle_radius)

    return min(abs(d_seg), d1, d2) <= attach_gap


# =========================================================
# EXTRACTION
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
                try:
                    for att in e.attribs:
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
                    pass

                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]
                        for be in block:
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
                    pass

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
                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]
                        for be in block:
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
# SMART DYNAMIC MARKER DETECTION
# =========================================================
def make_virtual_line(circle_center, orientation, line_layer):
    cx, cy = circle_center

    if orientation == "vertical":
        return {
            "entity": None,
            "orientation": "vertical",
            "coord": round(float(cx), 3),
            "start": (float(cx), float(cy - 1000.0)),
            "end": (float(cx), float(cy + 1000.0)),
            "length": 1000.0,
            "layer": line_layer,
            "virtual": True,
        }

    return {
        "entity": None,
        "orientation": "horizontal",
        "coord": round(float(cy), 3),
        "start": (float(cx - 1000.0), float(cy)),
        "end": (float(cx + 1000.0), float(cy)),
        "length": 1000.0,
        "layer": line_layer,
        "virtual": True,
    }


def nearest_line_any_orientation(circle_center, lines, attach_gap):
    cx, cy = circle_center
    best = None

    for ln in lines:
        x1, y1 = ln["start"]
        x2, y2 = ln["end"]

        if ln["orientation"] == "vertical":
            main_dist = abs(ln["coord"] - cx)
            span_min = min(y1, y2)
            span_max = max(y1, y2)

            if span_min - attach_gap * 4 <= cy <= span_max + attach_gap * 4:
                projection_penalty = 0.0
            else:
                projection_penalty = min(abs(cy - span_min), abs(cy - span_max))

        else:
            main_dist = abs(ln["coord"] - cy)
            span_min = min(x1, x2)
            span_max = max(x1, x2)

            if span_min - attach_gap * 4 <= cx <= span_max + attach_gap * 4:
                projection_penalty = 0.0
            else:
                projection_penalty = min(abs(cx - span_min), abs(cx - span_max))

        score = main_dist + projection_penalty * 0.15

        if best is None or score < best["score"]:
            best = {
                "line": ln,
                "score": score,
                "main_dist": main_dist,
            }

    return best


def infer_marker_orientation_from_same_label(bubble, all_bubbles, text_gap):
    """
    Dynamic orientation inference.

    It does NOT assume:
        alpha = vertical
        numeric = horizontal

    Instead:
        If same label appears above/below with close X -> vertical axis.
        If same label appears left/right with close Y -> horizontal axis.
    """
    label = bubble["label"]
    cx, cy = bubble["circle_center"]

    align_tol = max(text_gap * 4.0, 750.0)
    min_pair_span = 1000.0

    candidates = []

    for other in all_bubbles:
        if other is bubble:
            continue

        if other["label"] != label:
            continue

        ox, oy = other["circle_center"]

        dx = abs(cx - ox)
        dy = abs(cy - oy)

        if dx <= align_tol and dy >= min_pair_span:
            candidates.append({
                "orientation": "vertical",
                "score": dx,
                "coord": round(float((cx + ox) / 2.0), 3),
                "pair_distance": dy,
            })

        if dy <= align_tol and dx >= min_pair_span:
            candidates.append({
                "orientation": "horizontal",
                "score": dy,
                "coord": round(float((cy + oy) / 2.0), 3),
                "pair_distance": dx,
            })

    if candidates:
        candidates = sorted(candidates, key=lambda x: (x["score"], -x["pair_distance"]))
        return candidates[0]

    return None


def build_trusted_markers(doc, line_layer, text_layer, circle_layer, min_grid_length, text_gap, attach_gap):
    """
    Professional dynamic marker detector.

    Main rule:
        grid marker = circle + valid grid text inside

    Axis orientation is detected dynamically:
        1. same-label alignment
        2. strict attached line
        3. nearest grid line
        4. virtual fallback from circle position

    This avoids assuming any office convention.
    """
    texts = extract_texts(doc, text_layer)
    circles = extract_circles(doc, circle_layer)
    lines = extract_axis_lines(doc, line_layer, min_length=min_grid_length)

    bubbles = []
    rejected = []

    # Step 1: detect circle + single valid text inside.
    for c in circles:
        circle_center = c["center"]
        circle_radius = c["radius"]

        candidate_texts = []

        for t in texts:
            if not probable_grid_label(t["text"]):
                continue

            if text_inside_circle(t["point"], circle_center, circle_radius, extra_gap=text_gap):
                candidate_texts.append(t)

        if len(candidate_texts) == 1:
            t = candidate_texts[0]
            label = clean_text(t["text"])

            bubbles.append({
                "label": label,
                "text_entity": t["entity"],
                "text_point": t["point"],
                "text_source": t.get("source", "unknown"),
                "parent_insert": t.get("parent_insert"),
                "circle_entity": c["entity"],
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "circle_source": c.get("source", "unknown"),
            })
        else:
            rejected.append({
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "text_matches": len(candidate_texts),
                "line_matches": 0,
                "candidate_texts": [t["text"] for t in candidate_texts[:10]],
                "reason": "No valid text in marker" if len(candidate_texts) == 0 else "Multiple texts in marker",
            })

    trusted_markers = []

    for b in bubbles:
        circle_center = b["circle_center"]
        circle_radius = b["circle_radius"]

        best_line = None
        orientation = None
        coord = None
        detection_mode = ""

        # Step 2: infer from same-label alignment.
        inferred = infer_marker_orientation_from_same_label(b, bubbles, text_gap)

        if inferred is not None:
            orientation = inferred["orientation"]
            coord = inferred["coord"]

            # Try to find a real line in that orientation close to this coordinate.
            orientation_lines = [ln for ln in lines if ln["orientation"] == orientation]

            if orientation_lines:
                if orientation == "vertical":
                    best_line = min(orientation_lines, key=lambda ln: abs(ln["coord"] - coord))
                else:
                    best_line = min(orientation_lines, key=lambda ln: abs(ln["coord"] - coord))

                if abs(best_line["coord"] - coord) <= max(attach_gap * 8.0, 1500.0):
                    coord = best_line["coord"]
                    detection_mode = "same_label_alignment_plus_nearest_line"
                else:
                    best_line = make_virtual_line(circle_center, orientation, line_layer)
                    best_line["coord"] = coord
                    detection_mode = "same_label_alignment_virtual"
            else:
                best_line = make_virtual_line(circle_center, orientation, line_layer)
                best_line["coord"] = coord
                detection_mode = "same_label_alignment_virtual"

        # Step 3: strict attached line fallback.
        if best_line is None:
            attached_candidates = [
                ln for ln in lines
                if line_attached_to_circle(ln, circle_center, circle_radius, attach_gap=attach_gap)
            ]

            if attached_candidates:
                best_line = max(attached_candidates, key=lambda ln: ln["length"])
                orientation = best_line["orientation"]
                coord = best_line["coord"]
                detection_mode = "attached_line"

        # Step 4: nearest line fallback.
        if best_line is None:
            nearest = nearest_line_any_orientation(circle_center, lines, attach_gap)

            if nearest is not None:
                relaxed_limit = max(attach_gap * 10.0, 2000.0)

                if nearest["main_dist"] <= relaxed_limit:
                    best_line = nearest["line"]
                    orientation = best_line["orientation"]
                    coord = best_line["coord"]
                    detection_mode = "nearest_line"

        # Step 5: final virtual fallback.
        if best_line is None:
            # Use label distribution fallback.
            same_label_points = [
                x["circle_center"]
                for x in bubbles
                if x["label"] == b["label"]
            ]

            if len(same_label_points) >= 2:
                xs = [p[0] for p in same_label_points]
                ys = [p[1] for p in same_label_points]

                if (max(ys) - min(ys)) >= (max(xs) - min(xs)):
                    orientation = "vertical"
                    coord = round(float(circle_center[0]), 3)
                else:
                    orientation = "horizontal"
                    coord = round(float(circle_center[1]), 3)
            else:
                orientation = "vertical"
                coord = round(float(circle_center[0]), 3)

            best_line = make_virtual_line(circle_center, orientation, line_layer)
            best_line["coord"] = coord
            detection_mode = "final_virtual"

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
            "line_entity": best_line.get("entity"),
            "orientation": best_line["orientation"],
            "coord": best_line["coord"],
            "line_start": best_line["start"],
            "line_end": best_line["end"],
            "line_length": best_line["length"],
            "detection_mode": detection_mode,
        })

    return {
        "texts": texts,
        "circles": circles,
        "lines": lines,
        "trusted_markers": trusted_markers,
        "rejected_markers": rejected,
    }


# =========================================================
# AXIS GROUPING
# =========================================================
def resolve_axis_group(group, orientation):
    coord = round(sum(m["coord"] for m in group) / len(group), 3)
    labels = [m["label"] for m in group if clean_text(m["label"])]
    label = labels[0] if labels else ""

    xs = [m["circle_center"][0] for m in group]
    ys = [m["circle_center"][1] for m in group]
    writable_markers = [m for m in group if is_entity_writable(m)]

    return {
        "orientation": orientation,
        "coord": coord,
        "label": label,
        "markers": group,
        "marker_count": len(group),
        "writable_marker_count": len(writable_markers),
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


def get_family_groups_from_axis_groups(axis_groups):
    """
    Dynamic family extraction.

    Does not assume orientation.

    Numeric family = groups whose label is numeric.
    Alphabetic family = groups whose label is alphabetic.
    """
    numeric_groups = []
    alpha_groups = []

    for orientation in ["vertical", "horizontal"]:
        for g in axis_groups.get(orientation, []):
            item = dict(g)
            item["family_orientation"] = orientation

            if is_numeric_label(g["label"]):
                numeric_groups.append(item)
            elif is_alpha_label(g["label"]):
                alpha_groups.append(item)

    numeric_groups = sorted(numeric_groups, key=lambda x: (x["family_orientation"], x["coord"]))
    alpha_groups = sorted(alpha_groups, key=lambda x: (x["family_orientation"], x["coord"]))

    return numeric_groups, alpha_groups


def family_summary_from_axis_groups(axis_groups):
    vertical = axis_groups.get("vertical", [])
    horizontal = axis_groups.get("horizontal", [])

    v_num = len([g for g in vertical if is_numeric_label(g["label"])])
    v_alpha = len([g for g in vertical if is_alpha_label(g["label"])])
    h_num = len([g for g in horizontal if is_numeric_label(g["label"])])
    h_alpha = len([g for g in horizontal if is_alpha_label(g["label"])])

    if v_num >= h_num:
        numeric_orientation = "vertical"
    else:
        numeric_orientation = "horizontal"

    if v_alpha >= h_alpha:
        alpha_orientation = "vertical"
    else:
        alpha_orientation = "horizontal"

    return {
        "numeric_orientation_detected": numeric_orientation,
        "alpha_orientation_detected": alpha_orientation,
        "vertical_counts": {"numeric": v_num, "alpha": v_alpha},
        "horizontal_counts": {"numeric": h_num, "alpha": h_alpha},
    }


# =========================================================
# REGION SEGMENTATION
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

        numeric_groups, alpha_groups = get_family_groups_from_axis_groups(axis_groups)

        regions.append({
            "name": f"Region {n}",
            "markers": markers,
            "axis_groups": axis_groups,
            "numeric_groups": numeric_groups,
            "alpha_groups": alpha_groups,
            "bbox": bbox_from_markers(markers),
            "grid_bucket": key,
            "marker_count": len(markers),
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
# SMART MAPPING
# =========================================================
def axis_spacing_values_from_groups(groups):
    if len(groups) < 2:
        return []

    return [
        round(abs(groups[i + 1]["coord"] - groups[i]["coord"]), 3)
        for i in range(len(groups) - 1)
    ]


def best_window_indices(source_groups, target_groups, spacing_tol=50.0):
    ns = len(source_groups)
    nt = len(target_groups)

    if ns == 0 or nt == 0:
        return [], [], "empty", 0.0

    if ns == nt:
        return list(range(ns)), list(range(nt)), "equal_count", 1.0

    def score_windows(src_window, tgt_window):
        if len(src_window) <= 1 or len(tgt_window) <= 1:
            return 1.0

        src_sp = axis_spacing_values_from_groups(src_window)
        tgt_sp = axis_spacing_values_from_groups(tgt_window)

        check_count = min(len(src_sp), len(tgt_sp))

        if check_count == 0:
            return 1.0

        matched = 0

        for i in range(check_count):
            if abs(src_sp[i] - tgt_sp[i]) <= spacing_tol:
                matched += 1

        return round(matched / check_count, 3)

    if ns > nt:
        best_start = 0
        best_score = -1.0

        for start in range(ns - nt + 1):
            src_window = source_groups[start:start + nt]
            score = score_windows(src_window, target_groups)

            if score > best_score:
                best_score = score
                best_start = start

        return list(range(best_start, best_start + nt)), list(range(nt)), "source_window", best_score

    best_start = 0
    best_score = -1.0

    for start in range(nt - ns + 1):
        tgt_window = target_groups[start:start + ns]
        score = score_windows(source_groups, tgt_window)

        if score > best_score:
            best_score = score
            best_start = start

    return list(range(ns)), list(range(best_start, best_start + ns)), "target_window", best_score


def build_smart_mapping_preview(source_axis_groups, target_axis_groups, family_name, region_name, source_name, spacing_tol=50.0):
    preview = []

    source_indices, target_indices, mode, score = best_window_indices(
        source_axis_groups,
        target_axis_groups,
        spacing_tol=spacing_tol,
    )

    for pos, (si, ti) in enumerate(zip(source_indices, target_indices), start=1):
        src = source_axis_groups[si]
        tgt = target_axis_groups[ti]

        preview.append({
            "target_region": region_name,
            "source_reference": source_name,
            "family": family_name,
            "position": pos,
            "mapping_mode": mode,
            "mapping_score": score,
            "source_index": si + 1,
            "target_index": ti + 1,
            "source_label": src["label"],
            "target_old_label": tgt["label"],
            "source_coord": src["coord"],
            "target_coord": tgt["coord"],
            "source_visible_markers": src["marker_count"],
            "target_visible_markers": tgt["marker_count"],
            "target_writable_markers": tgt["writable_marker_count"],
        })

    return preview


def apply_axis_group_labels_smart(source_axis_groups, target_axis_groups, writable_only=True, spacing_tol=50.0):
    source_indices, target_indices, mode, score = best_window_indices(
        source_axis_groups,
        target_axis_groups,
        spacing_tol=spacing_tol,
    )

    changed = 0
    skipped_non_writable = 0

    for si, ti in zip(source_indices, target_indices):
        new_label = source_axis_groups[si]["label"]
        target_group = target_axis_groups[ti]

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

    return changed, skipped_non_writable, mode, score


def build_propagated_axis_groups_smart(source_axis_groups, target_axis_groups, spacing_tol=50.0):
    propagated = []

    for g in target_axis_groups:
        new_g = dict(g)
        propagated.append(new_g)

    source_indices, target_indices, mode, score = best_window_indices(
        source_axis_groups,
        target_axis_groups,
        spacing_tol=spacing_tol,
    )

    for si, ti in zip(source_indices, target_indices):
        propagated[ti]["label"] = source_axis_groups[si]["label"]

    return propagated, mode, score


# =========================================================
# SYNC STRATEGIES
# =========================================================
def prepare_direct_multi_plan_sync(
    structural_regions,
    numeric_arch_groups,
    alpha_arch_groups,
    tolerance
):
    matched_regions = []
    mapping_preview = []
    region_match_report = []

    for region in structural_regions:
        numeric_struc_groups = region["numeric_groups"]
        alpha_struc_groups = region["alpha_groups"]

        writable_count = len([m for m in region["markers"] if is_entity_writable(m)])
        numeric_count = len(numeric_struc_groups)
        alpha_count = len(alpha_struc_groups)

        usable = numeric_count > 0 or alpha_count > 0

        if usable:
            reason = (
                f"Accepted. Numeric axes={numeric_count}, alphabetic axes={alpha_count}. "
                "Dynamic orientation detection used."
            )

            matched_regions.append({
                "name": region["name"],
                "bbox": region["bbox"],
                "numeric_groups": numeric_struc_groups,
                "alpha_groups": alpha_struc_groups,
                "source_numeric_groups": numeric_arch_groups,
                "source_alpha_groups": alpha_arch_groups,
                "grid_bucket": region["grid_bucket"],
                "match_reason": reason,
                "match_mode": "direct_dynamic",
                "marker_count": len(region["markers"]),
                "source_reference": "Architecture",
            })

            if numeric_count > 0:
                mapping_preview.extend(build_smart_mapping_preview(
                    numeric_arch_groups,
                    numeric_struc_groups,
                    "numeric",
                    region["name"],
                    "Architecture",
                    spacing_tol=max(tolerance, 50.0),
                ))

            if alpha_count > 0:
                mapping_preview.extend(build_smart_mapping_preview(
                    alpha_arch_groups,
                    alpha_struc_groups,
                    "alphabetic",
                    region["name"],
                    "Architecture",
                    spacing_tol=max(tolerance, 50.0),
                ))

            classification = "direct_dynamic"
            will_sync = True
            score = 1.0

        else:
            reason = "Rejected. No usable numeric or alphabetic grid axes detected."
            classification = "rejected"
            will_sync = False
            score = 0.0

        region_match_report.append({
            "region": region["name"],
            "classification": classification,
            "source_reference": "Architecture",
            "trusted_markers": len(region["markers"]),
            "writable_markers": writable_count,
            "numeric_axes": numeric_count,
            "alphabetic_axes": alpha_count,
            "bbox_width": round(region["bbox"]["width"], 3),
            "bbox_height": round(region["bbox"]["height"], 3),
            "score": score,
            "reason": reason,
            "will_sync": will_sync,
        })

    return None, matched_regions, mapping_preview, region_match_report


def prepare_propagator_multi_plan_sync(
    structural_regions,
    numeric_arch_groups,
    alpha_arch_groups,
    tolerance
):
    matched_regions = []
    mapping_preview = []
    region_match_report = []

    if not structural_regions:
        return None, matched_regions, mapping_preview, region_match_report

    candidates = []

    for region in structural_regions:
        numeric_groups = region["numeric_groups"]
        alpha_groups = region["alpha_groups"]

        numeric_count = len(numeric_groups)
        alpha_count = len(alpha_groups)
        total_axes = numeric_count + alpha_count
        writable_count = len([m for m in region["markers"] if is_entity_writable(m)])
        usable = total_axes > 0

        candidates.append({
            "region": region,
            "numeric_groups": numeric_groups,
            "alpha_groups": alpha_groups,
            "numeric_count": numeric_count,
            "alpha_count": alpha_count,
            "total_axes": total_axes,
            "writable_count": writable_count,
            "usable": usable,
        })

    usable_candidates = [c for c in candidates if c["usable"]]

    if not usable_candidates:
        for c in candidates:
            region = c["region"]

            region_match_report.append({
                "region": region["name"],
                "classification": "rejected",
                "source_reference": "None",
                "trusted_markers": len(region["markers"]),
                "writable_markers": c["writable_count"],
                "numeric_axes": c["numeric_count"],
                "alphabetic_axes": c["alpha_count"],
                "bbox_width": round(region["bbox"]["width"], 3),
                "bbox_height": round(region["bbox"]["height"], 3),
                "score": 0.0,
                "reason": "Rejected. No usable grid axes detected.",
                "will_sync": False,
            })

        return None, matched_regions, mapping_preview, region_match_report

    # Pick the most complete structural plan as the propagator.
    anchor_candidate = max(
        usable_candidates,
        key=lambda c: (
            c["total_axes"],
            c["alpha_count"],
            c["numeric_count"],
            len(c["region"]["markers"]),
            c["writable_count"],
        )
    )

    anchor_region = anchor_candidate["region"]
    anchor_numeric = anchor_candidate["numeric_groups"]
    anchor_alpha = anchor_candidate["alpha_groups"]

    synced_anchor_numeric, _, _ = build_propagated_axis_groups_smart(
        numeric_arch_groups,
        anchor_numeric,
        spacing_tol=max(tolerance, 50.0),
    )

    synced_anchor_alpha, _, _ = build_propagated_axis_groups_smart(
        alpha_arch_groups,
        anchor_alpha,
        spacing_tol=max(tolerance, 50.0),
    )

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
        "match_reason": "Selected as first synced structural plan / propagator.",
        "match_mode": "anchor_dynamic",
        "marker_count": len(anchor_region["markers"]),
        "source_reference": "Architecture",
    })

    if len(anchor_numeric) > 0:
        mapping_preview.extend(build_smart_mapping_preview(
            numeric_arch_groups,
            anchor_numeric,
            "numeric",
            anchor_region["name"],
            "Architecture",
            spacing_tol=max(tolerance, 50.0),
        ))

    if len(anchor_alpha) > 0:
        mapping_preview.extend(build_smart_mapping_preview(
            alpha_arch_groups,
            anchor_alpha,
            "alphabetic",
            anchor_region["name"],
            "Architecture",
            spacing_tol=max(tolerance, 50.0),
        ))

    for c in candidates:
        region = c["region"]

        if region["name"] == anchor_region["name"]:
            region_match_report.append({
                "region": region["name"],
                "classification": "anchor_dynamic",
                "source_reference": "Architecture",
                "trusted_markers": len(region["markers"]),
                "writable_markers": c["writable_count"],
                "numeric_axes": c["numeric_count"],
                "alphabetic_axes": c["alpha_count"],
                "bbox_width": round(region["bbox"]["width"], 3),
                "bbox_height": round(region["bbox"]["height"], 3),
                "score": 1.0,
                "reason": "Selected as first synced structural plan / propagator.",
                "will_sync": True,
            })
            continue

        if c["usable"]:
            numeric_groups = c["numeric_groups"]
            alpha_groups = c["alpha_groups"]

            matched_regions.append({
                "name": region["name"],
                "bbox": region["bbox"],
                "numeric_groups": numeric_groups,
                "alpha_groups": alpha_groups,
                "source_numeric_groups": synced_anchor_numeric,
                "source_alpha_groups": synced_anchor_alpha,
                "grid_bucket": region["grid_bucket"],
                "match_reason": f"Synced from propagator {anchor_region['name']}.",
                "match_mode": "propagated_dynamic",
                "marker_count": len(region["markers"]),
                "source_reference": anchor_region["name"],
            })

            if c["numeric_count"] > 0:
                mapping_preview.extend(build_smart_mapping_preview(
                    synced_anchor_numeric,
                    numeric_groups,
                    "numeric",
                    region["name"],
                    anchor_region["name"],
                    spacing_tol=max(tolerance, 50.0),
                ))

            if c["alpha_count"] > 0:
                mapping_preview.extend(build_smart_mapping_preview(
                    synced_anchor_alpha,
                    alpha_groups,
                    "alphabetic",
                    region["name"],
                    anchor_region["name"],
                    spacing_tol=max(tolerance, 50.0),
                ))

            classification = "propagated_dynamic"
            will_sync = True
            score = 1.0
            reason = f"Accepted. Synced from propagator {anchor_region['name']}."

        else:
            classification = "rejected"
            will_sync = False
            score = 0.0
            reason = "Rejected. No usable grid axes detected."

        region_match_report.append({
            "region": region["name"],
            "classification": classification,
            "source_reference": anchor_region["name"],
            "trusted_markers": len(region["markers"]),
            "writable_markers": c["writable_count"],
            "numeric_axes": c["numeric_count"],
            "alphabetic_axes": c["alpha_count"],
            "bbox_width": round(region["bbox"]["width"], 3),
            "bbox_height": round(region["bbox"]["height"], 3),
            "score": score,
            "reason": reason,
            "will_sync": will_sync,
        })

    return anchor_candidate, matched_regions, mapping_preview, region_match_report


# =========================================================
# SESSION STATE
# =========================================================
def init_state():
    defaults = {
        "docs_loaded": False,
        "arch_doc": None,
        "struc_doc": None,
        "arch_name": "",
        "struc_name": "",
        "arch_sig": None,
        "struc_sig": None,
        "arch_detection": {},
        "struc_detection": {},
        "family_summary": {},
        "arch_axis_groups": {},
        "numeric_arch_groups": [],
        "alpha_arch_groups": [],
        "structural_regions": [],
        "segmentation_summary": {},
        "anchor_region": None,
        "matched_regions": [],
        "mapping_preview": [],
        "region_match_report": [],
        "apply_ready": False,
        "labels_changed_count": 0,
        "skipped_non_writable_count": 0,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_state():
    keys = [
        "docs_loaded",
        "arch_doc",
        "struc_doc",
        "arch_name",
        "struc_name",
        "arch_sig",
        "struc_sig",
        "arch_detection",
        "struc_detection",
        "family_summary",
        "arch_axis_groups",
        "numeric_arch_groups",
        "alpha_arch_groups",
        "structural_regions",
        "segmentation_summary",
        "anchor_region",
        "matched_regions",
        "mapping_preview",
        "region_match_report",
        "apply_ready",
        "labels_changed_count",
        "skipped_non_writable_count",
    ]

    for key in keys:
        if key in st.session_state:
            del st.session_state[key]


def clear_outputs():
    st.session_state.arch_detection = {}
    st.session_state.struc_detection = {}
    st.session_state.family_summary = {}
    st.session_state.arch_axis_groups = {}
    st.session_state.numeric_arch_groups = []
    st.session_state.alpha_arch_groups = []
    st.session_state.structural_regions = []
    st.session_state.segmentation_summary = {}
    st.session_state.anchor_region = None
    st.session_state.matched_regions = []
    st.session_state.mapping_preview = []
    st.session_state.region_match_report = []
    st.session_state.apply_ready = False
    st.session_state.labels_changed_count = 0
    st.session_state.skipped_non_writable_count = 0


def uploaded_file_signature(uploaded_file):
    if uploaded_file is None:
        return None
    return uploaded_file.name, len(uploaded_file.getvalue())


# =========================================================
# UI
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
                default=all_layers,
            )

            if st.button("🔥 Purge and Prepare Download"):
                deleted_count = purge_layers_from_modelspace(doc, layers_to_keep)
                dxf_bytes = write_doc_to_temp_bytes(doc)

                st.success(f"Deleted {deleted_count} modelspace entities.")

                st.download_button(
                    "📥 Download Cleaned DXF",
                    data=dxf_bytes,
                    file_name=f"CLEANED_{uploaded_file.name}",
                    mime="application/dxf",
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
        "Dynamic multi-plan grid label sync. "
        "No fixed assumption about numeric/alphabetic orientation."
    )

    init_state()

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
        attach_gap = st.slider("Line-to-Marker Attach Gap (mm)", 20.0, 1500.0, 180.0, 10.0)

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
        writable_only = st.checkbox("Write only to safe writable text entities", value=True)

    col_a, col_b = st.columns(2)

    with col_a:
        arch_file = st.file_uploader("Reference Architectural DXF", type=["dxf"], key="arch")

    with col_b:
        struc_file = st.file_uploader("Target Structural DXF", type=["dxf"], key="struc")

    current_arch_sig = uploaded_file_signature(arch_file)
    current_struc_sig = uploaded_file_signature(struc_file)

    if current_arch_sig != st.session_state.arch_sig or current_struc_sig != st.session_state.struc_sig:
        reset_state()
        init_state()
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
                    index=arch_layers.index(arch_line_default) if arch_line_default in arch_layers else 0,
                )

            with a2:
                arch_text_layer = st.selectbox(
                    "Arch Grid Text Layer",
                    arch_layers,
                    index=arch_layers.index(arch_text_default) if arch_text_default in arch_layers else 0,
                )

            with a3:
                arch_circle_layer = st.selectbox(
                    "Arch Grid Circle Layer",
                    arch_layers,
                    index=arch_layers.index(arch_circle_default) if arch_circle_default in arch_layers else 0,
                )

            s1, s2, s3 = st.columns(3)

            with s1:
                struc_line_layer = st.selectbox(
                    "Struc Grid Line Layer",
                    struc_layers,
                    index=struc_layers.index(struc_line_default) if struc_line_default in struc_layers else 0,
                )

            with s2:
                struc_text_layer = st.selectbox(
                    "Struc Grid Text Layer",
                    struc_layers,
                    index=struc_layers.index(struc_text_default) if struc_text_default in struc_layers else 0,
                )

            with s3:
                struc_circle_layer = st.selectbox(
                    "Struc Grid Circle Layer",
                    struc_layers,
                    index=struc_layers.index(struc_circle_default) if struc_circle_default in struc_layers else 0,
                )

            b1, b2 = st.columns(2)

            with b1:
                if st.button("🔎 Prepare Label Sync"):
                    clear_outputs()

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

                        fam = family_summary_from_axis_groups(arch_axis_groups)
                        st.session_state.family_summary = fam

                        numeric_arch_groups, alpha_arch_groups = get_family_groups_from_axis_groups(arch_axis_groups)

                        st.session_state.numeric_arch_groups = numeric_arch_groups
                        st.session_state.alpha_arch_groups = alpha_arch_groups

                        structural_regions, segmentation_summary = build_structural_regions_by_empty_space(
                            struc_detection["trusted_markers"],
                            min_region_markers=min_region_markers,
                        )

                        st.session_state.structural_regions = structural_regions
                        st.session_state.segmentation_summary = segmentation_summary

                        if strategy == "Option A - Direct Multi-Plan Sync":
                            anchor_candidate, matched_regions, mapping_preview, region_match_report = prepare_direct_multi_plan_sync(
                                structural_regions=structural_regions,
                                numeric_arch_groups=numeric_arch_groups,
                                alpha_arch_groups=alpha_arch_groups,
                                tolerance=tolerance,
                            )
                        else:
                            anchor_candidate, matched_regions, mapping_preview, region_match_report = prepare_propagator_multi_plan_sync(
                                structural_regions=structural_regions,
                                numeric_arch_groups=numeric_arch_groups,
                                alpha_arch_groups=alpha_arch_groups,
                                tolerance=tolerance,
                            )

                        st.session_state.anchor_region = anchor_candidate
                        st.session_state.matched_regions = matched_regions
                        st.session_state.mapping_preview = mapping_preview
                        st.session_state.region_match_report = region_match_report
                        st.session_state.apply_ready = len(matched_regions) > 0

                        if matched_regions:
                            st.success(
                                f"Detected {len(structural_regions)} structural region(s). "
                                f"{len(matched_regions)} region(s) are ready for sync."
                            )
                        else:
                            st.warning("No structural regions qualified for sync. Check the report below.")

                    except Exception as e:
                        st.error(f"Label sync preparation failed: {e}")

            with b2:
                if st.button("🧹 Reset Tool 2"):
                    reset_state()
                    st.rerun()

            if st.session_state.family_summary:
                fam = st.session_state.family_summary

                st.info(
                    f"Dynamic reference detection: "
                    f"Numeric family mostly = {fam['numeric_orientation_detected']} axes, "
                    f"Alphabetic family mostly = {fam['alpha_orientation_detected']} axes. "
                    f"Vertical counts = {fam['vertical_counts']}, "
                    f"Horizontal counts = {fam['horizontal_counts']}."
                )

            if st.session_state.anchor_region is not None:
                st.markdown("### Propagator / Anchor Region")

                try:
                    anchor_region = st.session_state.anchor_region["region"]

                    st.write({
                        "anchor_region": anchor_region["name"],
                        "trusted_markers": len(anchor_region["markers"]),
                        "numeric_axes": len(anchor_region["numeric_groups"]),
                        "alphabetic_axes": len(anchor_region["alpha_groups"]),
                        "reason": "This region is synced from Architecture first, then used as the propagator.",
                    })
                except Exception:
                    pass

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
            else:
                st.info("No sync preview yet.")

            st.markdown("### Apply Sync")

            if st.session_state.apply_ready:
                if st.button("✍️ Apply Label Sync"):
                    try:
                        changed = 0
                        skipped_non_writable = 0

                        for region in st.session_state.matched_regions:
                            c1, s1, mode1, score1 = apply_axis_group_labels_smart(
                                region["source_numeric_groups"],
                                region["numeric_groups"],
                                writable_only=writable_only,
                                spacing_tol=max(tolerance, 50.0),
                            )

                            c2, s2, mode2, score2 = apply_axis_group_labels_smart(
                                region["source_alpha_groups"],
                                region["alpha_groups"],
                                writable_only=writable_only,
                                spacing_tol=max(tolerance, 50.0),
                            )

                            changed += c1 + c2
                            skipped_non_writable += s1 + s2

                        st.session_state.labels_changed_count = changed
                        st.session_state.skipped_non_writable_count = skipped_non_writable

                        if changed > 0:
                            st.success(
                                f"Label sync complete. {changed} trusted structural marker text entities updated "
                                f"across {len(st.session_state.matched_regions)} matched region(s)."
                            )
                        else:
                            st.info(
                                "No text changes were needed, or the detected labels already match."
                            )

                        if skipped_non_writable > 0:
                            st.warning(
                                f"{skipped_non_writable} text entities were skipped because they are not safely writable. "
                                f"If your labels are inside blocks, try again with writable-only turned OFF."
                            )

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
                    mime="application/dxf",
                )

            with st.expander("Detection Details"):
                arch_det = st.session_state.arch_detection or {}
                struc_det = st.session_state.struc_detection or {}

                st.write("#### Architectural Detection")

                arch_modes = {}
                for m in arch_det.get("trusted_markers", []):
                    mode = m.get("detection_mode", "unknown")
                    arch_modes[mode] = arch_modes.get(mode, 0) + 1

                st.write({
                    "texts_on_selected_text_layer": len(arch_det.get("texts", [])),
                    "circles_or_block_markers_on_selected_circle_layer": len(arch_det.get("circles", [])),
                    "axis_lines_on_selected_line_layer": len(arch_det.get("lines", [])),
                    "trusted_markers_found": len(arch_det.get("trusted_markers", [])),
                    "rejected_markers": len(arch_det.get("rejected_markers", [])),
                    "detection_modes": arch_modes,
                    "numeric_arch_axes": len(st.session_state.numeric_arch_groups),
                    "alphabetic_arch_axes": len(st.session_state.alpha_arch_groups),
                })

                st.write("#### Structural Detection")
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
                    st.write("#### Dynamic Family Detection")
                    st.write(st.session_state.family_summary)

                if st.session_state.structural_regions:
                    st.write("#### Structural Region Summary")

                    rows = []

                    for r in st.session_state.structural_regions:
                        writable_count = len([
                            m for m in r["markers"]
                            if is_entity_writable(m)
                        ])

                        rows.append({
                            "region": r["name"],
                            "trusted_markers": len(r["markers"]),
                            "writable_markers": writable_count,
                            "numeric_axes": len(r.get("numeric_groups", [])),
                            "alphabetic_axes": len(r.get("alpha_groups", [])),
                            "vertical_axes": len(r["axis_groups"].get("vertical", [])),
                            "horizontal_axes": len(r["axis_groups"].get("horizontal", [])),
                            "bbox_width": round(r["bbox"]["width"], 3),
                            "bbox_height": round(r["bbox"]["height"], 3),
                            "grid_bucket": r["grid_bucket"],
                        })

                    st.dataframe(rows, use_container_width=True)

                if struc_det.get("rejected_markers"):
                    st.write("#### Rejected Structural Markers")
                    st.dataframe(struc_det["rejected_markers"], use_container_width=True)

                if struc_det.get("trusted_markers"):
                    st.write("#### Sample Trusted Structural Markers")

                    sample_rows = []

                    for m in struc_det.get("trusted_markers", [])[:100]:
                        sample_rows.append({
                            "label": m.get("label"),
                            "orientation": m.get("orientation"),
                            "coord": m.get("coord"),
                            "text_source": m.get("text_source"),
                            "detection_mode": m.get("detection_mode"),
                            "circle_x": round(m.get("circle_center", (0, 0))[0], 3),
                            "circle_y": round(m.get("circle_center", (0, 0))[1], 3),
                        })

                    st.dataframe(sample_rows, use_container_width=True)

                st.caption(
                    "Dynamic detector uses circle + text first, then same-label alignment, "
                    "attached lines, nearest lines, and virtual fallback. "
                    "It does not assume alphabetic or numeric grid orientation."
                )

    else:
        st.info("Please upload both the Architectural DXF and Structural DXF.")
