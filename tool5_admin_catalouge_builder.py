import io
import json
import math
import os
import re
import tempfile
import zipfile

import ezdxf
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="iLoveStructural - Stair Catalogue Admin",
    page_icon=":building_construction:",
    layout="wide",
)

st.title("Staircase Catalogue Builder")
st.caption(
    "Admin tool for turning clean individual staircase DXFs into layer-aware, dimension-aware parametric templates."
)


STAIRCASE_TAXONOMY = {
    "Materials": ["Reinforced Concrete (RC)", "Structural Steel", "Timber", "Composite"],
    "Families": {
        "Half-Turn (U-Shape)": {
            "Types": ["Dog-Leg", "Open-Well"],
            "Symmetry": ["Symmetrical", "Asymmetrical"],
        },
        "Quarter-Turn (L-Shape)": {
            "Types": ["Quarter-Space Landing", "Winder"],
            "Symmetry": ["N/A"],
        },
        "Straight": {
            "Types": ["Single Flight", "With Intermediate Landing"],
            "Symmetry": ["N/A"],
        },
        "Spiral": {
            "Types": ["Circular Spiral", "Helical"],
            "Symmetry": ["N/A"],
        },
        "Detail Components": {
            "Types": ["Reinforcement Detail", "Tread Section", "Landing Section"],
            "Symmetry": ["N/A"],
        },
    },
    "Structural_Classes": [
        "Solid Waist Slab",
        "Folded Plate",
        "Side Stringer",
        "Central Spine Beam",
        "Cantilever",
    ],
}


def taxonomy_family_id(family_name, stair_type):
    value = f"{family_name}_{stair_type}".lower()
    clean = re.sub(r"[^a-z0-9_.-]+", "_", value).strip("._")
    return clean or "stair_family"


def build_family_options_from_taxonomy():
    options = {}

    for family_name, family_data in STAIRCASE_TAXONOMY["Families"].items():
        for stair_type in family_data.get("Types", []):
            family_id = taxonomy_family_id(family_name, stair_type)
            options[family_id] = f"{family_name} - {stair_type}"

    return options


FAMILY_OPTIONS = build_family_options_from_taxonomy()

GENERATOR_BY_FAMILY = {
    taxonomy_family_id("Half-Turn (U-Shape)", "Dog-Leg"): "Dog-leg stair",
    taxonomy_family_id("Half-Turn (U-Shape)", "Open-Well"): "Open-well stair",
    taxonomy_family_id("Quarter-Turn (L-Shape)", "Quarter-Space Landing"): "L-shape stair",
    taxonomy_family_id("Quarter-Turn (L-Shape)", "Winder"): "Winder stair",
    taxonomy_family_id("Straight", "Single Flight"): "Straight flight",
    taxonomy_family_id("Straight", "With Intermediate Landing"): "Straight flight with landing",
    taxonomy_family_id("Spiral", "Circular Spiral"): "Spiral stair",
    taxonomy_family_id("Spiral", "Helical"): "Helical stair",
    taxonomy_family_id("Detail Components", "Reinforcement Detail"): "Reinforcement detail",
    taxonomy_family_id("Detail Components", "Tread Section"): "Tread section",
    taxonomy_family_id("Detail Components", "Landing Section"): "Landing section",
}

LAYER_ROLE_OPTIONS = [
    "geometry",
    "dimension",
    "reinforcement",
    "landing",
    "tread_riser",
    "section",
    "annotation",
    "hidden",
]

LAYER_ROLE_KEYWORDS = {
    "dimension": ["dim", "dimension", "measure"],
    "reinforcement": ["rebar", "rein", "reinforcement", "bar", "steel", "mesh", "link", "stirrup"],
    "landing": ["landing", "waist", "slab"],
    "tread_riser": ["tread", "riser", "going", "step", "stair"],
    "section": ["section", "sec", "cut", "profile"],
    "annotation": ["text", "note", "anno", "label", "tag"],
    "hidden": ["hidden", "dash", "center", "centre"],
}

DIMENSION_PARAMETER_PATTERNS = [
    ("floor_height", ["floor height", "floor to floor", "f.f", "ffl", "height", "rise total"]),
    ("stair_width", ["stair width", "flight width", "width"]),
    ("tread_depth", ["tread", "going", "goings"]),
    ("riser_height", ["riser", "rise"]),
    ("landing_length", ["landing"]),
    ("flight_gap", ["gap", "well", "void"]),
    ("waist_thickness", ["waist", "slab thickness", "thickness"]),
    ("bar_spacing", ["spacing", "c/c", "ctc", "@"]),
    ("bar_diameter", ["y", "t", "dia", "diameter", "bar"]),
]

ROLE_COLORS = {
    "geometry": "#e5e7eb",
    "dimension": "#fde047",
    "reinforcement": "#fb7185",
    "landing": "#38bdf8",
    "tread_riser": "#34d399",
    "section": "#c084fc",
    "annotation": "#f8fafc",
    "hidden": "#94a3b8",
}


