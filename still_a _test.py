import csv
import io
import math
import os
import re
import tempfile

import ezdxf
import streamlit as st


# =========================================================
# APP CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - Smart Grid Label Sync",
    page_icon=":building_construction:",
    layout="wide",
)

st.title("iLoveStructural")
st.subheader("Tool 2: Smart Grid Label Sync")
st.caption(
    "Upload reference/target DXFs, detect grid bubbles, map by geometry, handle omissions/subdivisions, review, then apply."
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
            if k in ("entity", "marker", "group"):
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
    txt = txt.replace("’", "'")
    txt = txt.replace("″", '"')
    txt = txt.replace("“", '"')
    txt = txt.replace("”", '"')
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()


def probable_grid_label(text):
    text = clean_text(text)

    patterns = [
        r"[A-Z]{1,3}(?:'{1,6}|\"{1,3})?",
        r"\d{1,3}[A-Z]{0,3}(?:'{1,6}|\"{1,3})?",
    ]

    return any(re.fullmatch(p, text) for p in patterns)


def is_numeric_label(text):
    return bool(re.fullmatch(r"\d{1,3}[A-Z]{0,3}(?:'{1,6}|\"{1,3})?", clean_text(text)))


def is_alpha_label(text):
    return bool(re.fullmatch(r"[A-Z]{1,3}(?:'{1,6}|\"{1,3})?", clean_text(text)))


def numeric_label_value(text):
    txt = clean_text(text)
    m = re.fullmatch(r"(\d{1,3})([A-Z]{0,3})(?:'{1,6}|\"{1,3})?", txt)

    if not m:
        return None

    return int(m.group(1))


def number_to_letters(n):
    n = int(n)
    out = ""

    while n > 0:
        n -= 1
        out = chr(65 + (n % 26)) + out
        n //= 26

    return out


def numeric_subdivision_label(base_label, step):
    if step <= 0:
        return clean_text(base_label)

    return f"{clean_text(base_label)}{number_to_letters(step)}"


def alpha_subdivision_label(base_label, step):
    base_label = clean_text(base_label)

    if step <= 0:
        return base_label

    if step == 1:
        return f"{base_label}'"

    if step == 2:
        return f'{base_label}"'

    return f"{base_label}" + ("'" * step)


def smart_subdivision_label(start_label, end_label, step, span, family_name):
    start_label = clean_text(start_label)
    end_label = clean_text(end_label)

    if step <= 0:
        return start_label

    if step >= span:
        return end_label

    if family_name == "numeric":
        return numeric_subdivision_label(start_label, step)

    return alpha_subdivision_label(start_label, step)


def axis_distance_for_marker(marker, orientation, coord):
    x, y = marker["circle_center"]

    if orientation == "vertical":
        return abs(float(x) - float(coord))

    if orientation == "horizontal":
        return abs(float(y) - float(coord))

    return 999999999.0


# =========================================================
# GEOMETRY HELPERS
# =========================================================

def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))


def squared_distance(p1, p2):
    return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2


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


# =========================================================
# DXF HELPERS
# =========================================================

WRITE_MODE_SAFE = "Safe mode: modelspace TEXT/MTEXT only"
WRITE_MODE_ATTRIB = "Attribute mode: modelspace TEXT/MTEXT + INSERT attributes"
WRITE_MODE_DANGEROUS = "Advanced/Dangerous: also edit block definition TEXT/MTEXT"


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


def norm_layer_name(value):
    return clean_text(value)


def layer_matches(entity_layer, selected_layer):
    return norm_layer_name(entity_layer) == norm_layer_name(selected_layer)


def nested_entity_layer_allowed(
    nested_layer,
    selected_layer,
    parent_layer=None,
    allow_layer0_from_selected_insert=True,
    strict_nested_block_layer_match=False,
):
    nested = norm_layer_name(nested_layer)
    selected = norm_layer_name(selected_layer)
    parent = norm_layer_name(parent_layer)

    if strict_nested_block_layer_match:
        return nested == selected

    if nested == selected:
        return True

    if allow_layer0_from_selected_insert and nested == "0" and parent == selected:
        return True

    return False


def attrib_layer_allowed_for_text(
    attrib_layer,
    selected_text_layer,
    parent_insert_layer=None,
    allow_layer0_from_selected_insert=True,
    strict_nested_block_layer_match=False,
):
    attrib = norm_layer_name(attrib_layer)
    selected = norm_layer_name(selected_text_layer)
    parent = norm_layer_name(parent_insert_layer)

    if strict_nested_block_layer_match:
        return attrib == selected

    if attrib == selected:
        return True

    if parent == selected:
        return True

    if allow_layer0_from_selected_insert and attrib == "0" and parent == selected:
        return True

    return False


def marker_write_profile(marker, write_mode=None, allow_block_text_write=False):
    if write_mode is None:
        write_mode = WRITE_MODE_DANGEROUS if allow_block_text_write else WRITE_MODE_ATTRIB

    src = marker.get("text_source", "")
    typ = marker.get("text_type", "")

    if src == "modelspace" and typ in ("TEXT", "MTEXT"):
        return {
            "writable": True,
            "write_source": src,
            "write_risk": "low",
            "write_reason": "modelspace_text",
        }

    if src in ("modelspace", "insert_attrib") and typ == "ATTRIB":
        if write_mode in (WRITE_MODE_ATTRIB, WRITE_MODE_DANGEROUS):
            return {
                "writable": True,
                "write_source": src,
                "write_risk": "medium",
                "write_reason": "attribute_instance",
            }

        return {
            "writable": False,
            "write_source": src,
            "write_risk": "blocked",
            "write_reason": "attribute_blocked_in_safe_mode",
        }

    if src == "block_text":
        if write_mode == WRITE_MODE_DANGEROUS or allow_block_text_write:
            return {
                "writable": True,
                "write_source": src,
                "write_risk": "high",
                "write_reason": "block_definition_text",
            }

        return {
            "writable": False,
            "write_source": src,
            "write_risk": "blocked",
            "write_reason": "block_definition_text_blocked",
        }

    return {
        "writable": False,
        "write_source": src,
        "write_risk": "blocked",
        "write_reason": "unknown_text_source",
    }


def is_marker_writable(marker, allow_block_text_write=False, write_mode=None):
    return marker_write_profile(
        marker,
        write_mode=write_mode,
        allow_block_text_write=allow_block_text_write,
    )["writable"]


def marker_layer(marker):
    return marker.get("text_layer") or marker.get("layer") or ""


def marker_entity_handle(marker):
    entity = marker.get("text_entity")
    return marker.get("text_handle") or get_entity_handle(entity)


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

