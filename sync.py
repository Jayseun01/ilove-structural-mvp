import streamlit as st
import ezdxf
import os
import tempfile
import math
import re
import io
import csv


# =========================================================
# APP CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - Grid Label Sync",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ iLoveStructural")
st.subheader("Tool 2: Grid Label Sync")
st.caption(
    "Workflow: Upload reference/target DXFs → detect grid labels → sync clean regions → use endpoint recovery for slab/detail regions."
)


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


def uploaded_file_signature(uploaded_file):
    if uploaded_file is None:
        return None
    return uploaded_file.name, len(uploaded_file.getvalue())


def audit_to_csv_bytes(audit_rows):
    if not audit_rows:
        return b""

    fieldnames = sorted(set().union(*[row.keys() for row in audit_rows]))

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()

    for row in audit_rows:
        safe_row = {}
        for k in fieldnames:
            v = row.get(k, "")
            if k in ("entity", "marker"):
                v = ""
            safe_row[k] = v
        writer.writerow(safe_row)

    return buffer.getvalue().encode("utf-8")


# =========================================================
# TEXT / LABEL HELPERS
# =========================================================

def clean_text(value):
    if value is None:
        return ""

    txt = str(value)
    txt = txt.replace("\\P", " ")
    txt = txt.replace("\n", " ")
    txt = txt.replace("′", "'")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()


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
    return bool(re.fullmatch(r"\d{1,3}[A-Z]?", clean_text(text)))


def is_alpha_label(text):
    return bool(re.fullmatch(r"[A-Z]{1,3}'?", clean_text(text)))


def numeric_label_value(text):
    txt = clean_text(text)
    m = re.fullmatch(r"(\d{1,3})([A-Z]?)", txt)

    if not m:
        return None

    return int(m.group(1))


# =========================================================
# GEOMETRY HELPERS
# =========================================================

def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))


def is_vertical(x1, y1, x2, y2, tol=2.0):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def is_horizontal(x1, y1, x2, y2, tol=2.0):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


def point_projects_on_segment(px, py, x1, y1, x2, y2, pad=0.0):
    return (
        min(x1, x2) - pad <= px <= max(x1, x2) + pad
        and min(y1, y2) - pad <= py <= max(y1, y2) + pad
    )


def point_to_segment_distance(px, py, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1

    if dx == 0 and dy == 0:
        return math.dist((px, py), (x1, y1))

    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))

    qx = x1 + t * dx
    qy = y1 + t * dy

    return math.dist((px, py), (qx, qy))


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


def squared_distance(p1, p2):
    return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2


# =========================================================
# DXF HELPERS
# =========================================================

def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def pick_default_layer(layers, candidates):
    upper = {x.upper(): x for x in layers}

    for c in candidates:
        if c.upper() in upper:
            return upper[c.upper()]

    return layers[0] if layers else None


def get_entity_handle(entity):
    try:
        return entity.dxf.handle
    except Exception:
        return ""


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

    a = math.radians(rot)
    xr = x * math.cos(a) - y * math.sin(a)
    yr = x * math.sin(a) + y * math.cos(a)

    return xr + ix, yr + iy


def transform_block_radius(radius, insert_entity):
    _, _, sx, sy, _ = get_insert_transform(insert_entity)
    return float(radius) * ((abs(sx) + abs(sy)) / 2.0)


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
            return True

        if entity.dxftype() == "MTEXT":
            entity.text = new_value
            return True

        if entity.dxftype() == "ATTRIB":
            entity.dxf.text = new_value
            return True

    except Exception:
        pass

    return False