def safe_filename(value, default="stair_template"):
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    clean = clean.strip("._")
    return clean or default


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def read_doc_from_bytes(data):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        return ezdxf.readfile(tmp_path)
    finally:
        safe_remove_file(tmp_path)


def clean_dxf_text(value):
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\\P", " ")
    text = re.sub(r"\{\\[^;{}]+;", "", text)
    text = re.sub(r"\\[A-Za-z0-9_.|+\\-]+", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def point_bbox(x, y, pad=1.0):
    return {
        "min_x": float(x) - pad,
        "min_y": float(y) - pad,
        "max_x": float(x) + pad,
        "max_y": float(y) + pad,
    }


def bbox_union(bboxes):
    clean = [b for b in bboxes if b]
    if not clean:
        return None
    return {
        "min_x": min(b["min_x"] for b in clean),
        "min_y": min(b["min_y"] for b in clean),
        "max_x": max(b["max_x"] for b in clean),
        "max_y": max(b["max_y"] for b in clean),
    }


def bbox_width(bbox):
    return max(1.0, float(bbox["max_x"]) - float(bbox["min_x"]))


def bbox_height(bbox):
    return max(1.0, float(bbox["max_y"]) - float(bbox["min_y"]))


def expand_bbox(bbox, ratio=0.05):
    pad = max(bbox_width(bbox), bbox_height(bbox)) * ratio
    return {
        "min_x": bbox["min_x"] - pad,
        "min_y": bbox["min_y"] - pad,
        "max_x": bbox["max_x"] + pad,
        "max_y": bbox["max_y"] + pad,
    }


def dxf_get(entity, name, default=None):
    try:
        return entity.dxf.get(name, default)
    except Exception:
        try:
            return getattr(entity.dxf, name)
        except Exception:
            return default


def point_tuple(value):
    try:
        return float(value.x), float(value.y)
    except Exception:
        try:
            return float(value[0]), float(value[1])
        except Exception:
            return None


def text_height(entity):
    for name in ["height", "char_height"]:
        value = dxf_get(entity, name, None)
        if value:
            try:
                return float(value)
            except Exception:
                pass
    return 250.0


def text_point(entity):
    for name in ["insert", "location", "align_point"]:
        point = point_tuple(dxf_get(entity, name, None))
        if point:
            return point
    return 0.0, 0.0


def get_entity_text(entity):
    try:
        if entity.dxftype() == "TEXT":
            return clean_dxf_text(entity.dxf.text)
        if entity.dxftype() == "MTEXT":
            return clean_dxf_text(entity.text)
    except Exception:
        pass
    return ""


def dimension_defpoints(entity):
    points = []
    for name in ["defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5"]:
        point = point_tuple(dxf_get(entity, name, None))
        if point and point not in points:
            points.append(point)
    return points


def dimension_measurement(entity):
    for method_name in ["get_measurement", "get_actual_measurement"]:
        try:
            method = getattr(entity, method_name)
            value = method()
            if value is not None:
                return float(value)
        except Exception:
            pass

    for name in ["actual_measurement", "measurement"]:
        try:
            value = dxf_get(entity, name, None)
            if value is not None:
                return float(value)
        except Exception:
            pass

    return None


def dimension_display_text(entity):
    raw = clean_dxf_text(dxf_get(entity, "text", ""))
    measurement = dimension_measurement(entity)

    if raw and raw != "<>":
        return raw

    if measurement is not None:
        return f"{measurement:.0f}"

    return raw


def infer_dimension_parameter(text, measurement=None):
    clean = str(text or "").lower()

    for parameter, keywords in DIMENSION_PARAMETER_PATTERNS:
        if any(keyword in clean for keyword in keywords):
            return parameter

    if measurement is None:
        return "unclassified_dimension"

    try:
        value = abs(float(measurement))
    except Exception:
        return "unclassified_dimension"

    if 120.0 <= value <= 220.0:
        return "riser_height"
    if 220.0 <= value <= 400.0:
        return "tread_depth"
    if 850.0 <= value <= 1800.0:
        return "stair_width_or_landing"
    if 2400.0 <= value <= 4500.0:
        return "floor_height_or_flight_length"

    return "unclassified_dimension"


def classify_layer_role(layer_name):
    lower = str(layer_name or "").lower()

    for role, keywords in LAYER_ROLE_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            return role

    return "geometry"


def layer_table(doc):
    rows = []

    try:
        layers = list(doc.layers)
    except Exception:
        layers = []

    for layer in layers:
        name = layer.dxf.name
        rows.append({
            "layer": name,
            "detected_role": classify_layer_role(name),
            "dxf_color": int(getattr(layer.dxf, "color", 7) or 7),
            "linetype": str(getattr(layer.dxf, "linetype", "") or ""),
        })

    return rows


def entity_bbox(entity):
    try:
        typ = entity.dxftype()

        if typ == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            return bbox_union([point_bbox(s.x, s.y, 0), point_bbox(e.x, e.y, 0)])

        if typ == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points()]
            return bbox_union([point_bbox(x, y, 0) for x, y in pts])

        if typ == "POLYLINE":
            pts = []
            for vertex in entity.vertices:
                loc = vertex.dxf.location
                pts.append((float(loc.x), float(loc.y)))
            return bbox_union([point_bbox(x, y, 0) for x, y in pts])

        if typ == "CIRCLE":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            return {"min_x": c.x - r, "min_y": c.y - r, "max_x": c.x + r, "max_y": c.y + r}

        if typ == "ARC":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            return {"min_x": c.x - r, "min_y": c.y - r, "max_x": c.x + r, "max_y": c.y + r}

        if typ in {"TEXT", "MTEXT"}:
            x, y = text_point(entity)
            text = get_entity_text(entity)
            height = text_height(entity)
            width = max(height * 2.0, len(text) * height * 0.55)
            return {"min_x": x, "min_y": y - height, "max_x": x + width, "max_y": y + height}

        if typ in {"DIMENSION", "INSERT"}:
            bboxes = []
            try:
                for virtual_entity in entity.virtual_entities():
                    bboxes.append(entity_bbox(virtual_entity))
            except Exception:
                pass

            if bboxes:
                return bbox_union(bboxes)

            points = dimension_defpoints(entity) if typ == "DIMENSION" else []
            return bbox_union([point_bbox(x, y, 0) for x, y in points])

    except Exception:
        return None

    return None