def extract_texts(
    doc,
    layer_name,
    allow_nested_layer0_from_selected_insert=True,
    strict_nested_block_layer_match=False,
):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
                if not layer_matches(e.dxf.layer, layer_name):
                    continue

                texts.append({
                    "entity": e,
                    "text": get_text_value(e),
                    "point": get_text_point(e),
                    "layer": e.dxf.layer,
                    "type": e.dxftype(),
                    "source": "modelspace",
                    "parent_insert": None,
                    "parent_layer": "",
                    "handle": get_entity_handle(e),
                })

            elif e.dxftype() == "INSERT":
                parent_layer = e.dxf.layer

                try:
                    for att in e.attribs:
                        if not attrib_layer_allowed_for_text(
                            att.dxf.layer,
                            layer_name,
                            parent_insert_layer=parent_layer,
                            allow_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
                            strict_nested_block_layer_match=strict_nested_block_layer_match,
                        ):
                            continue

                        texts.append({
                            "entity": att,
                            "text": get_text_value(att),
                            "point": get_text_point(att),
                            "layer": att.dxf.layer,
                            "type": "ATTRIB",
                            "source": "insert_attrib",
                            "parent_insert": e,
                            "parent_layer": parent_layer,
                            "handle": get_entity_handle(att),
                        })
                except Exception:
                    pass

                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]

                        for be in block:
                            if be.dxftype() not in ("TEXT", "MTEXT"):
                                continue

                            if not nested_entity_layer_allowed(
                                be.dxf.layer,
                                layer_name,
                                parent_layer=parent_layer,
                                allow_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
                                strict_nested_block_layer_match=strict_nested_block_layer_match,
                            ):
                                continue

                            lp = get_text_point(be)
                            wp = transform_block_point(lp, e)

                            texts.append({
                                "entity": be,
                                "text": get_text_value(be),
                                "point": wp,
                                "layer": be.dxf.layer,
                                "type": be.dxftype(),
                                "source": "block_text",
                                "parent_insert": e,
                                "parent_layer": parent_layer,
                                "handle": get_entity_handle(be),
                            })
                except Exception:
                    pass

        except Exception:
            continue

    return texts


def extract_circles(
    doc,
    layer_name,
    allow_nested_layer0_from_selected_insert=True,
    strict_nested_block_layer_match=False,
):
    circles = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "CIRCLE":
                if not layer_matches(e.dxf.layer, layer_name):
                    continue

                c = e.dxf.center
                circles.append({
                    "entity": e,
                    "center": (float(c.x), float(c.y)),
                    "radius": float(e.dxf.radius),
                    "layer": e.dxf.layer,
                    "source": "modelspace",
                    "parent_insert": None,
                    "parent_layer": "",
                    "handle": get_entity_handle(e),
                })

            elif e.dxftype() == "INSERT":
                parent_layer = e.dxf.layer

                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]

                        for be in block:
                            if be.dxftype() != "CIRCLE":
                                continue

                            if not nested_entity_layer_allowed(
                                be.dxf.layer,
                                layer_name,
                                parent_layer=parent_layer,
                                allow_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
                                strict_nested_block_layer_match=strict_nested_block_layer_match,
                            ):
                                continue

                            c = be.dxf.center
                            circles.append({
                                "entity": e,
                                "nested_entity": be,
                                "center": transform_block_point((float(c.x), float(c.y)), e),
                                "radius": transform_block_radius(float(be.dxf.radius), e),
                                "layer": be.dxf.layer,
                                "source": "block_circle",
                                "parent_insert": e,
                                "parent_layer": parent_layer,
                                "handle": get_entity_handle(e),
                                "nested_handle": get_entity_handle(be),
                            })
                except Exception:
                    pass

        except Exception:
            continue

    return circles


def add_axis_segment(
    lines,
    entity,
    x1,
    y1,
    x2,
    y2,
    layer_name,
    min_length,
    source="modelspace",
    parent_insert=None,
):
    length = math.dist((x1, y1), (x2, y2))

    if length < min_length:
        return

    base = {
        "entity": entity,
        "length": round(float(length), 3),
        "layer": layer_name,
        "handle": get_entity_handle(entity),
        "source": source,
        "parent_insert": parent_insert,
        "parent_insert_handle": get_entity_handle(parent_insert) if parent_insert is not None else "",
    }

    if is_vertical(x1, y1, x2, y2):
        row = dict(base)
        row.update({
            "orientation": "vertical",
            "coord": round(float((x1 + x2) / 2.0), 3),
            "start": (float(x1), float(y1)),
            "end": (float(x2), float(y2)),
        })
        lines.append(row)

    elif is_horizontal(x1, y1, x2, y2):
        row = dict(base)
        row.update({
            "orientation": "horizontal",
            "coord": round(float((y1 + y2) / 2.0), 3),
            "start": (float(x1), float(y1)),
            "end": (float(x2), float(y2)),
        })
        lines.append(row)


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


def add_polyline_segments_from_points(lines, entity, pts, layer_name, min_length, source, parent_insert=None):
    for i in range(len(pts) - 1):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])

        add_axis_segment(
            lines,
            entity,
            x1,
            y1,
            x2,
            y2,
            layer_name,
            min_length,
            source=source,
            parent_insert=parent_insert,
        )


