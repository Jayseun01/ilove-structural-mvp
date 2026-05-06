import streamlit as st
import ezdxf
import tempfile
import os
import math
import re
import io
import csv
import pandas as pd


# =========================================================
# APP CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - Beam Detail Label Sync",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ iLoveStructural")
st.subheader("Tool 3: Beam Detail Grid Label Sync")
st.caption(
    "Workflow: Upload beam detail DXF → load/edit old/new label mapping → detect circular detail labels → preview → apply approved changes."
)


# =========================================================
# CONSTANTS
# =========================================================

WRITE_MODE_SAFE = "Safe mode: modelspace TEXT/MTEXT only"
WRITE_MODE_ATTRIB = "Attribute mode: modelspace TEXT/MTEXT + INSERT attributes"

ALL_LAYERS = "__ALL_LAYERS__"


# =========================================================
# BASIC HELPERS
# =========================================================

def clean_text(value):
    if value is None:
        return ""

    txt = str(value)
    txt = txt.replace("\\P", " ")
    txt = txt.replace("\n", " ")
    txt = txt.replace("′", "'")
    txt = txt.replace("×", "X")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()


def probable_grid_label(text):
    text = clean_text(text)

    patterns = [
        r"[A-Z]{1,3}",
        r"[A-Z]{1,3}'",
        r"\d{1,3}",
        r"\d{1,3}[A-Z]?",
        r"\d{1,3}[A-Z]?'?",
    ]

    return any(re.fullmatch(p, text) for p in patterns)


def is_zero_padded_detail_number(text):
    text = clean_text(text)
    return bool(re.fullmatch(r"0\d{1,3}", text))