def entity_to_record(entity, source_type="", source_layer=""):
    bbox = entity_bbox(entity)
    if bbox is None:
        return None

    typ = entity.dxftype()
    layer = str(dxf_get(entity, "layer", "") or "")

    if source_layer and layer in {"", "0"}:
        layer = source_layer

    record = {
        "type": typ,
        "source_type": source_type or typ,
        "source_layer": source_layer or layer,
        "layer": layer,
        "bbox": bbox,
    }

    try:
        if typ == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            record["start"] = (float(s.x), float(s.y))
            record["end"] = (float(e.x), float(e.y))
        elif typ == "LWPOLYLINE":
            record["points"] = [(float(p[0]), float(p[1])) for p in entity.get_points()]
            record["closed"] = bool(entity.closed)
        elif typ == "POLYLINE":
            pts = []
            for vertex in entity.vertices:
                loc = vertex.dxf.location
                pts.append((float(loc.x), float(loc.y)))
            record["points"] = pts
            record["closed"] = bool(entity.is_closed)
        elif typ == "CIRCLE":
            c = entity.dxf.center
            record["center"] = (float(c.x), float(c.y))
            record["radius"] = float(entity.dxf.radius)
        elif typ == "ARC":
            c = entity.dxf.center
            record["center"] = (float(c.x), float(c.y))
            record["radius"] = float(entity.dxf.radius)
            record["start_angle"] = float(entity.dxf.start_angle)
            record["end_angle"] = float(entity.dxf.end_angle)
        elif typ in {"TEXT", "MTEXT"}:
            record["text"] = get_entity_text(entity)
            record["point"] = text_point(entity)
            record["height"] = text_height(entity)
            record["rotation"] = float(dxf_get(entity, "rotation", 0.0) or 0.0)
        elif typ == "DIMENSION":
            measurement = dimension_measurement(entity)
            text = dimension_display_text(entity)
            record["text"] = text
            record["measurement"] = measurement
            record["points"] = dimension_defpoints(entity)
            record["parameter_hint"] = infer_dimension_parameter(text, measurement)
    except Exception:
        pass

    return record


def virtual_entity_records(entity, source_type, source_layer, depth=0):
    if depth > 2:
        return []

    records = []

    try:
        virtual_entities = list(entity.virtual_entities())
    except Exception:
        return records

    for virtual_entity in virtual_entities:
        try:
            typ = virtual_entity.dxftype()
        except Exception:
            continue

        if typ in {"DIMENSION", "INSERT"}:
            records.extend(
                virtual_entity_records(
                    virtual_entity,
                    source_type=source_type,
                    source_layer=source_layer,
                    depth=depth + 1,
                )
            )
            continue

        record = entity_to_record(
            virtual_entity,
            source_type=source_type,
            source_layer=source_layer,
        )
        if record:
            records.append(record)

    return records