def extract_axis_lines(
    doc,
    layer_name,
    min_length=1000.0,
    allow_nested_layer0_from_selected_insert=True,
    strict_nested_block_layer_match=False,
):
    lines = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "LINE":
                if not layer_matches(e.dxf.layer, layer_name):
                    continue

                x1, y1, _ = e.dxf.start
                x2, y2, _ = e.dxf.end

                add_axis_segment(lines, e, x1, y1, x2, y2, e.dxf.layer, min_length)

            elif e.dxftype() == "LWPOLYLINE":
                if not layer_matches(e.dxf.layer, layer_name):
                    continue

                add_polyline_segments_from_points(
                    lines,
                    e,
                    list(e.get_points()),
                    e.dxf.layer,
                    min_length,
                    source="modelspace",
                )

            elif e.dxftype() == "POLYLINE":
                if not layer_matches(e.dxf.layer, layer_name):
                    continue

                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in e.vertices]
                add_polyline_segments_from_points(
                    lines,
                    e,
                    pts,
                    e.dxf.layer,
                    min_length,
                    source="modelspace",
                )

            elif e.dxftype() == "INSERT":
                parent_layer = e.dxf.layer

                try:
                    if e.dxf.name not in doc.blocks:
                        continue

                    block = doc.blocks[e.dxf.name]

                    for be in block:
                        if be.dxftype() not in ("LINE", "LWPOLYLINE", "POLYLINE"):
                            continue

                        if not nested_entity_layer_allowed(
                            be.dxf.layer,
                            layer_name,
                            parent_layer=parent_layer,
                            allow_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
                            strict_nested_block_layer_match=strict_nested_block_layer_match,
                        ):
                            continue

                        if be.dxftype() == "LINE":
                            x1, y1, _ = be.dxf.start
                            x2, y2, _ = be.dxf.end
                            p1 = transform_block_point((float(x1), float(y1)), e)
                            p2 = transform_block_point((float(x2), float(y2)), e)
                            add_axis_segment(
                                lines,
                                be,
                                p1[0],
                                p1[1],
                                p2[0],
                                p2[1],
                                be.dxf.layer,
                                min_length,
                                source="block_line",
                                parent_insert=e,
                            )

                        elif be.dxftype() == "LWPOLYLINE":
                            raw_pts = list(be.get_points())
                            pts = [transform_block_point((float(p[0]), float(p[1])), e) for p in raw_pts]
                            add_polyline_segments_from_points(
                                lines,
                                be,
                                pts,
                                be.dxf.layer,
                                min_length,
                                source="block_polyline",
                                parent_insert=e,
                            )

                        elif be.dxftype() == "POLYLINE":
                            raw_pts = [
                                (float(v.dxf.location.x), float(v.dxf.location.y))
                                for v in be.vertices
                            ]
                            pts = [transform_block_point(p, e) for p in raw_pts]
                            add_polyline_segments_from_points(
                                lines,
                                be,
                                pts,
                                be.dxf.layer,
                                min_length,
                                source="block_polyline",
                                parent_insert=e,
                            )
                except Exception:
                    pass

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

        if not point_projects_on_segment(cx, cy, x1, y1, x2, y2, pad=radius + attach_gap):
            return False

    elif line["orientation"] == "horizontal":
        if abs(line["coord"] - cy) > attach_gap:
            return False

        if not point_projects_on_segment(cx, cy, x1, y1, x2, y2, pad=radius + attach_gap):
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
    write_mode=None,
    allow_nested_layer0_from_selected_insert=True,
    strict_nested_block_layer_match=False,
):
    texts = extract_texts(
        doc,
        text_layer,
        allow_nested_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
        strict_nested_block_layer_match=strict_nested_block_layer_match,
    )

    circles = extract_circles(
        doc,
        circle_layer,
        allow_nested_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
        strict_nested_block_layer_match=strict_nested_block_layer_match,
    )

    lines = extract_axis_lines(
        doc,
        line_layer,
        min_length=min_grid_length,
        allow_nested_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
        strict_nested_block_layer_match=strict_nested_block_layer_match,
    )

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
                "circle_layer": c.get("layer", ""),
                "circle_source": c.get("source", ""),
            })
            continue

        candidates = sorted(candidates, key=lambda t: euclidean(t["point"], center))
        t = candidates[0]

        bubbles.append({
            "label": clean_text(t["text"]),
            "text_entity": t["entity"],
            "text_point": t["point"],
            "text_source": t["source"],
            "text_type": t.get("type", ""),
            "text_layer": t.get("layer", ""),
            "text_parent_layer": t.get("parent_layer", ""),
            "text_handle": t.get("handle", ""),
            "parent_insert": t.get("parent_insert"),
            "circle_entity": c["entity"],
            "circle_center": center,
            "circle_radius": radius,
            "circle_source": c["source"],
            "circle_layer": c.get("layer", ""),
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
                "text_layer": b.get("text_layer", ""),
                "text_source": b.get("text_source", ""),
                "circle_layer": b.get("circle_layer", ""),
                "circle_source": b.get("circle_source", ""),
            })
            continue

        marker = {
            "label": b["label"],
            "text_entity": b["text_entity"],
            "text_point": b["text_point"],
            "text_source": b["text_source"],
            "text_type": b.get("text_type", ""),
            "text_layer": b.get("text_layer", ""),
            "text_parent_layer": b.get("text_parent_layer", ""),
            "text_handle": b.get("text_handle", ""),
            "parent_insert": b.get("parent_insert"),
            "circle_entity": b["circle_entity"],
            "circle_center": b["circle_center"],
            "circle_radius": b["circle_radius"],
            "circle_source": b["circle_source"],
            "circle_layer": b.get("circle_layer", ""),
            "circle_handle": b.get("circle_handle", ""),
            "line_entity": selected.get("entity"),
            "orientation": selected["orientation"],
            "coord": selected["coord"],
            "line_start": selected["start"],
            "line_end": selected["end"],
            "line_length": selected["length"],
            "line_layer": selected.get("layer", ""),
            "line_source": selected.get("source", ""),
            "detection_mode": mode,
            "candidate_texts": b.get("candidate_texts", []),
            "text_matches": b.get("text_matches", 1),
        }

        profile = marker_write_profile(
            marker,
            write_mode=write_mode,
            allow_block_text_write=allow_block_text_write,
        )

        marker["writable"] = profile["writable"]
        marker["write_source"] = profile["write_source"]
        marker["write_risk"] = profile["write_risk"]
        marker["write_reason"] = profile["write_reason"]
        marker["confidence"] = marker_confidence(mode, marker["text_matches"], marker["writable"])

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
        "avg_confidence": round(sum([m.get("confidence", 0) for m in group]) / len(group), 1)
        if group
        else 0,
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
        reverse = family == "numeric" and orientation == "horizontal"

    return sorted(groups, key=lambda x: x["coord"], reverse=reverse)


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
# REGION SEGMENTATION / FILTER
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
        m
        for m in region["markers"]
        if marker_is_near_region_perimeter(m, bbox, band_ratio=band_ratio, min_band=min_band)
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
    first = min(range(len(points)), key=lambda i: (points[i][0] + points[i][1], points[i][0]))
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
# SMART OMISSION / PROXIMITY MAPPING
# =========================================================

def axis_position_ratios(groups):
    if not groups:
        return []

    if len(groups) == 1:
        return [0.0]

    first = float(groups[0].get("coord", 0.0))
    last = float(groups[-1].get("coord", 0.0))
    denom = last - first

    if abs(denom) < 1e-9:
        return [i / (len(groups) - 1) for i in range(len(groups))]

    return [(float(g.get("coord", 0.0)) - first) / denom for g in groups]


def smart_anchor_pairs(source_groups, target_groups, anchor_tolerance_ratio=0.14, force_endpoints=True):
    source_count = len(source_groups)
    target_count = len(target_groups)

    if source_count == 0 or target_count == 0:
        return [], list(range(source_count)), list(range(target_count))

    if source_count == 1:
        target_ratios = axis_position_ratios(target_groups)
        nearest = min(range(target_count), key=lambda i: abs(target_ratios[i]))
        skipped_targets = [i for i in range(target_count) if i != nearest]
        return [(0, nearest)], [], skipped_targets

    if target_count == 1:
        source_ratios = axis_position_ratios(source_groups)
        nearest = min(range(source_count), key=lambda i: abs(source_ratios[i]))
        omitted = [i for i in range(source_count) if i != nearest]
        return [(nearest, 0)], omitted, []

    source_ratios = axis_position_ratios(source_groups)
    target_ratios = axis_position_ratios(target_groups)

    fixed_pairs = []

    if force_endpoints:
        fixed_pairs = [(0, 0), (source_count - 1, target_count - 1)]
        source_indices = list(range(1, source_count - 1))
        target_indices = list(range(1, target_count - 1))
    else:
        source_indices = list(range(source_count))
        target_indices = list(range(target_count))

    ns = len(source_indices)
    nt = len(target_indices)

    if ns == 0:
        return sorted(fixed_pairs), [], target_indices

    if nt == 0:
        return sorted(fixed_pairs), source_indices[:], []

    source_omit_penalty = anchor_tolerance_ratio * 1.25
    target_extra_penalty = 0.015
    max_match_distance = anchor_tolerance_ratio

    dp = [[float("inf")] * (nt + 1) for _ in range(ns + 1)]
    parent = [[None] * (nt + 1) for _ in range(ns + 1)]
    dp[0][0] = 0.0

    for i in range(ns + 1):
        for j in range(nt + 1):
            current = dp[i][j]

            if current == float("inf"):
                continue

            if i < ns:
                cost = current + source_omit_penalty
                if cost < dp[i + 1][j]:
                    dp[i + 1][j] = cost
                    parent[i + 1][j] = (i, j, "omit_source")

            if j < nt:
                cost = current + target_extra_penalty
                if cost < dp[i][j + 1]:
                    dp[i][j + 1] = cost
                    parent[i][j + 1] = (i, j, "skip_target")

            if i < ns and j < nt:
                si = source_indices[i]
                tj = target_indices[j]
                dist = abs(source_ratios[si] - target_ratios[tj])

                if dist <= max_match_distance:
                    cost = current + dist
                    if cost < dp[i + 1][j + 1]:
                        dp[i + 1][j + 1] = cost
                        parent[i + 1][j + 1] = (i, j, "match")

    pairs = fixed_pairs[:]
    omitted = []
    skipped = []
    i = ns
    j = nt

    while i > 0 or j > 0:
        step = parent[i][j]

        if step is None:
            break

        pi, pj, action = step

        if action == "match":
            pairs.append((source_indices[pi], target_indices[pj]))
        elif action == "omit_source":
            omitted.append(source_indices[pi])
        elif action == "skip_target":
            skipped.append(target_indices[pj])

        i, j = pi, pj

    pairs = sorted(set(pairs), key=lambda x: (x[0], x[1]))
    omitted = sorted(set(omitted))
    skipped = sorted(set(skipped))

    return pairs, omitted, skipped


