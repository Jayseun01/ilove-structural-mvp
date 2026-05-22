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
st.caption("Admin tool for turning clean individual staircase DXFs into a visual family catalogue.")


FAMILY_OPTIONS = {
    "dog_leg": "Dog-leg Staircases",
    "straight": "Straight Flight Staircases",
    "l_shape": "L-shape Staircases",
    "spiral": "Spiral Staircases",
    "winder": "Winder Staircases",
    "reinforcement": "Reinforcement Details",
    "tread_section": "Tread Sections",
}

GENERATOR_BY_FAMILY = {
    "dog_leg": "Dog-leg stair",
    "straight": "Straight flight",
    "l_shape": "L-shape stair",
    "spiral": "Spiral stair",
    "winder": "Dog-leg stair",
    "reinforcement": "Dog-leg stair",
    "tread_section": "Straight flight",
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


def text_point(entity):
    try:
        ins = entity.dxf.insert
        return float(ins.x), float(ins.y)
    except Exception:
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
            height = float(getattr(entity.dxf, "height", 250.0) or 250.0)
            width = max(height * 2.0, len(text) * height * 0.55)
            return {"min_x": x, "min_y": y - height, "max_x": x + width, "max_y": y + height}

    except Exception:
        return None

    return None


def entity_to_record(entity):
    bbox = entity_bbox(entity)
    if bbox is None:
        return None

    record = {
        "type": entity.dxftype(),
        "layer": getattr(entity.dxf, "layer", ""),
        "bbox": bbox,
    }

    try:
        typ = entity.dxftype()
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
            record["height"] = float(getattr(entity.dxf, "height", 250.0) or 250.0)
    except Exception:
        pass

    return record


def analyse_dxf(data):
    doc = read_doc_from_bytes(data)
    msp = doc.modelspace()
    supported = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "TEXT", "MTEXT"}
    records = []
    entity_counts = {}
    layer_counts = {}

    for entity in msp:
        try:
            typ = entity.dxftype()
            layer = getattr(entity.dxf, "layer", "")
        except Exception:
            continue

        entity_counts[typ] = entity_counts.get(typ, 0) + 1
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

        if typ in supported:
            record = entity_to_record(entity)
            if record:
                records.append(record)

    bbox = bbox_union([record["bbox"] for record in records])
    if bbox:
        bbox = expand_bbox(bbox, ratio=0.04)

    return {
        "records": records,
        "bbox": bbox,
        "entity_counts": entity_counts,
        "layer_counts": layer_counts,
    }


def color_for_layer(layer):
    palette = ["#f8fafc", "#93c5fd", "#fbbf24", "#34d399", "#fb7185", "#c084fc", "#67e8f9"]
    return palette[abs(hash(layer or "")) % len(palette)]


def svg_xy(x, y, bbox, width, height, pad):
    sx = pad + (float(x) - bbox["min_x"]) / bbox_width(bbox) * (width - pad * 2)
    sy = pad + (bbox["max_y"] - float(y)) / bbox_height(bbox) * (height - pad * 2)
    return sx, sy


def render_svg(records, bbox, width=1000, height=700, max_records=6000):
    if not bbox:
        return ""

    pad = 24
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        'style="background:#0f172a;border-radius:8px;">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#0f172a"/>',
    ]

    for record in records[:max_records]:
        typ = record.get("type")
        color = color_for_layer(record.get("layer", ""))

        try:
            if typ == "LINE":
                x1, y1 = svg_xy(*record["start"], bbox, width, height, pad)
                x2, y2 = svg_xy(*record["end"], bbox, width, height, pad)
                parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="1"/>')
            elif typ in {"LWPOLYLINE", "POLYLINE"}:
                pts = ["{:.2f},{:.2f}".format(*svg_xy(x, y, bbox, width, height, pad)) for x, y in record.get("points", [])]
                if len(pts) >= 2:
                    if record.get("closed"):
                        pts.append(pts[0])
                    parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1"/>')
            elif typ == "CIRCLE":
                cx, cy = svg_xy(*record["center"], bbox, width, height, pad)
                scale = (width - pad * 2) / bbox_width(bbox)
                parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{record["radius"] * scale:.2f}" fill="none" stroke="{color}" stroke-width="1"/>')
            elif typ == "ARC":
                cx, cy = svg_xy(*record["center"], bbox, width, height, pad)
                scale = (width - pad * 2) / bbox_width(bbox)
                parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{record["radius"] * scale:.2f}" fill="none" stroke="{color}" stroke-width="1" stroke-dasharray="4 4"/>')
            elif typ in {"TEXT", "MTEXT"}:
                text = record.get("text", "")
                if text:
                    x, y = svg_xy(*record["point"], bbox, width, height, pad)
                    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    parts.append(f'<text x="{x:.2f}" y="{y:.2f}" fill="#e5e7eb" font-size="9" font-family="Arial">{safe[:90]}</text>')
        except Exception:
            continue

    parts.append("</svg>")
    return "\n".join(parts)