def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def save_uploaded_to_temp(uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name


def uploaded_file_signature(uploaded_file):
    if uploaded_file is None:
        return None
    return uploaded_file.name, len(uploaded_file.getvalue())


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


def get_entity_handle(entity):
    try:
        return entity.dxf.handle
    except Exception:
        return ""


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def layer_allowed(entity_layer, selected_layer):
    if selected_layer == ALL_LAYERS:
        return True

    return clean_text(entity_layer) == clean_text(selected_layer)


def audit_to_csv_bytes(rows):
    if not rows:
        return b""

    fieldnames = sorted(set().union(*[row.keys() for row in rows]))

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()

    for row in rows:
        safe_row = {}
        for k in fieldnames:
            v = row.get(k, "")
            if k in ("entity", "marker"):
                v = ""
            safe_row[k] = v
        writer.writerow(safe_row)

    return buffer.getvalue().encode("utf-8")


# =========================================================
# DXF TEXT HELPERS
# =========================================================

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


# =========================================================
# WRITE PROFILE
# =========================================================

def marker_write_profile(marker, write_mode):
    source = marker.get("text_source", "")
    text_type = marker.get("text_type", "")

    if source == "modelspace" and text_type in ("TEXT", "MTEXT"):
        return {
            "writable": True,
            "write_risk": "low",
            "write_reason": "modelspace_text",
        }

    if source == "insert_attrib" and text_type == "ATTRIB":
        if write_mode == WRITE_MODE_ATTRIB:
            return {
                "writable": True,
                "write_risk": "medium",
                "write_reason": "insert_attribute_instance",
            }

        return {
            "writable": False,
            "write_risk": "blocked",
            "write_reason": "attribute_blocked_in_safe_mode",
        }

    return {
        "writable": False,
        "write_risk": "blocked",
        "write_reason": "unsupported_or_block_definition_text",
    }


# =========================================================
# DXF EXTRACTION
# =========================================================

def extract_texts(doc, selected_text_layer):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() in ("TEXT", "MTEXT"):
                if not layer_allowed(e.dxf.layer, selected_text_layer):
                    continue

                texts.append({
                    "entity": e,
                    "text": get_text_value(e),
                    "point": get_text_point(e),
                    "layer": e.dxf.layer,
                    "type": e.dxftype(),
                    "source": "modelspace",
                    "handle": get_entity_handle(e),
                })

            elif e.dxftype() == "INSERT":
                try:
                    for att in e.attribs:
                        if not layer_allowed(att.dxf.layer, selected_text_layer):
                            continue

                        texts.append({
                            "entity": att,
                            "text": get_text_value(att),
                            "point": get_text_point(att),
                            "layer": att.dxf.layer,
                            "type": "ATTRIB",
                            "source": "insert_attrib",
                            "parent_insert": e,
                            "parent_layer": e.dxf.layer,
                            "handle": get_entity_handle(att),
                        })
                except Exception:
                    pass

                # Optional read-only block definition text detection.
                # We detect it for review, but do not write it in this MVP.
                try:
                    if e.dxf.name in doc.blocks:
                        block = doc.blocks[e.dxf.name]

                        for be in block:
                            if be.dxftype() not in ("TEXT", "MTEXT"):
                                continue

                            if not layer_allowed(be.dxf.layer, selected_text_layer):
                                continue

                            lp = get_text_point(be)
                            wp = transform_block_point(lp, e)

                            texts.append({
                                "entity": be,
                                "text": get_text_value(be),
                                "point": wp,
                                "layer": be.dxf.layer,
                                "type": be.dxftype(),
                                "source": "block_text_readonly",
                                "parent_insert": e,
                                "parent_layer": e.dxf.layer,
                                "handle": get_entity_handle(be),
                            })
                except Exception:
                    pass

        except Exception:
            continue

    return texts


def extract_circles(doc, selected_circle_layer):
    circles = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "CIRCLE":
                if not layer_allowed(e.dxf.layer, selected_circle_layer):
                    continue

                c = e.dxf.center

                circles.append({
                    "entity": e,
                    "center": (float(c.x), float(c.y)),
                    "radius": float(e.dxf.radius),
                    "layer": e.dxf.layer,
                    "source": "modelspace",
                    "handle": get_entity_handle(e),
                })

            elif e.dxftype() == "INSERT":
                try:
                    if e.dxf.name not in doc.blocks:
                        continue

                    block = doc.blocks[e.dxf.name]

                    for be in block:
                        if be.dxftype() != "CIRCLE":
                            continue

                        if not layer_allowed(be.dxf.layer, selected_circle_layer):
                            continue

                        c = be.dxf.center

                        circles.append({
                            "entity": e,
                            "nested_entity": be,
                            "center": transform_block_point((float(c.x), float(c.y)), e),
                            "radius": transform_block_radius(float(be.dxf.radius), e),
                            "layer": be.dxf.layer,
                            "source": "block_circle",
                            "handle": get_entity_handle(e),
                            "nested_handle": get_entity_handle(be),
                        })
                except Exception:
                    pass

        except Exception:
            continue

    return circles


# =========================================================
# MAPPING HELPERS
# =========================================================

def find_col(fieldnames, candidates):
    upper_map = {clean_text(x): x for x in fieldnames}

    for c in candidates:
        c_clean = clean_text(c)
        if c_clean in upper_map:
            return upper_map[c_clean]

    return None


def mapping_from_audit_csv(uploaded_csv):
    if uploaded_csv is None:
        return [], []

    raw = uploaded_csv.getvalue().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(raw))

    if not reader.fieldnames:
        return [], ["CSV has no header row."]

    old_col = find_col(reader.fieldnames, [
        "old_label",
        "old",
        "target_old_label",
        "from",
        "source_old_label",
    ])

    new_col = find_col(reader.fieldnames, [
        "new_label",
        "new",
        "proposed_new_label",
        "to",
        "source_label",
    ])

    changed_col = find_col(reader.fieldnames, [
        "changed",
        "updated",
    ])

    warnings = []

    if old_col is None or new_col is None:
        warnings.append(
            f"Could not find old/new label columns. CSV columns found: {reader.fieldnames}"
        )
        return [], warnings

    mapping = {}

    for row in reader:
        old_label = clean_text(row.get(old_col, ""))
        new_label = clean_text(row.get(new_col, ""))

        if not old_label or not new_label:
            continue

        if old_label == new_label:
            continue

        if changed_col:
            changed_value = clean_text(row.get(changed_col, ""))
            # Keep rows marked true, but also allow blank/unknown CSVs.
            if changed_value and changed_value not in ("TRUE", "1", "YES", "Y"):
                continue

        if old_label not in mapping:
            mapping[old_label] = new_label

        elif mapping[old_label] != new_label:
            warnings.append(
                f"Conflicting mapping for {old_label}: {mapping[old_label]} vs {new_label}. Keeping first."
            )

    rows = [
        {
            "old_label": old_label,
            "new_label": new_label,
            "apply": True,
        }
        for old_label, new_label in sorted(mapping.items())
    ]

    return rows, warnings