def analyse_dxf(data):
    doc = read_doc_from_bytes(data)
    msp = doc.modelspace()
    supported = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "TEXT", "MTEXT", "DIMENSION", "INSERT"}
    records = []
    dimension_records = []
    entity_counts = {}
    layer_counts = {}
    layers = layer_table(doc)
    layer_roles = {row["layer"]: row["detected_role"] for row in layers}

    for entity in msp:
        try:
            typ = entity.dxftype()
            layer = str(dxf_get(entity, "layer", "") or "")
        except Exception:
            continue

        entity_counts[typ] = entity_counts.get(typ, 0) + 1
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

        if typ not in supported:
            continue

        if typ in {"DIMENSION", "INSERT"}:
            record = entity_to_record(entity)
            virtual_records = virtual_entity_records(
                entity,
                source_type=typ,
                source_layer=layer,
            )

            if typ == "DIMENSION" and record:
                dimension_records.append(record)

            if virtual_records:
                records.extend(virtual_records)
            elif record:
                records.append(record)

            continue

        record = entity_to_record(entity)
        if record:
            records.append(record)

    bbox = bbox_union([record["bbox"] for record in records])
    if bbox:
        bbox = expand_bbox(bbox, ratio=0.04)

    dimension_parameters = {}
    for dim in dimension_records:
        parameter = dim.get("parameter_hint", "unclassified_dimension")
        dimension_parameters.setdefault(parameter, []).append({
            "text": dim.get("text", ""),
            "measurement": dim.get("measurement", None),
            "layer": dim.get("layer", ""),
            "bbox": dim.get("bbox", {}),
        })

    return {
        "records": records,
        "bbox": bbox,
        "entity_counts": entity_counts,
        "layer_counts": layer_counts,
        "layers": layers,
        "layer_roles": layer_roles,
        "dimension_records": dimension_records,
        "dimension_parameters": dimension_parameters,
    }


def stable_palette_index(value, length):
    total = sum(ord(ch) for ch in str(value or ""))
    return total % max(length, 1)


def color_for_record(record, layer_roles):
    layer = record.get("layer", "")
    source_type = record.get("source_type", "")
    role = layer_roles.get(layer, classify_layer_role(layer))

    if source_type == "DIMENSION" or record.get("type") == "DIMENSION":
        return ROLE_COLORS["dimension"]

    if role in ROLE_COLORS:
        return ROLE_COLORS[role]

    palette = ["#f8fafc", "#93c5fd", "#fbbf24", "#34d399", "#fb7185", "#c084fc", "#67e8f9"]
    return palette[stable_palette_index(layer, len(palette))]


def svg_xy(x, y, bbox, width, height, pad):
    sx = pad + (float(x) - bbox["min_x"]) / bbox_width(bbox) * (width - pad * 2)
    sy = pad + (bbox["max_y"] - float(y)) / bbox_height(bbox) * (height - pad * 2)
    return sx, sy


def svg_escape(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_text_element(x, y, text, color, font_size, rotation=0.0):
    clean = svg_escape(text)[:140]
    rotate = float(rotation or 0.0)
    transform = ""

    if abs(rotate) > 1e-6:
        # SVG has a downward Y axis after our coordinate conversion, so AutoCAD
        # text rotation must be inverted to keep vertical labels readable.
        transform = f' transform="rotate({-rotate:.3f} {x:.2f} {y:.2f})"'

    return (
        f'<text x="{x:.2f}" y="{y:.2f}" fill="{color}" '
        f'font-size="{font_size:.2f}" font-family="Arial" '
        f'text-anchor="start" dominant-baseline="middle"{transform}>{clean}</text>'
    )


def render_svg(records, bbox, layer_roles, width=1000, height=700, max_records=9000):
    if not bbox:
        return ""

    pad = 24
    scale_x = (width - pad * 2) / bbox_width(bbox)
    scale_y = (height - pad * 2) / bbox_height(bbox)
    radius_scale = min(scale_x, scale_y)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        'style="background:#0f172a;border-radius:8px;">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#0f172a"/>',
    ]

    for record in records[:max_records]:
        typ = record.get("type")
        color = color_for_record(record, layer_roles)
        source_type = record.get("source_type", "")
        stroke_width = 1.5 if source_type == "DIMENSION" or typ == "DIMENSION" else 1.0

        try:
            if typ == "LINE":
                x1, y1 = svg_xy(*record["start"], bbox, width, height, pad)
                x2, y2 = svg_xy(*record["end"], bbox, width, height, pad)
                parts.append(
                    f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                    f'stroke="{color}" stroke-width="{stroke_width}"/>'
                )
            elif typ in {"LWPOLYLINE", "POLYLINE"}:
                pts = ["{:.2f},{:.2f}".format(*svg_xy(x, y, bbox, width, height, pad)) for x, y in record.get("points", [])]
                if len(pts) >= 2:
                    if record.get("closed"):
                        pts.append(pts[0])
                    parts.append(
                        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{stroke_width}"/>'
                    )
            elif typ == "CIRCLE":
                cx, cy = svg_xy(*record["center"], bbox, width, height, pad)
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{record["radius"] * radius_scale:.2f}" '
                    f'fill="none" stroke="{color}" stroke-width="{stroke_width}"/>'
                )
            elif typ == "ARC":
                cx, cy = svg_xy(*record["center"], bbox, width, height, pad)
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{record["radius"] * radius_scale:.2f}" '
                    f'fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-dasharray="4 4"/>'
                )
            elif typ in {"TEXT", "MTEXT"}:
                text = record.get("text", "")
                if text:
                    x, y = svg_xy(*record["point"], bbox, width, height, pad)
                    raw_height = float(record.get("height", 250.0) or 250.0)
                    font_size = max(7.0, min(22.0, raw_height * radius_scale))
                    if source_type == "DIMENSION":
                        font_size = max(7.0, min(18.0, font_size))
                    parts.append(
                        svg_text_element(
                            x,
                            y,
                            text,
                            color,
                            font_size,
                            rotation=record.get("rotation", 0.0),
                        )
                    )
            elif typ == "DIMENSION":
                points = record.get("points", [])
                if len(points) >= 2:
                    p1 = svg_xy(*points[0], bbox, width, height, pad)
                    p2 = svg_xy(*points[-1], bbox, width, height, pad)
                    parts.append(
                        f'<line x1="{p1[0]:.2f}" y1="{p1[1]:.2f}" x2="{p2[0]:.2f}" y2="{p2[1]:.2f}" '
                        f'stroke="{color}" stroke-width="1.2" stroke-dasharray="3 3"/>'
                    )
                if record.get("text"):
                    bx = (record["bbox"]["min_x"] + record["bbox"]["max_x"]) / 2.0
                    by = (record["bbox"]["min_y"] + record["bbox"]["max_y"]) / 2.0
                    x, y = svg_xy(bx, by, bbox, width, height, pad)
                    parts.append(svg_text_element(x, y, record.get("text"), color, 10.0))
        except Exception:
            continue

    parts.append("</svg>")
    return "\n".join(parts)