def build_smart_axis_label_map(source_groups, target_groups, family_name):
    blockers = []
    warnings = []

    source_count = len(source_groups)
    target_count = len(target_groups)

    if source_count == 0:
        blockers.append(f"No source {family_name} axes were detected.")
        return [], blockers, warnings

    if target_count == 0:
        blockers.append(f"No target {family_name} axes were detected.")
        return [], blockers, warnings

    pairs, omitted_source_indexes, skipped_target_indexes = smart_anchor_pairs(
        source_groups,
        target_groups,
        anchor_tolerance_ratio=0.14,
        force_endpoints=True,
    )

    if len(pairs) < 2 and source_count > 1 and target_count > 1:
        blockers.append(
            f"Only {len(pairs)} reliable {family_name} geometry anchor was found. "
            "At least 2 anchors are needed for smart sync."
        )
        return [], blockers, warnings

    if target_count != source_count:
        warnings.append(
            f"{family_name.title()} reference has {source_count} axes and target has {target_count}. "
            "Smart proximity mapping with omissions/subdivisions will be used."
        )

    for source_index in omitted_source_indexes:
        label = clean_text(source_groups[source_index].get("label", ""))
        warnings.append(
            f"Reference {family_name} label {label} has no nearby target axis and will be treated as omitted."
        )

    rows_by_target_index = {}

    if len(pairs) == 1:
        source_index, target_index = pairs[0]
        base_label = clean_text(source_groups[source_index].get("label", ""))

        for i, target_group in enumerate(target_groups):
            offset = i - target_index

            if offset <= 0:
                new_label = base_label
            elif family_name == "numeric":
                new_label = numeric_subdivision_label(base_label, offset)
            else:
                new_label = alpha_subdivision_label(base_label, offset)

            rows_by_target_index[i] = {
                "target_group": target_group,
                "target_index": i + 1,
                "source_start_label": base_label,
                "source_end_label": "",
                "new_label": new_label,
                "subdivision_step": max(offset, 0),
                "subdivision_span": target_count - 1,
                "omitted_source_labels": "",
                "mapping_note": "single_anchor_fallback",
            }

        return [rows_by_target_index[i] for i in sorted(rows_by_target_index)], blockers, warnings

    for pair_index in range(len(pairs) - 1):
        source_start_index, target_start_index = pairs[pair_index]
        source_end_index, target_end_index = pairs[pair_index + 1]

        if target_end_index < target_start_index:
            continue

        span = max(1, target_end_index - target_start_index)
        start_label = clean_text(source_groups[source_start_index].get("label", ""))
        end_label = clean_text(source_groups[source_end_index].get("label", ""))

        omitted_between = [
            clean_text(source_groups[i].get("label", ""))
            for i in omitted_source_indexes
            if source_start_index < i < source_end_index
        ]

        for target_index in range(target_start_index, target_end_index + 1):
            step = target_index - target_start_index
            target_group = target_groups[target_index]

            new_label = smart_subdivision_label(
                start_label,
                end_label,
                step,
                span,
                family_name,
            )

            rows_by_target_index[target_index] = {
                "target_group": target_group,
                "target_index": target_index + 1,
                "source_start_label": start_label,
                "source_end_label": end_label,
                "new_label": new_label,
                "subdivision_step": step,
                "subdivision_span": span,
                "omitted_source_labels": ", ".join([x for x in omitted_between if x]),
                "mapping_note": "proximity_anchor" if not omitted_between else "omission_between_anchors",
            }

    # Extra target axes outside the selected anchor spans are still shown so the user can uncheck or edit them.
    for target_index in skipped_target_indexes:
        if target_index in rows_by_target_index:
            continue

        left_pair = None
        right_pair = None

        for pair in pairs:
            if pair[1] < target_index:
                left_pair = pair
            if pair[1] > target_index and right_pair is None:
                right_pair = pair

        if left_pair and right_pair:
            source_start_index, target_start_index = left_pair
            source_end_index, target_end_index = right_pair
            span = max(1, target_end_index - target_start_index)
            step = target_index - target_start_index
            start_label = clean_text(source_groups[source_start_index].get("label", ""))
            end_label = clean_text(source_groups[source_end_index].get("label", ""))
            new_label = smart_subdivision_label(start_label, end_label, step, span, family_name)
            source_end_label = end_label
        elif left_pair:
            source_start_index, target_start_index = left_pair
            start_label = clean_text(source_groups[source_start_index].get("label", ""))
            step = target_index - target_start_index
            new_label = numeric_subdivision_label(start_label, step) if family_name == "numeric" else alpha_subdivision_label(start_label, step)
            source_end_label = ""
            span = step
        elif right_pair:
            source_end_index, target_end_index = right_pair
            start_label = clean_text(source_groups[source_end_index].get("label", ""))
            step = 0
            span = 0
            new_label = start_label
            source_end_label = ""
        else:
            start_label = clean_text(source_groups[0].get("label", ""))
            source_end_label = ""
            step = target_index
            span = target_count - 1
            new_label = numeric_subdivision_label(start_label, step) if family_name == "numeric" else alpha_subdivision_label(start_label, step)

        rows_by_target_index[target_index] = {
            "target_group": target_groups[target_index],
            "target_index": target_index + 1,
            "source_start_label": start_label,
            "source_end_label": source_end_label,
            "new_label": new_label,
            "subdivision_step": step,
            "subdivision_span": span,
            "omitted_source_labels": "",
            "mapping_note": "extra_target_between_anchors",
        }

    return [rows_by_target_index[i] for i in sorted(rows_by_target_index)], blockers, warnings


# =========================================================
# SYNC PLAN / PREVIEW / APPLY
# =========================================================

def validate_axis_group_notes(groups, family_name):
    notes = []

    for idx, g in enumerate(groups, start=1):
        label_count = g.get("label_count", 1)
        mixed_labels = g.get("mixed_labels", [])
        label_counts = g.get("label_counts", {})
        marker_count = g.get("marker_count", 0)

        if label_count > 1:
            notes.append(
                f"{family_name} axis position {idx} at coord {g.get('coord')} has mixed labels "
                f"{mixed_labels}. Counts={label_counts}. Marker count={marker_count}."
            )

    return notes


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
    write_mode=None,
):
    numeric_groups, alpha_groups = get_family_groups(
        region,
        numeric_orientation,
        alpha_orientation,
        numeric_order,
        alpha_order,
    )

    blockers = []
    warnings = []

    numeric_map, numeric_blockers, numeric_warnings = build_smart_axis_label_map(
        source_numeric,
        numeric_groups,
        "numeric",
    )
    alpha_map, alpha_blockers, alpha_warnings = build_smart_axis_label_map(
        source_alpha,
        alpha_groups,
        "alphabetic",
    )

    blockers.extend(numeric_blockers)
    blockers.extend(alpha_blockers)
    warnings.extend(numeric_warnings)
    warnings.extend(alpha_warnings)
    warnings.extend(validate_axis_group_notes(numeric_groups, "Numeric"))
    warnings.extend(validate_axis_group_notes(alpha_groups, "Alphabetic"))

    selected_markers = []

    for item in numeric_map + alpha_map:
        selected_markers.extend(item["target_group"].get("markers", []))

    non_writable_markers = [
        m
        for m in selected_markers
        if not is_marker_writable(m, allow_block_text_write, write_mode=write_mode)
    ]

    if non_writable_markers:
        blockers.append(
            f"{len(non_writable_markers)} selected marker text entities are not writable in the selected write mode"
        )

    ready = len(blockers) == 0
    return ready, blockers, numeric_groups, alpha_groups, warnings