def normalize_mapping_rows(rows):
    mapping = {}
    warnings = []

    for idx, row in enumerate(rows, start=1):
        apply_row = row.get("apply", True)

        if isinstance(apply_row, str):
            apply_row = apply_row.strip().lower() in ("true", "1", "yes", "y")

        if not apply_row:
            continue

        old_label = clean_text(row.get("old_label", ""))
        new_label = clean_text(row.get("new_label", ""))

        if not old_label and not new_label:
            continue

        if not old_label or not new_label:
            warnings.append(f"Mapping row {idx} is incomplete and was ignored.")
            continue

        if not probable_grid_label(old_label):
            warnings.append(f"Mapping row {idx}: old label '{old_label}' does not look like a grid label.")

        if not probable_grid_label(new_label):
            warnings.append(f"Mapping row {idx}: new label '{new_label}' does not look like a grid label.")

        if old_label in mapping and mapping[old_label] != new_label:
            warnings.append(
                f"Duplicate old label '{old_label}' has conflicting values. Keeping first: {mapping[old_label]}."
            )
            continue

        if old_label != new_label:
            mapping[old_label] = new_label

    return mapping, warnings


def default_mapping_df(csv_rows):
    rows = list(csv_rows)

    # Add blank manual rows.
    for _ in range(8):
        rows.append({
            "apply": True,
            "old_label": "",
            "new_label": "",
        })

    return pd.DataFrame(rows, columns=["apply", "old_label", "new_label"])


# =========================================================
# BEAM DETAIL MARKER DETECTION
# =========================================================

def text_inside_circle(text_point, circle_center, radius, extra_gap):
    return euclidean(text_point, circle_center) <= radius + extra_gap


def detect_detail_label_markers(
    doc,
    text_layer,
    circle_layer,
    mapping,
    text_gap,
    write_mode,
    require_mapping_for_preview=False,
):
    texts = extract_texts(doc, text_layer)
    circles = extract_circles(doc, circle_layer)

    markers = []
    rejected = []

    for c in circles:
        center = c["center"]
        radius = c["radius"]

        candidates = []

        for t in texts:
            label = clean_text(t["text"])

            if not label:
                continue

            if not probable_grid_label(label):
                continue

            if is_zero_padded_detail_number(label):
                continue

            if require_mapping_for_preview and label not in mapping:
                continue

            if text_inside_circle(t["point"], center, radius, extra_gap=text_gap):
                candidates.append(t)

        if not candidates:
            rejected.append({
                "circle_center": center,
                "circle_radius": radius,
                "reason": "No probable grid label text inside circle",
                "circle_layer": c.get("layer", ""),
                "circle_source": c.get("source", ""),
            })
            continue

        candidates = sorted(candidates, key=lambda t: euclidean(t["point"], center))
        t = candidates[0]

        old_label = clean_text(t["text"])
        new_label = mapping.get(old_label, "")

        marker = {
            "old_label": old_label,
            "new_label": new_label,
            "mapped": bool(new_label),
            "entity": t["entity"],
            "text_point": t["point"],
            "circle_center": center,
            "circle_radius": radius,
            "text_layer": t.get("layer", ""),
            "circle_layer": c.get("layer", ""),
            "text_source": t.get("source", ""),
            "text_type": t.get("type", ""),
            "text_handle": t.get("handle", ""),
            "circle_source": c.get("source", ""),
            "circle_handle": c.get("handle", ""),
            "candidate_texts": [x["text"] for x in candidates[:5]],
            "text_matches": len(candidates),
        }

        profile = marker_write_profile(marker, write_mode)

        marker["writable"] = profile["writable"]
        marker["write_risk"] = profile["write_risk"]
        marker["write_reason"] = profile["write_reason"]

        confidence = 60

        if len(candidates) == 1:
            confidence += 15

        if marker["mapped"]:
            confidence += 15

        if marker["writable"]:
            confidence += 10
        else:
            confidence -= 20

        marker["confidence"] = max(0, min(100, confidence))

        markers.append(marker)

    return {
        "texts": texts,
        "circles": circles,
        "markers": markers,
        "rejected": rejected,
    }


