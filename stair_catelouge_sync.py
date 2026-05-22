import datetime
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


# =========================================================
# STREAMLIT CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - Staircase Detail Catalogue",
    page_icon=":building_construction:",
    layout="wide",
)

st.title("iLoveStructural")
st.subheader("Tool 5: Staircase Detail Catalogue Extractor")
st.caption(
    "DXF staircase detail library -> detected catalogue candidates -> reviewed stair templates."
)

st.info(
    "This first version extracts staircase detail candidates from an existing DXF catalogue. "
    "After the catalogue is clean, the next version will generate parametric 2D details and 3D stair models from floor height."
)


# =========================================================
# FILE HELPERS
# =========================================================

DEFAULT_LOCAL_DXF = (
    r"C:\Users\HP\Downloads\38833FF26BA1D.UnigramPreview_g9c9v27vpyspw!App"
    r"\STAIRCASE DETAILS.dxf"
)


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def save_bytes_to_temp_dxf(data):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(data)
        return tmp.name


def read_doc_from_bytes(data):
    tmp_path = None
    try:
        tmp_path = save_bytes_to_temp_dxf(data)
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


def safe_filename(value, default="stair_detail"):
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return text or default


# =========================================================
# TEXT HELPERS
# =========================================================

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


def get_entity_text(entity):
    try:
        if entity.dxftype() == "TEXT":
            return clean_dxf_text(entity.dxf.text)
        if entity.dxftype() == "MTEXT":
            return clean_dxf_text(entity.text)
    except Exception:
        pass
    return ""


def text_point(entity):
    try:
        ins = entity.dxf.insert
        return float(ins.x), float(ins.y)
    except Exception:
        return 0.0, 0.0


def is_primary_stair_label(text, regex):
    cleaned = clean_dxf_text(text).upper()

    if not cleaned:
        return False

    if cleaned in {"PLAN", "DETAILS", "SECTION"}:
        return False

    try:
        return bool(re.search(regex, cleaned, flags=re.IGNORECASE))
    except Exception:
        return False


def infer_stair_type(text):
    value = clean_dxf_text(text).upper()

    if "SPIRAL" in value:
        return "spiral"
    if "DOG" in value:
        return "dog_leg"
    if "STAIRCASE 1" in value or "STAIR CASE 1" in value:
        return "staircase_1"
    if "STAIRCASE 2" in value or "STAIR CASE 2" in value:
        return "staircase_2"
    if "TREAD" in value:
        return "tread_section"
    if "REINFORCEMENT" in value or "REBAR" in value:
        return "reinforcement_detail"
    if "SECTION" in value:
        return "section"
    return "staircase_detail"


# =========================================================
# GEOMETRY HELPERS
# =========================================================

def bbox_union(bboxes):
    clean = [b for b in bboxes if b is not None]

    if not clean:
        return None

    return {
        "min_x": min(b["min_x"] for b in clean),
        "min_y": min(b["min_y"] for b in clean),
        "max_x": max(b["max_x"] for b in clean),
        "max_y": max(b["max_y"] for b in clean),
    }


def bbox_width(bbox):
    return max(0.0, float(bbox["max_x"]) - float(bbox["min_x"]))


def bbox_height(bbox):
    return max(0.0, float(bbox["max_y"]) - float(bbox["min_y"]))


def bbox_intersects(a, b):
    return not (
        a["max_x"] < b["min_x"]
        or a["min_x"] > b["max_x"]
        or a["max_y"] < b["min_y"]
        or a["min_y"] > b["max_y"]
    )


def bbox_center(bbox):
    return (
        (float(bbox["min_x"]) + float(bbox["max_x"])) / 2.0,
        (float(bbox["min_y"]) + float(bbox["max_y"])) / 2.0,
    )


def point_bbox(x, y, pad=1.0):
    return {
        "min_x": float(x) - pad,
        "min_y": float(y) - pad,
        "max_x": float(x) + pad,
        "max_y": float(y) + pad,
    }