def make_clean_approval_id(region_name, family_name, position, group):
    return f"{region_name}|clean_sync|{family_name}|{position}|{group.get('orientation')}|{group.get('coord')}"


def safe_same_axis_tolerance(target_group, target_groups, base_tol=80.0):
    coord = float(target_group.get("coord", 0.0))
    orientation = target_group.get("orientation", "")

    neighbor_gaps = [
        abs(coord - float(g.get("coord", 0.0)))
        for g in target_groups
        if g is not target_group and g.get("orientation") == orientation
    ]

    if not neighbor_gaps:
        return base_tol

    nearest_gap = min([g for g in neighbor_gaps if g > 0] or [base_tol])
    return max(5.0, min(base_tol, nearest_gap * 0.45))


def build_clean_plan_for_region(region, source_numeric, source_alpha, numeric_groups, alpha_groups):
    rows = []

    family_sets = [
        ("numeric", source_numeric, numeric_groups),
        ("alphabetic", source_alpha, alpha_groups),
    ]

    for family_name, source_groups, target_groups in family_sets:
        label_map, blockers, warnings = build_smart_axis_label_map(
            source_groups,
            target_groups,
            family_name,
        )

        for item in label_map:
            t = item["target_group"]
            new_label = clean_text(item["new_label"])
            position = item["target_index"]

            rows.append({
                "approval_id": make_clean_approval_id(region["name"], family_name, position, t),
                "apply": True,
                "region": region["name"],
                "sync_mode": "clean_sync",
                "family": family_name,
                "axis_position": position,
                "source_label": item["source_start_label"],
                "source_end_label": item["source_end_label"],
                "target_old_label": t["label"],
                "proposed_new_label": new_label,
                "new_label": new_label,
                "subdivision_step": item["subdivision_step"],
                "subdivision_span": item["subdivision_span"],
                "omitted_source_labels": item.get("omitted_source_labels", ""),
                "mapping_note": item.get("mapping_note", ""),
                "target_axis_coord": t["coord"],
                "target_markers": t["marker_count"],
                "target_writable_markers": t["writable_marker_count"],
                "target_avg_confidence": t["avg_confidence"],
                "target_mixed_labels": ", ".join(t.get("mixed_labels", [])),
                "write_risks": ", ".join(sorted(set(m.get("write_risk", "") for m in t.get("markers", [])))),
                "group": t,
                "same_axis_marker_pool": region.get("all_markers", region.get("markers", [])),
                "same_axis_update_tolerance": safe_same_axis_tolerance(t, target_groups),
            })

    return rows


def clean_preview_rows(plan_rows):
    rows = []

    for r in plan_rows:
        row = dict(r)
        row["same_axis_marker_count"] = len(same_axis_markers_for_plan(r))
        row.pop("group", None)
        row.pop("same_axis_marker_pool", None)
        rows.append(row)

    return rows


def same_axis_markers_for_plan(plan):
    group = plan.get("group") or {}
    pool = plan.get("same_axis_marker_pool") or group.get("markers", [])
    orientation = group.get("orientation", "")
    coord = group.get("coord", "")
    tolerance = float(plan.get("same_axis_update_tolerance", 0.0) or 0.0)

    if coord == "":
        return group.get("markers", [])

    selected = []
    seen = set()

    def add_marker(marker):
        entity = marker.get("text_entity")
        handle = marker_entity_handle(marker)
        key = handle or f"entity_{id(entity)}"

        if key in seen:
            return

        seen.add(key)
        selected.append(marker)

    for marker in group.get("markers", []):
        add_marker(marker)

    for marker in pool:
        if marker.get("orientation") != orientation:
            continue

        if axis_distance_for_marker(marker, orientation, coord) > tolerance:
            continue

        if not probable_grid_label(marker.get("label", "")):
            continue

        add_marker(marker)

    return selected


def editor_result_to_rows(editor_result):
    if editor_result is None:
        return []

    if isinstance(editor_result, list):
        return editor_result

    try:
        return editor_result.to_dict("records")
    except Exception:
        pass

    try:
        return list(editor_result)
    except Exception:
        return []


def checkbox_truthy(value):
    if value is True:
        return True

    if value is False or value is None:
        return False

    return str(value).strip().lower() in ("true", "1", "yes", "y", "checked")


def clean_plan_from_editor(plan_rows, editor_result):
    editor_rows = editor_result_to_rows(editor_result)
    editor_by_id = {}

    for row in editor_rows:
        approval_id = row.get("approval_id", "")
        if approval_id:
            editor_by_id[approval_id] = row

    approved = []
    unapproved = []
    invalid = []

    for original in plan_rows:
        approval_id = original.get("approval_id", "")
        edited = editor_by_id.get(approval_id, {})
        apply_row = checkbox_truthy(edited.get("apply", False))
        row_copy = dict(original)
        edited_new_label = clean_text(edited.get("new_label", original.get("new_label", "")))

        if not apply_row:
            unapproved.append(row_copy)
            continue

        if not edited_new_label:
            bad = dict(row_copy)
            bad["invalid_reason"] = "Edited new_label is blank"
            invalid.append(bad)
            continue

        if not probable_grid_label(edited_new_label):
            bad = dict(row_copy)
            bad["invalid_reason"] = f"Edited new_label '{edited_new_label}' is not a probable grid label"
            invalid.append(bad)
            continue

        row_copy["user_edited_new_label"] = edited_new_label
        row_copy["original_proposed_new_label"] = row_copy.get("new_label", "")
        row_copy["new_label"] = edited_new_label
        approved.append(row_copy)

    return approved, unapproved, invalid


def clean_plan_apply_counts(plan_rows):
    approved_axis_rows = len(plan_rows)
    will_change = 0
    already_match = 0
    high_risk = 0
    unwritable = 0

    for r in plan_rows:
        new = clean_text(r.get("new_label", ""))

        for marker in same_axis_markers_for_plan(r):
            old = get_text_value(marker["text_entity"])

            if old != new:
                will_change += 1
            else:
                already_match += 1

            if marker.get("write_risk") == "high":
                high_risk += 1

            if not marker.get("writable"):
                unwritable += 1

    return {
        "approved_axis_rows": approved_axis_rows,
        "will_change_text_entities": will_change,
        "already_match_text_entities": already_match,
        "high_risk_text_entities": high_risk,
        "unwritable_text_entities": unwritable,
    }