def build_preview_rows(markers):
    rows = []

    for idx, m in enumerate(markers, start=1):
        old_label = clean_text(m.get("old_label", ""))
        new_label = clean_text(m.get("new_label", ""))

        will_change = bool(new_label and old_label != new_label and m.get("writable"))

        rows.append({
            "apply": will_change,
            "row_id": idx,
            "old_label": old_label,
            "new_label": new_label,
            "will_change": will_change,
            "mapped": m.get("mapped", False),
            "writable": m.get("writable", False),
            "write_risk": m.get("write_risk", ""),
            "write_reason": m.get("write_reason", ""),
            "confidence": m.get("confidence", 0),
            "text_source": m.get("text_source", ""),
            "text_type": m.get("text_type", ""),
            "text_layer": m.get("text_layer", ""),
            "circle_layer": m.get("circle_layer", ""),
            "text_handle": m.get("text_handle", ""),
            "x": round(float(m["circle_center"][0]), 3),
            "y": round(float(m["circle_center"][1]), 3),
        })

    return rows


def build_dry_run_summary_from_preview(approved_markers, skipped_markers, all_markers):
    """
    Display-only dry-run summary.
    This does not modify DXF, mappings, markers, detection, or apply logic.
    """

    total_detected = len(all_markers or [])

    mapped_found = len([
        m for m in all_markers
        if m.get("mapped")
    ])

    approved_rows = len(approved_markers or [])

    approved_changes = len([
        m for m in approved_markers
        if clean_text(m.get("old_label", "")) != clean_text(m.get("new_label", ""))
        and m.get("writable")
        and clean_text(m.get("new_label", ""))
    ])

    already_matching = len([
        m for m in approved_markers
        if clean_text(m.get("old_label", "")) == clean_text(m.get("new_label", ""))
        and clean_text(m.get("new_label", ""))
    ])

    blocked_or_not_writable = len([
        m for m in approved_markers
        if not m.get("writable")
    ])

    unmapped_detected = len([
        m for m in all_markers
        if not m.get("mapped")
    ])

    unchecked_rows = len(skipped_markers or [])

    return {
        "total_detected": total_detected,
        "mapped_found": mapped_found,
        "approved_rows": approved_rows,
        "approved_changes": approved_changes,
        "already_matching": already_matching,
        "blocked_or_not_writable": blocked_or_not_writable,
        "unmapped_detected": unmapped_detected,
        "unchecked_rows": unchecked_rows,
    }


def preview_editor_to_approved_markers(markers, editor_result):
    if editor_result is None:
        return [], markers

    try:
        rows = editor_result.to_dict("records")
    except Exception:
        rows = list(editor_result)

    marker_by_id = {
        i + 1: marker
        for i, marker in enumerate(markers)
    }

    approved = []
    skipped = []

    for row in rows:
        row_id = row.get("row_id")
        marker = marker_by_id.get(row_id)

        if marker is None:
            continue

        apply_row = row.get("apply", False)

        if isinstance(apply_row, str):
            apply_row = apply_row.strip().lower() in ("true", "1", "yes", "y")

        edited_new_label = clean_text(row.get("new_label", marker.get("new_label", "")))

        marker_copy = dict(marker)
        marker_copy["new_label"] = edited_new_label

        if apply_row:
            approved.append(marker_copy)
        else:
            skipped.append(marker_copy)

    return approved, skipped


def apply_label_changes(markers):
    changed = 0
    skipped = 0
    audit = []
    seen_handles = set()

    for m in markers:
        entity = m["entity"]
        handle = m.get("text_handle") or get_entity_handle(entity) or f"entity_{id(entity)}"

        if handle in seen_handles:
            skipped += 1
            audit.append({
                "old_label": m.get("old_label", ""),
                "new_label": m.get("new_label", ""),
                "changed": False,
                "skipped": True,
                "reason": "duplicate_entity_handle_skipped",
                "text_handle": handle,
                "text_layer": m.get("text_layer", ""),
            })
            continue

        seen_handles.add(handle)

        old_label = get_text_value(entity)
        new_label = clean_text(m.get("new_label", ""))

        base = {
            "old_label": old_label,
            "new_label": new_label,
            "text_handle": handle,
            "text_layer": m.get("text_layer", ""),
            "circle_layer": m.get("circle_layer", ""),
            "text_source": m.get("text_source", ""),
            "text_type": m.get("text_type", ""),
            "write_risk": m.get("write_risk", ""),
            "write_reason": m.get("write_reason", ""),
            "confidence": m.get("confidence", ""),
            "x": round(float(m["circle_center"][0]), 3),
            "y": round(float(m["circle_center"][1]), 3),
        }

        if not m.get("writable"):
            skipped += 1
            row = dict(base)
            row.update({
                "changed": False,
                "skipped": True,
                "reason": "not_writable",
            })
            audit.append(row)
            continue

        if not new_label:
            skipped += 1
            row = dict(base)
            row.update({
                "changed": False,
                "skipped": True,
                "reason": "blank_new_label",
            })
            audit.append(row)
            continue

        if old_label == new_label:
            row = dict(base)
            row.update({
                "changed": False,
                "skipped": False,
                "reason": "already_matches",
            })
            audit.append(row)
            continue

        ok = set_text_value(entity, new_label)

        if ok:
            changed += 1
        else:
            skipped += 1

        row = dict(base)
        row.update({
            "changed": ok,
            "skipped": not ok,
            "reason": "updated" if ok else "write_failed",
        })
        audit.append(row)

    return changed, skipped, audit