def entity_bbox(entity):
    try:
        dxftype = entity.dxftype()

        if dxftype == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            return bbox_union([
                point_bbox(float(s.x), float(s.y), 0.0),
                point_bbox(float(e.x), float(e.y), 0.0),
            ])

        if dxftype == "LWPOLYLINE":
            points = [(float(p[0]), float(p[1])) for p in entity.get_points()]
            return bbox_union([point_bbox(x, y, 0.0) for x, y in points])

        if dxftype == "POLYLINE":
            points = []
            for vertex in entity.vertices:
                loc = vertex.dxf.location
                points.append((float(loc.x), float(loc.y)))
            return bbox_union([point_bbox(x, y, 0.0) for x, y in points])

        if dxftype == "CIRCLE":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            return {
                "min_x": float(c.x) - r,
                "min_y": float(c.y) - r,
                "max_x": float(c.x) + r,
                "max_y": float(c.y) + r,
            }

        if dxftype == "ARC":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            return {
                "min_x": float(c.x) - r,
                "min_y": float(c.y) - r,
                "max_x": float(c.x) + r,
                "max_y": float(c.y) + r,
            }

        if dxftype in ("TEXT", "MTEXT"):
            x, y = text_point(entity)
            text = get_entity_text(entity)
            height = float(getattr(entity.dxf, "height", 250.0) or 250.0)
            width = max(height * 2.0, len(text) * height * 0.55)
            return {
                "min_x": x,
                "min_y": y - height,
                "max_x": x + width,
                "max_y": y + height,
            }

        if dxftype == "INSERT":
            ins = entity.dxf.insert
            return point_bbox(float(ins.x), float(ins.y), 100.0)

        if dxftype == "DIMENSION":
            try:
                p = entity.dxf.defpoint
                return point_bbox(float(p.x), float(p.y), 500.0)
            except Exception:
                return None

    except Exception:
        return None

    return None


def make_region_from_label(label, left, right, down, up):
    x = float(label["x"])
    y = float(label["y"])
    return {
        "min_x": x - float(left),
        "max_x": x + float(right),
        "min_y": y - float(down),
        "max_y": y + float(up),
    }


def region_area(region):
    return bbox_width(region) * bbox_height(region)


def overlap_area(a, b):
    x1 = max(a["min_x"], b["min_x"])
    x2 = min(a["max_x"], b["max_x"])
    y1 = max(a["min_y"], b["min_y"])
    y2 = min(a["max_y"], b["max_y"])

    if x2 <= x1 or y2 <= y1:
        return 0.0

    return (x2 - x1) * (y2 - y1)


def dedupe_candidates(candidates, overlap_ratio=0.80):
    kept = []

    for candidate in sorted(candidates, key=lambda c: (c["bbox"]["min_y"], c["bbox"]["min_x"])):
        duplicate = False

        for existing in kept:
            area = min(region_area(candidate["bbox"]), region_area(existing["bbox"]))
            if area <= 0:
                continue
            if overlap_area(candidate["bbox"], existing["bbox"]) / area >= overlap_ratio:
                existing["nearby_titles"].append(candidate["title"])
                duplicate = True
                break

        if not duplicate:
            kept.append(candidate)

    for idx, candidate in enumerate(kept, start=1):
        candidate["id"] = f"ST-{idx:03d}"
        candidate["name"] = f"{candidate['stair_type'].replace('_', ' ').title()} {idx:02d}"

    return kept


# =========================================================
# DXF ANALYSIS
# =========================================================

SUPPORTED_PREVIEW_TYPES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "CIRCLE",
    "ARC",
    "TEXT",
    "MTEXT",
}


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
        dxftype = entity.dxftype()

        if dxftype == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            record.update({
                "start": (float(s.x), float(s.y)),
                "end": (float(e.x), float(e.y)),
            })

        elif dxftype == "LWPOLYLINE":
            record["points"] = [(float(p[0]), float(p[1])) for p in entity.get_points()]
            record["closed"] = bool(entity.closed)

        elif dxftype == "POLYLINE":
            points = []
            for vertex in entity.vertices:
                loc = vertex.dxf.location
                points.append((float(loc.x), float(loc.y)))
            record["points"] = points
            record["closed"] = bool(entity.is_closed)

        elif dxftype == "CIRCLE":
            c = entity.dxf.center
            record.update({
                "center": (float(c.x), float(c.y)),
                "radius": float(entity.dxf.radius),
            })

        elif dxftype == "ARC":
            c = entity.dxf.center
            record.update({
                "center": (float(c.x), float(c.y)),
                "radius": float(entity.dxf.radius),
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
            })

        elif dxftype in ("TEXT", "MTEXT"):
            record.update({
                "text": get_entity_text(entity),
                "point": text_point(entity),
                "height": float(getattr(entity.dxf, "height", 250.0) or 250.0),
            })

    except Exception:
        pass

    return record


