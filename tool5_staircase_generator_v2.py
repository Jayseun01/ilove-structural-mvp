import datetime
import html
import io
import json
import math
import os
import re
import tempfile
import zipfile
from pathlib import Path

import ezdxf
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# =========================================================
# STREAMLIT CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - Staircase Detail Generator",
    page_icon=":building_construction:",
    layout="wide",
)

st.title("Staircase Detail Generator")
st.caption("Pick a staircase visually, enter floor height, then download the generated 2D and 3D outputs.")


# =========================================================
# ASSET PATHS
# =========================================================

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR / "tool5_catalogue_assets"
CATALOGUE_JSON = ASSET_DIR / "stair_catalogue.json"
CATALOGUE_ZIP = ASSET_DIR / "stair_catalogue_package.zip"


def find_catalogue_asset_paths():
    """
    Streamlit Cloud may run the app from a subfolder while the assets sit at repo root.
    Search a few safe repo locations before reporting that the catalogue is missing.
    """

    roots = []
    for root in [APP_DIR, APP_DIR.parent, Path.cwd(), Path.cwd().parent]:
        try:
            resolved = root.resolve()
        except Exception:
            continue
        if resolved not in roots:
            roots.append(resolved)

    for root in roots:
        candidate_dirs = [
            root / "tool5_catalogue_assets",
            root,
        ]
        for directory in candidate_dirs:
            json_path = directory / "stair_catalogue.json"
            zip_path = directory / "stair_catalogue_package.zip"
            if json_path.exists() and zip_path.exists():
                return json_path, zip_path

    for root in roots:
        try:
            json_matches = list(root.rglob("stair_catalogue.json"))
        except Exception:
            json_matches = []

        for json_path in json_matches:
            possible_zips = [
                json_path.parent / "stair_catalogue_package.zip",
                json_path.parent.parent / "stair_catalogue_package.zip",
                json_path.parent.parent / "tool5_catalogue_assets" / "stair_catalogue_package.zip",
            ]
            for zip_path in possible_zips:
                if zip_path.exists():
                    return json_path, zip_path

    return None, None


# =========================================================
# BASIC HELPERS
# =========================================================

def safe_filename(value, default="stair_detail"):
    clean = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or ""))
    clean = "_".join(part for part in clean.split("_") if part)
    return clean.strip("._") or default


def mm(value):
    return float(value)


def bbox_from_points(points):
    if not points:
        return {"min_x": 0.0, "min_y": 0.0, "max_x": 1.0, "max_y": 1.0}

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }


def expand_bbox(bbox, pad):
    return {
        "min_x": bbox["min_x"] - pad,
        "min_y": bbox["min_y"] - pad,
        "max_x": bbox["max_x"] + pad,
        "max_y": bbox["max_y"] + pad,
    }


def bbox_width(bbox):
    return max(1.0, bbox["max_x"] - bbox["min_x"])


def bbox_height(bbox):
    return max(1.0, bbox["max_y"] - bbox["min_y"])


def rotate_point(x, y, cx, cy, angle_deg):
    angle = math.radians(angle_deg)
    dx = x - cx
    dy = y - cy
    return (
        cx + dx * math.cos(angle) - dy * math.sin(angle),
        cy + dx * math.sin(angle) + dy * math.cos(angle),
    )


def box_plan_corners(step):
    x = step["x"]
    y = step["y"]
    dx = step["dx"]
    dy = step["dy"]
    rotation = float(step.get("rotation", 0.0))
    cx = x + dx / 2.0
    cy = y + dy / 2.0
    corners = [
        (x, y),
        (x + dx, y),
        (x + dx, y + dy),
        (x, y + dy),
    ]

    if abs(rotation) <= 1e-9:
        return corners

    return [rotate_point(px, py, cx, cy, rotation) for px, py in corners]


# =========================================================
# CATALOGUE LOADING
# =========================================================

def zip_source_to_file(zip_source):
    if isinstance(zip_source, (bytes, bytearray)):
        return io.BytesIO(zip_source)
    return zip_source


@st.cache_data(show_spinner=False)
def load_catalogue(catalogue_source, zip_source):
    if isinstance(catalogue_source, (bytes, bytearray)):
        catalogue = json.loads(catalogue_source.decode("utf-8"))
    else:
        with open(catalogue_source, "r", encoding="utf-8") as f:
            catalogue = json.load(f)

    entries = []
    with zipfile.ZipFile(zip_source_to_file(zip_source), "r") as zf:
        entries = zf.namelist()

    return catalogue, entries


def find_package_entry(entries, folder, item_id, item_name, extension):
    exact_stem = safe_filename(f"{item_id}_{item_name}")
    exact = f"{folder}/{exact_stem}.{extension}"
    if exact in entries:
        return exact

    prefix = f"{folder}/{item_id}_"
    suffix = f".{extension}"
    matches = [entry for entry in entries if entry.startswith(prefix) and entry.endswith(suffix)]
    return matches[0] if matches else None


@st.cache_data(show_spinner=False)
def read_package_text(zip_source, entry_name):
    if not entry_name:
        return ""
    with zipfile.ZipFile(zip_source_to_file(zip_source), "r") as zf:
        return zf.read(entry_name).decode("utf-8", errors="replace")