# =========================================================
# SESSION STATE
# =========================================================

def init_state():
    defaults = {
        "doc_loaded": False,
        "doc": None,
        "dxf_name": "",
        "dxf_sig": None,
        "detection": {},
        "preview_rows": [],
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
        "doc_loaded",
        "doc",
        "dxf_name",
        "dxf_sig",
        "detection",
        "preview_rows",
        "audit",
        "changed",
        "skipped",
        "prepared",
    ]:
        if k in st.session_state:
            del st.session_state[k]


init_state()


# =========================================================
# UI - SETTINGS
# =========================================================

st.markdown("### 1. Mapping Source")

m1, m2 = st.columns(2)

with m1:
    audit_csv_file = st.file_uploader(
        "Optional: Upload Plan Sync Audit CSV",
        type=["csv"],
        help="Recommended if you already ran Tool 2. The tool reads old_label → new_label mapping.",
        key="audit_csv_upload",
    )

with m2:
    st.info(
        "If no CSV is available, manually enter old/new label mapping below. "
        "Manual edits override or add to CSV mappings."
    )

csv_rows, csv_warnings = mapping_from_audit_csv(audit_csv_file)

for w in csv_warnings:
    st.warning(w)

st.write("#### Label Mapping Table")

mapping_df = default_mapping_df(csv_rows)

edited_mapping = st.data_editor(
    mapping_df,
    use_container_width=True,
    hide_index=True,
    num_rows="dynamic",
    column_config={
        "apply": st.column_config.CheckboxColumn(
            "Use?",
            default=True,
            help="Uncheck a mapping row to ignore it.",
        ),
        "old_label": st.column_config.TextColumn(
            "Old Label",
            help="Label currently found in beam detail bubbles.",
        ),
        "new_label": st.column_config.TextColumn(
            "New Label",
            help="Replacement label.",
        ),
    },
    key="mapping_editor",
)

mapping, mapping_warnings = normalize_mapping_rows(
    edited_mapping.to_dict("records")
)

for w in mapping_warnings:
    st.warning(w)

st.write({
    "active_mapping_count": len(mapping),
    "mapping": mapping,
})

# Safe addition:
# Optional mapping export. This only exports the current old_label -> new_label table.
# It does not affect DXF detection or writing.
if mapping:
    mapping_export_df = pd.DataFrame(
        [
            {
                "old_label": old_label,
                "new_label": new_label,
            }
            for old_label, new_label in sorted(mapping.items())
        ]
    )

    st.download_button(
        "📄 Download Current Mapping CSV",
        data=mapping_export_df.to_csv(index=False).encode("utf-8"),
        file_name="BEAM_DETAIL_LABEL_MAPPING.csv",
        mime="text/csv",
        key="download_beam_detail_mapping_csv",
    )


st.markdown("### 2. Upload Beam Detail DXF")

dxf_file = st.file_uploader(
    "Beam Detail / Target DXF",
    type=["dxf"],
    key="beam_detail_dxf_upload",
)

dxf_sig = uploaded_file_signature(dxf_file)

if dxf_sig != st.session_state.dxf_sig:
    reset_state()
    init_state()
    st.session_state.dxf_sig = dxf_sig

