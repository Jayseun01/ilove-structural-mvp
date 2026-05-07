import math
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import ezdxf
import streamlit as st
from ezdxf.document import Drawing
from ezdxf.entities import Line, Text, MText
from ezdxf.math import Vec3


# ============================================================
# iLoveStructural - Beam Rebar Healer
# ============================================================
#
# Purpose:
#   Heal "Broken Rebar Syndrome" in exported beam detailing DXFs.
#
# Main operations:
#   1. Detect collinear horizontal reinforcement segments.
#   2. Remove intermediate vertical hooks.
#   3. Merge broken horizontal segments into continuous bars.
#   4. Apply Nigerian 12m stock length rule.
#   5. Insert laps:
#        - Top bars: lap near mid-span.
#        - Bottom bars: lap near supports.
#   6. Consolidate duplicate bar labels.
#
# Notes:
#   - This tool assumes reinforcement is drawn as LINE entities.
#   - Bar labels are assumed to be TEXT or MTEXT entities.
#   - Coordinates are assumed to be in millimetres.
# ============================================================


@dataclass
class HorizontalSegment:
    entity: Line
    x1: float
    x2: float
    y: float


@dataclass
class VerticalHook:
    entity: Line
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
# Geometry helpers
# ============================================================

def nearly_equal(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def line_start_end(line: Line) -> Tuple[Vec3, Vec3]:
    return Vec3(line.dxf.start), Vec3(line.dxf.end)


def is_horizontal_line(line: Line, axis_tol: float) -> bool:
    start, end = line_start_end(line)
    return abs(start.y - end.y) <= axis_tol and abs(start.x - end.x) > axis_tol


def is_vertical_line(line: Line, axis_tol: float) -> bool:
    start, end = line_start_end(line)
    return abs(start.x - end.x) <= axis_tol and abs(start.y - end.y) > axis_tol


def normalize_horizontal(line: Line) -> HorizontalSegment:
    start, end = line_start_end(line)
    x1 = min(start.x, end.x)
    x2 = max(start.x, end.x)
    y = (start.y + end.y) / 2
    return HorizontalSegment(entity=line, x1=x1, x2=x2, y=y)


def normalize_vertical(line: Line) -> VerticalHook:
    start, end = line_start_end(line)
    x = (start.x + end.x) / 2
    y1 = min(start.y, end.y)
    y2 = max(start.y, end.y)
    return VerticalHook(entity=line, x=x, y1=y1, y2=y2)


def interval_gap(a1: float, a2: float, b1: float, b2: float) -> float:
    """
    Returns positive gap between two 1D intervals.
    Returns 0 if touching or overlapping.
    """
    if a2 < b1:
        return b1 - a2
    if b2 < a1:
        return a1 - b2
    return 0.0


def text_plain_value(entity) -> str:
    if entity.dxftype() == "TEXT":
        return entity.dxf.text.strip()
    if entity.dxftype() == "MTEXT":
        return entity.plain_text().strip()
    return ""


def text_insert_point(entity) -> Vec3:
    if entity.dxftype() == "TEXT":
        return Vec3(entity.dxf.insert)
    if entity.dxftype() == "MTEXT":
        return Vec3(entity.dxf.insert)
    return Vec3(0, 0, 0)


def get_text_height(entity, default_height: float = 250.0) -> float:
    try:
        return float(entity.dxf.height)
    except Exception:
        return default_height


def set_text_position(entity, x: float, y: float):
    if entity.dxftype() == "TEXT":
        entity.dxf.insert = (x, y, 0)
    elif entity.dxftype() == "MTEXT":
        entity.dxf.insert = (x, y, 0)


def safe_delete(msp, entity):
    try:
        msp.delete_entity(entity)
    except Exception:
        pass


# ============================================================
# Clustering and run detection
# ============================================================

def cluster_segments_by_y(
    segments: List[HorizontalSegment],
    axis_tol: float
) -> Dict[float, List[HorizontalSegment]]:
    """
    Groups horizontal segments into Y-axis buckets.
    Uses a dynamic representative Y rather than raw rounding.
    """
    clusters: List[Tuple[float, List[HorizontalSegment]]] = []

    for seg in sorted(segments, key=lambda s: s.y):
        placed = False

        for idx, (rep_y, bucket) in enumerate(clusters):
            if abs(seg.y - rep_y) <= axis_tol:
                bucket.append(seg)
                new_rep = sum(s.y for s in bucket) / len(bucket)
                clusters[idx] = (new_rep, bucket)
                placed = True
                break

        if not placed:
            clusters.append((seg.y, [seg]))

    return {rep_y: bucket for rep_y, bucket in clusters}


def split_into_continuous_runs(
    y: float,
    segments: List[HorizontalSegment],
    axis_tol: float,
    max_merge_gap: float
) -> List[BarRun]:
    """
    Splits a group of collinear horizontal segments into continuous runs.

    max_merge_gap controls whether small gaps between exported spans
    should still be interpreted as one physical bar.
    """
    if not segments:
        return []

    sorted_segments = sorted(segments, key=lambda s: s.x1)
    runs: List[BarRun] = []

    current = [sorted_segments[0]]
    current_x1 = sorted_segments[0].x1
    current_x2 = sorted_segments[0].x2

    for seg in sorted_segments[1:]:
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


def hook_intersects_axis(hook: VerticalHook, y: float, axis_tol: float) -> bool:
    return hook.y1 - axis_tol <= y <= hook.y2 + axis_tol


def hook_is_intermediate_for_run(
    hook: VerticalHook,
    run: BarRun,
    axis_tol: float
) -> bool:
    """
    A hook is considered intermediate when:
      - it crosses the bar axis Y,
      - it lies within the run, not at extreme ends,
      - there are horizontal segments on both sides near that X.
    """
    if not hook_intersects_axis(hook, run.y, axis_tol):
        return False

    if hook.x <= run.x1 + axis_tol or hook.x >= run.x2 - axis_tol:
        return False

    has_left = any(seg.x1 <= hook.x + axis_tol and seg.x2 < hook.x + axis_tol for seg in run.segments)
    has_right = any(seg.x1 > hook.x - axis_tol and seg.x2 >= hook.x - axis_tol for seg in run.segments)

    if not has_left:
        has_left = any(seg.x2 <= hook.x + axis_tol for seg in run.segments)

    if not has_right:
        has_right = any(seg.x1 >= hook.x - axis_tol for seg in run.segments)

    return has_left and has_right


# ============================================================
# Lapping logic
# ============================================================

def choose_lap_center_x(
    run: BarRun,
    lap_rule: str,
    standard_bar_length: float,
    lap_length: float
) -> float:
    """
    Chooses the lap centre location.

    TOP:
        Lap at mid-span.

    BOTTOM:
        Prefer an intermediate support location.
        If no support was detected, use a point near standard_bar_length
        from the left end, while keeping the lap inside the run.
    """
    x1, x2 = run.x1, run.x2
    run_len = x2 - x1
    lap_rule = lap_rule.upper()

    min_x = x1 + lap_length / 2
    max_x = x2 - lap_length / 2

    if min_x >= max_x:
        return (x1 + x2) / 2

    if lap_rule == "TOP":
        desired = (x1 + x2) / 2
        return max(min_x, min(max_x, desired))

    # BOTTOM: lap near support.
    internal_supports = [
        sx for sx in run.support_xs
        if min_x <= sx <= max_x
    ]

    if internal_supports:
        target = x1 + min(standard_bar_length, run_len * 0.6)
        return min(internal_supports, key=lambda sx: abs(sx - target))

    fallback = x1 + min(standard_bar_length, run_len / 2)
    return max(min_x, min(max_x, fallback))


def draw_lapped_bar(
    msp,
    run: BarRun,
    layer: str,
    color: Optional[int],
    standard_bar_length: float,
    lap_length: float,
    lap_rule: str,
):
    """
    Draws a healed bar.

    If run length <= standard_bar_length:
        Draw one continuous line.

    If longer:
        Draw two overlapping line entities:
          left bar:  x1 -> lap_end
          right bar: lap_start -> x2

    The overlap distance is lap_length.
    """
    x1, x2, y = run.x1, run.x2, run.y
    total_len = x2 - x1

    attribs = {"layer": layer}
    if color is not None:
        attribs["color"] = color

    if total_len <= standard_bar_length:
        msp.add_line((x1, y, 0), (x2, y, 0), dxfattribs=attribs)
        return

    lap_center = choose_lap_center_x(
        run=run,
        lap_rule=lap_rule,
        standard_bar_length=standard_bar_length,
        lap_length=lap_length,
    )

    lap_start = lap_center - lap_length / 2
    lap_end = lap_center + lap_length / 2

    lap_start = max(x1, lap_start)
    lap_end = min(x2, lap_end)

    # Two overlapping bars.
    msp.add_line((x1, y, 0), (lap_end, y, 0), dxfattribs=attribs)
    msp.add_line((lap_start, y, 0), (x2, y, 0), dxfattribs=attribs)

    # Optional lap marker text.
    marker_y = y + 200 if lap_rule.upper() == "BOTTOM" else y - 200
    msp.add_text(
        f"LAP {int(lap_length)}",
        dxfattribs={
            "layer": layer,
            "height": 150,
            "color": color or 1,
        },
    ).set_placement((lap_center, marker_y, 0))


# ============================================================
# Label consolidation
# ============================================================

def looks_like_rebar_label(value: str) -> bool:
    """
    Detect labels like:
      2Y16
      3Y20
      4T25
      2-Y16
      2 Y 16
    """
    value = value.upper().replace(" ", "")
    pattern = r"^\d+[-]?[YT]\d+"
    return re.match(pattern, value) is not None


def consolidate_labels(
    msp,
    text_entities: List,
    text_layer: str,
    runs: List[BarRun],
    axis_tol: float,
):
    """
    For each healed run, find repeated labels along the same Y-axis.
    Delete duplicates and place one centred label for the entire run.
    """
    used_texts = set()

    for run in runs:
        candidates = []

        for txt in text_entities:
            if txt.dxf.handle in used_texts:
                continue

            value = text_plain_value(txt)

            if not value or not looks_like_rebar_label(value):
                continue

            insert = text_insert_point(txt)

            same_axis = abs(insert.y - run.y) <= max(axis_tol * 5, 250)
            within_run = run.x1 - 500 <= insert.x <= run.x2 + 500

            if same_axis and within_run:
                candidates.append(txt)

        if not candidates:
            continue

        # Group candidates by exact label string.
        label_groups: Dict[str, List] = {}

        for txt in candidates:
            value = text_plain_value(txt).upper().replace(" ", "")
            label_groups.setdefault(value, []).append(txt)

        # Pick the most repeated label. If tied, use the first.
        chosen_label, chosen_group = max(
            label_groups.items(),
            key=lambda item: len(item[1])
        )

        sample = chosen_group[0]
        sample_height = get_text_height(sample)
        sample_color = getattr(sample.dxf, "color", 256)

        # Delete all duplicate rebar labels in the run.
        for txt in candidates:
            used_texts.add(txt.dxf.handle)
            safe_delete(msp, txt)

        # Add one centred label.
        label_x = (run.x1 + run.x2) / 2
        label_y = run.y + 300

        new_text = msp.add_text(
            chosen_label,
            dxfattribs={
                "layer": text_layer,
                "height": sample_height,
                "color": sample_color,
            },
        )
        new_text.set_placement((label_x, label_y, 0))


# ============================================================
# Main healing function
# ============================================================

def heal_beam_rebar(
    doc: Drawing,
    rebar_layer: str,
    axis_tol: float,
    lap_rule: str,
    text_layer: Optional[str] = None,
    standard_bar_length: float = 12000.0,
    bar_diameter: float = 16.0,
    max_merge_gap: Optional[float] = None,
) -> Drawing:
    """
    Heal broken reinforcement lines in a DXF beam detail.

    Parameters
    ----------
    doc:
        ezdxf Drawing object.

    rebar_layer:
        Layer containing reinforcement LINE entities.

    axis_tol:
        Tolerance in drawing units, usually millimetres.

    lap_rule:
        "TOP" or "BOTTOM".

    text_layer:
        Layer containing reinforcement text labels.
        If None, label consolidation is skipped.

    standard_bar_length:
        Maximum stock length before lapping, default 12000mm.

    bar_diameter:
        Diameter used for lap length calculation.
        Lap length = 50 * bar_diameter.

    max_merge_gap:
        Maximum X gap between broken segments considered continuous.
        If None, a smart default is used.

    Returns
    -------
    doc:
        Modified ezdxf Drawing.
    """
    msp = doc.modelspace()

    if max_merge_gap is None:
        max_merge_gap = max(axis_tol * 10, 300.0)

    lap_length = 50.0 * bar_diameter

    line_entities = [
        e for e in msp.query("LINE")
        if e.dxf.layer == rebar_layer
    ]

    horizontal_segments = [
        normalize_horizontal(e)
        for e in line_entities
        if is_horizontal_line(e, axis_tol)
    ]

    vertical_hooks = [
        normalize_vertical(e)
        for e in line_entities
        if is_vertical_line(e, axis_tol)
    ]

    if not horizontal_segments:
        return doc

    # Preserve original rebar entity appearance where possible.
    first_color = getattr(horizontal_segments[0].entity.dxf, "color", 256)

    # 1. Group collinear horizontal segments by Y-axis.
    y_groups = cluster_segments_by_y(horizontal_segments, axis_tol)

    all_runs: List[BarRun] = []
    hooks_to_delete = set()

    # 2. Split each Y group into continuous runs.
    for y, segs in y_groups.items():
        runs = split_into_continuous_runs(
            y=y,
            segments=segs,
            axis_tol=axis_tol,
            max_merge_gap=max_merge_gap,
        )

        for run in runs:
            # 3. Detect and mark intermediate hooks.
            for hook in vertical_hooks:
                if hook_is_intermediate_for_run(hook, run, axis_tol):
                    hooks_to_delete.add(hook.entity.dxf.handle)
                    run.support_xs.append(hook.x)

            all_runs.append(run)

    # 4. Delete original horizontal segments.
    for seg in horizontal_segments:
        safe_delete(msp, seg.entity)

    # 5. Delete intermediate hooks only.
    for hook in vertical_hooks:
        if hook.entity.dxf.handle in hooks_to_delete:
            safe_delete(msp, hook.entity)

    # 6. Redraw healed bars with lapping rule.
    for run in all_runs:
        draw_lapped_bar(
            msp=msp,
            run=run,
            layer=rebar_layer,
            color=first_color,
            standard_bar_length=standard_bar_length,
            lap_length=lap_length,
            lap_rule=lap_rule,
        )

    # 7. Consolidate labels.
    if text_layer:
        text_entities = [
            e for e in msp
            if e.dxftype() in {"TEXT", "MTEXT"} and e.dxf.layer == text_layer
        ]

        consolidate_labels(
            msp=msp,
            text_entities=text_entities,
            text_layer=text_layer,
            runs=all_runs,
            axis_tol=axis_tol,
        )

    return doc


# ============================================================
# DXF IO helpers for Streamlit
# ============================================================

def read_uploaded_dxf(uploaded_file) -> Drawing:
    data = uploaded_file.read()
    stream = BytesIO(data)
    return ezdxf.read(stream)


def dxf_to_bytes(doc: Drawing) -> bytes:
    stream = BytesIO()
    doc.write(stream)
    stream.seek(0)
    return stream.getvalue()


# ============================================================
# Streamlit App
# ============================================================

def streamlit_app():
    st.set_page_config(
        page_title="iLoveStructural - Rebar Healer",
        page_icon="🏗️",
        layout="wide",
    )

    st.title("🏗️ iLoveStructural")
    st.subheader("Beam Rebar Healer for Broken DXF Reinforcement")

    st.markdown(
        """
        This tool heals beam reinforcement exported as broken DXF lines.

        It removes intermediate hooks, joins collinear bar segments, applies the
        **12m Nigerian stock length rule**, inserts laps, and consolidates duplicate
        bar labels such as `2Y16`.
        """
    )

    uploaded_file = st.file_uploader(
        "Upload beam detailing DXF",
        type=["dxf"],
    )

    if uploaded_file is None:
        st.info("Upload a DXF file to begin.")
        return

    try:
        doc = read_uploaded_dxf(uploaded_file)
    except Exception as e:
        st.error(f"Could not read DXF file: {e}")
        return

    layers = sorted([layer.dxf.name for layer in doc.layers])

    if not layers:
        st.error("No layers found in this DXF.")
        return

    col1, col2 = st.columns(2)

    with col1:
        rebar_layer = st.selectbox(
            "Reinforcement Layer",
            layers,
            help="Select the layer containing reinforcement LINE entities.",
        )

    with col2:
        text_layer = st.selectbox(
            "Text Layer",
            layers,
            help="Select the layer containing reinforcement labels.",
        )

    st.divider()

    col3, col4, col5 = st.columns(3)

    with col3:
        axis_tol = st.slider(
            "Axis Tolerance",
            min_value=0.5,
            max_value=50.0,
            value=5.0,
            step=0.5,
            help="Tolerance in mm for detecting lines on the same horizontal axis.",
        )

    with col4:
        standard_bar_length = st.slider(
            "Standard Bar Length",
            min_value=6000,
            max_value=18000,
            value=12000,
            step=500,
            help="Maximum available stock length. Default is 12000mm.",
        )

    with col5:
        bar_diameter = st.slider(
            "Bar Diameter",
            min_value=8,
            max_value=40,
            value=16,
            step=2,
            help="Used to compute lap length: 50 × bar diameter.",
        )

    col6, col7 = st.columns(2)

    with col6:
        lap_rule = st.radio(
            "Lap Rule",
            options=["TOP", "BOTTOM"],
            horizontal=True,
            help="Top bars lap at mid-span. Bottom bars lap at supports.",
        )

    with col7:
        max_merge_gap = st.slider(
            "Maximum Merge Gap",
            min_value=50,
            max_value=2000,
            value=500,
            step=50,
            help="Maximum gap between broken segments that should be healed as one bar.",
        )

    lap_length = 50 * bar_diameter

    st.info(
        f"Current lap length = 50 × {bar_diameter}mm = **{lap_length}mm**"
    )

    if st.button("Heal Rebar DXF", type="primary"):
        try:
            healed_doc = heal_beam_rebar(
                doc=doc,
                rebar_layer=rebar_layer,
                axis_tol=axis_tol,
                lap_rule=lap_rule,
                text_layer=text_layer,
                standard_bar_length=float(standard_bar_length),
                bar_diameter=float(bar_diameter),
                max_merge_gap=float(max_merge_gap),
            )

            healed_bytes = dxf_to_bytes(healed_doc)

            st.success("Rebar healing complete.")

            st.download_button(
                label="Download Healed DXF",
                data=healed_bytes,
                file_name="iLoveStructural_healed_rebar.dxf",
                mime="application/dxf",
            )

        except Exception as e:
            st.error(f"Healing failed: {e}")


if __name__ == "__main__":
    streamlit_app()