def build_template_record(template_id, name, family_id, source_filename, preview_filename):
    return {
        "id": template_id,
        "name": name,
        "family_id": family_id,
        "family_name": FAMILY_OPTIONS[family_id],
        "generator_type": GENERATOR_BY_FAMILY.get(family_id, "Dog-leg stair"),
        "source_dxf": f"curated_stair_assets/{family_id}/{template_id}/{source_filename}",
        "preview_svg": f"curated_stair_assets/{family_id}/{template_id}/{preview_filename}",
        "tags": [family_id],
        "default_inputs": {
            "floor_height": 3000,
            "stair_width": 1200,
            "target_riser": 175,
            "min_riser": 150,
            "max_riser": 190,
            "tread_depth": 300,
            "landing_length": 1200,
            "flight_gap": 200
        },
        "detailing_rules": {
            "riser_strategy": "auto_count_from_floor_height",
            "reinforcement_strategy": "family_rule_based",
            "notes": "Curated source template. Generated detail must be verified by a qualified engineer."
        }
    }


def build_curated_package(items):
    buffer = io.BytesIO()
    families = {}

    for item in items:
        families.setdefault(item["family_id"], {
            "id": item["family_id"],
            "name": FAMILY_OPTIONS[item["family_id"]],
            "templates": [],
        })
        families[item["family_id"]]["templates"].append(item["record"])

    manifest = {
        "version": "1.0",
        "families": list(families.values()),
    }

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("curated_stair_catalogue.json", json.dumps(manifest, indent=2).encode("utf-8"))
        for item in items:
            base = f"curated_stair_assets/{item['family_id']}/{item['template_id']}"
            zf.writestr(f"{base}/source.dxf", item["dxf_bytes"])
            zf.writestr(f"{base}/preview.svg", item["preview_svg"].encode("utf-8"))

    buffer.seek(0)
    return buffer.getvalue()


if "curated_items" not in st.session_state:
    st.session_state.curated_items = []


left, right = st.columns([0.85, 1.15])

with left:
    st.markdown("### Add Stair Template")
    uploaded_dxf = st.file_uploader("Clean individual stair DXF", type=["dxf"])

    family_id = st.selectbox(
        "Family",
        list(FAMILY_OPTIONS.keys()),
        format_func=lambda key: FAMILY_OPTIONS[key],
    )

    template_name = st.text_input("Display name", value="")
    template_id = st.text_input(
        "Template ID",
        value=safe_filename(template_name.lower()) if template_name else "",
        help="Use a stable ID like dog_leg_residential_01.",
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
            preview_svg = render_svg(analysis["records"], analysis["bbox"], width=1000, height=700)
            components.html(preview_svg, height=720, scrolling=False)
        except Exception as e:
            st.error(f"Could not read DXF: {e}")

    if uploaded_dxf is None:
        st.info("Upload a clean single-stair DXF to preview it here.")

if add_template:
    if uploaded_dxf is None:
        st.error("Upload a DXF first.")
    elif not template_name.strip() or not template_id.strip():
        st.error("Enter both display name and template ID.")
    elif not preview_svg:
        st.error("Preview could not be generated.")
    else:
        template_id_clean = safe_filename(template_id)
        source_name = "source.dxf"
        preview_name = "preview.svg"
        record = build_template_record(
            template_id=template_id_clean,
            name=template_name.strip(),
            family_id=family_id,
            source_filename=source_name,
            preview_filename=preview_name,
        )
        st.session_state.curated_items.append({
            "template_id": template_id_clean,
            "family_id": family_id,
            "record": record,
            "dxf_bytes": uploaded_dxf.getvalue(),
            "preview_svg": preview_svg,
        })
        st.success(f"Added {template_name.strip()} to {FAMILY_OPTIONS[family_id]}.")


st.markdown("### Current Catalogue")

if not st.session_state.curated_items:
    st.info("No templates added yet.")
else:
    rows = []
    for item in st.session_state.curated_items:
        rows.append({
            "template_id": item["template_id"],
            "family": FAMILY_OPTIONS[item["family_id"]],
            "name": item["record"]["name"],
            "generator_type": item["record"]["generator_type"],
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    package_bytes = build_curated_package(st.session_state.curated_items)
    st.download_button(
        "Download Curated Stair Catalogue Package",
        data=package_bytes,
        file_name="curated_stair_catalogue_package.zip",
        mime="application/zip",
        use_container_width=True,
    )