if dxf_file:
    if not st.session_state.doc_loaded:
        tmp_path = None

        try:
            tmp_path = save_uploaded_to_temp(dxf_file)
            st.session_state.doc = ezdxf.readfile(tmp_path)
            st.session_state.dxf_name = dxf_file.name
            st.session_state.doc_loaded = True
            st.success("DXF loaded successfully.")

        except Exception as e:
            st.error(f"Failed to load DXF: {e}")
            st.stop()

        finally:
            safe_remove_file(tmp_path)

else:
    st.info("Upload a Beam Detail DXF to continue.")
    st.stop()


doc = st.session_state.doc
layers = get_layer_names(doc)
layer_options = [ALL_LAYERS] + layers


st.markdown("### 3. Detection Settings")

c1, c2, c3 = st.columns(3)

with c1:
    text_layer = st.selectbox(
        "Beam Detail Label Text Layer",
        layer_options,
        index=0,
        help="Choose the layer containing endpoint label text. Use ALL_LAYERS if unsure.",
        key="beam_text_layer",
    )

with c2:
    circle_layer = st.selectbox(
        "Beam Detail Bubble/Circle Layer",
        layer_options,
        index=0,
        help="Choose the layer containing circular endpoint bubbles. Use ALL_LAYERS if unsure.",
        key="beam_circle_layer",
    )

with c3:
    text_gap = st.slider(
        "Text-in-Bubble Gap",
        20.0,
        2000.0,
        180.0,
        10.0,
        help="Extra tolerance for matching text to circular bubbles.",
        key="beam_text_gap",
    )

c4, c5 = st.columns(2)

with c4:
    write_mode = st.selectbox(
        "Write Mode",
        [
            WRITE_MODE_SAFE,
            WRITE_MODE_ATTRIB,
        ],
        index=0,
        help="Start with Safe mode. Attribute mode writes INSERT attributes too.",
        key="beam_write_mode",
    )

with c5:
    require_mapping_for_preview = st.checkbox(
        "Only show labels that exist in mapping",
        value=False,
        help="If ON, unmapped bubble labels are hidden from preview.",
        key="require_mapping_for_preview",
    )

analyze = st.button(
    "🔎 Analyze Beam Detail Labels",
    type="primary",
    key="analyze_beam_detail_labels",
)


# =========================================================
# ANALYZE
# =========================================================

if analyze:
    if not mapping:
        st.warning("No active mapping found. Add mapping rows or upload a valid audit CSV first.")

    detection = detect_detail_label_markers(
        doc,
        text_layer=text_layer,
        circle_layer=circle_layer,
        mapping=mapping,
        text_gap=text_gap,
        write_mode=write_mode,
        require_mapping_for_preview=require_mapping_for_preview,
    )

    preview_rows = build_preview_rows(detection["markers"])

    st.session_state.detection = detection
    st.session_state.preview_rows = preview_rows
    st.session_state.audit = []
    st.session_state.changed = 0
    st.session_state.skipped = 0
    st.session_state.prepared = True

    mapped_count = len([m for m in detection["markers"] if m.get("mapped")])
    writable_mapped = len([
        m for m in detection["markers"]
        if m.get("mapped") and m.get("writable")
    ])

    st.success(
        f"Analysis complete. Circles found: {len(detection['circles'])}. "
        f"Text candidates found: {len(detection['texts'])}. "
        f"Detail label markers found: {len(detection['markers'])}. "
        f"Mapped labels: {mapped_count}. Writable mapped labels: {writable_mapped}."
    )


# =========================================================
# RESULTS / PREVIEW / APPLY
# =========================================================

