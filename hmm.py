import streamlit as st
import ezdxf
import tempfile
import os
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ============================================================
# APP CONFIG
# ============================================================

st.set_page_config(
    page_title="iLoveStructural - Beam Rebar Healer",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ iLoveStructural")
st.subheader("Beam Rebar Healer for Broken DXF Reinforcement")
st.caption(
    "Workflow: Upload DXF → select rebar/text layers → heal broken bars → apply 12m lapping rule → download healed DXF."
)


# ============================================================
# CONSTANTS
# ============================================================

ALL_LAYERS = "__ALL_LAYERS__"


# ============================================================
# TEMP FILE HELPERS
# ============================================================

def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def save_uploaded_to_temp(uploaded_file):
    """
    Streamlit uploaded files are bytes-like.
    ezdxf.readfile() is more reliable than ezdxf.read(BytesIO(...))
    because DXF is text-based and ezdxf.read() expects a text stream.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name


def read_uploaded_dxf(uploaded_file):
    tmp_path = None

    try:
        tmp_path = save_uploaded_to_temp(uploaded_file)
        return ezdxf.readfile(tmp_path)

    finally:
        safe_remove_file(tmp_path)


def write_doc_to_temp_bytes(doc):
    """
    Reliable DXF export for Streamlit download_button.
    """
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


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


# ============================================================
# GEOMETRY DATA CLASSES
# ============================================================

@dataclass
class HorizontalSegment:
    entity: object
    x1: float
    x2: float
    y: float


@dataclass
class VerticalHook:
    entity: object
    x: float
    y1: float
    y2: float


@dataclass
class BarRun:
    y: float
    x1: float
    x2: float
    segments: List[HorizontalSegment]
    support_xs: List[float]


# ============================================================
# BASIC GEOMETRY HELPERS
# ============================================================

def safe_delete(msp, entity):
    try:
        msp.delete_entity(entity)
    except Exception:
        pass


def line_points(line):
    start = line.dxf.start
    end = line.dxf.end
    return start, end


def is_horizontal_line(line, axis_tol):
    start, end = line_points(line)
    return abs(float(start.y) - float(end.y)) <= axis_tol and abs(float(start.x) - float(end.x)) > axis_tol


def is_vertical_line(line, axis_tol):
    start, end = line_points(line)
    return abs(float(start.x) - float(end.x)) <= axis_tol and abs(float(start.y) - float(end.y)) > axis_tol


def normalize_horizontal(line):
    start, end = line_points(line)

    x1 = min(float(start.x), float(end.x))
    x2 = max(float(start.x), float(end.x))
    y = (float(start.y) + float(end.y)) / 2.0

    return HorizontalSegment(
        entity=line,
        x1=x1,
        x2=x2,
        y=y,
    )


def normalize_vertical(line):
    start, end = line_points(line)

    x = (float(start.x) + float(end.x)) / 2.0
    y1 = min(float(start.y), float(end.y))
    y2 = max(float(start.y), float(end.y))

    return VerticalHook(
        entity=line,
        x=x,
        y1=y1,
        y2=y2,
    )


def interval_gap(a1, a2, b1, b2):
    if a2 < b1:
        return b1 - a2

    if b2 < a1:
        return a1 - b2

    return 0.0


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    txt = str(value)
    txt = txt.replace("\\P", " ")
    txt = txt.replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt)

    return txt.strip().upper()


def text_plain_value(entity):
    try:
        if entity.dxftype() == "TEXT":
            return clean_text(entity.dxf.text)

        if entity.dxftype() == "MTEXT":
            return clean_text(entity.text)

    except Exception:
        pass

    return ""


def text_insert_point(entity):
    try:
        if entity.dxftype() in ("TEXT", "MTEXT"):
            p = entity.dxf.insert
            return float(p.x), float(p.y)

    except Exception:
        pass

    return 0.0, 0.0


def get_text_height(entity, default_height=250.0):
    try:
        return float(entity.dxf.height)
    except Exception:
        return default_height


def looks_like_rebar_label(value):
    """
    Examples:
        2Y16
        2 Y16
        2-Y16
        3T20
        4T25
        2Y16-01
    """
    value = clean_text(value).replace(" ", "")
    return re.match(r"^\d+[-]?[YT]\d+", value) is not None


# ============================================================
# BAR GROUPING AND HOOK DETECTION
# ============================================================

def cluster_segments_by_y(segments, axis_tol):
    clusters = []

    for seg in sorted(segments, key=lambda s: s.y):
        placed = False

        for idx, item in enumerate(clusters):
            rep_y, bucket = item

            if abs(seg.y - rep_y) <= axis_tol:
                bucket.append(seg)
                new_rep_y = sum(s.y for s in bucket) / len(bucket)
                clusters[idx] = (new_rep_y, bucket)
                placed = True
                break

        if not placed:
            clusters.append((seg.y, [seg]))

    return {
        rep_y: bucket
        for rep_y, bucket in clusters
    }


def split_into_continuous_runs(y, segments, max_merge_gap):
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: s.x1)

    runs = []

    current = [ordered[0]]
    current_x1 = ordered[0].x1
    current_x2 = ordered[0].x2

    for seg in ordered[1:]:
        gap = interval_gap(current_x1, current_x2, seg.x1, seg.x2)

        if gap <= max_merge_gap:
            current.append(seg)
            current_x1 = min(current_x1, seg.x1)
            current_x2 = max(current_x2, seg.x2)
        else:
            runs.append(
                BarRun(
                    y=y,
                    x1=current_x1,
                    x2=current_x2,
                    segments=current,
                    support_xs=[],
                )
            )

            current = [seg]
            current_x1 = seg.x1
            current_x2 = seg.x2

    runs.append(
        BarRun(
            y=y,
            x1=current_x1,
            x2=current_x2,
            segments=current,
            support_xs=[],
        )
    )

    return runs


def hook_crosses_axis(hook, y, axis_tol):
    return hook.y1 - axis_tol <= y <= hook.y2 + axis_tol


def hook_is_intermediate_for_run(hook, run, axis_tol):
    """
    Delete vertical hooks only if they are likely intermediate hooks:
    - hook crosses the horizontal bar axis,
    - hook lies inside the run, not at the extreme start/end,
    - there is reinforcement segment evidence on both sides.
    """
    if not hook_crosses_axis(hook, run.y, axis_tol):
        return False

    if hook.x <= run.x1 + axis_tol:
        return False

    if hook.x >= run.x2 - axis_tol:
        return False

    has_left = any(seg.x2 <= hook.x + axis_tol for seg in run.segments)
    has_right = any(seg.x1 >= hook.x - axis_tol for seg in run.segments)

    return has_left and has_right


# ============================================================
# LAPPING LOGIC
# ============================================================

def choose_lap_center_x(run, lap_rule, standard_bar_length, lap_length):
    x1 = run.x1
    x2 = run.x2

    min_x = x1 + lap_length / 2.0
    max_x = x2 - lap_length / 2.0

    if min_x >= max_x:
        return (x1 + x2) / 2.0

    lap_rule = clean_text(lap_rule)

    if lap_rule == "TOP":
        desired = (x1 + x2) / 2.0
        return max(min_x, min(max_x, desired))

    # BOTTOM: prefer internal support/hook location.
    internal_supports = [
        sx for sx in run.support_xs
        if min_x <= sx <= max_x
    ]

    if internal_supports:
        # Choose support closest to where a stock bar would naturally end.
        target = x1 + standard_bar_length
        return min(internal_supports, key=lambda sx: abs(sx - target))

    # Fallback if no support hook was detected.
    desired = x1 + standard_bar_length
    return max(min_x, min(max_x, desired))


def draw_healed_or_lapped_bar(
    msp,
    run,
    rebar_layer,
    standard_bar_length,
    lap_length,
    lap_rule,
    color=256,
):
    total_length = run.x2 - run.x1

    dxfattribs = {
        "layer": rebar_layer,
        "color": color,
    }

    if total_length <= standard_bar_length:
        msp.add_line(
            (run.x1, run.y, 0),
            (run.x2, run.y, 0),
            dxfattribs=dxfattribs,
        )
        return {
            "run_length": total_length,
            "lapped": False,
            "lap_center": None,
        }

    lap_center = choose_lap_center_x(
        run=run,
        lap_rule=lap_rule,
        standard_bar_length=standard_bar_length,
        lap_length=lap_length,
    )

    lap_start = lap_center - lap_length / 2.0
    lap_end = lap_center + lap_length / 2.0

    lap_start = max(run.x1, lap_start)
    lap_end = min(run.x2, lap_end)

    # First physical stock bar.
    msp.add_line(
        (run.x1, run.y, 0),
        (lap_end, run.y, 0),
        dxfattribs=dxfattribs,
    )

    # Second physical stock bar.
    msp.add_line(
        (lap_start, run.y, 0),
        (run.x2, run.y, 0),
        dxfattribs=dxfattribs,
    )

    # Add small lap note.
    note_y_offset = -250 if clean_text(lap_rule) == "TOP" else 250

    note = msp.add_text(
        f"LAP {int(lap_length)}",
        dxfattribs={
            "layer": rebar_layer,
            "height": 150,
            "color": color,
        },
    )
    note.set_placement((lap_center, run.y + note_y_offset, 0))

    return {
        "run_length": total_length,
        "lapped": True,
        "lap_center": lap_center,
    }


# ============================================================
# LABEL CONSOLIDATION
# ============================================================

def consolidate_rebar_labels(msp, text_layer, runs, axis_tol):
    if not text_layer:
        return 0

    text_entities = [
        e for e in msp
        if e.dxftype() in ("TEXT", "MTEXT")
        and e.dxf.layer == text_layer
    ]

    deleted_count = 0

    for run in runs:
        candidates = []

        for txt in text_entities:
            value = text_plain_value(txt)

            if not value:
                continue

            if not looks_like_rebar_label(value):
                continue

            tx, ty = text_insert_point(txt)

            same_axis = abs(ty - run.y) <= max(axis_tol * 5, 300)
            within_run = run.x1 - 500 <= tx <= run.x2 + 500

            if same_axis and within_run:
                candidates.append(txt)

        if len(candidates) <= 1:
            continue

        groups = {}

        for txt in candidates:
            value = clean_text(text_plain_value(txt)).replace(" ", "")
            groups.setdefault(value, []).append(txt)

        chosen_label, chosen_group = max(
            groups.items(),
            key=lambda item: len(item[1]),
        )

        sample = chosen_group[0]
        sample_height = get_text_height(sample)

        for txt in candidates:
            safe_delete(msp, txt)
            deleted_count += 1

        label_x = (run.x1 + run.x2) / 2.0
        label_y = run.y + 300

        new_text = msp.add_text(
            chosen_label,
            dxfattribs={
                "layer": text_layer,
                "height": sample_height,
                "color": getattr(sample.dxf, "color", 256),
            },
        )
        new_text.set_placement((label_x, label_y, 0))

    return deleted_count


# ============================================================
# MAIN HEALING FUNCTION
# ============================================================

def heal_beam_rebar(
    doc,
    rebar_layer,
    axis_tol,
    lap_rule,
    text_layer=None,
    standard_bar_length=12000.0,
    bar_diameter=16.0,
    max_merge_gap=500.0,
):
    """
    Heal beam reinforcement lines.

    Required core signature:
        heal_beam_rebar(doc, rebar_layer, axis_tol, lap_rule)

    Extra optional parameters are used by the Streamlit UI.

    Logic:
      - detect horizontal rebar LINE entities on rebar_layer,
      - group collinear segments,
      - remove intermediate vertical hooks,
      - merge broken spans into bar runs,
      - if run > standard_bar_length, break with lap,
      - consolidate repeated labels on text_layer.
    """
    msp = doc.modelspace()

    lines = [
        e for e in msp.query("LINE")
        if e.dxf.layer == rebar_layer
    ]

    horizontal_segments = [
        normalize_horizontal(e)
        for e in lines
        if is_horizontal_line(e, axis_tol)
    ]

    vertical_hooks = [
        normalize_vertical(e)
        for e in lines
        if is_vertical_line(e, axis_tol)
    ]

    if not horizontal_segments:
        return {
            "doc": doc,
            "runs": 0,
            "hooks_removed": 0,
            "bars_lapped": 0,
            "labels_consolidated": 0,
            "message": "No horizontal reinforcement LINE entities found on selected layer.",
        }

    first_color = getattr(horizontal_segments[0].entity.dxf, "color", 256)

    y_groups = cluster_segments_by_y(horizontal_segments, axis_tol)

    all_runs = []
    hook_handles_to_delete = set()

    for y, segments in y_groups.items():
        runs = split_into_continuous_runs(
            y=y,
            segments=segments,
            max_merge_gap=max_merge_gap,
        )

        for run in runs:
            for hook in vertical_hooks:
                if hook_is_intermediate_for_run(hook, run, axis_tol):
                    hook_handles_to_delete.add(hook.entity.dxf.handle)
                    run.support_xs.append(hook.x)

            all_runs.append(run)

    # Delete original horizontal broken pieces.
    for seg in horizontal_segments:
        safe_delete(msp, seg.entity)

    # Delete intermediate hooks only.
    hooks_removed = 0

    for hook in vertical_hooks:
        if hook.entity.dxf.handle in hook_handles_to_delete:
            safe_delete(msp, hook.entity)
            hooks_removed += 1

    lap_length = 50.0 * float(bar_diameter)
    bars_lapped = 0

    for run in all_runs:
        result = draw_healed_or_lapped_bar(
            msp=msp,
            run=run,
            rebar_layer=rebar_layer,
            standard_bar_length=float(standard_bar_length),
            lap_length=lap_length,
            lap_rule=lap_rule,
            color=first_color,
        )

        if result["lapped"]:
            bars_lapped += 1

    labels_consolidated = consolidate_rebar_labels(
        msp=msp,
        text_layer=text_layer,
        runs=all_runs,
        axis_tol=axis_tol,
    )

    return {
        "doc": doc,
        "runs": len(all_runs),
        "hooks_removed": hooks_removed,
        "bars_lapped": bars_lapped,
        "labels_consolidated": labels_consolidated,
        "lap_length": lap_length,
        "message": "Beam reinforcement healing complete.",
    }


# ============================================================
# SESSION STATE
# ============================================================

def init_state():
    defaults = {
        "doc_loaded": False,
        "doc": None,
        "dxf_name": "",
        "dxf_sig": None,
        "healed": False,
        "heal_result": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_state():
    for key in [
        "doc_loaded",
        "doc",
        "dxf_name",
        "dxf_sig",
        "healed",
        "heal_result",
    ]:
        if key in st.session_state:
            del st.session_state[key]


init_state()


# ============================================================
# STREAMLIT UI
# ============================================================

st.markdown("### 1. Upload Beam Detail DXF")

uploaded_dxf = st.file_uploader(
    "Upload beam detailing DXF",
    type=["dxf"],
    key="beam_rebar_healer_upload",
)

dxf_sig = uploaded_file_signature(uploaded_dxf)

if dxf_sig != st.session_state.dxf_sig:
    reset_state()
    init_state()
    st.session_state.dxf_sig = dxf_sig

if uploaded_dxf is None:
    st.info("Upload a DXF file to begin.")
    st.stop()

if not st.session_state.doc_loaded:
    try:
        st.session_state.doc = read_uploaded_dxf(uploaded_dxf)
        st.session_state.dxf_name = uploaded_dxf.name
        st.session_state.doc_loaded = True
        st.success("DXF loaded successfully.")

    except Exception as e:
        st.error(f"Could not read DXF file: {e}")
        st.stop()


doc = st.session_state.doc
layers = get_layer_names(doc)

if not layers:
    st.error("No layers were found in this DXF.")
    st.stop()


st.markdown("### 2. Layer Selection")

c1, c2 = st.columns(2)

with c1:
    rebar_layer = st.selectbox(
        "Reinforcement Layer",
        layers,
        help="Select the layer containing reinforcement LINE entities.",
        key="rebar_layer",
    )

with c2:
    text_layer = st.selectbox(
        "Text Layer",
        layers,
        help="Select the layer containing reinforcement labels such as 2Y16.",
        key="rebar_text_layer",
    )


st.markdown("### 3. Healing Settings")

c3, c4, c5 = st.columns(3)

with c3:
    axis_tol = st.slider(
        "Axis Tolerance",
        min_value=0.5,
        max_value=50.0,
        value=5.0,
        step=0.5,
        help="Tolerance in mm for detecting horizontal bars on the same Y-axis.",
        key="axis_tol",
    )

with c4:
    standard_bar_length = st.slider(
        "Standard Bar Length",
        min_value=6000,
        max_value=18000,
        value=12000,
        step=500,
        help="Default Nigerian stock length is 12000mm.",
        key="standard_bar_length",
    )

with c5:
    bar_diameter = st.slider(
        "Bar Diameter",
        min_value=8,
        max_value=40,
        value=16,
        step=2,
        help="Lap length = 50 × bar diameter.",
        key="bar_diameter",
    )

c6, c7 = st.columns(2)

with c6:
    lap_rule = st.radio(
        "Lap Rule",
        options=["TOP", "BOTTOM"],
        horizontal=True,
        help="Top bars lap at mid-span. Bottom bars lap near supports.",
        key="lap_rule",
    )

with c7:
    max_merge_gap = st.slider(
        "Maximum Merge Gap",
        min_value=50,
        max_value=2500,
        value=500,
        step=50,
        help="Maximum gap between span segments that should still be treated as one continuous bar.",
        key="max_merge_gap",
    )


lap_length_preview = 50 * bar_diameter

st.info(
    f"Lap length = 50 × {bar_diameter}mm = **{lap_length_preview}mm**. "
    f"Bars longer than **{standard_bar_length}mm** will be broken with lap overlap."
)


st.markdown("### 4. Heal DXF")

confirm = st.checkbox(
    "I understand this will modify reinforcement LINE entities and matching duplicate labels in the uploaded DXF.",
    value=False,
    key="confirm_rebar_heal",
)

if st.button(
    "🩺 Heal Beam Rebar",
    type="primary",
    disabled=not confirm,
    key="heal_rebar_button",
):
    try:
        result = heal_beam_rebar(
            doc=st.session_state.doc,
            rebar_layer=rebar_layer,
            axis_tol=float(axis_tol),
            lap_rule=lap_rule,
            text_layer=text_layer,
            standard_bar_length=float(standard_bar_length),
            bar_diameter=float(bar_diameter),
            max_merge_gap=float(max_merge_gap),
        )

        st.session_state.heal_result = result
        st.session_state.healed = True

        if result["runs"] == 0:
            st.warning(result["message"])
        else:
            st.success(result["message"])

    except Exception as e:
        st.error(f"Healing failed: {e}")


if st.session_state.heal_result:
    st.markdown("### 5. Healing Summary")

    result = st.session_state.heal_result

    s1, s2, s3, s4 = st.columns(4)

    s1.metric("Bar runs healed", result.get("runs", 0))
    s2.metric("Hooks removed", result.get("hooks_removed", 0))
    s3.metric("Bars lapped", result.get("bars_lapped", 0))
    s4.metric("Labels consolidated", result.get("labels_consolidated", 0))

    st.write(
        {
            "lap_length_mm": result.get("lap_length", 50 * bar_diameter),
            "standard_bar_length_mm": standard_bar_length,
            "lap_rule": lap_rule,
        }
    )


if st.session_state.healed:
    st.markdown("### 6. Download Healed DXF")

    try:
        healed_bytes = write_doc_to_temp_bytes(st.session_state.doc)

        st.download_button(
            "📥 Download Healed DXF",
            data=healed_bytes,
            file_name=f"HEALED_REBAR_{st.session_state.dxf_name}",
            mime="application/dxf",
            key="download_healed_rebar_dxf",
        )

    except Exception as e:
        st.error(f"Could not prepare DXF download: {e}")