# =========================================================
# ENTITY EXTRACTION
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
                    "parent_insert": None,
                    "handle": get_entity_handle(e),
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
                                "handle": get_entity_handle(att),
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

                                lp = get_text_point(be)
                                wp = transform_block_point(lp, e)

                                texts.append({
                                    "entity": be,
                                    "text": get_text_value(be),
                                    "point": wp,
                                    "layer": e.dxf.layer,
                                    "type": be.dxftype(),
                                    "source": "block_text",
                                    "parent_insert": e,
                                    "handle": get_entity_handle(be),
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
                    "parent_insert": None,
                    "handle": get_entity_handle(e),
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

                                circles.append({
                                    "entity": e,
                                    "nested_entity": be,
                                    "center": transform_block_point((float(c.x), float(c.y)), e),
                                    "radius": transform_block_radius(float(be.dxf.radius), e),
                                    "layer": e.dxf.layer,
                                    "source": "block_circle",
                                    "parent_insert": e,
                                    "handle": get_entity_handle(e),
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
            "handle": get_entity_handle(entity),
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
            "handle": get_entity_handle(entity),
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
        same = (
            item["orientation"] == current[-1]["orientation"]
            and abs(item["coord"] - current[-1]["coord"]) <= tol
        )

        if same:
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

                add_axis_segment(
                    lines,
                    e,
                    x1,
                    y1,
                    x2,
                    y2,
                    e.dxf.layer,
                    min_length,
                )

            elif e.dxftype() == "LWPOLYLINE":
                pts = list(e.get_points())

                for i in range(len(pts) - 1):
                    x1, y1 = float(pts[i][0]), float(pts[i][1])
                    x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])

                    add_axis_segment(
                        lines,
                        e,
                        x1,
                        y1,
                        x2,
                        y2,
                        e.dxf.layer,
                        min_length,
                    )

            elif e.dxftype() == "POLYLINE":
                pts = [
                    (float(v.dxf.location.x), float(v.dxf.location.y))
                    for v in e.vertices
                ]

                for i in range(len(pts) - 1):
                    x1, y1 = pts[i]
                    x2, y2 = pts[i + 1]

                    add_axis_segment(
                        lines,
                        e,
                        x1,
                        y1,
                        x2,
                        y2,
                        e.dxf.layer,
                        min_length,
                    )

        except Exception:
            continue

    return deduplicate_axis_lines(lines)

# =========================================================
# MARKER DETECTION
# =========================================================

def text_inside_circle(text_point, circle_center, circle_radius, extra_gap=180.0):
    return euclidean(text_point, circle_center) <= circle_radius + extra_gap


def line_attached_to_circle(line, center, radius, attach_gap):
    cx, cy = center
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    if line["orientation"] == "vertical":
        if abs(line["coord"] - cx) > attach_gap:
            return False

        if not point_projects_on_segment(
            cx,
            cy,
            x1,
            y1,
            x2,
            y2,
            pad=radius + attach_gap,
        ):
            return False

    elif line["orientation"] == "horizontal":
        if abs(line["coord"] - cy) > attach_gap:
            return False

        if not point_projects_on_segment(
            cx,
            cy,
            x1,
            y1,
            x2,
            y2,
            pad=radius + attach_gap,
        ):
            return False

    else:
        return False

    d_seg = point_to_segment_distance(cx, cy, x1, y1, x2, y2)
    d1 = abs(euclidean((x1, y1), center) - radius)
    d2 = abs(euclidean((x2, y2), center) - radius)

    return min(d_seg, d1, d2) <= attach_gap


def closest_grid_line_to_circle(center, lines, attach_gap):
    if not lines:
        return None, None

    cx, cy = center
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

    return best["line"], best["perp"]


def infer_orientation_from_same_label(bubble, bubbles, text_gap):
    label = bubble["label"]
    cx, cy = bubble["circle_center"]

    align_tol = max(text_gap * 4.0, 750.0)
    min_span = 1000.0

    v = []
    h = []

    for other in bubbles:
        if other is bubble:
            continue

        if other["label"] != label:
            continue

        ox, oy = other["circle_center"]
        dx = abs(cx - ox)
        dy = abs(cy - oy)

        if dx <= align_tol and dy >= min_span:
            v.append((dx, dy, (cx + ox) / 2.0))

        if dy <= align_tol and dx >= min_span:
            h.append((dy, dx, (cy + oy) / 2.0))

    if v and not h:
        v.sort(key=lambda x: (x[0], -x[1]))
        return "vertical", round(float(v[0][2]), 3)

    if h and not v:
        h.sort(key=lambda x: (x[0], -x[1]))
        return "horizontal", round(float(h[0][2]), 3)

    if v and h:
        v.sort(key=lambda x: (x[0], -x[1]))
        h.sort(key=lambda x: (x[0], -x[1]))

        if v[0][0] <= h[0][0]:
            return "vertical", round(float(v[0][2]), 3)

        return "horizontal", round(float(h[0][2]), 3)

    return None, None


def make_virtual_axis(center, orientation, coord, line_layer):
    cx, cy = center

    if orientation == "vertical":
        return {
            "entity": None,
            "orientation": "vertical",
            "coord": round(float(coord), 3),
            "start": (float(coord), cy - 1000.0),
            "end": (float(coord), cy + 1000.0),
            "length": 1000.0,
            "layer": line_layer,
            "virtual": True,
        }

    return {
        "entity": None,
        "orientation": "horizontal",
        "coord": round(float(coord), 3),
        "start": (cx - 1000.0, float(coord)),
        "end": (cx + 1000.0, float(coord)),
        "length": 1000.0,
        "layer": line_layer,
        "virtual": True,
    }


def is_marker_writable(marker, allow_block_text_write=False):
    src = marker.get("text_source", "")

    if src in ("modelspace", "insert_attrib"):
        return True

    if allow_block_text_write and src == "block_text":
        return True

    return False


def marker_confidence(mode, text_matches, writable):
    score = 35

    if text_matches == 1:
        score += 25
    elif text_matches > 1:
        score += 15

    if mode == "attached_gridline":
        score += 35
    elif mode == "closest_gridline":
        score += 20
    elif mode == "same_label_virtual_axis":
        score += 12

    if writable:
        score += 5
    else:
        score -= 15

    return max(0, min(100, score))


def build_trusted_markers(
    doc,
    line_layer,
    text_layer,
    circle_layer,
    min_grid_length,
    text_gap,
    attach_gap,
    allow_block_text_write=False,
):
    texts = extract_texts(doc, text_layer)
    circles = extract_circles(doc, circle_layer)
    lines = extract_axis_lines(doc, line_layer, min_length=min_grid_length)

    bubbles = []
    rejected = []

    for c in circles:
        center = c["center"]
        radius = c["radius"]

        candidates = []

        for t in texts:
            if not probable_grid_label(t["text"]):
                continue

            if text_inside_circle(t["point"], center, radius, extra_gap=text_gap):
                candidates.append(t)

        if not candidates:
            rejected.append({
                "circle_center": center,
                "circle_radius": radius,
                "candidate_texts": [],
                "text_matches": 0,
                "reason": "No valid grid text inside marker",
            })
            continue

        candidates = sorted(candidates, key=lambda t: euclidean(t["point"], center))
        t = candidates[0]

        bubbles.append({
            "label": clean_text(t["text"]),
            "text_entity": t["entity"],
            "text_point": t["point"],
            "text_source": t["source"],
            "text_handle": t.get("handle", ""),
            "parent_insert": t.get("parent_insert"),
            "circle_entity": c["entity"],
            "circle_center": center,
            "circle_radius": radius,
            "circle_source": c["source"],
            "circle_handle": c.get("handle", ""),
            "candidate_texts": [x["text"] for x in candidates[:10]],
            "text_matches": len(candidates),
        })

    trusted = []

    for b in bubbles:
        center = b["circle_center"]
        radius = b["circle_radius"]

        attached = [
            ln for ln in lines
            if line_attached_to_circle(ln, center, radius, attach_gap)
        ]

        selected = None
        mode = ""

        if attached:
            selected = max(attached, key=lambda x: x["length"])
            mode = "attached_gridline"

        else:
            close, perp = closest_grid_line_to_circle(center, lines, attach_gap)
            relaxed_limit = max(attach_gap * 6.0, 1200.0)

            if close is not None and perp is not None and perp <= relaxed_limit:
                selected = close
                mode = "closest_gridline"

            else:
                orient, coord = infer_orientation_from_same_label(b, bubbles, text_gap)

                if orient:
                    selected = make_virtual_axis(center, orient, coord, line_layer)
                    mode = "same_label_virtual_axis"

        if selected is None:
            rejected.append({
                "circle_center": center,
                "circle_radius": radius,
                "candidate_texts": b.get("candidate_texts", []),
                "text_matches": b.get("text_matches", 1),
                "reason": "Text found, but no gridline/axis could be inferred",
            })
            continue

        marker = {
            "label": b["label"],
            "text_entity": b["text_entity"],
            "text_point": b["text_point"],
            "text_source": b["text_source"],
            "text_handle": b.get("text_handle", ""),
            "parent_insert": b.get("parent_insert"),
            "circle_entity": b["circle_entity"],
            "circle_center": b["circle_center"],
            "circle_radius": b["circle_radius"],
            "circle_source": b["circle_source"],
            "circle_handle": b.get("circle_handle", ""),
            "line_entity": selected.get("entity"),
            "orientation": selected["orientation"],
            "coord": selected["coord"],
            "line_start": selected["start"],
            "line_end": selected["end"],
            "line_length": selected["length"],
            "detection_mode": mode,
            "candidate_texts": b.get("candidate_texts", []),
            "text_matches": b.get("text_matches", 1),
        }

        marker["writable"] = is_marker_writable(marker, allow_block_text_write)
        marker["confidence"] = marker_confidence(
            mode,
            marker["text_matches"],
            marker["writable"],
        )

        trusted.append(marker)

    return {
        "texts": texts,
        "circles": circles,
        "lines": lines,
        "trusted_markers": trusted,
        "rejected_markers": rejected,
    }


# =========================================================
# AXIS GROUPING / FAMILY INFERENCE
# =========================================================

def resolve_axis_group(group, orientation):
    coord = round(sum(m["coord"] for m in group) / len(group), 3)

    counts = {}

    for m in group:
        label = clean_text(m.get("label", ""))

        if label:
            counts[label] = counts.get(label, 0) + 1

    sorted_counts = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    label = sorted_counts[0][0] if sorted_counts else ""

    xs = [m["circle_center"][0] for m in group]
    ys = [m["circle_center"][1] for m in group]

    label_count = len(counts)
    majority_count = sorted_counts[0][1] if sorted_counts else 0
    majority_ratio = majority_count / len(group) if group else 0.0

    return {
        "orientation": orientation,
        "coord": coord,
        "label": label,
        "markers": group,
        "marker_count": len(group),
        "writable_marker_count": len([m for m in group if m.get("writable")]),
        "min_confidence": min([m.get("confidence", 0) for m in group]) if group else 0,
        "avg_confidence": round(
            sum([m.get("confidence", 0) for m in group]) / len(group),
            1,
        ) if group else 0,
        "label_count": label_count,
        "label_counts": counts,
        "mixed_labels": sorted(counts.keys()),
        "majority_label_count": majority_count,
        "majority_label_ratio": round(majority_ratio, 3),
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
    grouped = {
        "vertical": [],
        "horizontal": [],
    }

    for orientation in ["vertical", "horizontal"]:
        subset = sorted(
            [m for m in markers if m["orientation"] == orientation],
            key=lambda x: x["coord"],
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


def infer_families(axis_groups):
    vertical = axis_groups.get("vertical", [])
    horizontal = axis_groups.get("horizontal", [])

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

    return {
        "numeric_orientation": numeric_orientation,
        "alpha_orientation": alpha_orientation,
        "vertical_counts": {
            "numeric": v_num,
            "alpha": v_alpha,
        },
        "horizontal_counts": {
            "numeric": h_num,
            "alpha": h_alpha,
        },
    }


def sort_groups(groups, orientation, order_mode, family):
    if order_mode == "Ascending":
        reverse = False
    elif order_mode == "Descending":
        reverse = True
    else:
        # Auto: horizontal numeric grids are often read top-to-bottom.
        reverse = family == "numeric" and orientation == "horizontal"

    return sorted(groups, key=lambda x: x["coord"], reverse=reverse)


def sort_axis_lines_for_family(lines, orientation, order_mode, family):
    if order_mode == "Ascending":
        reverse = False
    elif order_mode == "Descending":
        reverse = True
    else:
        reverse = family == "numeric" and orientation == "horizontal"

    return sorted(lines, key=lambda x: x["coord"], reverse=reverse)


def get_family_groups(region_or_axis_groups, numeric_orientation, alpha_orientation, numeric_order, alpha_order):
    if "axis_groups" in region_or_axis_groups:
        axis_groups = region_or_axis_groups["axis_groups"]
    else:
        axis_groups = region_or_axis_groups

    numeric = sort_groups(
        axis_groups.get(numeric_orientation, []),
        numeric_orientation,
        numeric_order,
        "numeric",
    )

    alpha = sort_groups(
        axis_groups.get(alpha_orientation, []),
        alpha_orientation,
        alpha_order,
        "alpha",
    )

    return numeric, alpha


# =========================================================
# REGION SEGMENTATION / PERIMETER FILTER
# =========================================================

def marker_is_near_region_perimeter(marker, bbox, band_ratio=0.18, min_band=1500.0):
    x, y = marker["circle_center"]

    width = max(float(bbox.get("width", 0.0)), 1.0)
    height = max(float(bbox.get("height", 0.0)), 1.0)

    band_x = max(width * band_ratio, min_band)
    band_y = max(height * band_ratio, min_band)

    near_left = abs(x - bbox["min_x"]) <= band_x
    near_right = abs(x - bbox["max_x"]) <= band_x
    near_bottom = abs(y - bbox["min_y"]) <= band_y
    near_top = abs(y - bbox["max_y"]) <= band_y

    return near_left or near_right or near_bottom or near_top


def filter_region_perimeter_markers(region, axis_tol, band_ratio=0.18, min_band=1500.0):
    bbox = region.get("bbox") or bbox_from_markers(region["markers"])

    perimeter_markers = [
        m for m in region["markers"]
        if marker_is_near_region_perimeter(
            m,
            bbox,
            band_ratio=band_ratio,
            min_band=min_band,
        )
    ]

    filtered = dict(region)
    filtered["all_markers"] = region["markers"]
    filtered["markers"] = perimeter_markers
    filtered["interior_marker_count"] = len(region["markers"]) - len(perimeter_markers)
    filtered["axis_groups"] = group_markers_by_axis(perimeter_markers, tol=axis_tol)

    return filtered


def marker_xy(marker):
    x, y = marker["circle_center"]
    return float(x), float(y)


def kmeans_regions(markers, k, max_iter=80):
    if not markers or k < 1:
        return None

    points = [marker_xy(m) for m in markers]

    first = min(
        range(len(points)),
        key=lambda i: (points[i][0] + points[i][1], points[i][0]),
    )

    centers = [points[first]]

    while len(centers) < k:
        farthest = max(
            range(len(points)),
            key=lambda i: min(squared_distance(points[i], c) for c in centers),
        )
        centers.append(points[farthest])

    clusters = [[] for _ in range(k)]

    for _ in range(max_iter):
        new_clusters = [[] for _ in range(k)]

        for marker, point in zip(markers, points):
            idx = min(range(k), key=lambda i: squared_distance(point, centers[i]))
            new_clusters[idx].append(marker)

        if any(len(c) == 0 for c in new_clusters):
            return None

        new_centers = []

        for c in new_clusters:
            xs = [marker_xy(m)[0] for m in c]
            ys = [marker_xy(m)[1] for m in c]
            new_centers.append((sum(xs) / len(xs), sum(ys) / len(ys)))

        movement = sum(squared_distance(a, b) for a, b in zip(centers, new_centers))

        centers = new_centers
        clusters = new_clusters

        if movement < 1e-6:
            break

    return clusters


def compute_major_gap_threshold(values):
    vals = sorted(set(round(v, 3) for v in values))

    if len(vals) < 2:
        return None

    gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    gaps = [g for g in gaps if g > 0]

    if not gaps:
        return None

    typical = sorted(gaps)[len(gaps) // 2]
    return max(typical * 3.0, 6000.0)


def value_groups(values, threshold):
    vals = sorted(set(round(v, 3) for v in values))

    if not vals:
        return []

    groups = [[vals[0]]]

    for v in vals[1:]:
        if abs(v - groups[-1][-1]) > threshold:
            groups.append([v])
        else:
            groups[-1].append(v)

    return groups


def assign_value(value, groups):
    v = round(value, 3)

    for i, g in enumerate(groups):
        if g[0] <= v <= g[-1]:
            return i

    return None


def build_regions(markers, axis_tol, expected_markers_per_region=None, forced_region_count=0, min_region_markers=4):
    if not markers:
        return [], {}

    total = len(markers)
    k = None
    method_reason = ""

    if forced_region_count and forced_region_count > 0:
        k = int(forced_region_count)
        method_reason = "forced_region_count"

    elif expected_markers_per_region and expected_markers_per_region > 0:
        raw = total / expected_markers_per_region
        rounded = int(round(raw))

        if rounded >= 2 and abs(raw - rounded) <= 0.25:
            k = rounded
            method_reason = "marker_count_ratio"

    if k and k >= 1:
        clusters = kmeans_regions(markers, k)

        if clusters:
            sortable = []

            for c in clusters:
                bbox = bbox_from_markers(c)
                cx, cy = bbox["centroid"]
                sortable.append((-cy, cx, c, bbox))

            sortable.sort()

            regions = []

            for i, (_, _, c, bbox) in enumerate(sortable, start=1):
                if len(c) < min_region_markers:
                    continue

                regions.append({
                    "name": f"Region {i}",
                    "markers": c,
                    "axis_groups": group_markers_by_axis(c, tol=axis_tol),
                    "bbox": bbox,
                    "marker_count": len(c),
                    "segmentation_key": method_reason,
                })

            return regions, {
                "segmentation_method": "kmeans_spatial_clustering",
                "method_reason": method_reason,
                "total_markers": total,
                "expected_markers_per_region": expected_markers_per_region,
                "forced_region_count": forced_region_count,
                "regions_found": len(regions),
                "region_marker_counts": [r["marker_count"] for r in regions],
            }

    # Empty-space fallback.
    xs = [m["circle_center"][0] for m in markers]
    ys = [m["circle_center"][1] for m in markers]

    x_threshold = compute_major_gap_threshold(xs) or 6000.0
    y_threshold = compute_major_gap_threshold(ys) or 6000.0

    x_groups = value_groups(xs, x_threshold)
    y_groups = value_groups(ys, y_threshold)

    buckets = {}

    for m in markers:
        xi = assign_value(m["circle_center"][0], x_groups)
        yi = assign_value(m["circle_center"][1], y_groups)
        buckets.setdefault((xi, yi), []).append(m)

    regions = []
    n = 1
    skipped = 0

    for key, bucket in sorted(buckets.items(), key=lambda item: (item[0][1], item[0][0])):
        if len(bucket) < min_region_markers:
            skipped += 1
            continue

        regions.append({
            "name": f"Region {n}",
            "markers": bucket,
            "axis_groups": group_markers_by_axis(bucket, tol=axis_tol),
            "bbox": bbox_from_markers(bucket),
            "marker_count": len(bucket),
            "segmentation_key": key,
        })

        n += 1

    return regions, {
        "segmentation_method": "empty_space_fallback",
        "total_markers": total,
        "x_gap_threshold": x_threshold,
        "y_gap_threshold": y_threshold,
        "raw_bucket_count": len(buckets),
        "regions_found": len(regions),
        "small_buckets_skipped": skipped,
        "region_marker_counts": [r["marker_count"] for r in regions],
    }
# =========================================================
# CLEAN SYNC VALIDATION / PREVIEW / APPLY
# =========================================================

def validate_axis_group_purity(groups, family_name):
    blockers = []

    for idx, g in enumerate(groups, start=1):
        label_count = g.get("label_count", 1)
        mixed_labels = g.get("mixed_labels", [])
        label_counts = g.get("label_counts", {})
        marker_count = g.get("marker_count", 0)

        if label_count > 1:
            blockers.append(
                f"{family_name} axis position {idx} at coord {g.get('coord')} has mixed labels "
                f"{mixed_labels}. Counts={label_counts}. Marker count={marker_count}."
            )

    return blockers


def get_region_sync_plan(
    region,
    source_numeric,
    source_alpha,
    numeric_orientation,
    alpha_orientation,
    numeric_order,
    alpha_order,
    allow_block_text_write,
    expected_reference_marker_count=None,
    max_region_marker_ratio=1.5,
):
    numeric_groups, alpha_groups = get_family_groups(
        region,
        numeric_orientation,
        alpha_orientation,
        numeric_order,
        alpha_order,
    )

    blockers = []

    expected_numeric = len(source_numeric)
    expected_alpha = len(source_alpha)
    expected_total = expected_numeric + expected_alpha

    if expected_total == 0:
        blockers.append("No source axes were detected from the reference drawing")

    if len(numeric_groups) != expected_numeric:
        blockers.append(
            f"Numeric axis count mismatch: found {len(numeric_groups)}, expected {expected_numeric}"
        )

    if len(alpha_groups) != expected_alpha:
        blockers.append(
            f"Alphabetic axis count mismatch: found {len(alpha_groups)}, expected {expected_alpha}"
        )

    if expected_reference_marker_count and expected_reference_marker_count > 0:
        max_allowed = int(math.ceil(expected_reference_marker_count * max_region_marker_ratio))

        if len(region["markers"]) > max_allowed:
            blockers.append(
                f"Region has too many sync markers: found {len(region['markers'])}, "
                f"maximum allowed {max_allowed}. This may mean detail/slab bubbles were included."
            )

    blockers.extend(validate_axis_group_purity(numeric_groups, "Numeric"))
    blockers.extend(validate_axis_group_purity(alpha_groups, "Alphabetic"))

    non_writable_markers = [
        m for m in region["markers"]
        if not is_marker_writable(m, allow_block_text_write)
    ]

    if non_writable_markers:
        blockers.append(
            f"{len(non_writable_markers)} marker text entities are not writable in the selected write mode"
        )

    ready = len(blockers) == 0

    return ready, blockers, numeric_groups, alpha_groups


def build_region_report(
    regions,
    source_numeric,
    source_alpha,
    numeric_orientation,
    alpha_orientation,
    numeric_order,
    alpha_order,
    allow_block_text_write,
    expected_reference_marker_count=None,
    max_region_marker_ratio=1.5,
):
    rows = []

    expected_numeric = len(source_numeric)
    expected_alpha = len(source_alpha)
    expected_total = expected_numeric + expected_alpha

    for r in regions:
        ready, blockers, num, alp = get_region_sync_plan(
            r,
            source_numeric,
            source_alpha,
            numeric_orientation,
            alpha_orientation,
            numeric_order,
            alpha_order,
            allow_block_text_write,
            expected_reference_marker_count=expected_reference_marker_count,
            max_region_marker_ratio=max_region_marker_ratio,
        )

        writable = len([m for m in r["markers"] if is_marker_writable(m, allow_block_text_write)])
        non_writable = len(r["markers"]) - writable

        min_conf = min([m.get("confidence", 0) for m in r["markers"]]) if r["markers"] else 0
        avg_conf = round(
            sum([m.get("confidence", 0) for m in r["markers"]]) / len(r["markers"]),
            1,
        ) if r["markers"] else 0

        complete = len(num) == expected_numeric and len(alp) == expected_alpha and expected_total > 0

        if ready:
            status = "Ready"
        elif len(num) != expected_numeric or len(alp) != expected_alpha or expected_total == 0:
            status = "Incomplete"
        elif non_writable > 0:
            status = "Blocked"
        else:
            status = "Review"

        rows.append({
            "region": r["name"],
            "status": status,
            "ready_to_sync": ready,
            "complete": complete,
            "trusted_markers": len(r["markers"]),
            "all_detected_markers": len(r.get("all_markers", r["markers"])),
            "interior_markers_ignored": r.get("interior_marker_count", 0),
            "writable_markers": writable,
            "non_writable_markers": non_writable,
            "numeric_axes_found": len(num),
            "numeric_axes_expected": expected_numeric,
            "alpha_axes_found": len(alp),
            "alpha_axes_expected": expected_alpha,
            "numeric_labels_found": ", ".join([g["label"] for g in num]),
            "alpha_labels_found": ", ".join([g["label"] for g in alp]),
            "min_confidence": min_conf,
            "avg_confidence": avg_conf,
            "sync_blockers": "; ".join(blockers),
        })

    return rows


def build_preview_for_region(region, source_numeric, source_alpha, numeric_groups, alpha_groups):
    rows = []

    for i, (s, t) in enumerate(zip(source_numeric, numeric_groups), start=1):
        rows.append({
            "region": region["name"],
            "family": "numeric",
            "position": i,
            "source_label": s["label"],
            "target_old_label": t["label"],
            "target_axis_coord": t["coord"],
            "target_markers": t["marker_count"],
            "target_writable_markers": t["writable_marker_count"],
            "target_avg_confidence": t["avg_confidence"],
            "target_mixed_labels": ", ".join(t.get("mixed_labels", [])),
        })

    for i, (s, t) in enumerate(zip(source_alpha, alpha_groups), start=1):
        rows.append({
            "region": region["name"],
            "family": "alphabetic",
            "position": i,
            "source_label": s["label"],
            "target_old_label": t["label"],
            "target_axis_coord": t["coord"],
            "target_markers": t["marker_count"],
            "target_writable_markers": t["writable_marker_count"],
            "target_avg_confidence": t["avg_confidence"],
            "target_mixed_labels": ", ".join(t.get("mixed_labels", [])),
        })

    return rows


def apply_group_labels(source_groups, target_groups, allow_block_text_write=False):
    changed = 0
    skipped = 0
    audit = []

    if len(source_groups) != len(target_groups):
        return 0, len(target_groups), [{
            "region_axis_position": "",
            "old_label": "",
            "new_label": "",
            "changed": False,
            "skipped": True,
            "reason": f"Group count mismatch. Source={len(source_groups)}, Target={len(target_groups)}. No partial sync applied.",
            "text_source": "",
            "detection_mode": "",
            "confidence": "",
            "entity_handle": "",
        }]

    for i, (s, t) in enumerate(zip(source_groups, target_groups), start=1):
        new_label = clean_text(s["label"])

        for marker in t["markers"]:
            entity = marker["text_entity"]
            old = get_text_value(entity)
            writable = is_marker_writable(marker, allow_block_text_write)

            if not writable:
                skipped += 1
                audit.append({
                    "region_axis_position": i,
                    "old_label": old,
                    "new_label": new_label,
                    "changed": False,
                    "skipped": True,
                    "reason": f"Not writable source={marker.get('text_source')}",
                    "text_source": marker.get("text_source"),
                    "detection_mode": marker.get("detection_mode"),
                    "confidence": marker.get("confidence"),
                    "entity_handle": marker.get("text_handle", get_entity_handle(entity)),
                })
                continue

            if old != new_label:
                ok = set_text_value(entity, new_label)

                if ok:
                    changed += 1

                audit.append({
                    "region_axis_position": i,
                    "old_label": old,
                    "new_label": new_label,
                    "changed": ok,
                    "skipped": False,
                    "reason": "updated" if ok else "write_failed",
                    "text_source": marker.get("text_source"),
                    "detection_mode": marker.get("detection_mode"),
                    "confidence": marker.get("confidence"),
                    "entity_handle": marker.get("text_handle", get_entity_handle(entity)),
                })

            else:
                audit.append({
                    "region_axis_position": i,
                    "old_label": old,
                    "new_label": new_label,
                    "changed": False,
                    "skipped": False,
                    "reason": "already_matches",
                    "text_source": marker.get("text_source"),
                    "detection_mode": marker.get("detection_mode"),
                    "confidence": marker.get("confidence"),
                    "entity_handle": marker.get("text_handle", get_entity_handle(entity)),
                })

    return changed, skipped, audit


# =========================================================
# ENDPOINT RECOVERY LOGIC
# =========================================================

def source_numeric_range(source_numeric, extra=5):
    vals = []

    for g in source_numeric:
        v = numeric_label_value(g.get("label", ""))

        if v is not None:
            vals.append(v)

    if not vals:
        return 1, 999

    return max(1, min(vals) - extra), max(vals) + extra


def plausible_recovery_label(label, family, source_numeric, source_alpha, numeric_extra=5):
    label = clean_text(label)

    if family == "numeric":
        v = numeric_label_value(label)

        if v is None:
            return False

        lo, hi = source_numeric_range(source_numeric, extra=numeric_extra)

        return lo <= v <= hi

    if family == "alphabetic":
        if not is_alpha_label(label):
            return False

        source_labels = {
            clean_text(g.get("label", ""))
            for g in source_alpha
        }

        if label in source_labels:
            return True

        return bool(re.fullmatch(r"[A-Z]{1,2}'?", label))

    return False


def line_overlaps_region_bbox(line, bbox, margin=2000.0):
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    if line["orientation"] == "vertical":
        coord = line["coord"]

        if coord < bbox["min_x"] - margin or coord > bbox["max_x"] + margin:
            return False

        seg_min = min(y1, y2)
        seg_max = max(y1, y2)

        return seg_max >= bbox["min_y"] - margin and seg_min <= bbox["max_y"] + margin

    if line["orientation"] == "horizontal":
        coord = line["coord"]

        if coord < bbox["min_y"] - margin or coord > bbox["max_y"] + margin:
            return False

        seg_min = min(x1, x2)
        seg_max = max(x1, x2)

        return seg_max >= bbox["min_x"] - margin and seg_min <= bbox["max_x"] + margin

    return False


def get_region_axis_lines(region, all_lines, orientation, order_mode, family, search_margin):
    bbox = region["bbox"]

    lines = [
        ln for ln in all_lines
        if ln.get("orientation") == orientation
        and line_overlaps_region_bbox(ln, bbox, margin=search_margin)
    ]

    return sort_axis_lines_for_family(lines, orientation, order_mode, family)


def endpoint_points_for_axis_line(line, bbox):
    if line["orientation"] == "vertical":
        x = line["coord"]

        return [
            (x, bbox["min_y"]),
            (x, bbox["max_y"]),
        ]

    y = line["coord"]

    return [
        (bbox["min_x"], y),
        (bbox["max_x"], y),
    ]


def marker_endpoint_distance(marker, endpoints):
    p = marker["circle_center"]

    distances = [
        euclidean(p, ep)
        for ep in endpoints
    ]

    best = min(distances)

    return best, distances.index(best)


def build_endpoint_recovery_plan(
    region,
    source_numeric,
    source_alpha,
    numeric_orientation,
    alpha_orientation,
    numeric_order,
    alpha_order,
    all_lines,
    endpoint_radius,
    allow_block_text_write,
    numeric_extra=5,
):
    blockers = []
    warnings = []
    plan_rows = []

    bbox = region["bbox"]

    # Use all markers before perimeter filtering if available.
    candidate_markers = region.get("all_markers", region.get("markers", []))

    numeric_lines = get_region_axis_lines(
        region,
        all_lines,
        numeric_orientation,
        numeric_order,
        "numeric",
        search_margin=endpoint_radius,
    )

    alpha_lines = get_region_axis_lines(
        region,
        all_lines,
        alpha_orientation,
        alpha_order,
        "alphabetic",
        search_margin=endpoint_radius,
    )

    if len(numeric_lines) != len(source_numeric):
        blockers.append(
            f"Recovery numeric axis-line count mismatch: found {len(numeric_lines)}, expected {len(source_numeric)}."
        )

    if len(alpha_lines) != len(source_alpha):
        blockers.append(
            f"Recovery alphabetic axis-line count mismatch: found {len(alpha_lines)}, expected {len(source_alpha)}."
        )

    families = [
        ("numeric", source_numeric, numeric_lines),
        ("alphabetic", source_alpha, alpha_lines),
    ]

    seen_handles = set()

    for family_name, source_groups, axis_lines in families:
        if len(source_groups) != len(axis_lines):
            continue

        for pos, (source_group, line) in enumerate(zip(source_groups, axis_lines), start=1):
            new_label = clean_text(source_group.get("label", ""))
            endpoints = endpoint_points_for_axis_line(line, bbox)

            endpoint_candidates = {
                0: [],
                1: [],
            }

            for marker in candidate_markers:
                old_label = clean_text(marker.get("label", ""))

                if not plausible_recovery_label(
                    old_label,
                    family_name,
                    source_numeric,
                    source_alpha,
                    numeric_extra=numeric_extra,
                ):
                    continue

                d, endpoint_index = marker_endpoint_distance(marker, endpoints)

                if d <= endpoint_radius:
                    endpoint_candidates[endpoint_index].append((d, marker))

            selected = []

            for endpoint_index in [0, 1]:
                cands = endpoint_candidates[endpoint_index]

                if not cands:
                    continue

                cands.sort(key=lambda x: x[0])
                selected.append((endpoint_index, cands[0][0], cands[0][1]))

            if not selected:
                blockers.append(
                    f"Recovery could not find endpoint grid-label candidate for {family_name} axis position {pos}, source label {new_label}."
                )
                continue

            for endpoint_index, distance, marker in selected:
                entity = marker["text_entity"]
                handle = marker.get("text_handle", get_entity_handle(entity))

                if handle in seen_handles:
                    continue

                seen_handles.add(handle)

                old_label = get_text_value(entity)
                writable = is_marker_writable(marker, allow_block_text_write)

                if not writable:
                    blockers.append(
                        f"Recovery candidate for {family_name} axis position {pos} is not writable. Handle={handle}."
                    )

                plan_rows.append({
                    "region": region["name"],
                    "family": family_name,
                    "axis_position": pos,
                    "source_label": new_label,
                    "old_label": old_label,
                    "new_label": new_label,
                    "endpoint": "A" if endpoint_index == 0 else "B",
                    "distance_to_endpoint": round(distance, 3),
                    "axis_coord": line["coord"],
                    "line_handle": line.get("handle", ""),
                    "text_handle": handle,
                    "text_source": marker.get("text_source"),
                    "confidence": marker.get("confidence"),
                    "writable": writable,
                    "entity": entity,
                    "marker": marker,
                })

    ready = len(blockers) == 0

    return ready, blockers, warnings, plan_rows


def recovery_preview_rows(plan_rows):
    rows = []

    for r in plan_rows:
        rows.append({
            "region": r["region"],
            "family": r["family"],
            "axis_position": r["axis_position"],
            "old_label": r["old_label"],
            "new_label": r["new_label"],
            "endpoint": r["endpoint"],
            "distance_to_endpoint": r["distance_to_endpoint"],
            "axis_coord": r["axis_coord"],
            "text_handle": r["text_handle"],
            "text_source": r["text_source"],
            "confidence": r["confidence"],
            "writable": r["writable"],
        })

    return rows


def apply_endpoint_recovery_plan(plan_rows):
    changed = 0
    skipped = 0
    audit = []

    for r in plan_rows:
        entity = r["entity"]
        old = get_text_value(entity)
        new = clean_text(r["new_label"])

        if not r.get("writable"):
            skipped += 1

            audit.append({
                "region": r["region"],
                "family": r["family"],
                "region_axis_position": r["axis_position"],
                "old_label": old,
                "new_label": new,
                "changed": False,
                "skipped": True,
                "reason": "recovery_not_writable",
                "sync_mode": "endpoint_recovery",
                "endpoint": r["endpoint"],
                "distance_to_endpoint": r["distance_to_endpoint"],
                "entity_handle": r["text_handle"],
                "text_source": r["text_source"],
                "confidence": r["confidence"],
            })

            continue

        if old != new:
            ok = set_text_value(entity, new)

            if ok:
                changed += 1

            audit.append({
                "region": r["region"],
                "family": r["family"],
                "region_axis_position": r["axis_position"],
                "old_label": old,
                "new_label": new,
                "changed": ok,
                "skipped": False,
                "reason": "endpoint_recovery_updated" if ok else "endpoint_recovery_write_failed",
                "sync_mode": "endpoint_recovery",
                "endpoint": r["endpoint"],
                "distance_to_endpoint": r["distance_to_endpoint"],
                "entity_handle": r["text_handle"],
                "text_source": r["text_source"],
                "confidence": r["confidence"],
            })

        else:
            audit.append({
                "region": r["region"],
                "family": r["family"],
                "region_axis_position": r["axis_position"],
                "old_label": old,
                "new_label": new,
                "changed": False,
                "skipped": False,
                "reason": "already_matches",
                "sync_mode": "endpoint_recovery",
                "endpoint": r["endpoint"],
                "distance_to_endpoint": r["distance_to_endpoint"],
                "entity_handle": r["text_handle"],
                "text_source": r["text_source"],
                "confidence": r["confidence"],
            })

    return changed, skipped, audit


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
        "arch_axis_groups": {},
        "family": {},
        "source_numeric": [],
        "source_alpha": [],
        "regions": [],
        "segmentation": {},
        "region_report": [],
        "preview": [],
        "audit": [],
        "changed": 0,
        "skipped": 0,
        "prepared": False,
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_state():
    for k in [
        "docs_loaded",
        "arch_doc",
        "struc_doc",
        "arch_name",
        "struc_name",
        "arch_detection",
        "struc_detection",
        "arch_axis_groups",
        "family",
        "source_numeric",
        "source_alpha",
        "regions",
        "segmentation",
        "region_report",
        "preview",
        "audit",
        "changed",
        "skipped",
        "prepared",
    ]:
        if k in st.session_state:
            del st.session_state[k]
# =========================================================
# MAIN UI
# =========================================================

init_state()

st.markdown("### 1. Detection Settings")

c1, c2, c3, c4 = st.columns(4)

with c1:
    axis_tol = st.slider(
        "Axis Group Tolerance",
        0.0,
        500.0,
        10.0,
        0.5,
    )

with c2:
    text_gap = st.slider(
        "Text-in-Bubble Gap",
        20.0,
        2000.0,
        180.0,
        10.0,
    )

with c3:
    attach_gap = st.slider(
        "Gridline Attach Gap",
        20.0,
        3000.0,
        180.0,
        10.0,
    )

with c4:
    min_grid_length = st.number_input(
        "Min Grid Line Length",
        min_value=1.0,
        value=1000.0,
        step=100.0,
    )

d1, d2, d3, d4 = st.columns(4)

with d1:
    numeric_order = st.selectbox(
        "Numeric Axis Order",
        ["Auto", "Ascending", "Descending"],
        index=0,
    )

with d2:
    alpha_order = st.selectbox(
        "Alphabetic Axis Order",
        ["Auto", "Ascending", "Descending"],
        index=0,
    )

with d3:
    forced_region_count = st.number_input(
        "Target Region Count Override",
        min_value=0,
        value=0,
        step=1,
        help="Use 0 for auto. If the target has 8 plans, enter 8.",
    )

with d4:
    min_region_markers = st.number_input(
        "Min Markers Per Region",
        min_value=1,
        value=4,
        step=1,
    )

e1, e2, e3 = st.columns(3)

with e1:
    allow_block_text_write = st.checkbox(
        "Advanced: allow block TEXT/MTEXT write",
        value=False,
        help="Use only on copied DXF files. Editing block definitions can affect repeated inserts.",
    )

with e2:
    min_confidence_required = st.slider(
        "Minimum Marker Confidence",
        0,
        100,
        50,
        5,
    )

with e3:
    max_region_marker_ratio = st.slider(
        "Max Sync Marker Ratio",
        1.0,
        5.0,
        1.5,
        0.1,
        help="Blocks clean sync if a region still has far more markers than the reference.",
    )

f1, f2, f3 = st.columns(3)

with f1:
    ignore_interior_detail_bubbles = st.checkbox(
        "Ignore interior/detail bubbles for clean sync",
        value=True,
        help="Recommended. Clean sync keeps only perimeter markers so slab/detail bubbles are not touched.",
    )

with f2:
    perimeter_band_ratio = st.slider(
        "Perimeter Band Ratio",
        0.05,
        0.40,
        0.18,
        0.01,
        help="Higher keeps more markers near the plan edge. Lower is stricter.",
    )

with f3:
    perimeter_min_band = st.number_input(
        "Minimum Perimeter Band",
        min_value=100.0,
        value=1500.0,
        step=100.0,
        help="Minimum drawing-unit distance from region edge to treat a bubble as perimeter.",
    )

st.markdown("#### Slab/Grid Endpoint Recovery Settings")

r1, r2 = st.columns(2)

with r1:
    recovery_endpoint_radius = st.slider(
        "Recovery Endpoint Search Radius",
        500.0,
        10000.0,
        2500.0,
        100.0,
        help="Recovery mode searches this distance around grid-line ends for grid-label bubbles.",
    )

with r2:
    recovery_numeric_extra = st.slider(
        "Recovery Numeric Label Extra Range",
        0,
        20,
        5,
        1,
        help="If reference numeric labels are 1–17, extra 5 allows candidates up to 22 but rejects slab labels like 145.",
    )


# =========================================================
# FILE UPLOAD
# =========================================================

st.markdown("### 2. Upload Files")

u1, u2 = st.columns(2)

with u1:
    arch_file = st.file_uploader(
        "Reference DXF",
        type=["dxf"],
        key="arch",
    )

with u2:
    struc_file = st.file_uploader(
        "Target DXF",
        type=["dxf"],
        key="struc",
    )

arch_sig = uploaded_file_signature(arch_file)
struc_sig = uploaded_file_signature(struc_file)

if arch_sig != st.session_state.arch_sig or struc_sig != st.session_state.struc_sig:
    reset_state()
    init_state()
    st.session_state.arch_sig = arch_sig
    st.session_state.struc_sig = struc_sig

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

            st.success("DXF files loaded successfully.")

        except Exception as e:
            st.error(f"Failed to load DXF files: {e}")
            st.stop()

        finally:
            safe_remove_file(arch_tmp)
            safe_remove_file(struc_tmp)

else:
    st.info("Upload both the Reference DXF and Target DXF to continue.")
    st.stop()


# =========================================================
# LAYER SETUP
# =========================================================

arch_doc = st.session_state.arch_doc
struc_doc = st.session_state.struc_doc

arch_layers = get_layer_names(arch_doc)
struc_layers = get_layer_names(struc_doc)

st.markdown("### 3. Layer Setup")

arch_line_default = pick_default_layer(
    arch_layers,
    ["S-GRID", "GRID", "GRIDLINELAYER"],
)
arch_text_default = pick_default_layer(
    arch_layers,
    ["S-GRID-IDEN", "GRID-ID", "DEFAULTLAYER"],
)
arch_circle_default = pick_default_layer(
    arch_layers,
    ["S-GRID-IDEN", "GRID-ID", "DEFAULTLAYER"],
)

struc_line_default = pick_default_layer(
    struc_layers,
    ["GRIDLINELAYER", "S-GRID", "GRID"],
)
struc_text_default = pick_default_layer(
    struc_layers,
    ["DEFAULTLAYER", "S-STRS-IDEN", "S-GRID-IDEN", "GRID-ID"],
)
struc_circle_default = pick_default_layer(
    struc_layers,
    ["DEFAULTLAYER", "S-GRID-IDEN", "GRID-ID"],
)

la, lb, lc = st.columns(3)

with la:
    arch_line_layer = st.selectbox(
        "Reference Grid Line Layer",
        arch_layers,
        index=arch_layers.index(arch_line_default) if arch_line_default in arch_layers else 0,
    )

with lb:
    arch_text_layer = st.selectbox(
        "Reference Grid Text Layer",
        arch_layers,
        index=arch_layers.index(arch_text_default) if arch_text_default in arch_layers else 0,
    )

with lc:
    arch_circle_layer = st.selectbox(
        "Reference Grid Bubble Layer",
        arch_layers,
        index=arch_layers.index(arch_circle_default) if arch_circle_default in arch_layers else 0,
    )

sa, sb, sc = st.columns(3)

with sa:
    struc_line_layer = st.selectbox(
        "Target Grid Line Layer",
        struc_layers,
        index=struc_layers.index(struc_line_default) if struc_line_default in struc_layers else 0,
    )

with sb:
    struc_text_layer = st.selectbox(
        "Target Grid Text Layer",
        struc_layers,
        index=struc_layers.index(struc_text_default) if struc_text_default in struc_layers else 0,
    )

with sc:
    struc_circle_layer = st.selectbox(
        "Target Grid Bubble Layer",
        struc_layers,
        index=struc_layers.index(struc_circle_default) if struc_circle_default in struc_layers else 0,
    )

b1, b2 = st.columns(2)

with b1:
    analyze = st.button("🔎 Analyze / Prepare Sync", type="primary")

with b2:
    if st.button("🧹 Reset"):
        reset_state()
        st.rerun()


# =========================================================
# ANALYZE
# =========================================================

if analyze:
    try:
        arch_det = build_trusted_markers(
            arch_doc,
            arch_line_layer,
            arch_text_layer,
            arch_circle_layer,
            min_grid_length,
            text_gap,
            attach_gap,
            allow_block_text_write,
        )

        struc_det = build_trusted_markers(
            struc_doc,
            struc_line_layer,
            struc_text_layer,
            struc_circle_layer,
            min_grid_length,
            text_gap,
            attach_gap,
            allow_block_text_write,
        )

        arch_trusted = [
            m for m in arch_det["trusted_markers"]
            if m.get("confidence", 0) >= min_confidence_required
        ]

        struc_trusted = [
            m for m in struc_det["trusted_markers"]
            if m.get("confidence", 0) >= min_confidence_required
        ]

        arch_det["trusted_markers"] = arch_trusted
        struc_det["trusted_markers"] = struc_trusted

        arch_groups = group_markers_by_axis(arch_trusted, tol=axis_tol)
        family = infer_families(arch_groups)

        source_numeric, source_alpha = get_family_groups(
            arch_groups,
            family["numeric_orientation"],
            family["alpha_orientation"],
            numeric_order,
            alpha_order,
        )

        expected_markers = len(arch_trusted)

        regions, seg = build_regions(
            struc_trusted,
            axis_tol=axis_tol,
            expected_markers_per_region=expected_markers,
            forced_region_count=forced_region_count,
            min_region_markers=min_region_markers,
        )

        if ignore_interior_detail_bubbles:
            regions = [
                filter_region_perimeter_markers(
                    r,
                    axis_tol=axis_tol,
                    band_ratio=perimeter_band_ratio,
                    min_band=perimeter_min_band,
                )
                for r in regions
            ]

            seg["interior_detail_filter"] = "enabled"
            seg["perimeter_band_ratio"] = perimeter_band_ratio
            seg["perimeter_min_band"] = perimeter_min_band
            seg["interior_markers_removed_by_region"] = {
                r["name"]: r.get("interior_marker_count", 0)
                for r in regions
            }
            seg["sync_marker_counts_after_filter"] = {
                r["name"]: len(r["markers"])
                for r in regions
            }

        else:
            seg["interior_detail_filter"] = "disabled"

        report = build_region_report(
            regions,
            source_numeric,
            source_alpha,
            family["numeric_orientation"],
            family["alpha_orientation"],
            numeric_order,
            alpha_order,
            allow_block_text_write,
            expected_reference_marker_count=len(arch_trusted),
            max_region_marker_ratio=max_region_marker_ratio,
        )

        preview = []

        for r in regions:
            ready, blockers, num, alp = get_region_sync_plan(
                r,
                source_numeric,
                source_alpha,
                family["numeric_orientation"],
                family["alpha_orientation"],
                numeric_order,
                alpha_order,
                allow_block_text_write,
                expected_reference_marker_count=len(arch_trusted),
                max_region_marker_ratio=max_region_marker_ratio,
            )

            if ready:
                preview.extend(
                    build_preview_for_region(
                        r,
                        source_numeric,
                        source_alpha,
                        num,
                        alp,
                    )
                )

        st.session_state.arch_detection = arch_det
        st.session_state.struc_detection = struc_det
        st.session_state.arch_axis_groups = arch_groups
        st.session_state.family = family
        st.session_state.source_numeric = source_numeric
        st.session_state.source_alpha = source_alpha
        st.session_state.regions = regions
        st.session_state.segmentation = seg
        st.session_state.region_report = report
        st.session_state.preview = preview
        st.session_state.audit = []
        st.session_state.changed = 0
        st.session_state.skipped = 0
        st.session_state.prepared = True

        ready_count = len([r for r in report if r.get("ready_to_sync")])

        st.success(
            f"Analysis complete. Regions found: {len(regions)}. Ready clean-sync regions: {ready_count}."
        )

        if arch_det["rejected_markers"]:
            st.warning(f"Reference rejected markers: {len(arch_det['rejected_markers'])}")

        if struc_det["rejected_markers"]:
            st.warning(f"Target rejected markers: {len(struc_det['rejected_markers'])}")

    except Exception as e:
        st.error(f"Prepare failed: {e}")


# =========================================================
# RESULTS / CLEAN SYNC / RECOVERY
# =========================================================

if st.session_state.prepared:
    st.markdown("---")
    st.markdown("### 4. Detection Summary")

    arch_det = st.session_state.arch_detection
    struc_det = st.session_state.struc_detection
    family = st.session_state.family

    s1, s2 = st.columns(2)

    with s1:
        st.write("#### Reference Detection")
        st.write({
            "texts_on_selected_layer": len(arch_det.get("texts", [])),
            "circles_on_selected_layer": len(arch_det.get("circles", [])),
            "axis_lines_on_selected_layer": len(arch_det.get("lines", [])),
            "trusted_markers_used": len(arch_det.get("trusted_markers", [])),
            "rejected_markers": len(arch_det.get("rejected_markers", [])),
            "numeric_orientation": family.get("numeric_orientation"),
            "alpha_orientation": family.get("alpha_orientation"),
            "numeric_source_labels": [g["label"] for g in st.session_state.source_numeric],
            "alpha_source_labels": [g["label"] for g in st.session_state.source_alpha],
            "family_counts": {
                "vertical": family.get("vertical_counts"),
                "horizontal": family.get("horizontal_counts"),
            },
        })

    with s2:
        modes = {}

        for m in struc_det.get("trusted_markers", []):
            mode = m.get("detection_mode", "unknown")
            modes[mode] = modes.get(mode, 0) + 1

        st.write("#### Target Detection")
        st.write({
            "texts_on_selected_layer": len(struc_det.get("texts", [])),
            "circles_on_selected_layer": len(struc_det.get("circles", [])),
            "axis_lines_on_selected_layer": len(struc_det.get("lines", [])),
            "trusted_markers_used_before_filter": len(struc_det.get("trusted_markers", [])),
            "rejected_markers": len(struc_det.get("rejected_markers", [])),
            "regions_found": len(st.session_state.regions),
            "detection_modes": modes,
        })

    st.markdown("### 5. Segmentation / Interior Filter")
    st.write(st.session_state.segmentation)

    st.markdown("### 6. Region Completeness Dashboard")
    st.dataframe(st.session_state.region_report, use_container_width=True)

    ready_regions = [
        r["region"]
        for r in st.session_state.region_report
        if r.get("ready_to_sync")
    ]

    all_regions = [
        r["region"]
        for r in st.session_state.region_report
    ]

    selected_regions = st.multiselect(
        "Select clean regions to sync",
        options=all_regions,
        default=ready_regions,
        help="Only clean ready regions are selected by default. Slab/detail regions can be handled in Recovery Mode below.",
    )

    st.markdown("### 7. Clean Sync Preview")

    filtered_preview = [
        row for row in st.session_state.preview
        if row["region"] in selected_regions
    ]

    if filtered_preview:
        st.dataframe(filtered_preview, use_container_width=True)
    else:
        st.info("No clean-sync preview rows. This usually means no clean ready regions are selected.")

    st.markdown("### 8. Apply Clean Sync")

    region_by_name = {
        r["name"]: r
        for r in st.session_state.regions
    }

    blocked_selected = []

    for region_name in selected_regions:
        r = region_by_name.get(region_name)

        if not r:
            blocked_selected.append({
                "region": region_name,
                "reason": "Region not found in session state",
            })
            continue

        ready, blockers, num, alp = get_region_sync_plan(
            r,
            st.session_state.source_numeric,
            st.session_state.source_alpha,
            family["numeric_orientation"],
            family["alpha_orientation"],
            numeric_order,
            alpha_order,
            allow_block_text_write,
            expected_reference_marker_count=len(st.session_state.arch_detection.get("trusted_markers", [])),
            max_region_marker_ratio=max_region_marker_ratio,
        )

        if not ready:
            blocked_selected.append({
                "region": region_name,
                "reason": "; ".join(blockers),
            })

    if blocked_selected:
        st.error(
            "One or more selected clean-sync regions are not safe. "
            "Unselect them or use Recovery Mode where appropriate."
        )
        st.dataframe(blocked_selected, use_container_width=True)

    else:
        if st.button("✍️ Apply Clean Sync to Selected Regions"):
            total_changed = 0
            total_skipped = 0
            audit = []

            for region_name in selected_regions:
                r = region_by_name.get(region_name)

                if not r:
                    continue

                ready, blockers, num, alp = get_region_sync_plan(
                    r,
                    st.session_state.source_numeric,
                    st.session_state.source_alpha,
                    family["numeric_orientation"],
                    family["alpha_orientation"],
                    numeric_order,
                    alpha_order,
                    allow_block_text_write,
                    expected_reference_marker_count=len(st.session_state.arch_detection.get("trusted_markers", [])),
                    max_region_marker_ratio=max_region_marker_ratio,
                )

                if not ready:
                    audit.append({
                        "region": region_name,
                        "family": "region",
                        "changed": False,
                        "skipped": True,
                        "reason": "Region preflight failed: " + "; ".join(blockers),
                    })
                    continue

                ch1, sk1, au1 = apply_group_labels(
                    st.session_state.source_numeric,
                    num,
                    allow_block_text_write=allow_block_text_write,
                )

                ch2, sk2, au2 = apply_group_labels(
                    st.session_state.source_alpha,
                    alp,
                    allow_block_text_write=allow_block_text_write,
                )

                for row in au1:
                    row["region"] = region_name
                    row["family"] = "numeric"
                    row["sync_mode"] = "clean_sync"

                for row in au2:
                    row["region"] = region_name
                    row["family"] = "alphabetic"
                    row["sync_mode"] = "clean_sync"

                total_changed += ch1 + ch2
                total_skipped += sk1 + sk2
                audit.extend(au1 + au2)

            st.session_state.changed += total_changed
            st.session_state.skipped += total_skipped
            st.session_state.audit.extend(audit)

            if total_changed:
                st.success(f"Clean sync complete. Changed {total_changed} text entities.")
            else:
                st.info("Clean sync completed. No labels needed changing.")

            if total_skipped:
                st.warning(f"Clean sync skipped {total_skipped} text entities.")

    st.markdown("### 8B. Slab/Grid Endpoint Recovery Mode")

    st.caption(
        "Use this for slab/detail regions. It finds actual grid-line ends and searches only near those endpoints for plausible grid labels."
    )

    recovery_candidates = [
        row["region"]
        for row in st.session_state.region_report
        if not row.get("ready_to_sync")
    ]

    if recovery_candidates:
        selected_recovery_regions = st.multiselect(
            "Select blocked/review slab-detail regions for endpoint recovery",
            options=recovery_candidates,
            default=[],
            help="Select slab/detail regions that still need grid-label sync.",
        )

        recovery_preview = []
        recovery_blocked = []
        recovery_plan_rows_all = []

        for region_name in selected_recovery_regions:
            r = region_by_name.get(region_name)

            if not r:
                recovery_blocked.append({
                    "region": region_name,
                    "reason": "Region not found",
                })
                continue

            rec_ready, rec_blockers, rec_warnings, rec_plan_rows = build_endpoint_recovery_plan(
                r,
                st.session_state.source_numeric,
                st.session_state.source_alpha,
                family["numeric_orientation"],
                family["alpha_orientation"],
                numeric_order,
                alpha_order,
                st.session_state.struc_detection.get("lines", []),
                recovery_endpoint_radius,
                allow_block_text_write,
                numeric_extra=recovery_numeric_extra,
            )

            if not rec_ready:
                recovery_blocked.append({
                    "region": region_name,
                    "reason": "; ".join(rec_blockers),
                })

            if rec_plan_rows:
                recovery_plan_rows_all.extend(rec_plan_rows)
                recovery_preview.extend(recovery_preview_rows(rec_plan_rows))

        if recovery_blocked:
            st.warning(
                "Some selected recovery regions are not safe for endpoint recovery yet. "
                "Try increasing Recovery Endpoint Search Radius, or verify selected layers."
            )
            st.dataframe(recovery_blocked, use_container_width=True)

        if recovery_preview:
            st.write("#### Endpoint Recovery Preview")
            st.dataframe(recovery_preview, use_container_width=True)

        if selected_recovery_regions and recovery_plan_rows_all and not recovery_blocked:
            if st.button("🩹 Apply Endpoint Recovery Sync"):
                ch, sk, au = apply_endpoint_recovery_plan(recovery_plan_rows_all)

                st.session_state.changed += ch
                st.session_state.skipped += sk
                st.session_state.audit.extend(au)

                if ch:
                    st.success(f"Endpoint recovery complete. Changed {ch} grid-label text entities.")
                else:
                    st.info("Endpoint recovery completed. No labels needed changing.")

                if sk:
                    st.warning(f"Endpoint recovery skipped {sk} text entities.")

        elif selected_recovery_regions and not recovery_plan_rows_all:
            st.info(
                "No endpoint recovery candidates found. Try increasing the Recovery Endpoint Search Radius."
            )

    else:
        st.info("No blocked/review regions are available for recovery.")

    if st.session_state.audit:
        st.markdown("### 9. Audit Report")
        st.dataframe(st.session_state.audit, use_container_width=True)

        audit_csv = audit_to_csv_bytes(st.session_state.audit)

        st.download_button(
            "📄 Download Audit CSV",
            data=audit_csv,
            file_name=f"AUDIT_{st.session_state.struc_name}.csv",
            mime="text/csv",
        )

    st.markdown("### 10. Download")

    data = write_doc_to_temp_bytes(st.session_state.struc_doc)

    st.download_button(
        "📥 Download Target DXF",
        data=data,
        file_name=f"RELABELED_{st.session_state.struc_name}",
        mime="application/dxf",
    )