def apply_clean_plan_rows(plan_rows, allow_block_text_write=False, write_mode=None):
    changed = 0
    skipped = 0
    audit = []

    for plan in plan_rows:
        group = plan.get("group") or {}
        new_label = clean_text(plan.get("new_label", ""))

        for marker in same_axis_markers_for_plan(plan):
            entity = marker["text_entity"]
            old = get_text_value(entity)
            profile = marker_write_profile(
                marker,
                write_mode=write_mode,
                allow_block_text_write=allow_block_text_write,
            )

            base = {
                "region": plan.get("region", ""),
                "sync_mode": "clean_sync",
                "approved_by_user": True,
                "family": plan.get("family", ""),
                "axis_position": plan.get("axis_position", ""),
                "axis_position_coord": group.get("coord", ""),
                "old_label": old,
                "new_label": new_label,
                "original_proposed_new_label": plan.get("original_proposed_new_label", plan.get("proposed_new_label", "")),
                "user_edited_new_label": plan.get("user_edited_new_label", new_label),
                "source_label": plan.get("source_label", ""),
                "source_end_label": plan.get("source_end_label", ""),
                "omitted_source_labels": plan.get("omitted_source_labels", ""),
                "mapping_note": plan.get("mapping_note", ""),
                "text_source": marker.get("text_source"),
                "write_source": profile.get("write_source", ""),
                "write_risk": profile.get("write_risk", ""),
                "write_reason": profile.get("write_reason", ""),
                "detection_mode": marker.get("detection_mode"),
                "confidence": marker.get("confidence"),
                "entity_handle": marker_entity_handle(marker),
                "layer": marker_layer(marker),
            }

            if not profile["writable"]:
                skipped += 1
                row = dict(base)
                row.update({
                    "changed": False,
                    "skipped": True,
                    "reason": f"Not writable source={marker.get('text_source')}",
                })
                audit.append(row)
                continue

            if old != new_label:
                ok = set_text_value(entity, new_label)

                if ok:
                    changed += 1

                row = dict(base)
                row.update({
                    "changed": ok,
                    "skipped": False,
                    "reason": "updated" if ok else "write_failed",
                })
                audit.append(row)
            else:
                row = dict(base)
                row.update({
                    "changed": False,
                    "skipped": False,
                    "reason": "already_matches",
                })
                audit.append(row)

    return changed, skipped, audit


def build_unapproved_clean_audit_rows(plan_rows):
    audit = []

    for r in plan_rows:
        audit.append({
            "region": r.get("region", ""),
            "sync_mode": "clean_sync",
            "approved_by_user": False,
            "family": r.get("family", ""),
            "axis_position": r.get("axis_position", ""),
            "axis_position_coord": r.get("target_axis_coord", ""),
            "old_label": r.get("target_old_label", ""),
            "new_label": r.get("new_label", ""),
            "changed": False,
            "skipped": True,
            "reason": "user_unchecked_in_manual_clean_sync_table",
        })

    return audit


def build_segmentation_diagnostics_rows(
    regions,
    source_numeric,
    source_alpha,
    numeric_orientation,
    alpha_orientation,
    numeric_order,
    alpha_order,
    axis_tol,
):
    rows = []

    for r in regions:
        all_markers = r.get("all_markers", r.get("markers", []))
        sync_markers = r.get("markers", [])
        raw_axis_groups = group_markers_by_axis(all_markers, tol=axis_tol)
        sync_axis_groups = r.get("axis_groups", group_markers_by_axis(sync_markers, tol=axis_tol))

        raw_numeric, raw_alpha = get_family_groups(
            raw_axis_groups,
            numeric_orientation,
            alpha_orientation,
            numeric_order,
            alpha_order,
        )
        sync_numeric, sync_alpha = get_family_groups(
            {"axis_groups": sync_axis_groups},
            numeric_orientation,
            alpha_orientation,
            numeric_order,
            alpha_order,
        )

        numeric_map, _, numeric_warnings = build_smart_axis_label_map(source_numeric, sync_numeric, "numeric")
        alpha_map, _, alpha_warnings = build_smart_axis_label_map(source_alpha, sync_alpha, "alphabetic")

        rows.append({
            "region": r.get("name", ""),
            "all_detected_markers": len(all_markers),
            "clean_sync_markers_after_filter": len(sync_markers),
            "interior_markers_removed": r.get("interior_marker_count", 0),
            "raw_numeric_axes_before_filter": len(raw_numeric),
            "sync_numeric_axes_after_filter": len(sync_numeric),
            "expected_numeric_axes": len(source_numeric),
            "smart_numeric_output_labels": ", ".join([x["new_label"] for x in numeric_map]),
            "raw_alpha_axes_before_filter": len(raw_alpha),
            "sync_alpha_axes_after_filter": len(sync_alpha),
            "expected_alpha_axes": len(source_alpha),
            "smart_alpha_output_labels": ", ".join([x["new_label"] for x in alpha_map]),
            "mapping_warnings": " | ".join(numeric_warnings + alpha_warnings),
        })

    return rows


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
    write_mode=None,
):
    rows = []

    for r in regions:
        ready, blockers, num, alp, warnings = get_region_sync_plan(
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
            write_mode=write_mode,
        )

        plan = build_clean_plan_for_region(r, source_numeric, source_alpha, num, alp) if ready else []
        selected_markers = []
        for item in plan:
            selected_markers.extend(item.get("group", {}).get("markers", []))

        writable = len([
            m
            for m in selected_markers
            if is_marker_writable(m, allow_block_text_write, write_mode=write_mode)
        ])
        non_writable = len(selected_markers) - writable
        min_conf = min([m.get("confidence", 0) for m in selected_markers]) if selected_markers else 0
        avg_conf = round(sum([m.get("confidence", 0) for m in selected_markers]) / len(selected_markers), 1) if selected_markers else 0

        rows.append({
            "region": r["name"],
            "status": "Ready" if ready else "Blocked",
            "ready_to_sync": ready,
            "clean_preview_axis_rows": len(plan),
            "trusted_markers": len(r["markers"]),
            "selected_markers_for_sync": len(selected_markers),
            "all_detected_markers": len(r.get("all_markers", r["markers"])),
            "interior_markers_ignored": r.get("interior_marker_count", 0),
            "writable_markers": writable,
            "non_writable_markers": non_writable,
            "numeric_axes_found": len(num),
            "numeric_source_axes": len(source_numeric),
            "alpha_axes_found": len(alp),
            "alpha_source_axes": len(source_alpha),
            "numeric_labels_found": ", ".join([g["label"] for g in num]),
            "alpha_labels_found": ", ".join([g["label"] for g in alp]),
            "min_confidence": min_conf,
            "avg_confidence": avg_conf,
            "sync_warnings": "; ".join(warnings),
            "sync_blockers": "; ".join(blockers),
        })

    return rows


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
        "clean_plan": [],
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
        "clean_plan",
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
    axis_tol = st.slider("Axis Group Tolerance", 0.0, 500.0, 10.0, 0.5, key="axis_tol")

with c2:
    text_gap = st.slider("Text-in-Bubble Gap", 20.0, 2000.0, 180.0, 10.0, key="text_gap")

with c3:
    attach_gap = st.slider("Gridline Attach Gap", 20.0, 3000.0, 180.0, 10.0, key="attach_gap")

with c4:
    min_grid_length = st.number_input(
        "Min Grid Line Length",
        min_value=1.0,
        value=1000.0,
        step=100.0,
        key="min_grid_length",
    )

d1, d2, d3, d4 = st.columns(4)

with d1:
    numeric_order = st.selectbox(
        "Numeric Axis Order",
        ["Auto", "Ascending", "Descending"],
        index=0,
        key="numeric_order",
    )