def layers_to_dataframe(analysis):
    rows = []
    layer_counts = analysis.get("layer_counts", {})
    for row in analysis.get("layers", []):
        rows.append({
            "layer": row["layer"],
            "detected_role": row["detected_role"],
            "entity_count": layer_counts.get(row["layer"], 0),
            "dxf_color": row.get("dxf_color", ""),
            "linetype": row.get("linetype", ""),
        })
    return pd.DataFrame(rows)


def dimensions_to_dataframe(analysis):
    rows = []
    for idx, dim in enumerate(analysis.get("dimension_records", []), start=1):
        rows.append({
            "id": idx,
            "parameter_hint": dim.get("parameter_hint", ""),
            "text": dim.get("text", ""),
            "measurement": dim.get("measurement", ""),
            "layer": dim.get("layer", ""),
            "min_x": round(dim.get("bbox", {}).get("min_x", 0.0), 3),
            "min_y": round(dim.get("bbox", {}).get("min_y", 0.0), 3),
            "max_x": round(dim.get("bbox", {}).get("max_x", 0.0), 3),
            "max_y": round(dim.get("bbox", {}).get("max_y", 0.0), 3),
        })
    return pd.DataFrame(rows)


def compact_analysis_summary(analysis):
    return {
        "entity_counts": analysis.get("entity_counts", {}),
        "layer_counts": analysis.get("layer_counts", {}),
        "layers": analysis.get("layers", []),
        "layer_roles": analysis.get("layer_roles", {}),
        "dimension_count": len(analysis.get("dimension_records", [])),
        "dimension_parameters": analysis.get("dimension_parameters", {}),
    }


def family_display_name(family_id, record=None):
    if record and record.get("family_name"):
        return record["family_name"]
    return FAMILY_OPTIONS.get(family_id, str(family_id or "Unclassified stair"))


def build_template_record(
    template_id,
    name,
    family_id,
    source_filename,
    preview_filename,
    analysis_summary,
    taxonomy_metadata,
):
    return {
        "id": template_id,
        "name": name,
        "family_id": family_id,
        "family_name": FAMILY_OPTIONS.get(family_id, taxonomy_metadata.get("family", family_id)),
        "generator_type": GENERATOR_BY_FAMILY.get(family_id, "Dog-leg stair"),
        "source_dxf": f"curated_stair_assets/{family_id}/{template_id}/{source_filename}",
        "preview_svg": f"curated_stair_assets/{family_id}/{template_id}/{preview_filename}",
        "analysis_json": f"curated_stair_assets/{family_id}/{template_id}/analysis.json",
        "taxonomy": taxonomy_metadata,
        "material": taxonomy_metadata.get("material", ""),
        "stair_family": taxonomy_metadata.get("family", ""),
        "stair_type": taxonomy_metadata.get("type", ""),
        "symmetry": taxonomy_metadata.get("symmetry", ""),
        "structural_class": taxonomy_metadata.get("structural_class", ""),
        "tags": [
            family_id,
            taxonomy_family_id("material", taxonomy_metadata.get("material", "")),
            taxonomy_family_id("structural", taxonomy_metadata.get("structural_class", "")),
        ],
        "layer_roles": analysis_summary.get("layer_roles", {}),
        "dimension_parameters": analysis_summary.get("dimension_parameters", {}),
        "parametric_intent": {
            "geometry_layers_scale_from_dimensions": True,
            "reinforcement_layers_recalculate_from_span": True,
            "dimension_layers_preserve_as_annotation": True,
            "notes": "Layer roles and detected dimensions are stored so the future calculator can resize geometry and regenerate reinforcement rules separately.",
        },
        "default_inputs": {
            "floor_height": 3000,
            "stair_width": 1200,
            "target_riser": 175,
            "min_riser": 150,
            "max_riser": 190,
            "tread_depth": 300,
            "landing_length": 1200,
            "flight_gap": 200,
        },
        "detailing_rules": {
            "riser_strategy": "auto_count_from_floor_height",
            "reinforcement_strategy": "layer_role_and_family_rule_based",
            "notes": "Curated source template. Generated detail must be verified by a qualified engineer.",
        },
    }