@st.cache_data(show_spinner=False)
def analyze_dxf_bytes(data, label_regex, crop_left, crop_right, crop_down, crop_up, max_candidates):
    doc = read_doc_from_bytes(data)
    msp = doc.modelspace()

    entity_counts = {}
    layer_counts = {}
    labels = []
    records = []

    for entity in msp:
        try:
            dxftype = entity.dxftype()
            layer = getattr(entity.dxf, "layer", "")
        except Exception:
            continue

        entity_counts[dxftype] = entity_counts.get(dxftype, 0) + 1
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

        if dxftype in SUPPORTED_PREVIEW_TYPES:
            record = entity_to_record(entity)
            if record:
                records.append(record)

        if dxftype in ("TEXT", "MTEXT"):
            text = get_entity_text(entity)
            if is_primary_stair_label(text, label_regex):
                x, y = text_point(entity)
                labels.append({
                    "text": text,
                    "x": x,
                    "y": y,
                    "layer": layer,
                    "type": dxftype,
                    "stair_type": infer_stair_type(text),
                })

    candidates = []

    for label in labels:
        bbox = make_region_from_label(
            label,
            left=crop_left,
            right=crop_right,
            down=crop_down,
            up=crop_up,
        )
        entities_in_region = [r for r in records if bbox_intersects(r["bbox"], bbox)]
        nearby_titles = [
            l["text"]
            for l in labels
            if bbox["min_x"] <= l["x"] <= bbox["max_x"] and bbox["min_y"] <= l["y"] <= bbox["max_y"]
        ]
        candidate = {
            "id": "",
            "title": label["text"],
            "name": "",
            "stair_type": label["stair_type"],
            "label_x": label["x"],
            "label_y": label["y"],
            "label_layer": label["layer"],
            "bbox": bbox,
            "entity_count": len(entities_in_region),
            "nearby_titles": nearby_titles,
        }
        candidates.append(candidate)

    candidates = dedupe_candidates(candidates)
    candidates = candidates[: int(max_candidates)]

    return {
        "entity_counts": entity_counts,
        "layer_counts": layer_counts,
        "labels": labels,
        "candidates": candidates,
        "records": records,
    }


# =========================================================
# PREVIEW / EXPORT
# =========================================================

LAYER_COLORS = [
    "#d8dee9",
    "#88c0d0",
    "#a3be8c",
    "#ebcb8b",
    "#bf616a",
    "#b48ead",
    "#5e81ac",
    "#e5e9f0",
]


def color_for_layer(layer):
    idx = abs(hash(layer or "")) % len(LAYER_COLORS)
    return LAYER_COLORS[idx]


def region_records(records, bbox, limit=2500):
    selected = [r for r in records if bbox_intersects(r["bbox"], bbox)]
    return selected[:limit]


def svg_point(x, y, bbox, width, height, pad):
    bw = max(1e-9, bbox_width(bbox))
    bh = max(1e-9, bbox_height(bbox))
    sx = pad + (float(x) - bbox["min_x"]) / bw * (width - pad * 2)
    sy = pad + (bbox["max_y"] - float(y)) / bh * (height - pad * 2)
    return sx, sy