with d2:
    alpha_order = st.selectbox(
        "Alphabetic Axis Order",
        ["Auto", "Ascending", "Descending"],
        index=0,
        key="alpha_order",
    )

with d3:
    forced_region_count = st.number_input(
        "Target Region Count Override",
        min_value=0,
        value=0,
        step=1,
        help="Use 0 for auto. If the target has 8 plans, enter 8.",
        key="forced_region_count",
    )

with d4:
    min_region_markers = st.number_input(
        "Min Markers Per Region",
        min_value=1,
        value=4,
        step=1,
        key="min_region_markers",
    )

e1, e2, e3 = st.columns(3)

with e1:
    write_mode = st.selectbox(
        "Write Mode",
        [WRITE_MODE_SAFE, WRITE_MODE_ATTRIB, WRITE_MODE_DANGEROUS],
        index=1,
        help=(
            "Safe mode writes only modelspace TEXT/MTEXT. "
            "Attribute mode also writes INSERT attributes. "
            "Dangerous mode allows block definition TEXT/MTEXT edits and may affect repeated symbols."
        ),
        key="write_mode_select",
    )
    allow_block_text_write = write_mode == WRITE_MODE_DANGEROUS

with e2:
    min_confidence_required = st.slider(
        "Minimum Marker Confidence",
        0,
        100,
        50,
        5,
        key="min_confidence_required",
    )

with e3:
    max_region_marker_ratio = st.slider(
        "Marker Ratio Warning",
        1.0,
        5.0,
        1.5,
        0.1,
        help="Warning only. Smart sync can still map extra target axes.",
        key="max_region_marker_ratio",
    )

if write_mode == WRITE_MODE_DANGEROUS:
    st.warning("Dangerous write mode is enabled. Block definition edits can affect repeated symbols.")

with st.expander("Advanced nested block layer handling", expanded=False):
    allow_nested_layer0_from_selected_insert = st.checkbox(
        "Allow nested block entities on layer 0 when parent INSERT is on selected grid layer",
        value=True,
        help="Recommended ON. Common CAD convention: block geometry/text is drawn on layer 0 and inherits the INSERT layer.",
        key="allow_nested_layer0_from_selected_insert",
    )

    strict_nested_block_layer_match = st.checkbox(
        "Strict nested block layer match only",
        value=False,
        help="If ON, nested entities must be directly on the selected grid layer.",
        key="strict_nested_block_layer_match",
    )

f1, f2, f3 = st.columns(3)

with f1:
    ignore_interior_detail_bubbles = st.checkbox(
        "Ignore interior/detail bubbles for clean sync",
        value=True,
        help="Recommended. Clean sync keeps only perimeter markers.",
        key="ignore_interior_detail_bubbles",
    )

with f2:
    perimeter_band_ratio = st.slider(
        "Perimeter Band Ratio",
        0.05,
        0.40,
        0.18,
        0.01,
        key="perimeter_band_ratio",
    )

with f3:
    perimeter_min_band = st.number_input(
        "Minimum Perimeter Band",
        min_value=100.0,
        value=1500.0,
        step=100.0,
        key="perimeter_min_band",
    )


# =========================================================
# FILE UPLOAD
# =========================================================

st.markdown("### 2. Upload Files")
u1, u2 = st.columns(2)

with u1:
    arch_file = st.file_uploader("Reference DXF", type=["dxf"], key="arch_file_upload")

with u2:
    struc_file = st.file_uploader("Target DXF", type=["dxf"], key="struc_file_upload")

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

arch_line_default = pick_default_layer(arch_layers, ["S-GRID", "GRID", "GRIDLINELAYER"])
arch_text_default = pick_default_layer(arch_layers, ["S-GRID-IDEN", "GRID-ID", "DEFAULTLAYER"])
arch_circle_default = pick_default_layer(arch_layers, ["S-GRID-IDEN", "GRID-ID", "DEFAULTLAYER"])

struc_line_default = pick_default_layer(struc_layers, ["GRIDLINELAYER", "S-GRID", "GRID"])
struc_text_default = pick_default_layer(struc_layers, ["DEFAULTLAYER", "S-STRS-IDEN", "S-GRID-IDEN", "GRID-ID"])
struc_circle_default = pick_default_layer(struc_layers, ["DEFAULTLAYER", "S-GRID-IDEN", "GRID-ID"])

la, lb, lc = st.columns(3)

with la:
    arch_line_layer = st.selectbox(
        "Reference Grid Line Layer",
        arch_layers,
        index=arch_layers.index(arch_line_default) if arch_line_default in arch_layers else 0,
        key="arch_line_layer_select",
    )

with lb:
    arch_text_layer = st.selectbox(
        "Reference Grid Text Layer",
        arch_layers,
        index=arch_layers.index(arch_text_default) if arch_text_default in arch_layers else 0,
        key="arch_text_layer_select",
    )

with lc:
    arch_circle_layer = st.selectbox(
        "Reference Grid Bubble Layer",
        arch_layers,
        index=arch_layers.index(arch_circle_default) if arch_circle_default in arch_layers else 0,
        key="arch_circle_layer_select",
    )

sa, sb, sc = st.columns(3)

with sa:
    struc_line_layer = st.selectbox(
        "Target Grid Line Layer",
        struc_layers,
        index=struc_layers.index(struc_line_default) if struc_line_default in struc_layers else 0,
        key="struc_line_layer_select",
    )

with sb:
    struc_text_layer = st.selectbox(
        "Target Grid Text Layer",
        struc_layers,
        index=struc_layers.index(struc_text_default) if struc_text_default in struc_layers else 0,
        key="struc_text_layer_select",
    )

with sc:
    struc_circle_layer = st.selectbox(
        "Target Grid Bubble Layer",
        struc_layers,
        index=struc_layers.index(struc_circle_default) if struc_circle_default in struc_layers else 0,
        key="struc_circle_layer_select",
    )

b1, b2 = st.columns(2)

with b1:
    analyze = st.button("Analyze / Prepare Sync", type="primary", key="analyze_button")

with b2:
    if st.button("Reset", key="reset_button"):
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
            allow_block_text_write=allow_block_text_write,
            write_mode=write_mode,
            allow_nested_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
            strict_nested_block_layer_match=strict_nested_block_layer_match,
        )

        struc_det = build_trusted_markers(
            struc_doc,
            struc_line_layer,
            struc_text_layer,
            struc_circle_layer,
            min_grid_length,
            text_gap,
            attach_gap,
            allow_block_text_write=allow_block_text_write,
            write_mode=write_mode,
            allow_nested_layer0_from_selected_insert=allow_nested_layer0_from_selected_insert,
            strict_nested_block_layer_match=strict_nested_block_layer_match,
        )

        arch_trusted = [
            m
            for m in arch_det["trusted_markers"]
            if m.get("confidence", 0) >= min_confidence_required
        ]

        struc_trusted = [
            m
            for m in struc_det["trusted_markers"]
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
            write_mode=write_mode,
        )

        clean_plan = []

        for r in regions:
            ready, blockers, num, alp, warnings = get_region_sync_plan(
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
                write_mode=write_mode,
            )

            if ready:
                clean_plan.extend(build_clean_plan_for_region(r, source_numeric, source_alpha, num, alp))

        st.session_state.arch_detection = arch_det
        st.session_state.struc_detection = struc_det
        st.session_state.arch_axis_groups = arch_groups
        st.session_state.family = family
        st.session_state.source_numeric = source_numeric
        st.session_state.source_alpha = source_alpha
        st.session_state.regions = regions
        st.session_state.segmentation = seg
        st.session_state.region_report = report
        st.session_state.clean_plan = clean_plan
        st.session_state.audit = []
        st.session_state.changed = 0
        st.session_state.skipped = 0
        st.session_state.prepared = True

        ready_count = len([r for r in report if r.get("ready_to_sync")])
        st.success(f"Analysis complete. Regions found: {len(regions)}. Ready smart-sync regions: {ready_count}.")

        if arch_det["rejected_markers"]:
            st.warning(f"Reference rejected markers: {len(arch_det['rejected_markers'])}")

        if struc_det["rejected_markers"]:
            st.warning(f"Target rejected markers: {len(struc_det['rejected_markers'])}")

    except Exception as e:
        st.error(f"Prepare failed: {e}")