def build_curated_package(items):
    buffer = io.BytesIO()
    families = {}

    for item in items:
        families.setdefault(item["family_id"], {
            "id": item["family_id"],
            "name": family_display_name(item["family_id"], item.get("record", {})),
            "templates": [],
        })
        families[item["family_id"]]["templates"].append(item["record"])

    manifest = {
        "version": "1.2",
        "purpose": "dimension_and_layer_aware_stair_template_catalogue",
        "taxonomy": STAIRCASE_TAXONOMY,
        "families": list(families.values()),
    }

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("curated_stair_catalogue.json", json.dumps(manifest, indent=2).encode("utf-8"))
        for item in items:
            base = f"curated_stair_assets/{item['family_id']}/{item['template_id']}"
            zf.writestr(f"{base}/source.dxf", item["dxf_bytes"])
            zf.writestr(f"{base}/preview.svg", item["preview_svg"].encode("utf-8"))
            zf.writestr(f"{base}/analysis.json", json.dumps(item["analysis_summary"], indent=2).encode("utf-8"))

    buffer.seek(0)
    return buffer.getvalue()


def load_curated_package(data):
    items = []

    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        manifest = json.loads(zf.read("curated_stair_catalogue.json").decode("utf-8"))

        for family in manifest.get("families", []):
            family_id = family.get("id", "")
            for record in family.get("templates", []):
                template_id = record.get("id", "")
                base = f"curated_stair_assets/{family_id}/{template_id}"
                preview_svg = ""
                dxf_bytes = b""
                analysis_summary = {}

                preview_path = record.get("preview_svg", f"{base}/preview.svg")
                source_path = record.get("source_dxf", f"{base}/source.dxf")
                analysis_path = record.get("analysis_json", f"{base}/analysis.json")

                try:
                    preview_svg = zf.read(preview_path).decode("utf-8")
                except Exception:
                    pass

                try:
                    dxf_bytes = zf.read(source_path)
                except Exception:
                    pass

                try:
                    analysis_summary = json.loads(zf.read(analysis_path).decode("utf-8"))
                except Exception:
                    analysis_summary = {
                        "dimension_count": len(record.get("dimension_parameters", {})),
                        "layer_counts": {},
                        "layer_roles": record.get("layer_roles", {}),
                        "dimension_parameters": record.get("dimension_parameters", {}),
                    }

                items.append({
                    "template_id": template_id,
                    "family_id": family_id,
                    "record": record,
                    "dxf_bytes": dxf_bytes,
                    "preview_svg": preview_svg,
                    "analysis_summary": analysis_summary,
                })

    return items


if "curated_items" not in st.session_state:
    st.session_state.curated_items = []


with st.expander("Open Existing Catalogue Package", expanded=False):
    catalogue_package = st.file_uploader(
        "Upload curated_stair_catalogue_package.zip",
        type=["zip"],
        key="catalogue_package_upload",
        help="Use this to view or continue a catalogue package that was downloaded earlier.",
    )

    if catalogue_package is not None:
        try:
            loaded_items = load_curated_package(catalogue_package.getvalue())
            if loaded_items:
                st.session_state.curated_items = loaded_items
                st.success(f"Loaded {len(loaded_items)} template(s) from catalogue package.")
            else:
                st.warning("The package was readable, but no templates were found.")
        except Exception as e:
            st.error(f"Could not open catalogue package: {e}")


left, right = st.columns([0.85, 1.15])