def render_svg_preview(records, bbox, width=520, height=360, max_records=1800):
    selected = region_records(records, bbox, limit=max_records)
    pad = 18
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" style="background:#111827;border-radius:8px;">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#111827"/>',
    ]

    for record in selected:
        color = color_for_layer(record.get("layer", ""))
        typ = record.get("type")

        try:
            if typ == "LINE":
                x1, y1 = svg_point(*record["start"], bbox, width, height, pad)
                x2, y2 = svg_point(*record["end"], bbox, width, height, pad)
                parts.append(
                    f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                    f'stroke="{color}" stroke-width="1"/>'
                )

            elif typ in ("LWPOLYLINE", "POLYLINE"):
                points = record.get("points", [])
                if len(points) >= 2:
                    pts = [
                        "{:.2f},{:.2f}".format(*svg_point(x, y, bbox, width, height, pad))
                        for x, y in points
                    ]
                    close = "Z" if record.get("closed") else ""
                    parts.append(
                        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" '
                        f'stroke-width="1"/>'
                    )
                    if close and len(pts) > 2:
                        parts.append(
                            f'<line x1="{pts[-1].split(",")[0]}" y1="{pts[-1].split(",")[1]}" '
                            f'x2="{pts[0].split(",")[0]}" y2="{pts[0].split(",")[1]}" '
                            f'stroke="{color}" stroke-width="1"/>'
                        )

            elif typ == "CIRCLE":
                cx, cy = svg_point(*record["center"], bbox, width, height, pad)
                scale = (width - pad * 2) / max(1e-9, bbox_width(bbox))
                r = max(1.0, float(record["radius"]) * scale)
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
                    f'fill="none" stroke="{color}" stroke-width="1"/>'
                )

            elif typ == "ARC":
                cx, cy = svg_point(*record["center"], bbox, width, height, pad)
                scale = (width - pad * 2) / max(1e-9, bbox_width(bbox))
                r = max(1.0, float(record["radius"]) * scale)
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
                    f'fill="none" stroke="{color}" stroke-width="1" stroke-dasharray="4 3"/>'
                )

            elif typ in ("TEXT", "MTEXT"):
                text = record.get("text", "")
                if text:
                    x, y = svg_point(*record["point"], bbox, width, height, pad)
                    safe_text = (
                        text.replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    parts.append(
                        f'<text x="{x:.2f}" y="{y:.2f}" fill="#f9fafb" '
                        f'font-size="8" font-family="Arial">{safe_text[:80]}</text>'
                    )

        except Exception:
            continue

    parts.append("</svg>")
    return "\n".join(parts)


def build_candidate_rows(candidates):
    rows = []

    for candidate in candidates:
        bbox = candidate["bbox"]
        rows.append({
            "approve": True,
            "id": candidate["id"],
            "catalogue_name": candidate["name"],
            "stair_type": candidate["stair_type"],
            "source_title": candidate["title"],
            "entity_count": candidate["entity_count"],
            "label_x": round(candidate["label_x"], 3),
            "label_y": round(candidate["label_y"], 3),
            "min_x": round(bbox["min_x"], 3),
            "min_y": round(bbox["min_y"], 3),
            "max_x": round(bbox["max_x"], 3),
            "max_y": round(bbox["max_y"], 3),
            "nearby_titles": " | ".join(candidate.get("nearby_titles", [])[:6]),
        })

    return rows


def approved_catalogue_from_editor(editor_rows, candidates, source_name):
    by_id = {c["id"]: c for c in candidates}
    catalogue = []

    if isinstance(editor_rows, pd.DataFrame):
        rows = editor_rows.to_dict("records")
    else:
        rows = list(editor_rows)

    for row in rows:
        if not row.get("approve"):
            continue

        candidate = by_id.get(row.get("id"))
        if not candidate:
            continue

        item = {
            "id": row.get("id"),
            "name": row.get("catalogue_name") or candidate["name"],
            "stair_type": row.get("stair_type") or candidate["stair_type"],
            "source_title": candidate["title"],
            "source_file": source_name,
            "bbox": candidate["bbox"],
            "label": {
                "x": candidate["label_x"],
                "y": candidate["label_y"],
                "layer": candidate["label_layer"],
            },
            "nearby_titles": candidate.get("nearby_titles", []),
            "status": "approved_for_catalogue_review",
            "generator_status": "needs_parametric_mapping",
        }
        catalogue.append(item)

    return catalogue


def add_record_to_dxf(msp, record, origin_x, origin_y):
    layer = record.get("layer") or "0"
    typ = record.get("type")

    try:
        if typ == "LINE":
            x1, y1 = record["start"]
            x2, y2 = record["end"]
            msp.add_line((x1 - origin_x, y1 - origin_y), (x2 - origin_x, y2 - origin_y), dxfattribs={"layer": layer})

        elif typ in ("LWPOLYLINE", "POLYLINE"):
            points = [(x - origin_x, y - origin_y) for x, y in record.get("points", [])]
            if len(points) >= 2:
                msp.add_lwpolyline(points, close=bool(record.get("closed")), dxfattribs={"layer": layer})

        elif typ == "CIRCLE":
            cx, cy = record["center"]
            msp.add_circle((cx - origin_x, cy - origin_y), record["radius"], dxfattribs={"layer": layer})

        elif typ == "ARC":
            cx, cy = record["center"]
            msp.add_arc(
                (cx - origin_x, cy - origin_y),
                record["radius"],
                record.get("start_angle", 0.0),
                record.get("end_angle", 360.0),
                dxfattribs={"layer": layer},
            )

        elif typ in ("TEXT", "MTEXT"):
            text = record.get("text", "")
            if not text:
                return
            x, y = record["point"]
            height = max(50.0, float(record.get("height", 250.0)))
            text_entity = msp.add_text(text[:255], dxfattribs={"layer": layer, "height": height})
            try:
                text_entity.dxf.insert = (x - origin_x, y - origin_y)
            except Exception:
                pass
    except Exception:
        pass


def candidate_dxf_bytes(candidate, records):
    bbox = candidate["bbox"]
    doc = ezdxf.new()
    msp = doc.modelspace()

    for record in region_records(records, bbox, limit=5000):
        add_record_to_dxf(msp, record, bbox["min_x"], bbox["min_y"])

    return write_doc_to_bytes(doc)


def build_catalogue_zip(catalogue, candidates, records):
    candidate_by_id = {c["id"]: c for c in candidates}
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stair_catalogue.json", json.dumps(catalogue, indent=2).encode("utf-8"))

        for item in catalogue:
            candidate = candidate_by_id.get(item["id"])
            if not candidate:
                continue
            name = safe_filename(f"{item['id']}_{item['name']}")
            zf.writestr(f"candidate_dxfs/{name}.dxf", candidate_dxf_bytes(candidate, records))
            zf.writestr(
                f"candidate_previews/{name}.svg",
                render_svg_preview(records, candidate["bbox"], width=900, height=650).encode("utf-8"),
            )

    buffer.seek(0)
    return buffer.getvalue()


# =========================================================
# UI
# =========================================================

st.markdown("### 1. Source DXF")

source_mode = st.radio(
    "Source",
    ["Upload DXF", "Use local DXF path"],
    horizontal=True,
)

uploaded_file = None
local_path = DEFAULT_LOCAL_DXF
source_name = ""
source_bytes = None

if source_mode == "Upload DXF":
    uploaded_file = st.file_uploader("Upload staircase details DXF", type=["dxf"])
    if uploaded_file is not None:
        source_bytes = uploaded_file.getvalue()
        source_name = uploaded_file.name
else:
    local_path = st.text_input("Local DXF path", value=DEFAULT_LOCAL_DXF)
    if local_path and os.path.exists(local_path):
        with open(local_path, "rb") as f:
            source_bytes = f.read()
        source_name = os.path.basename(local_path)
        st.success(f"Loaded local DXF: {local_path}")
    else:
        st.warning("Local DXF path not found.")

if source_bytes is None:
    st.stop()

st.write({
    "source_file": source_name,
    "file_size_mb": round(len(source_bytes) / (1024 * 1024), 2),
})


st.markdown("### 2. Extraction Settings")

with st.expander("Stair label detection", expanded=True):
    d1, d2 = st.columns(2)

    label_regex = d1.text_input(
        "Candidate label regex",
        value=r"STAIR|SPIRAL|TREAD SECTION|RISER|LANDING|WAIST|REINFORCEMENT",
        help="The extractor uses matching text labels as catalogue anchors.",
    )

    max_candidates = d2.number_input(
        "Maximum candidates",
        min_value=1,
        value=80,
        step=5,
    )

with st.expander("Crop window around each label", expanded=True):
    c1, c2, c3, c4 = st.columns(4)

    crop_left = c1.number_input("Left of label", min_value=0.0, value=12000.0, step=500.0)
    crop_right = c2.number_input("Right of label", min_value=0.0, value=18000.0, step=500.0)
    crop_down = c3.number_input("Below label", min_value=0.0, value=14000.0, step=500.0)
    crop_up = c4.number_input("Above label", min_value=0.0, value=5000.0, step=500.0)

with st.expander("Preview settings", expanded=False):
    p1, p2, p3 = st.columns(3)
    preview_count = p1.number_input("Preview candidates", min_value=1, value=12, step=1)
    preview_width = p2.number_input("Preview width", min_value=300, value=520, step=20)
    preview_height = p3.number_input("Preview height", min_value=220, value=360, step=20)

analyze = st.button("Extract Staircase Catalogue Candidates", type="primary")

if not analyze and "tool5_analysis" not in st.session_state:
    st.stop()

if analyze:
    with st.spinner("Reading DXF and detecting staircase labels..."):
        st.session_state.tool5_analysis = analyze_dxf_bytes(
            source_bytes,
            label_regex,
            crop_left,
            crop_right,
            crop_down,
            crop_up,
            max_candidates,
        )
        st.session_state.tool5_source_name = source_name

analysis = st.session_state.tool5_analysis
records = analysis["records"]
candidates = analysis["candidates"]


st.markdown("### 3. Detection Summary")

s1, s2, s3, s4 = st.columns(4)
s1.metric("Detected labels", len(analysis["labels"]))
s2.metric("Catalogue candidates", len(candidates))
s3.metric("Previewable entities", len(records))
s4.metric("Modelspace entity types", len(analysis["entity_counts"]))

summary_tabs = st.tabs(["Candidates", "Entity Counts", "Top Layers", "Preview"])

with summary_tabs[0]:
    candidate_rows = build_candidate_rows(candidates)

    if not candidate_rows:
        st.warning("No staircase catalogue candidates were found. Relax the regex or adjust the source file.")
        st.stop()

    edited_rows = st.data_editor(
        candidate_rows,
        use_container_width=True,
        hide_index=True,
        key="stair_catalogue_candidate_editor",
        column_config={
            "approve": st.column_config.CheckboxColumn("Approve?", default=True),
            "catalogue_name": st.column_config.TextColumn("Catalogue Name"),
            "stair_type": st.column_config.TextColumn("Stair Type"),
        },
        disabled=[
            "id",
            "source_title",
            "entity_count",
            "label_x",
            "label_y",
            "min_x",
            "min_y",
            "max_x",
            "max_y",
            "nearby_titles",
        ],
    )

with summary_tabs[1]:
    entity_df = pd.DataFrame(
        [
            {"entity_type": k, "count": v}
            for k, v in sorted(analysis["entity_counts"].items(), key=lambda item: item[1], reverse=True)
        ]
    )
    st.dataframe(entity_df, use_container_width=True)

with summary_tabs[2]:
    layer_df = pd.DataFrame(
        [
            {"layer": k, "count": v}
            for k, v in sorted(analysis["layer_counts"].items(), key=lambda item: item[1], reverse=True)
        ]
    )
    st.dataframe(layer_df, use_container_width=True)

with summary_tabs[3]:
    if not candidates:
        st.info("No previews available.")
    else:
        for row_index in range(0, min(int(preview_count), len(candidates)), 2):
            cols = st.columns(2)
            for offset, col in enumerate(cols):
                idx = row_index + offset
                if idx >= len(candidates) or idx >= int(preview_count):
                    continue
                candidate = candidates[idx]
                with col:
                    st.write(f"**{candidate['id']} - {candidate['title']}**")
                    st.caption(
                        f"{candidate['stair_type']} | entities: {candidate['entity_count']} | "
                        f"label: ({round(candidate['label_x'], 1)}, {round(candidate['label_y'], 1)})"
                    )
                    svg = render_svg_preview(
                        records,
                        candidate["bbox"],
                        width=int(preview_width),
                        height=int(preview_height),
                    )
                    components.html(svg, height=int(preview_height) + 12)


st.markdown("### 4. Export Reviewed Catalogue")

approved_catalogue = approved_catalogue_from_editor(
    edited_rows,
    candidates,
    st.session_state.get("tool5_source_name", source_name),
)

e1, e2, e3 = st.columns(3)
e1.metric("Approved catalogue items", len(approved_catalogue))
e2.metric("Needs parametric mapping", len(approved_catalogue))
e3.metric("Generated at", datetime.datetime.now().strftime("%H:%M"))

catalogue_json = json.dumps(approved_catalogue, indent=2).encode("utf-8")

st.download_button(
    "Download Stair Catalogue JSON",
    data=catalogue_json,
    file_name="stair_catalogue.json",
    mime="application/json",
)

if approved_catalogue:
    with st.spinner("Preparing approved candidate DXF/SVG ZIP..."):
        zip_bytes = build_catalogue_zip(approved_catalogue, candidates, records)

    st.download_button(
        "Download Catalogue Package ZIP",
        data=zip_bytes,
        file_name="stair_catalogue_package.zip",
        mime="application/zip",
    )

st.success(
    "Catalogue extraction is ready. The next build will map approved catalogue items to parametric stair generators for floor-height-driven 2D and 3D output."
)

