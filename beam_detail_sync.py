# beam_detail_sync.py
# iLoveStructural - Tool 3: Beam Detail Grid/Axis Endpoint Label Sync
#
# Purpose:
# - Upload old_label -> new_label mapping from Tool 2 audit CSV and/or manual table
# - Upload beam detail DXF
# - Detect grid/axis labels inside/near circular bubbles
# - Preview proposed changes
# - User approves rows
# - Apply approved changes to writable TEXT/MTEXT/ATTRIB entities
# - Download audit CSV and updated DXF
#
# Safe philosophy:
# - Does NOT alter rebar notes/dimensions unless they are detected inside/near a circle AND mapped
# - Does NOT modify block definition text
# - Writes only modelspace TEXT/MTEXT in Safe Mode
# - Optionally writes INSERT attributes in Attribute Mode

import io
import math
import re
from typing import Dict, List, Any, Optional, Tuple

import ezdxf
import pandas as pd
import streamlit as st


# ============================================================
# Basic text helpers
# ============================================================

def clean_text(value) -> str:
    """Clean TEXT/MTEXT/ATTRIB content into a simple comparable label."""
    if value is None:
        return ""

    s = str(value)

    # Common MTEXT line breaks / formatting leftovers
    s = s.replace("\\P", " ")
    s = s.replace("\\~", " ")

    # Remove simple MTEXT formatting codes like {\...;} conservatively
    s = re.sub(r"\\[A-Za-z][^;]*;", "", s)
    s = s.replace("{", "").replace("}", "")

    # Normalize whitespace
    s = re.sub(r"\s+", " ", s).strip()

    return s


def is_zero_padded_number(label: str) -> bool:
    """Reject detail numbers like 01, 02, 003."""
    s = clean_text(label)
    return bool(re.fullmatch(r"0\d+", s))


def is_numeric_label(label: str) -> bool:
    s = clean_text(label)
    return bool(re.fullmatch(r"\d+", s))


def is_alpha_label(label: str) -> bool:
    s = clean_text(label).upper()
    return bool(re.fullmatch(r"[A-Z]{1,3}", s))


def probable_grid_label(label: str, max_numeric_label: int = 99) -> bool:
    """
    Conservative grid label filter.

    Accepts:
    - 1, 2, 3 ... up to max_numeric_label
    - A, B, C, AA, AB etc.

    Rejects:
    - 01, 02
    - 2Y16
    - 3-250
    - 450 if max_numeric_label is 99
    - long notes
    """
    s = clean_text(label)

    if not s:
        return False

    if is_zero_padded_number(s):
        return False

    if is_numeric_label(s):
        try:
            return 1 <= int(s) <= int(max_numeric_label)
        except Exception:
            return False

    if is_alpha_label(s):
        return True

    return False


def boolish(value) -> bool:
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    s = str(value).strip().lower()
    return s in {"true", "yes", "y", "1", "changed", "ok"}


# ============================================================
# Geometry helpers
# ============================================================

def get_xy(point) -> Tuple[float, float]:
    """Safely get x/y from ezdxf point/vector."""
    try:
        return float(point.x), float(point.y)
    except Exception:
        try:
            return float(point[0]), float(point[1])
        except Exception:
            return 0.0, 0.0


def rotate_point(x: float, y: float, angle_deg: float) -> Tuple[float, float]:
    a = math.radians(angle_deg or 0.0)
    ca = math.cos(a)
    sa = math.sin(a)
    return x * ca - y * sa, x * sa + y * ca


def transform_block_point(
    local_x: float,
    local_y: float,
    insert_x: float,
    insert_y: float,
    xscale: float,
    yscale: float,
    rotation_deg: float,
) -> Tuple[float, float]:
    """Simple INSERT transform for non-nested block geometry."""
    sx = local_x * (xscale or 1.0)
    sy = local_y * (yscale or 1.0)
    rx, ry = rotate_point(sx, sy, rotation_deg or 0.0)
    return insert_x + rx, insert_y + ry


def dist2d(x1, y1, x2, y2) -> float:
    return math.hypot(float(x1) - float(x2), float(y1) - float(y2))


# ============================================================
# CSV mapping extraction
# ============================================================