with left:
    st.markdown("### Add Stair Template")
    uploaded_dxf = st.file_uploader("Clean individual stair DXF", type=["dxf"])

    tax_1, tax_2 = st.columns(2)

    material = tax_1.selectbox(
        "Material",
        STAIRCASE_TAXONOMY["Materials"],
        index=0,
    )

    taxonomy_family = tax_2.selectbox(
        "Stair family",
        list(STAIRCASE_TAXONOMY["Families"].keys()),
        index=0,
    )

    family_data = STAIRCASE_TAXONOMY["Families"][taxonomy_family]

    tax_3, tax_4 = st.columns(2)

    stair_type = tax_3.selectbox(
        "Type",
        family_data["Types"],
        index=0,
    )

    symmetry = tax_4.selectbox(
        "Symmetry",
        family_data["Symmetry"],
        index=0,
    )

    structural_class = st.selectbox(
        "Structural class",
        STAIRCASE_TAXONOMY["Structural_Classes"],
        index=0,
    )

    family_id = taxonomy_family_id(taxonomy_family, stair_type)

    taxonomy_metadata = {
        "material": material,
        "family": taxonomy_family,
        "type": stair_type,
        "symmetry": symmetry,
        "structural_class": structural_class,
    }

    st.caption(f"Catalogue class: {FAMILY_OPTIONS.get(family_id, family_id)} | {structural_class}")

    template_name = st.text_input("Display name", value="")
    template_id = st.text_input(
        "Template ID",
        value=safe_filename(template_name.lower()) if template_name else "",
        help="Use a stable ID like dog_leg_residential_01.",
    )

    st.info(
        "Best catalogue DXF: one stair detail only, with dimension layers kept, reinforcement on its own layers, "
        "and geometry separated from notes/text where possible."
    )

    add_template = st.button("Add To Catalogue", type="primary", use_container_width=True)

with right:
    st.markdown("### Preview")

    analysis = None
    preview_svg = ""

    if uploaded_dxf is not None:
        try:
            dxf_bytes = uploaded_dxf.getvalue()
            analysis = analyse_dxf(dxf_bytes)
            preview_svg = render_svg(
                analysis["records"],
                analysis["bbox"],
                analysis["layer_roles"],
                width=1000,
                height=700,
            )
            components.html(preview_svg, height=720, scrolling=False)
        except Exception as e:
            st.error(f"Could not read DXF: {e}")

    if uploaded_dxf is None:
        st.info("Upload a clean single-stair DXF to preview it here.")

if analysis:
    st.markdown("### Template Intelligence")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Renderable records", len(analysis.get("records", [])))
    m2.metric("DXF dimensions", len(analysis.get("dimension_records", [])))
    m3.metric("Layers", len(analysis.get("layer_counts", {})))
    m4.metric("Parameter groups", len(analysis.get("dimension_parameters", {})))

    with st.expander("Layer roles for future calculator", expanded=True):
        layer_df = layers_to_dataframe(analysis)
        if layer_df.empty:
            st.info("No layer data found.")
        else:
            edited_layer_df = st.data_editor(
                layer_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "detected_role": st.column_config.SelectboxColumn(
                        "layer_role",
                        options=LAYER_ROLE_OPTIONS,
                        required=True,
                    )
                },
                disabled=["layer", "entity_count", "dxf_color", "linetype"],
                key="stair_layer_role_editor",
            )
            analysis["layer_roles"] = {
                str(row["layer"]): str(row["detected_role"])
                for _, row in edited_layer_df.iterrows()
            }
            preview_svg = render_svg(
                analysis["records"],
                analysis["bbox"],
                analysis["layer_roles"],
                width=1000,
                height=700,
            )
            st.download_button(
                "Download Layer Role CSV",
                data=edited_layer_df.to_csv(index=False).encode("utf-8"),
                file_name="ILS_STAIR_LAYER_ROLES.csv",
                mime="text/csv",
            )

    with st.expander("Detected dimensions and parameter hints", expanded=True):
        dimension_df = dimensions_to_dataframe(analysis)
        if dimension_df.empty:
            st.warning(
                "No DIMENSION entities were detected. If the dimensions are exploded into lines/text, they will preview, "
                "but the future calculator will not know their measurements automatically."
            )
        else:
            st.dataframe(dimension_df, use_container_width=True)
            st.download_button(
                "Download Dimension Parameters CSV",
                data=dimension_df.to_csv(index=False).encode("utf-8"),
                file_name="ILS_STAIR_DIMENSION_PARAMETERS.csv",
                mime="text/csv",
            )

if add_template:
    if uploaded_dxf is None:
        st.error("Upload a DXF first.")
    elif not template_name.strip() or not template_id.strip():
        st.error("Enter both display name and template ID.")
    elif not preview_svg or not analysis:
        st.error("Preview could not be generated.")
    else:
        template_id_clean = safe_filename(template_id)
        source_name = "source.dxf"
        preview_name = "preview.svg"
        analysis_summary = compact_analysis_summary(analysis)
        record = build_template_record(
            template_id=template_id_clean,
            name=template_name.strip(),
            family_id=family_id,
            source_filename=source_name,
            preview_filename=preview_name,
            analysis_summary=analysis_summary,
            taxonomy_metadata=taxonomy_metadata,
        )
        st.session_state.curated_items.append({
            "template_id": template_id_clean,
            "family_id": family_id,
            "record": record,
            "dxf_bytes": uploaded_dxf.getvalue(),
            "preview_svg": preview_svg,
            "analysis_summary": analysis_summary,
        })
        st.success(f"Added {template_name.strip()} to {family_display_name(family_id, record)}.")