# =========================================================
# RESULTS / APPLY / DOWNLOAD
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

    st.markdown("### 5. Segmentation / Smart Mapping Diagnostics")
    seg_diag_rows = build_segmentation_diagnostics_rows(
        st.session_state.regions,
        st.session_state.source_numeric,
        st.session_state.source_alpha,
        family["numeric_orientation"],
        family["alpha_orientation"],
        numeric_order,
        alpha_order,
        axis_tol,
    )
    st.dataframe(seg_diag_rows, use_container_width=True)

    with st.expander("Raw segmentation settings/details", expanded=False):
        st.write(st.session_state.segmentation)

    st.markdown("### 6. Region Completeness Dashboard")
    st.dataframe(st.session_state.region_report, use_container_width=True)

    ready_regions = [r["region"] for r in st.session_state.region_report if r.get("ready_to_sync")]
    all_regions = [r["region"] for r in st.session_state.region_report]

    selected_regions = st.multiselect(
        "Select regions to sync",
        options=all_regions,
        default=ready_regions,
        help="Ready smart-sync regions are selected by default.",
        key="selected_clean_regions",
    )

    st.markdown("### 7. Smart Sync Editable Preview")

    filtered_clean_plan = [
        row
        for row in st.session_state.clean_plan
        if row["region"] in selected_regions
    ]

    approved_clean_plan_rows = []
    unapproved_clean_plan_rows = []
    invalid_clean_plan_rows = []

    if filtered_clean_plan:
        clean_preview = clean_preview_rows(filtered_clean_plan)
        edited_clean_preview = st.data_editor(
            clean_preview,
            use_container_width=True,
            hide_index=True,
            key="clean_sync_approval_editor",
            column_config={
                "apply": st.column_config.CheckboxColumn(
                    "Apply?",
                    help="Uncheck rows that should not be modified.",
                    default=True,
                ),
                "new_label": st.column_config.TextColumn(
                    "New Label",
                    help="Editable. Change this before sync is applied.",
                    required=True,
                ),
            },
            disabled=[
                "approval_id",
                "region",
                "sync_mode",
                "family",
                "axis_position",
                "source_label",
                "source_end_label",
                "target_old_label",
                "proposed_new_label",
                "subdivision_step",
                "subdivision_span",
                "omitted_source_labels",
                "mapping_note",
                "target_axis_coord",
                "target_markers",
                "same_axis_marker_count",
                "target_writable_markers",
                "target_avg_confidence",
                "target_mixed_labels",
                "write_risks",
            ],
        )

        approved_clean_plan_rows, unapproved_clean_plan_rows, invalid_clean_plan_rows = clean_plan_from_editor(
            filtered_clean_plan,
            edited_clean_preview,
        )

        if invalid_clean_plan_rows:
            st.error("Some approved rows have invalid edited New Label values. Fix them or uncheck Apply.")
            invalid_preview = []

            for bad in invalid_clean_plan_rows:
                invalid_preview.append({
                    "region": bad.get("region", ""),
                    "family": bad.get("family", ""),
                    "axis_position": bad.get("axis_position", ""),
                    "target_old_label": bad.get("target_old_label", ""),
                    "proposed_new_label": bad.get("original_proposed_new_label", bad.get("new_label", "")),
                    "edited_new_label": bad.get("user_edited_new_label", bad.get("new_label", "")),
                    "reason": bad.get("invalid_reason", ""),
                })

            st.dataframe(invalid_preview, use_container_width=True)

        clean_counts = clean_plan_apply_counts(approved_clean_plan_rows)
        st.write("#### Smart Sync Apply Summary")
        st.write({
            "approved_axis_rows": clean_counts["approved_axis_rows"],
            "will_change_text_entities": clean_counts["will_change_text_entities"],
            "already_match_text_entities": clean_counts["already_match_text_entities"],
            "high_risk_text_entities": clean_counts["high_risk_text_entities"],
            "unwritable_text_entities": clean_counts["unwritable_text_entities"],
            "unchecked_axis_rows": len(unapproved_clean_plan_rows),
            "write_mode": write_mode,
        })
    else:
        st.info("No smart-sync preview rows. Select a ready region or check the blockers in Section 6.")

    st.markdown("### 8. Apply Smart Sync")

    sync_confirm = st.checkbox(
        "I reviewed the editable smart sync preview and understand these changes will modify the DXF.",
        value=False,
        key="confirm_clean_sync_apply",
    )

    dangerous_confirm = True

    if write_mode == WRITE_MODE_DANGEROUS:
        dangerous_confirm = st.checkbox(
            "I understand block definition edits may affect repeated symbols.",
            value=False,
            key="confirm_dangerous_block_write",
        )

    smart_sync_disabled = (
        not sync_confirm
        or not dangerous_confirm
        or not selected_regions
        or not approved_clean_plan_rows
        or bool(invalid_clean_plan_rows)
    )

    if st.button(
        "Apply Approved Smart Sync to Selected Regions",
        key="apply_clean_sync_button",
        disabled=smart_sync_disabled,
    ):
        total_changed, total_skipped, audit = apply_clean_plan_rows(
            approved_clean_plan_rows,
            allow_block_text_write=allow_block_text_write,
            write_mode=write_mode,
        )

        skipped_audit = build_unapproved_clean_audit_rows(unapproved_clean_plan_rows)
        st.session_state.changed += total_changed
        st.session_state.skipped += total_skipped + len(unapproved_clean_plan_rows)
        st.session_state.audit.extend(audit + skipped_audit)

        if total_changed:
            st.success(f"Smart sync complete. Changed {total_changed} text entities.")
        else:
            st.info("Smart sync completed. No labels needed changing.")

        if total_skipped:
            st.warning(f"Smart sync skipped {total_skipped} text entities.")

        if unapproved_clean_plan_rows:
            st.info(f"{len(unapproved_clean_plan_rows)} axis rows were intentionally unchecked and skipped.")

    if st.session_state.audit:
        st.markdown("### 9. Audit Report")
        st.dataframe(st.session_state.audit, use_container_width=True)

        audit_csv = audit_to_csv_bytes(st.session_state.audit)
        st.download_button(
            "Download Audit CSV",
            data=audit_csv,
            file_name=f"AUDIT_{st.session_state.struc_name}.csv",
            mime="text/csv",
            key="download_audit_csv",
        )

    st.markdown("### 10. Download")

    data = write_doc_to_temp_bytes(st.session_state.struc_doc)
    st.download_button(
        "Download Target DXF",
        data=data,
        file_name=f"RELABELED_{st.session_state.struc_name}",
        mime="application/dxf",
        key="download_relabelled_dxf",
    )