@st.cache_data(show_spinner=False)
def read_package_bytes(zip_source, entry_name):
    if not entry_name:
        return b""
    with zipfile.ZipFile(zip_source_to_file(zip_source), "r") as zf:
        return zf.read(entry_name)


def catalogue_dataframe(catalogue):
    rows = []
    for item in catalogue:
        rows.append({
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "stair_type": item.get("stair_type", ""),
            "source_title": item.get("source_title", ""),
            "generator_status": item.get("generator_status", ""),
        })
    return pd.DataFrame(rows)


def readable_stair_type(value):
    labels = {
        "staircase_detail": "Stair detail",
        "staircase_1": "Staircase 1",
        "staircase_2": "Staircase 2",
        "reinforcement_detail": "Reinforcement",
        "spiral": "Spiral",
        "tread_section": "Tread section",
    }
    return labels.get(str(value or ""), str(value or "Other").replace("_", " ").title())


def fit_source_svg(svg, height=220):
    if not svg:
        return ""

    fitted = re.sub(r'\swidth="[^"]+"', ' width="100%"', svg, count=1)
    fitted = re.sub(r'\sheight="[^"]+"', f' height="{int(height)}"', fitted, count=1)
    if "preserveAspectRatio" not in fitted[:250]:
        fitted = fitted.replace("<svg ", '<svg preserveAspectRatio="xMidYMid meet" ', 1)
    return fitted


def default_generator_type(stair_type):
    if stair_type == "spiral":
        return "Spiral stair"
    if stair_type in {"staircase_1", "staircase_2", "staircase_detail"}:
        return "Dog-leg stair"
    return "Straight flight"


# =========================================================
# STAIR CALCULATION
# =========================================================

def choose_risers(floor_height, target_riser, min_riser, max_riser):
    floor_height = max(1.0, float(floor_height))
    target_riser = max(1.0, float(target_riser))
    min_riser = max(1.0, float(min_riser))
    max_riser = max(min_riser, float(max_riser))

    low_count = max(1, int(math.ceil(floor_height / max_riser)))
    high_count = max(low_count, int(math.floor(floor_height / min_riser)))

    candidates = []
    for count in range(low_count, high_count + 1):
        riser = floor_height / count
        candidates.append((abs(riser - target_riser), count, riser))

    if candidates:
        _, count, riser = min(candidates, key=lambda item: item[0])
    else:
        count = max(1, int(round(floor_height / target_riser)))
        riser = floor_height / count

    warnings = []
    if riser < min_riser or riser > max_riser:
        warnings.append(
            f"Computed riser {round(riser, 1)} mm is outside the selected range "
            f"{round(min_riser, 1)}-{round(max_riser, 1)} mm."
        )

    return count, riser, warnings