st.markdown("### Current Catalogue")

if not st.session_state.curated_items:
    st.info("No templates added yet.")
else:
    rows = []
    for item in st.session_state.curated_items:
        summary = item.get("analysis_summary", {})
        record = item.get("record", {})
        taxonomy = record.get("taxonomy", {})
        rows.append({
            "template_id": item["template_id"],
            "family": family_display_name(item["family_id"], record),
            "type": taxonomy.get("type", record.get("stair_type", "")),
            "material": taxonomy.get("material", record.get("material", "")),
            "structural_class": taxonomy.get("structural_class", record.get("structural_class", "")),
            "symmetry": taxonomy.get("symmetry", record.get("symmetry", "")),
            "name": record["name"],
            "generator_type": record["generator_type"],
            "dimension_count": summary.get("dimension_count", 0),
            "layer_count": len(summary.get("layer_counts", {})),
        })

    table_tab, visual_tab, download_tab = st.tabs(["Catalogue Table", "Visual Catalogue", "Package"])

    with table_tab:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with visual_tab:
        available_families = sorted(set(item["family_id"] for item in st.session_state.curated_items))
        available_materials = sorted(set(
            item.get("record", {}).get("taxonomy", {}).get("material", item.get("record", {}).get("material", ""))
            for item in st.session_state.curated_items
            if item.get("record", {}).get("taxonomy", {}).get("material", item.get("record", {}).get("material", ""))
        ))
        available_structural_classes = sorted(set(
            item.get("record", {}).get("taxonomy", {}).get("structural_class", item.get("record", {}).get("structural_class", ""))
            for item in st.session_state.curated_items
            if item.get("record", {}).get("taxonomy", {}).get("structural_class", item.get("record", {}).get("structural_class", ""))
        ))

        v1, v2, v3 = st.columns(3)

        selected_family = v1.selectbox(
            "View family",
            ["All"] + available_families,
            format_func=lambda key: "All Families" if key == "All" else family_display_name(key),
            key="catalogue_view_family",
        )

        selected_material = v2.selectbox(
            "View material",
            ["All"] + available_materials,
            key="catalogue_view_material",
        )

        selected_structural_class = v3.selectbox(
            "View structural class",
            ["All"] + available_structural_classes,
            key="catalogue_view_structural_class",
        )

        visible_items = [
            item for item in st.session_state.curated_items
            if (
                (selected_family == "All" or item["family_id"] == selected_family)
                and (
                    selected_material == "All"
                    or item.get("record", {}).get("taxonomy", {}).get("material", item.get("record", {}).get("material", "")) == selected_material
                )
                and (
                    selected_structural_class == "All"
                    or item.get("record", {}).get("taxonomy", {}).get("structural_class", item.get("record", {}).get("structural_class", "")) == selected_structural_class
                )
            )
        ]

        if not visible_items:
            st.info("No templates in this family yet.")
        else:
            for start in range(0, len(visible_items), 2):
                cols = st.columns(2)
                for col, item in zip(cols, visible_items[start:start + 2]):
                    with col:
                        record = item.get("record", {})
                        summary = item.get("analysis_summary", {})
                        taxonomy = record.get("taxonomy", {})
                        st.markdown(f"#### {record.get('name', item['template_id'])}")
                        st.caption(
                            f"{family_display_name(item['family_id'], record)} | "
                            f"{taxonomy.get('material', record.get('material', ''))} | "
                            f"{taxonomy.get('structural_class', record.get('structural_class', ''))} | "
                            f"{summary.get('dimension_count', 0)} dimensions | "
                            f"{len(summary.get('layer_roles', {}))} layer roles"
                        )

                        if item.get("preview_svg"):
                            components.html(item["preview_svg"], height=360, scrolling=False)
                        else:
                            st.info("No preview SVG saved for this template.")

                        c1, c2 = st.columns(2)
                        if item.get("dxf_bytes"):
                            c1.download_button(
                                "Download source DXF",
                                data=item["dxf_bytes"],
                                file_name=f"{item['template_id']}.dxf",
                                mime="application/dxf",
                                key=f"source_{item['family_id']}_{item['template_id']}",
                                use_container_width=True,
                            )
                        c2.download_button(
                            "Download analysis JSON",
                            data=json.dumps(summary, indent=2).encode("utf-8"),
                            file_name=f"{item['template_id']}_analysis.json",
                            mime="application/json",
                            key=f"analysis_{item['family_id']}_{item['template_id']}",
                            use_container_width=True,
                        )

    with download_tab:
        package_bytes = build_curated_package(st.session_state.curated_items)
        st.download_button(
            "Download Curated Stair Catalogue Package",
            data=package_bytes,
            file_name="curated_stair_catalogue_package.zip",
            mime="application/zip",
            use_container_width=True,
        )