def find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find a column by relaxed normalized name."""
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(c).strip().lower()): c
        for c in df.columns
    }

    for cand in candidates:
        key = re.sub(r"[^a-z0-9]", "", cand.strip().lower())
        if key in normalized:
            return normalized[key]

    return None


def mapping_from_audit_csv(uploaded_file) -> pd.DataFrame:
    """
    Extract old_label -> new_label rows from Tool 2 audit CSV.

    Supports flexible column names:
    old:
      old_label, old, target_old_label, from
    new:
      new_label, new, proposed_new_label, to, source_label

    If changed column exists and contains any true rows, prefer changed rows.
    """
    if uploaded_file is None:
        return pd.DataFrame(columns=["old_label", "new_label"])

    try:
        df = pd.read_csv(uploaded_file)
    except Exception:
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, encoding="latin1")

    if df.empty:
        return pd.DataFrame(columns=["old_label", "new_label"])

    old_col = find_column(
        df,
        [
            "old_label",
            "old",
            "target_old_label",
            "from",
            "from_label",
            "existing_label",
        ],
    )

    new_col = find_column(
        df,
        [
            "new_label",
            "new",
            "proposed_new_label",
            "to",
            "to_label",
            "source_label",
            "replacement_label",
        ],
    )

    if old_col is None or new_col is None:
        return pd.DataFrame(columns=["old_label", "new_label"])

    work = df.copy()

    changed_col = find_column(work, ["changed", "is_changed", "will_change"])
    if changed_col is not None:
        changed_mask = work[changed_col].apply(boolish)
        if changed_mask.any():
            work = work[changed_mask].copy()

    out_rows = []

    for _, row in work.iterrows():
        old_label = clean_text(row.get(old_col, ""))
        new_label = clean_text(row.get(new_col, ""))

        if not old_label or not new_label:
            continue

        if old_label == new_label:
            continue

        out_rows.append(
            {
                "old_label": old_label,
                "new_label": new_label,
            }
        )

    if not out_rows:
        return pd.DataFrame(columns=["old_label", "new_label"])

    out = pd.DataFrame(out_rows)
    out = out.drop_duplicates(subset=["old_label"], keep="last").reset_index(drop=True)
    return out


def dataframe_to_mapping(df: pd.DataFrame) -> Dict[str, str]:
    """Convert manual/editor mapping table to dict."""
    mapping = {}

    if df is None or df.empty:
        return mapping

    if "old_label" not in df.columns or "new_label" not in df.columns:
        return mapping

    for _, row in df.iterrows():
        old_label = clean_text(row.get("old_label", ""))
        new_label = clean_text(row.get("new_label", ""))

        if not old_label or not new_label:
            continue

        if old_label == new_label:
            continue

        mapping[old_label] = new_label

    return mapping


def mapping_df_with_blank_rows(base_df: pd.DataFrame, blank_rows: int = 5) -> pd.DataFrame:
    if base_df is None or base_df.empty:
        base_df = pd.DataFrame(columns=["old_label", "new_label"])

    base_df = base_df[["old_label", "new_label"]].copy()

    blanks = pd.DataFrame(
        [{"old_label": "", "new_label": ""} for _ in range(blank_rows)]
    )

    return pd.concat([base_df, blanks], ignore_index=True)


# ============================================================
# DXF reading / writing
# ============================================================

def read_dxf_from_upload(uploaded_file):
    """Read DXF from Streamlit uploader."""
    data = uploaded_file.getvalue()

    # DXF is normally text. Try utf-8 first, fallback latin1.
    try:
        text = data.decode("utf-8")
    except Exception:
        text = data.decode("latin1", errors="ignore")

    return ezdxf.read(io.StringIO(text))


def write_dxf_to_bytes(doc) -> bytes:
    """Write ezdxf document to downloadable bytes."""
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8", errors="ignore")


def get_layer_names(doc) -> List[str]:
    try:
        names = sorted([layer.dxf.name for layer in doc.layers])
        return names
    except Exception:
        return []


# ============================================================
# DXF entity extraction
# ============================================================

def get_entity_layer(entity) -> str:
    try:
        return str(entity.dxf.layer)
    except Exception:
        return ""


def get_entity_handle(entity) -> str:
    try:
        return str(entity.dxf.handle)
    except Exception:
        return ""


def extract_entity_text(entity) -> str:
    dxftype = entity.dxftype()

    try:
        if dxftype in {"TEXT", "ATTRIB"}:
            return clean_text(entity.dxf.text)

        if dxftype == "MTEXT":
            try:
                return clean_text(entity.plain_text())
            except Exception:
                return clean_text(entity.text)

    except Exception:
        return ""

    return ""


def get_text_insert_xy(entity) -> Tuple[float, float]:
    try:
        if entity.dxftype() in {"TEXT", "ATTRIB", "MTEXT"}:
            return get_xy(entity.dxf.insert)
    except Exception:
        pass

    return 0.0, 0.0


def extract_text_records(doc, include_insert_attributes: bool = True) -> List[Dict[str, Any]]:
    """
    Extract text-like records.

    Writable:
    - modelspace TEXT
    - modelspace MTEXT
    - ATTRIB if write mode allows it

    Review-only:
    - block definition TEXT/MTEXT transformed through simple INSERT
    """
    records = []
    msp = doc.modelspace()

    # Modelspace TEXT/MTEXT
    for e in msp:
        dxftype = e.dxftype()

        if dxftype in {"TEXT", "MTEXT"}:
            label = extract_entity_text(e)
            if not label:
                continue

            x, y = get_text_insert_xy(e)

            records.append(
                {
                    "entity": e,
                    "raw_text": label,
                    "label": clean_text(label),
                    "x": x,
                    "y": y,
                    "layer": get_entity_layer(e),
                    "handle": get_entity_handle(e),
                    "text_type": dxftype,
                    "text_source": "modelspace",
                    "base_writable": True,
                }
            )

        # INSERT attributes
        if dxftype == "INSERT" and include_insert_attributes:
            try:
                attribs = list(e.attribs())
            except Exception:
                attribs = []

            for a in attribs:
                label = extract_entity_text(a)
                if not label:
                    continue

                x, y = get_text_insert_xy(a)

                records.append(
                    {
                        "entity": a,
                        "raw_text": label,
                        "label": clean_text(label),
                        "x": x,
                        "y": y,
                        "layer": get_entity_layer(a) or get_entity_layer(e),
                        "handle": get_entity_handle(a),
                        "text_type": "ATTRIB",
                        "text_source": "insert_attribute",
                        "base_writable": True,
                    }
                )

    # Simple block TEXT/MTEXT inside INSERT - review only, not written
    for ins in msp.query("INSERT"):
        try:
            block_name = ins.dxf.name
            block = doc.blocks.get(block_name)
        except Exception:
            continue

        ix, iy = get_xy(ins.dxf.insert)

        try:
            xs = float(ins.dxf.xscale)
        except Exception:
            xs = 1.0

        try:
            ys = float(ins.dxf.yscale)
        except Exception:
            ys = 1.0

        try:
            rot = float(ins.dxf.rotation)
        except Exception:
            rot = 0.0

        for be in block:
            if be.dxftype() not in {"TEXT", "MTEXT"}:
                continue

            label = extract_entity_text(be)
            if not label:
                continue

            lx, ly = get_text_insert_xy(be)
            wx, wy = transform_block_point(lx, ly, ix, iy, xs, ys, rot)

            records.append(
                {
                    "entity": None,
                    "raw_text": label,
                    "label": clean_text(label),
                    "x": wx,
                    "y": wy,
                    "layer": get_entity_layer(be),
                    "handle": f"{get_entity_handle(ins)}:{get_entity_handle(be)}",
                    "text_type": be.dxftype(),
                    "text_source": "block_definition_review_only",
                    "base_writable": False,
                }
            )

    return records


def extract_circle_records(doc) -> List[Dict[str, Any]]:
    """
    Extract modelspace circles and simple block circles inside INSERT.

    Block circles are used for detection/review but are not themselves modified.
    """
    records = []
    msp = doc.modelspace()

    # Modelspace CIRCLE
    for c in msp.query("CIRCLE"):
        try:
            cx, cy = get_xy(c.dxf.center)
            r = float(c.dxf.radius)
        except Exception:
            continue

        records.append(
            {
                "x": cx,
                "y": cy,
                "radius": r,
                "layer": get_entity_layer(c),
                "handle": get_entity_handle(c),
                "circle_source": "modelspace",
            }
        )

    # Simple block CIRCLE inside INSERT
    for ins in msp.query("INSERT"):
        try:
            block_name = ins.dxf.name
            block = doc.blocks.get(block_name)
        except Exception:
            continue

        ix, iy = get_xy(ins.dxf.insert)

        try:
            xs = float(ins.dxf.xscale)
        except Exception:
            xs = 1.0

        try:
            ys = float(ins.dxf.yscale)
        except Exception:
            ys = 1.0

        try:
            rot = float(ins.dxf.rotation)
        except Exception:
            rot = 0.0

        scale_for_radius = (abs(xs) + abs(ys)) / 2.0

        for bc in block:
            if bc.dxftype() != "CIRCLE":
                continue

            try:
                lx, ly = get_xy(bc.dxf.center)
                local_r = float(bc.dxf.radius)
            except Exception:
                continue

            wx, wy = transform_block_point(lx, ly, ix, iy, xs, ys, rot)
            wr = abs(local_r * scale_for_radius)

            records.append(
                {
                    "x": wx,
                    "y": wy,
                    "radius": wr,
                    "layer": get_entity_layer(bc),
                    "handle": f"{get_entity_handle(ins)}:{get_entity_handle(bc)}",
                    "circle_source": "block_circle",
                }
            )

    return records


# ============================================================
# Detection
# ============================================================

def layer_allowed(layer: str, selected_layers: List[str], use_all_layers: bool) -> bool:
    if use_all_layers:
        return True

    if not selected_layers:
        return True

    return layer in selected_layers


def detect_bubble_labels(
    text_records: List[Dict[str, Any]],
    circle_records: List[Dict[str, Any]],
    text_layers: List[str],
    circle_layers: List[str],
    use_all_layers: bool,
    text_in_bubble_gap: float,
    max_numeric_label: int,
    min_circle_radius: float,
    max_circle_radius: float,
    require_mapping_for_preview: bool,
    mapping: Dict[str, str],
    write_mode: str,
) -> pd.DataFrame:
    rows = []
    row_id = 1

    for t in text_records:
        label = clean_text(t.get("label", ""))

        if not probable_grid_label(label, max_numeric_label=max_numeric_label):
            continue

        if require_mapping_for_preview and label not in mapping:
            continue

        if not layer_allowed(t.get("layer", ""), text_layers, use_all_layers):
            continue

        best_circle = None
        best_score = None

        for c in circle_records:
            if not layer_allowed(c.get("layer", ""), circle_layers, use_all_layers):
                continue

            radius = float(c.get("radius", 0.0))
            if radius <= 0:
                continue

            if min_circle_radius > 0 and radius < min_circle_radius:
                continue

            if max_circle_radius > 0 and radius > max_circle_radius:
                continue

            d = dist2d(t["x"], t["y"], c["x"], c["y"])

            # Text must be inside or near the circle
            if d <= radius + float(text_in_bubble_gap):
                # Lower is better
                normalized = d / radius if radius else d

                if best_score is None or normalized < best_score:
                    best_score = normalized
                    best_circle = c

        if best_circle is None:
            continue

        mapped = label in mapping
        proposed_new = mapping.get(label, "")

        will_change = bool(mapped and proposed_new and proposed_new != label)

        writable, write_risk, write_reason = get_write_status(t, write_mode)

        default_apply = bool(mapped and will_change and writable)

        confidence = "high" if best_score is not None and best_score <= 1.0 else "medium"

        rows.append(
            {
                "apply": default_apply,
                "row_id": row_id,
                "old_label": label,
                "new_label": proposed_new,
                "will_change": will_change,
                "mapped": mapped,
                "writable": writable,
                "write_risk": write_risk,
                "write_reason": write_reason,
                "confidence": confidence,
                "text_source": t.get("text_source", ""),
                "text_type": t.get("text_type", ""),
                "text_layer": t.get("layer", ""),
                "circle_layer": best_circle.get("layer", ""),
                "text_handle": t.get("handle", ""),
                "circle_handle": best_circle.get("handle", ""),
                "x": round(float(t.get("x", 0.0)), 3),
                "y": round(float(t.get("y", 0.0)), 3),
            }
        )

        row_id += 1

    if not rows:
        return pd.DataFrame(
            columns=[
                "apply",
                "row_id",
                "old_label",
                "new_label",
                "will_change",
                "mapped",
                "writable",
                "write_risk",
                "write_reason",
                "confidence",
                "text_source",
                "text_type",
                "text_layer",
                "circle_layer",
                "text_handle",
                "circle_handle",
                "x",
                "y",
            ]
        )

    return pd.DataFrame(rows)


def get_write_status(text_record: Dict[str, Any], write_mode: str) -> Tuple[bool, str, str]:
    """
    Decide whether a detected text record is writable.

    write_mode:
    - Safe mode: modelspace TEXT/MTEXT only
    - Attribute mode: modelspace TEXT/MTEXT + INSERT attributes
    """
    source = text_record.get("text_source", "")
    text_type = text_record.get("text_type", "")
    base_writable = bool(text_record.get("base_writable", False))

    if not base_writable:
        return False, "blocked", "Review-only text from block definition; not modified."

    if source == "modelspace" and text_type in {"TEXT", "MTEXT"}:
        return True, "safe", "Writable modelspace TEXT/MTEXT."

    if source == "insert_attribute" and text_type == "ATTRIB":
        if write_mode.startswith("Attribute"):
            return True, "attribute", "Writable INSERT attribute because Attribute Mode is enabled."
        return False, "blocked", "INSERT attribute blocked in Safe Mode."

    return False, "blocked", "Unsupported or unsafe text source."


# ============================================================
# Dry-run summary
# ============================================================

def build_dry_run_summary(preview_df: pd.DataFrame) -> Dict[str, int]:
    """
    Display-only dry-run summary.

    This does not modify the DXF, preview table, mappings, or apply logic.
    It only counts what is already present in the preview dataframe.
    """
    if preview_df is None or preview_df.empty:
        return {
            "total_detected": 0,
            "mapped_found": 0,
            "approved_changes": 0,
            "already_matching": 0,
            "blocked_or_not_writable": 0,
            "unmapped_ignored": 0,
        }

    df = preview_df.copy()

    def safe_bool_col(col_name, default=False):
        if col_name not in df.columns:
            return pd.Series([default] * len(df), index=df.index)

        return df[col_name].fillna(default).apply(boolish)

    mapped = safe_bool_col("mapped", False)
    writable = safe_bool_col("writable", False)
    will_change = safe_bool_col("will_change", False)
    apply_col = safe_bool_col("apply", False)

    return {
        "total_detected": int(len(df)),
        "mapped_found": int(mapped.sum()),
        "approved_changes": int((mapped & writable & will_change & apply_col).sum()),
        "already_matching": int((mapped & ~will_change).sum()),
        "blocked_or_not_writable": int((mapped & will_change & ~writable).sum()),
        "unmapped_ignored": int((~mapped).sum()),
    }


# ============================================================
# Apply changes
# ============================================================

def set_entity_text(entity, new_text: str) -> bool:
    """Write new text to TEXT/MTEXT/ATTRIB entity."""
    if entity is None:
        return False

    try:
        dxftype = entity.dxftype()

        if dxftype in {"TEXT", "ATTRIB"}:
            entity.dxf.text = str(new_text)
            return True

        if dxftype == "MTEXT":
            # Keep it simple: replace MTEXT content with plain label.
            entity.text = str(new_text)
            return True

    except Exception:
        return False

    return False


def apply_approved_changes(
    text_records: List[Dict[str, Any]],
    edited_preview_df: pd.DataFrame,
    write_mode: str,
) -> pd.DataFrame:
    """
    Apply approved changes from edited preview table.

    Dedupe by text_handle so the same entity is not changed twice.
    """
    handle_to_record = {
        str(r.get("handle", "")): r
        for r in text_records
        if r.get("handle", "")
    }

    audit_rows = []
    changed_handles = set()

    if edited_preview_df is None or edited_preview_df.empty:
        return pd.DataFrame(columns=AUDIT_COLUMNS)

    for _, row in edited_preview_df.iterrows():
        apply_row = boolish(row.get("apply", False))
        old_label = clean_text(row.get("old_label", ""))
        new_label = clean_text(row.get("new_label", ""))
        text_handle = str(row.get("text_handle", "")).strip()

        mapped = boolish(row.get("mapped", False))
        writable = boolish(row.get("writable", False))
        will_change = boolish(row.get("will_change", False))

        base_audit = {
            "old_label": old_label,
            "new_label": new_label,
            "changed": False,
            "skipped": True,
            "reason": "",
            "text_handle": text_handle,
            "text_layer": row.get("text_layer", ""),
            "circle_layer": row.get("circle_layer", ""),
            "text_source": row.get("text_source", ""),
            "text_type": row.get("text_type", ""),
            "write_risk": row.get("write_risk", ""),
            "write_reason": row.get("write_reason", ""),
            "confidence": row.get("confidence", ""),
            "x": row.get("x", ""),
            "y": row.get("y", ""),
        }

        if not apply_row:
            base_audit["reason"] = "Not approved."
            audit_rows.append(base_audit)
            continue

        if not mapped:
            base_audit["reason"] = "No mapping."
            audit_rows.append(base_audit)
            continue

        if not writable:
            base_audit["reason"] = "Not writable."
            audit_rows.append(base_audit)
            continue

        if not will_change:
            base_audit["reason"] = "Already matching or no change required."
            audit_rows.append(base_audit)
            continue

        if not new_label:
            base_audit["reason"] = "Blank new label."
            audit_rows.append(base_audit)
            continue

        if text_handle in changed_handles:
            base_audit["reason"] = "Duplicate entity handle skipped."
            audit_rows.append(base_audit)
            continue

        rec = handle_to_record.get(text_handle)
        if not rec:
            base_audit["reason"] = "Text entity not found."
            audit_rows.append(base_audit)
            continue

        writable_now, _, reason_now = get_write_status(rec, write_mode)
        if not writable_now:
            base_audit["reason"] = f"Blocked at write time: {reason_now}"
            audit_rows.append(base_audit)
            continue

        ok = set_entity_text(rec.get("entity"), new_label)

        if ok:
            changed_handles.add(text_handle)
            base_audit["changed"] = True
            base_audit["skipped"] = False
            base_audit["reason"] = "Changed."
        else:
            base_audit["reason"] = "Write failed."

        audit_rows.append(base_audit)

    if not audit_rows:
        return pd.DataFrame(columns=AUDIT_COLUMNS)

    return pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS)


AUDIT_COLUMNS = [
    "old_label",
    "new_label",
    "changed",
    "skipped",
    "reason",
    "text_handle",
    "text_layer",
    "circle_layer",
    "text_source",
    "text_type",
    "write_risk",
    "write_reason",
    "confidence",
    "x",
    "y",
]


# ============================================================
# Streamlit App
# ============================================================

def main():
    st.set_page_config(
        page_title="iLoveStructural - Beam Detail Grid Label Sync",
        layout="wide",
    )

    st.title("iLoveStructural — Beam Detail Grid Label Sync")
    st.caption(
        "Tool 3: Sync grid/axis endpoint labels inside circular bubbles in beam longitudinal details."
    )

    st.info(
        "Recommended: Run Tool 2 Plan Grid Label Sync first and upload its audit CSV here. "
        "If you have not synced the plan, manually enter old/new mappings."
    )

    # --------------------------------------------------------
    # Mapping input
    # --------------------------------------------------------
    st.header("1. Label Mapping")

    audit_csv = st.file_uploader(
        "Optional: Upload Tool 2 audit CSV",
        type=["csv"],
        help="CSV should contain old_label and new_label columns, or similar names.",
    )

    csv_mapping_df = pd.DataFrame(columns=["old_label", "new_label"])

    if audit_csv is not None:
        csv_mapping_df = mapping_from_audit_csv(audit_csv)

        if csv_mapping_df.empty:
            st.warning(
                "No mapping rows found in the uploaded CSV. "
                "You can still enter mappings manually below."
            )
        else:
            st.success(f"Loaded {len(csv_mapping_df)} mapping row(s) from CSV.")

    st.subheader("Manual / Override Mapping Table")

    st.caption(
        "You may edit this table. Manual rows override CSV mappings when the same old_label appears."
    )

    initial_mapping_df = mapping_df_with_blank_rows(csv_mapping_df, blank_rows=8)

    mapping_editor_df = st.data_editor(
        initial_mapping_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "old_label": st.column_config.TextColumn("old_label"),
            "new_label": st.column_config.TextColumn("new_label"),
        },
        key="beam_mapping_editor",
    )

    mapping = dataframe_to_mapping(mapping_editor_df)

    c_map1, c_map2 = st.columns([1, 3])
    c_map1.metric("Active mappings", len(mapping))

    mapping_export_df = pd.DataFrame(
        [{"old_label": k, "new_label": v} for k, v in mapping.items()]
    )

    c_map2.download_button(
        "Download mapping CSV",
        data=mapping_export_df.to_csv(index=False).encode("utf-8"),
        file_name="beam_label_mapping.csv",
        mime="text/csv",
        disabled=mapping_export_df.empty,
    )

    if mapping:
        with st.expander("View active mapping dictionary"):
            st.dataframe(mapping_export_df, use_container_width=True)
    else:
        st.warning("No active mappings yet. Add at least one old_label → new_label pair.")

    # --------------------------------------------------------
    # DXF upload
    # --------------------------------------------------------
    st.header("2. Beam Detail DXF")

    dxf_upload = st.file_uploader(
        "Upload Beam Detail / Target DXF",
        type=["dxf"],
    )

    if dxf_upload is None:
        st.stop()

    try:
        doc = read_dxf_from_upload(dxf_upload)
    except Exception as e:
        st.error(f"Could not read DXF: {e}")
        st.stop()

    layers = get_layer_names(doc)

    st.success("DXF loaded successfully.")

    # --------------------------------------------------------
    # Settings
    # --------------------------------------------------------
    st.header("3. Detection Settings")

    with st.expander("Layer and safety settings", expanded=True):
        use_all_layers = st.checkbox(
            "Use all layers",
            value=True,
            help="If checked, text and circles from all layers are considered.",
        )

        if use_all_layers:
            text_layers = []
            circle_layers = []
        else:
            text_layers = st.multiselect(
                "Text layers",
                options=layers,
                default=layers,
            )

            circle_layers = st.multiselect(
                "Circle / bubble layers",
                options=layers,
                default=layers,
            )

        c1, c2, c3 = st.columns(3)

        text_in_bubble_gap = c1.number_input(
            "Text-in-bubble gap",
            min_value=0.0,
            value=2.0,
            step=0.5,
            help="Extra distance allowed outside the circle radius.",
        )

        max_numeric_label = c2.number_input(
            "Max numeric grid label",
            min_value=1,
            value=99,
            step=1,
            help="Numeric labels above this are ignored, helping avoid dimensions.",
        )

        require_mapping_for_preview = c3.checkbox(
            "Show mapped labels only",
            value=True,
            help="Recommended. Reduces noise by hiding unmapped detected bubble labels.",
        )

        c4, c5, c6 = st.columns(3)

        min_circle_radius = c4.number_input(
            "Minimum circle radius",
            min_value=0.0,
            value=0.0,
            step=0.5,
            help="0 means no minimum filter.",
        )

        max_circle_radius = c5.number_input(
            "Maximum circle radius",
            min_value=0.0,
            value=0.0,
            step=0.5,
            help="0 means no maximum filter.",
        )

        write_mode = c6.selectbox(
            "Write mode",
            options=[
                "Safe mode: modelspace TEXT/MTEXT only",
                "Attribute mode: modelspace TEXT/MTEXT + INSERT attributes",
            ],
            index=0,
        )

    # --------------------------------------------------------
    # Extract and detect
    # --------------------------------------------------------
    st.header("4. Preview Detected Bubble Labels")

    text_records = extract_text_records(
        doc,
        include_insert_attributes=True,
    )

    circle_records = extract_circle_records(doc)

    c_ext1, c_ext2 = st.columns(2)
    c_ext1.metric("Text-like records extracted", len(text_records))
    c_ext2.metric("Circle records extracted", len(circle_records))

    preview_df = detect_bubble_labels(
        text_records=text_records,
        circle_records=circle_records,
        text_layers=text_layers,
        circle_layers=circle_layers,
        use_all_layers=use_all_layers,
        text_in_bubble_gap=text_in_bubble_gap,
        max_numeric_label=max_numeric_label,
        min_circle_radius=min_circle_radius,
        max_circle_radius=max_circle_radius,
        require_mapping_for_preview=require_mapping_for_preview,
        mapping=mapping,
        write_mode=write_mode,
    )

    if preview_df.empty:
        st.warning(
            "No bubble labels detected with the current settings. "
            "Try increasing Text-in-bubble gap, using all layers, or disabling mapped-only preview."
        )
        st.stop()

    st.caption(
        "Review rows below. Only rows with apply=True, mapped=True, writable=True, "
        "and will_change=True will be written."
    )

    edited_preview_df = st.data_editor(
        preview_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "apply": st.column_config.CheckboxColumn("apply"),
            "old_label": st.column_config.TextColumn("old_label", disabled=True),
            "new_label": st.column_config.TextColumn(
                "new_label",
                help="You may edit the proposed replacement before applying.",
            ),
            "will_change": st.column_config.CheckboxColumn("will_change", disabled=True),
            "mapped": st.column_config.CheckboxColumn("mapped", disabled=True),
            "writable": st.column_config.CheckboxColumn("writable", disabled=True),
        },
        disabled=[
            "row_id",
            "old_label",
            "will_change",
            "mapped",
            "writable",
            "write_risk",
            "write_reason",
            "confidence",
            "text_source",
            "text_type",
            "text_layer",
            "circle_layer",
            "text_handle",
            "circle_handle",
            "x",
            "y",
        ],
        key="beam_preview_editor",
    )

    # --------------------------------------------------------
    # Dry-run summary
    # --------------------------------------------------------
    st.subheader("Dry-Run Summary")

    summary = build_dry_run_summary(edited_preview_df)

    s1, s2, s3 = st.columns(3)
    s4, s5, s6 = st.columns(3)

    s1.metric("Total bubble labels detected", summary["total_detected"])
    s2.metric("Mapped labels found", summary["mapped_found"])
    s3.metric("Approved changes", summary["approved_changes"])

    s4.metric("Already matching", summary["already_matching"])
    s5.metric("Blocked / not writable", summary["blocked_or_not_writable"])
    s6.metric("Unmapped ignored", summary["unmapped_ignored"])

    # --------------------------------------------------------
    # Apply
    # --------------------------------------------------------
    st.header("5. Apply Approved Changes")

    st.warning(
        "This will modify only approved writable labels in memory. "
        "Your original uploaded DXF is not changed. Download the updated DXF after applying."
    )

    apply_clicked = st.button(
        "Apply Approved Beam Detail Label Changes",
        type="primary",
        disabled=summary["approved_changes"] == 0,
    )

    if not apply_clicked:
        st.stop()

    audit_df = apply_approved_changes(
        text_records=text_records,
        edited_preview_df=edited_preview_df,
        write_mode=write_mode,
    )

    changed_count = int(audit_df["changed"].sum()) if not audit_df.empty else 0
    skipped_count = int(audit_df["skipped"].sum()) if not audit_df.empty else 0

    st.success(f"Apply complete. Changed: {changed_count}. Skipped: {skipped_count}.")

    st.subheader("Audit Report")
    st.dataframe(audit_df, use_container_width=True)

    audit_bytes = audit_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        "Download Beam Detail Sync Audit CSV",
        data=audit_bytes,
        file_name="beam_detail_label_sync_audit.csv",
        mime="text/csv",
    )

    try:
        updated_dxf_bytes = write_dxf_to_bytes(doc)

        st.download_button(
            "Download Updated Beam Detail DXF",
            data=updated_dxf_bytes,
            file_name="beam_detail_labels_synced.dxf",
            mime="application/dxf",
        )

    except Exception as e:
        st.error(f"Could not write updated DXF: {e}")


if __name__ == "__main__":
    main()