def add_rect_shape(shapes, x1, y1, x2, y2, layer="outline"):
    shapes.append({
        "type": "polyline",
        "points": [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
        "closed": True,
        "layer": layer,
    })


def add_line_shape(shapes, x1, y1, x2, y2, layer="treads"):
    shapes.append({
        "type": "line",
        "start": (x1, y1),
        "end": (x2, y2),
        "layer": layer,
    })


def add_text_shape(shapes, text, x, y, height=180.0):
    shapes.append({
        "type": "text",
        "text": text,
        "point": (x, y),
        "height": height,
        "layer": "text",
    })


def add_step_box(steps, x, y, dx, dy, height, rotation=0.0, name="step"):
    if dx <= 0 or dy <= 0 or height <= 0:
        return
    steps.append({
        "name": name,
        "x": float(x),
        "y": float(y),
        "dx": float(dx),
        "dy": float(dy),
        "height": float(height),
        "rotation": float(rotation),
    })


def straight_layout(params):
    floor_height = params["floor_height"]
    width = params["stair_width"]
    tread = params["tread_depth"]
    risers = params["riser_count"]
    riser = params["riser_height"]
    treads = max(1, risers - 1)
    run = treads * tread

    shapes = []
    steps = []

    add_rect_shape(shapes, 0, 0, run, width, "outline")
    for i in range(1, treads):
        x = i * tread
        add_line_shape(shapes, x, 0, x, width, "treads")
    add_line_shape(shapes, tread * 0.5, width * 0.5, run - tread * 0.5, width * 0.5, "arrow")
    add_text_shape(shapes, f"UP {risers}R @ {riser:.1f}", tread, width + 350)

    for i in range(treads):
        add_step_box(
            steps,
            x=i * tread,
            y=0,
            dx=tread,
            dy=width,
            height=(i + 1) * riser,
            name=f"step_{i + 1}",
        )

    points = [(0, 0), (run, width), (run, 0), (0, width)]
    bbox = expand_bbox(bbox_from_points(points), 800.0)
    return shapes, steps, bbox, {
        "flight_count": 1,
        "treads": treads,
        "total_going": run,
        "landing_count": 0,
    }


def dog_leg_layout(params):
    floor_height = params["floor_height"]
    width = params["stair_width"]
    tread = params["tread_depth"]
    landing = params["landing_length"]
    gap = params["flight_gap"]
    risers = params["riser_count"]
    riser = params["riser_height"]

    lower_risers = max(1, risers // 2)
    upper_risers = max(1, risers - lower_risers)
    lower_treads = max(1, lower_risers - 1)
    upper_treads = max(1, upper_risers - 1)
    run1 = lower_treads * tread
    run2 = upper_treads * tread

    shapes = []
    steps = []

    upper_y = width + gap
    min_x = min(0.0, run1 - run2)
    max_x = run1 + landing
    total_y = width * 2.0 + gap

    add_rect_shape(shapes, 0, 0, run1, width, "outline")
    add_rect_shape(shapes, run1, 0, run1 + landing, total_y, "landing")
    add_rect_shape(shapes, run1 - run2, upper_y, run1, upper_y + width, "outline")

    for i in range(1, lower_treads):
        x = i * tread
        add_line_shape(shapes, x, 0, x, width, "treads")

    for i in range(1, upper_treads):
        x = run1 - i * tread
        add_line_shape(shapes, x, upper_y, x, upper_y + width, "treads")

    add_line_shape(shapes, tread * 0.5, width * 0.5, run1 - tread * 0.5, width * 0.5, "arrow")
    add_line_shape(shapes, run1 - tread * 0.5, upper_y + width * 0.5, run1 - run2 + tread * 0.5, upper_y + width * 0.5, "arrow")
    add_text_shape(shapes, f"UP {risers}R @ {riser:.1f}", min_x, total_y + 350)

    for i in range(lower_treads):
        add_step_box(steps, i * tread, 0, tread, width, (i + 1) * riser, name=f"lower_step_{i + 1}")

    landing_height = lower_risers * riser
    add_step_box(steps, run1, 0, landing, total_y, landing_height, name="half_landing")

    for i in range(upper_treads):
        x = run1 - (i + 1) * tread
        add_step_box(
            steps,
            x,
            upper_y,
            tread,
            width,
            (lower_risers + i + 1) * riser,
            name=f"upper_step_{i + 1}",
        )

    bbox = expand_bbox(bbox_from_points([(min_x, 0), (max_x, total_y)]), 800.0)
    return shapes, steps, bbox, {
        "flight_count": 2,
        "lower_treads": lower_treads,
        "upper_treads": upper_treads,
        "total_going": run1 + run2,
        "landing_count": 1,
    }


def l_shape_layout(params):
    width = params["stair_width"]
    tread = params["tread_depth"]
    landing = params["landing_length"]
    risers = params["riser_count"]
    riser = params["riser_height"]

    lower_risers = max(1, risers // 2)
    upper_risers = max(1, risers - lower_risers)
    lower_treads = max(1, lower_risers - 1)
    upper_treads = max(1, upper_risers - 1)
    run1 = lower_treads * tread
    run2 = upper_treads * tread

    shapes = []
    steps = []

    add_rect_shape(shapes, 0, 0, run1, width, "outline")
    add_rect_shape(shapes, run1, 0, run1 + landing, landing, "landing")
    add_rect_shape(shapes, run1, landing, run1 + width, landing + run2, "outline")

    for i in range(1, lower_treads):
        x = i * tread
        add_line_shape(shapes, x, 0, x, width, "treads")

    for i in range(1, upper_treads):
        y = landing + i * tread
        add_line_shape(shapes, run1, y, run1 + width, y, "treads")

    add_line_shape(shapes, tread * 0.5, width * 0.5, run1 - tread * 0.5, width * 0.5, "arrow")
    add_line_shape(shapes, run1 + width * 0.5, landing + tread * 0.5, run1 + width * 0.5, landing + run2 - tread * 0.5, "arrow")
    add_text_shape(shapes, f"UP {risers}R @ {riser:.1f}", 0, landing + run2 + 350)

    for i in range(lower_treads):
        add_step_box(steps, i * tread, 0, tread, width, (i + 1) * riser, name=f"lower_step_{i + 1}")

    landing_height = lower_risers * riser
    add_step_box(steps, run1, 0, landing, landing, landing_height, name="quarter_landing")

    for i in range(upper_treads):
        y = landing + i * tread
        add_step_box(steps, run1, y, width, tread, (lower_risers + i + 1) * riser, name=f"upper_step_{i + 1}")

    bbox = expand_bbox(bbox_from_points([(0, 0), (run1 + landing, landing + run2)]), 800.0)
    return shapes, steps, bbox, {
        "flight_count": 2,
        "lower_treads": lower_treads,
        "upper_treads": upper_treads,
        "total_going": run1 + run2,
        "landing_count": 1,
    }


def spiral_layout(params):
    floor_height = params["floor_height"]
    width = params["stair_width"]
    tread = params["tread_depth"]
    risers = params["riser_count"]
    riser = params["riser_height"]
    inner_radius = params["inner_radius"]
    turn_degrees = params["turn_degrees"]
    outer_radius = inner_radius + width
    avg_radius = (inner_radius + outer_radius) / 2.0
    step_angle = turn_degrees / max(1, risers)

    shapes = []
    steps = []

    shapes.append({"type": "circle", "center": (0.0, 0.0), "radius": inner_radius, "layer": "outline"})
    shapes.append({"type": "circle", "center": (0.0, 0.0), "radius": outer_radius, "layer": "outline"})

    for i in range(risers + 1):
        angle = math.radians(i * step_angle)
        add_line_shape(
            shapes,
            inner_radius * math.cos(angle),
            inner_radius * math.sin(angle),
            outer_radius * math.cos(angle),
            outer_radius * math.sin(angle),
            "treads",
        )

    add_text_shape(shapes, f"SPIRAL {risers}R @ {riser:.1f}", -outer_radius, outer_radius + 350)

    tangent_depth = max(tread, avg_radius * math.radians(abs(step_angle)) * 0.80)
    for i in range(risers):
        angle_deg = i * step_angle + step_angle * 0.5
        angle = math.radians(angle_deg)
        cx = avg_radius * math.cos(angle)
        cy = avg_radius * math.sin(angle)
        add_step_box(
            steps,
            cx - width / 2.0,
            cy - tangent_depth / 2.0,
            width,
            tangent_depth,
            (i + 1) * riser,
            rotation=angle_deg + 90.0,
            name=f"spiral_step_{i + 1}",
        )

    bbox = expand_bbox(
        {
            "min_x": -outer_radius,
            "min_y": -outer_radius,
            "max_x": outer_radius,
            "max_y": outer_radius,
        },
        800.0,
    )
    return shapes, steps, bbox, {
        "flight_count": 1,
        "treads": risers,
        "total_going": avg_radius * math.radians(abs(turn_degrees)),
        "landing_count": 0,
    }


def generate_layout(generator_type, values):
    riser_count, riser_height, warnings = choose_risers(
        values["floor_height"],
        values["target_riser"],
        values["min_riser"],
        values["max_riser"],
    )

    params = dict(values)
    params["riser_count"] = riser_count
    params["riser_height"] = riser_height

    if generator_type == "Straight flight":
        shapes, steps, bbox, extra = straight_layout(params)
    elif generator_type == "L-shape stair":
        shapes, steps, bbox, extra = l_shape_layout(params)
    elif generator_type == "Spiral stair":
        shapes, steps, bbox, extra = spiral_layout(params)
    else:
        shapes, steps, bbox, extra = dog_leg_layout(params)

    tread_count = max(1, riser_count - 1)
    summary = {
        "generator_type": generator_type,
        "floor_height": values["floor_height"],
        "riser_count": riser_count,
        "riser_height": riser_height,
        "tread_depth": values["tread_depth"],
        "typical_tread_count": tread_count,
        "stair_width": values["stair_width"],
        "warnings": warnings,
        **extra,
    }

    return {
        "summary": summary,
        "params": params,
        "shapes": shapes,
        "steps": steps,
        "bbox": bbox,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


# =========================================================
# SVG / DXF / OBJ OUTPUT
# =========================================================

SHAPE_COLORS = {
    "outline": "#f8fafc",
    "landing": "#93c5fd",
    "treads": "#fbbf24",
    "arrow": "#34d399",
    "section": "#f8fafc",
    "rebar": "#fb7185",
    "dimension": "#a7f3d0",
    "text": "#f8fafc",
}


def svg_xy(x, y, bbox, width, height, pad):
    sx = pad + (x - bbox["min_x"]) / bbox_width(bbox) * (width - pad * 2.0)
    sy = pad + (bbox["max_y"] - y) / bbox_height(bbox) * (height - pad * 2.0)
    return sx, sy


def shapes_svg(shapes, bbox, width=720, height=460):
    pad = 28.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'style="background:#0f172a;border-radius:8px;">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#0f172a"/>',
    ]

    for shape in shapes:
        layer = shape.get("layer", "outline")
        color = SHAPE_COLORS.get(layer, "#e5e7eb")

        if shape["type"] == "line":
            x1, y1 = svg_xy(*shape["start"], bbox, width, height, pad)
            x2, y2 = svg_xy(*shape["end"], bbox, width, height, pad)
            stroke_width = 2 if layer in {"outline", "arrow", "section", "rebar"} else 1
            parts.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="{color}" stroke-width="{stroke_width}"/>'
            )

        elif shape["type"] == "polyline":
            pts = [
                "{:.2f},{:.2f}".format(*svg_xy(x, y, bbox, width, height, pad))
                for x, y in shape.get("points", [])
            ]
            if pts:
                fill = "rgba(147,197,253,0.18)" if layer == "landing" else "none"
                close = " ".join(pts + [pts[0]]) if shape.get("closed") else " ".join(pts)
                parts.append(
                    f'<polyline points="{close}" fill="{fill}" stroke="{color}" stroke-width="2"/>'
                )

        elif shape["type"] == "circle":
            cx, cy = svg_xy(*shape["center"], bbox, width, height, pad)
            scale = (width - pad * 2.0) / bbox_width(bbox)
            r = max(1.0, shape["radius"] * scale)
            parts.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="none" stroke="{color}" stroke-width="2"/>'
            )

        elif shape["type"] == "text":
            x, y = svg_xy(*shape["point"], bbox, width, height, pad)
            text = html.escape(shape.get("text", ""))
            parts.append(
                f'<text x="{x:.2f}" y="{y:.2f}" fill="{color}" font-size="13" font-family="Arial">{text}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def generated_plan_svg(layout, width=720, height=460):
    return shapes_svg(layout["shapes"], layout["bbox"], width=width, height=height)


def build_detail_sheet(layout):
    plan_shapes = [dict(shape) for shape in layout["shapes"]]
    summary = layout["summary"]
    params = layout["params"]

    floor_height = float(summary["floor_height"])
    riser_count = int(summary["riser_count"])
    riser_height = float(summary["riser_height"])
    tread_depth = float(summary["tread_depth"])
    stair_width = float(summary["stair_width"])
    total_going = max(float(summary.get("total_going", 0.0)), tread_depth * max(1, riser_count - 1))
    treads = max(1, riser_count - 1)

    section_x0 = layout["bbox"]["min_x"]
    section_y0 = layout["bbox"]["min_y"] - floor_height - 2400.0
    section_x1 = section_x0 + total_going
    section_y1 = section_y0 + floor_height

    detail_shapes = []
    add_text_shape(
        detail_shapes,
        f"Generated stair detail - {summary['generator_type']}",
        layout["bbox"]["min_x"],
        layout["bbox"]["max_y"] + 550.0,
        height=220.0,
    )

    add_text_shape(
        detail_shapes,
        f"{riser_count} risers @ {riser_height:.1f} mm | going {tread_depth:.0f} mm | width {stair_width:.0f} mm",
        layout["bbox"]["min_x"],
        layout["bbox"]["max_y"] + 250.0,
        height=170.0,
    )

    stair_points = [(section_x0, section_y0)]
    tread_run = total_going / treads
    for i in range(treads):
        x_next = section_x0 + (i + 1) * tread_run
        y_current = section_y0 + i * riser_height
        y_next = section_y0 + (i + 1) * riser_height
        stair_points.append((x_next, y_current))
        stair_points.append((x_next, y_next))

    if stair_points[-1][1] < section_y1:
        stair_points.append((section_x1, section_y1))

    detail_shapes.append({
        "type": "polyline",
        "points": stair_points,
        "closed": False,
        "layer": "section",
    })

    waist_offset = max(175.0, floor_height * 0.045)
    add_line_shape(detail_shapes, section_x0, section_y0 - waist_offset, section_x1, section_y1 - waist_offset, "section")
    add_line_shape(detail_shapes, section_x0, section_y0 + 110.0, section_x1, section_y1 + 110.0, "rebar")
    add_line_shape(detail_shapes, section_x0, section_y0 - 500.0, section_x1, section_y0 - 500.0, "dimension")
    add_line_shape(detail_shapes, section_x1 + 450.0, section_y0, section_x1 + 450.0, section_y1, "dimension")

    main_bar_count = max(3, int(math.ceil(stair_width / 150.0)) + 1)
    distribution_spacing = 200 if riser_count <= 18 else 175
    landing_bar_count = max(4, int(math.ceil(float(params.get("landing_length", 1200.0)) / 200.0)))
    extra_top_bars = max(2, int(math.ceil(riser_count / 8.0)))

    note_x = section_x0
    note_y = section_y0 - 1050.0
    add_text_shape(detail_shapes, f"Main waist bars: {main_bar_count}Y12 continuous along flight", note_x, note_y, 160.0)
    add_text_shape(detail_shapes, f"Distribution bars: Y10 @ {distribution_spacing} c/c", note_x, note_y - 260.0, 160.0)
    add_text_shape(detail_shapes, f"Landing/top support bars: {landing_bar_count}Y12 + {extra_top_bars} extra top bars", note_x, note_y - 520.0, 160.0)
    add_text_shape(detail_shapes, "Reinforcement shown is preliminary; verify by structural design.", note_x, note_y - 780.0, 150.0)

    all_shapes = plan_shapes + detail_shapes
    all_points = []
    for shape in all_shapes:
        if shape["type"] == "line":
            all_points.extend([shape["start"], shape["end"]])
        elif shape["type"] == "polyline":
            all_points.extend(shape.get("points", []))
        elif shape["type"] == "circle":
            cx, cy = shape["center"]
            r = shape["radius"]
            all_points.extend([(cx - r, cy - r), (cx + r, cy + r)])
        elif shape["type"] == "text":
            all_points.append(shape["point"])

    return {
        "shapes": all_shapes,
        "bbox": expand_bbox(bbox_from_points(all_points), 900.0),
        "reinforcement": {
            "main_bar_count": main_bar_count,
            "distribution_spacing": distribution_spacing,
            "landing_bar_count": landing_bar_count,
            "extra_top_bars": extra_top_bars,
        },
    }


def generated_detail_svg(layout, width=860, height=620):
    detail = build_detail_sheet(layout)
    return shapes_svg(detail["shapes"], detail["bbox"], width=width, height=height)


def safe_layer(doc, name, color=7):
    if name not in [layer.dxf.name for layer in doc.layers]:
        doc.layers.new(name=name, dxfattribs={"color": color})


def write_doc_to_bytes(doc):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp_path = tmp.name
    tmp.close()

    try:
        doc.saveas(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def build_plan_dxf(layout):
    doc = ezdxf.new()
    msp = doc.modelspace()
    safe_layer(doc, "ILS_STAIR_OUTLINE", color=7)
    safe_layer(doc, "ILS_STAIR_TREADS", color=2)
    safe_layer(doc, "ILS_STAIR_LANDING", color=4)
    safe_layer(doc, "ILS_STAIR_ARROW", color=3)
    safe_layer(doc, "ILS_STAIR_SECTION", color=7)
    safe_layer(doc, "ILS_STAIR_REBAR", color=1)
    safe_layer(doc, "ILS_STAIR_DIMENSION", color=3)
    safe_layer(doc, "ILS_STAIR_TEXT", color=7)

    layer_map = {
        "outline": "ILS_STAIR_OUTLINE",
        "treads": "ILS_STAIR_TREADS",
        "landing": "ILS_STAIR_LANDING",
        "arrow": "ILS_STAIR_ARROW",
        "section": "ILS_STAIR_SECTION",
        "rebar": "ILS_STAIR_REBAR",
        "dimension": "ILS_STAIR_DIMENSION",
        "text": "ILS_STAIR_TEXT",
    }

    detail = build_detail_sheet(layout)

    for shape in detail["shapes"]:
        layer = layer_map.get(shape.get("layer", "outline"), "ILS_STAIR_OUTLINE")

        if shape["type"] == "line":
            msp.add_line(shape["start"], shape["end"], dxfattribs={"layer": layer})
        elif shape["type"] == "polyline":
            msp.add_lwpolyline(shape.get("points", []), close=bool(shape.get("closed")), dxfattribs={"layer": layer})
        elif shape["type"] == "circle":
            msp.add_circle(shape["center"], shape["radius"], dxfattribs={"layer": layer})
        elif shape["type"] == "text":
            text = str(shape.get("text", ""))[:255]
            entity = msp.add_text(text, dxfattribs={"layer": "ILS_STAIR_TEXT", "height": shape.get("height", 180.0)})
            try:
                entity.dxf.insert = shape["point"]
            except Exception:
                pass

    return write_doc_to_bytes(doc)


def add_obj_box(lines, step, vertex_index):
    corners = box_plan_corners(step)
    h = step["height"]
    vertices = []
    for x, y in corners:
        vertices.append((x, y, 0.0))
    for x, y in corners:
        vertices.append((x, y, h))

    for x, y, z in vertices:
        lines.append(f"v {x:.3f} {y:.3f} {z:.3f}")

    i = vertex_index
    faces = [
        (i, i + 1, i + 2, i + 3),
        (i + 4, i + 7, i + 6, i + 5),
        (i, i + 4, i + 5, i + 1),
        (i + 1, i + 5, i + 6, i + 2),
        (i + 2, i + 6, i + 7, i + 3),
        (i + 3, i + 7, i + 4, i),
    ]
    for face in faces:
        lines.append("f " + " ".join(str(v) for v in face))

    return vertex_index + 8


def build_obj(layout):
    lines = [
        "# iLoveStructural Tool 5 generated staircase model",
        "o ILS_Staircase",
    ]
    vertex_index = 1
    for step in layout["steps"]:
        lines.append(f"g {safe_filename(step.get('name', 'step'))}")
        vertex_index = add_obj_box(lines, step, vertex_index)
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_calc_json(layout, selected_item):
    detail = build_detail_sheet(layout)
    payload = {
        "source_catalogue_item": selected_item,
        "generated_layout": {
            "summary": layout["summary"],
            "params": layout["params"],
            "reinforcement": detail["reinforcement"],
            "created_at": layout["created_at"],
        },
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def rotating_3d_html(layout, width=720, height=520):
    data = {
        "steps": layout["steps"],
        "bbox": layout["bbox"],
        "summary": layout["summary"],
    }
    data_json = json.dumps(data)
    return f"""
<div id="stair3d" style="width:100%;height:{height}px;background:#020617;border-radius:8px;overflow:hidden;"></div>
<script src="https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js"></script>
<script>
const data = {data_json};
const container = document.getElementById("stair3d");
const width = container.clientWidth || {width};
const height = {height};
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x020617);

const camera = new THREE.PerspectiveCamera(45, width / height, 1, 200000);
const bw = Math.max(1, data.bbox.max_x - data.bbox.min_x);
const bh = Math.max(1, data.bbox.max_y - data.bbox.min_y);
const maxDim = Math.max(bw, bh, data.summary.floor_height || 3000);
camera.position.set(maxDim * 1.45, maxDim * 1.20, maxDim * 1.65);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setSize(width, height);
container.appendChild(renderer.domElement);

const hemi = new THREE.HemisphereLight(0xffffff, 0x1f2937, 1.4);
scene.add(hemi);
const dir = new THREE.DirectionalLight(0xffffff, 1.2);
dir.position.set(maxDim, maxDim, maxDim);
scene.add(dir);

const group = new THREE.Group();
scene.add(group);

const material = new THREE.MeshStandardMaterial({{
  color: 0x60a5fa,
  metalness: 0.08,
  roughness: 0.52
}});
const landingMaterial = new THREE.MeshStandardMaterial({{
  color: 0x93c5fd,
  metalness: 0.05,
  roughness: 0.62
}});

for (const step of data.steps) {{
  const geom = new THREE.BoxGeometry(step.dx, step.height, step.dy);
  const mat = step.name && step.name.includes("landing") ? landingMaterial : material;
  const mesh = new THREE.Mesh(geom, mat);
  mesh.position.set(step.x + step.dx / 2, step.height / 2, -(step.y + step.dy / 2));
  mesh.rotation.y = -((step.rotation || 0) * Math.PI / 180);
  group.add(mesh);

  const edge = new THREE.EdgesGeometry(geom);
  const line = new THREE.LineSegments(edge, new THREE.LineBasicMaterial({{ color: 0xe5e7eb, transparent: true, opacity: 0.35 }}));
  line.position.copy(mesh.position);
  line.rotation.copy(mesh.rotation);
  group.add(line);
}}

const centerX = (data.bbox.min_x + data.bbox.max_x) / 2;
const centerY = (data.bbox.min_y + data.bbox.max_y) / 2;
group.position.set(-centerX, 0, centerY);

const padGeom = new THREE.CylinderGeometry(maxDim * 0.70, maxDim * 0.70, 35, 96);
const padMat = new THREE.MeshStandardMaterial({{ color: 0x111827, roughness: 0.7 }});
const pad = new THREE.Mesh(padGeom, padMat);
pad.position.y = -25;
scene.add(pad);

function animate() {{
  requestAnimationFrame(animate);
  group.rotation.y += 0.008;
  renderer.render(scene, camera);
}}
animate();
</script>
"""


# =========================================================
# UI
# =========================================================

catalogue_json_path, catalogue_zip_path = find_catalogue_asset_paths()
catalogue_source = str(catalogue_json_path) if catalogue_json_path else ""
zip_source = str(catalogue_zip_path) if catalogue_zip_path else ""

if not catalogue_json_path or not catalogue_zip_path:
    st.error(
        "The staircase catalogue is not installed with this app. "
        "Please contact the app administrator."
    )
    with st.expander("Administrator setup", expanded=False):
        st.code(
            "tool5_catalogue_assets/\n"
            "    stair_catalogue.json\n"
            "    stair_catalogue_package.zip",
            language="text",
        )
        st.write("App folder checked:", str(APP_DIR))
        st.write("Working folder checked:", str(Path.cwd()))
    st.stop()

try:
    catalogue, package_entries = load_catalogue(catalogue_source, zip_source)
except Exception as e:
    st.error("The staircase catalogue could not be loaded. Please contact the app administrator.")
    with st.expander("Technical error", expanded=False):
        st.write(str(e))
    st.stop()

catalogue_df = catalogue_dataframe(catalogue)

st.markdown("### Choose A Design")

filter_col, search_col, page_col = st.columns([1.1, 1.7, 0.8])
type_options = ["All"] + sorted(catalogue_df["stair_type"].dropna().unique().tolist())
type_filter = filter_col.selectbox(
    "Filter",
    type_options,
    format_func=lambda value: "All designs" if value == "All" else readable_stair_type(value),
)
search_text = search_col.text_input("Search", value="", placeholder="Search by ID, title, or type")

filtered_items = catalogue
if type_filter != "All":
    filtered_items = [item for item in filtered_items if item.get("stair_type") == type_filter]
if search_text.strip():
    q = search_text.strip().lower()
    filtered_items = [
        item for item in filtered_items
        if q in item.get("id", "").lower()
        or q in item.get("name", "").lower()
        or q in item.get("source_title", "").lower()
    ]

if not filtered_items:
    st.warning("No catalogue items match the current filter.")
    st.stop()

if (
    "selected_catalogue_id" not in st.session_state
    or not any(item.get("id") == st.session_state.selected_catalogue_id for item in catalogue)
):
    st.session_state.selected_catalogue_id = filtered_items[0].get("id")

if not any(item.get("id") == st.session_state.selected_catalogue_id for item in filtered_items):
    st.session_state.selected_catalogue_id = filtered_items[0].get("id")

page_size = 9
page_count = max(1, math.ceil(len(filtered_items) / page_size))
page = int(
    page_col.number_input(
        "Page",
        min_value=1,
        max_value=page_count,
        value=1,
        step=1,
        key=f"catalogue_page_{safe_filename(type_filter)}_{len(filtered_items)}",
    )
)
start = (page - 1) * page_size
page_items = filtered_items[start:start + page_size]

for row_start in range(0, len(page_items), 3):
    cols = st.columns(3)
    for offset, col in enumerate(cols):
        idx = row_start + offset
        if idx >= len(page_items):
            continue

        item = page_items[idx]
        item_id = item.get("id", "")
        item_name = item.get("name", "")
        item_type = readable_stair_type(item.get("stair_type", ""))
        is_selected = item_id == st.session_state.selected_catalogue_id
        item_preview_entry = find_package_entry(
            package_entries,
            "candidate_previews",
            item_id,
            item_name,
            "svg",
        )

        with col:
            if item_preview_entry:
                thumb_svg = fit_source_svg(read_package_text(zip_source, item_preview_entry), height=210)
                components.html(thumb_svg, height=225, scrolling=False)
            else:
                st.empty()

            st.markdown(f"**{item_type}**")
            st.caption(item_id)
            button_label = "Selected" if is_selected else "Use this design"
            if st.button(button_label, key=f"use_catalogue_{item_id}", use_container_width=True):
                st.session_state.selected_catalogue_id = item_id

selected_item = next(
    item for item in catalogue
    if item.get("id") == st.session_state.selected_catalogue_id
)

preview_entry = find_package_entry(
    package_entries,
    "candidate_previews",
    selected_item["id"],
    selected_item["name"],
    "svg",
)
dxf_entry = find_package_entry(
    package_entries,
    "candidate_dxfs",
    selected_item["id"],
    selected_item["name"],
    "dxf",
)

st.markdown("### Selected Design")
selected_left, selected_right = st.columns([1.25, 0.75])

with selected_left:
    if preview_entry:
        source_svg = fit_source_svg(read_package_text(zip_source, preview_entry), height=420)
        components.html(source_svg, height=440, scrolling=False)
    else:
        st.warning("No source preview was found for this catalogue item.")

with selected_right:
    st.metric("Design", selected_item.get("id", ""))
    st.metric("Type", readable_stair_type(selected_item.get("stair_type", "")))
    if dxf_entry:
        st.download_button(
            "Download Source DXF",
            data=read_package_bytes(zip_source, dxf_entry),
            file_name=f"{safe_filename(selected_item['id'] + '_' + selected_item['name'])}_source.dxf",
            mime="application/dxf",
            use_container_width=True,
        )

with st.expander("Technical catalogue table", expanded=False):
    st.dataframe(catalogue_df, use_container_width=True)


st.markdown("### Generate Stair")

default_type = default_generator_type(selected_item.get("stair_type", ""))
generator_types = ["Straight flight", "Dog-leg stair", "L-shape stair", "Spiral stair"]
default_type_index = generator_types.index(default_type) if default_type in generator_types else 1

settings_a, settings_b, settings_c = st.columns(3)

generator_type = settings_a.selectbox(
    "Generator type",
    generator_types,
    index=default_type_index,
)
floor_height = settings_a.number_input("Floor-to-floor height", min_value=300.0, value=3000.0, step=25.0)
stair_width = settings_a.number_input("Stair width", min_value=600.0, value=1200.0, step=50.0)

target_riser = settings_b.number_input("Target riser", min_value=100.0, value=175.0, step=5.0)
min_riser = settings_b.number_input("Minimum riser", min_value=80.0, value=150.0, step=5.0)
max_riser = settings_b.number_input("Maximum riser", min_value=100.0, value=190.0, step=5.0)

tread_depth = settings_c.number_input("Tread/going depth", min_value=150.0, value=300.0, step=10.0)
landing_length = settings_c.number_input("Landing length", min_value=600.0, value=1200.0, step=50.0)
flight_gap = settings_c.number_input("Dog-leg flight gap", min_value=0.0, value=200.0, step=25.0)

spiral_col1, spiral_col2 = st.columns(2)
inner_radius = spiral_col1.number_input("Spiral inner radius", min_value=100.0, value=450.0, step=50.0)
turn_degrees = spiral_col2.number_input("Spiral total turn degrees", min_value=90.0, value=360.0, step=15.0)

values = {
    "floor_height": mm(floor_height),
    "stair_width": mm(stair_width),
    "target_riser": mm(target_riser),
    "min_riser": mm(min_riser),
    "max_riser": mm(max_riser),
    "tread_depth": mm(tread_depth),
    "landing_length": mm(landing_length),
    "flight_gap": mm(flight_gap),
    "inner_radius": mm(inner_radius),
    "turn_degrees": mm(turn_degrees),
}

layout = generate_layout(generator_type, values)
summary = layout["summary"]

s1, s2, s3, s4, s5 = st.columns(5)
s1.metric("Risers", summary["riser_count"])
s2.metric("Riser height", f"{summary['riser_height']:.1f} mm")
s3.metric("Tread depth", f"{summary['tread_depth']:.0f} mm")
s4.metric("Total going", f"{summary['total_going']:.0f} mm")
s5.metric("Flights", summary["flight_count"])

for warning in summary.get("warnings", []):
    st.warning(warning)

preview_2d, preview_3d = st.columns([1, 1])

with preview_2d:
    st.caption("Generated 2D detail preview")
    components.html(generated_detail_svg(layout), height=640, scrolling=False)

with preview_3d:
    st.caption("Generated rotating 3D preview")
    components.html(rotating_3d_html(layout), height=540, scrolling=False)


st.markdown("### 4. Download Generated Outputs")

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
base_name = safe_filename(f"{selected_item.get('id')}_{generator_type}_{int(floor_height)}mm_{timestamp}")

plan_dxf = build_plan_dxf(layout)
obj_model = build_obj(layout)
calc_json = build_calc_json(layout, selected_item)

d1, d2, d3 = st.columns(3)
d1.download_button(
    "Download Generated 2D Detail DXF",
    data=plan_dxf,
    file_name=f"{base_name}_detail.dxf",
    mime="application/dxf",
)
d2.download_button(
    "Download Generated 3D OBJ",
    data=obj_model,
    file_name=f"{base_name}_model.obj",
    mime="text/plain",
)
d3.download_button(
    "Download Stair Calculation JSON",
    data=calc_json,
    file_name=f"{base_name}_calculation.json",
    mime="application/json",
)

st.success("Ready.")