if st.session_state.prepared:
    st.markdown("---")
    st.markdown("### 4. Detection Summary")

    detection = st.session_state.detection

    summary = {
        "texts_found": len(detection.get("texts", [])),
        "circles_found": len(detection.get("circles", [])),
        "detail_label_markers_found": len(detection.get("markers", [])),
        "rejected_circles": len(detection.get("rejected", [])),
        "mapped_markers": len([m for m in detection.get("markers", []) if m.get("mapped")]),
        "writable_markers": len([m for m in detection.get("markers", []) if m.get("writable")]),
        "write_mode": write_mode,
    }

    st.write(summary)

    if detection.get("rejected"):
        with st.expander("Rejected circles / no label found", expanded=False):
            st.dataframe(detection["rejected"], use_container_width=True)

    st.markdown("### 5. Preview Beam Detail Label Changes")

    if not st.session_state.preview_rows:
        st.info("No beam detail label markers found.")
    else:
        preview_df = pd.DataFrame(st.session_state.preview_rows)

        edited_preview = st.data_editor(
            preview_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "apply": st.column_config.CheckboxColumn(
                    "Apply?",
                    default=True,
                    help="Only checked rows will be modified.",
                ),
                "new_label": st.column_config.TextColumn(
                    "New Label",
                    help="Editable replacement label.",
                ),
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
                "x",
                "y",
            ],
            key="beam_preview_editor",
        )

        approved_markers, skipped_markers = preview_editor_to_approved_markers(
            detection["markers"],
            edited_preview,
        )

        # Safe addition:
        # Dry-run summary only reads the preview result.
        # It does not change DXF detection, marker selection, or writing.
        dry_run_summary = build_dry_run_summary_from_preview(
            approved_markers=approved_markers,
            skipped_markers=skipped_markers,
            all_markers=detection.get("markers", []),
        )

        will_change = dry_run_summary["approved_changes"]
        blocked = dry_run_summary["blocked_or_not_writable"]

        st.markdown("### 6. Apply Approved Changes")

        st.write({
            "total_detected": dry_run_summary["total_detected"],
            "mapped_found": dry_run_summary["mapped_found"],
            "approved_rows": dry_run_summary["approved_rows"],
            "will_change": dry_run_summary["approved_changes"],
            "already_matching": dry_run_summary["already_matching"],
            "blocked_or_not_writable": dry_run_summary["blocked_or_not_writable"],
            "unmapped_detected": dry_run_summary["unmapped_detected"],
            "unchecked_rows": dry_run_summary["unchecked_rows"],
        })

        d1, d2, d3, d4 = st.columns(4)

        d1.metric("Detected labels", dry_run_summary["total_detected"])
        d2.metric("Mapped labels", dry_run_summary["mapped_found"])
        d3.metric("Will change", dry_run_summary["approved_changes"])
        d4.metric("Blocked", dry_run_summary["blocked_or_not_writable"])

        confirm = st.checkbox(
            "I reviewed the preview and understand this will modify the DXF.",
            value=False,
            key="confirm_beam_detail_apply",
        )

        if st.button(
            "✍️ Apply Approved Beam Detail Label Sync",
            disabled=not confirm or not approved_markers,
            key="apply_beam_detail_sync",
        ):
            changed, skipped, audit = apply_label_changes(approved_markers)

            unchecked_audit = []

            for m in skipped_markers:
                unchecked_audit.append({
                    "old_label": m.get("old_label", ""),
                    "new_label": m.get("new_label", ""),
                    "changed": False,
                    "skipped": True,
                    "reason": "user_unchecked",
                    "text_handle": m.get("text_handle", ""),
                    "text_layer": m.get("text_layer", ""),
                    "x": round(float(m["circle_center"][0]), 3),
                    "y": round(float(m["circle_center"][1]), 3),
                })

            st.session_state.changed += changed
            st.session_state.skipped += skipped + len(skipped_markers)
            st.session_state.audit.extend(audit + unchecked_audit)

            if changed:
                st.success(f"Beam detail label sync complete. Changed {changed} text entities.")
            else:
                st.info("Beam detail label sync completed. No text entities needed changing.")

            if skipped:
                st.warning(f"Skipped {skipped} approved rows.")

            if skipped_markers:
                st.info(f"{len(skipped_markers)} rows were unchecked and skipped.")

    if st.session_state.audit:
        st.markdown("### 7. Audit Report")
        st.dataframe(st.session_state.audit, use_container_width=True)

        audit_csv = audit_to_csv_bytes(st.session_state.audit)

        st.download_button(
            "📄 Download Beam Detail Audit CSV",
            data=audit_csv,
            file_name=f"BEAM_DETAIL_AUDIT_{st.session_state.dxf_name}.csv",
            mime="text/csv",
            key="download_beam_detail_audit_csv",
        )

    st.markdown("### 8. Download Updated DXF")

    dxf_bytes = write_doc_to_temp_bytes(st.session_state.doc)

    st.download_button(
        "📥 Download Updated Beam Detail DXF",
        data=dxf_bytes,
        file_name=f"BEAM_DETAIL_RELABELED_{st.session_state.dxf_name}",
        mime="application/dxf",
        key="download_beam_detail_dxf",
    )
