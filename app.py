import streamlit as st
import ezdxf
import io
import os
import tempfile
import re
import math
import csv

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# =========================================================
# SIDEBAR / HEADER
# =========================================================
st.sidebar.title("Navigation")
tool_choice = st.sidebar.radio(
    "Select a Tool:",
    ["1. DXF Smart Purger", "2. Grid & Beam Sync"]
)
st.sidebar.markdown("---")
st.sidebar.info("Developed by James Oluwaseun Emmanuel")

st.title("🏗️ iLoveStructural")


# =========================================================
# GENERAL HELPERS
# =========================================================
def save_uploaded_to_temp(uploaded_file):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp.write(uploaded_file.getvalue())
    tmp.close()
    return tmp.name


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def dist(p1, p2):
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def midpoint(p1, p2):
    return ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)


def clean_text(text):
    if text is None:
        return ""
    return str(text).strip().upper()


def is_probable_grid_label(text):
    """
    Keep only likely grid labels.
    Examples accepted:
    A, B, AA
    1, 2, 10
    A', B'
    1A, 2A
    """
    text = clean_text(text)

    patterns = [
        r"^[A-Z]{1,2}$",        # A, B, AA
        r"^\d{1,2}$",           # 1, 2, 10
        r"^[A-Z]{1,2}'$",       # A', B'
        r"^\d{1,2}[A-Z]$",      # 1A, 2A
    ]
    return any(re.match(p, text) for p in patterns)


def classify_label(text):
    text = clean_text(text)
    if re.match(r"^\d{1,2}$", text):
        return "numeric"
    if re.match(r"^[A-Z]{1,2}$", text):
        return "alpha"
    if re.match(r"^[A-Z]{1,2}'$", text):
        return "alpha_prime"
    if re.match(r"^\d{1,2}[A-Z]$", text):
        return "numeric_sub"
    return "other"


def extract_all_text_entities(doc):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            t = e.dxftype()
            if t == "TEXT":
                text = clean_text(e.dxf.text)
                insert = e.dxf.insert
                texts.append({
                    "text": text,
                    "point": (float(insert.x), float(insert.y)),
                    "layer": e.dxf.layer,
                    "type": "TEXT"
                })
            elif t == "MTEXT":
                text = clean_text(e.text)
                insert = e.dxf.insert
                texts.append({
                    "text": text,
                    "point": (float(insert.x), float(insert.y)),
                    "layer": e.dxf.layer,
                    "type": "MTEXT"
                })
        except Exception:
            continue
    return texts


def extract_all_line_entities(doc):
    """
    Collect horizontal/vertical long lines from modelspace.
    Supports LINE and lightweight POLYLINE segments in a basic way.
    """
    lines = []
    msp = doc.modelspace()

    for e in msp:
        try:
            t = e.dxftype()

            if t == "LINE":
                x1, y1, _ = e.dxf.start
                x2, y2, _ = e.dxf.end
                lines.append({
                    "start": (float(x1), float(y1)),
                    "end": (float(x2), float(y2)),
                    "layer": e.dxf.layer,
                    "type": "LINE"
                })

            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points()]
                for i in range(len(pts) - 1):
                    lines.append({
                        "start": pts[i],
                        "end": pts[i + 1],
                        "layer": e.dxf.layer,
                        "type": "LWPOLYLINE_SEG"
                    })
        except Exception:
            continue

    return lines


def is_vertical(line, tol=1.0):
    x1, y1 = line["start"]
    x2, y2 = line["end"]
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def is_horizontal(line, tol=1.0):
    x1, y1 = line["start"]
    x2, y2 = line["end"]
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


def line_length(line):
    return dist(line["start"], line["end"])


def normalize_grid_line(line):
    if is_vertical(line):
        x = round(line["start"][0], 3)
        y1 = min(line["start"][1], line["end"][1])
        y2 = max(line["start"][1], line["end"][1])
        return {
            "orientation": "vertical",
            "coord": x,
            "span_min": round(y1, 3),
            "span_max": round(y2, 3),
            "length": round(y2 - y1, 3),
            "start": line["start"],
            "end": line["end"],
            "layer": line["layer"],
        }

    if is_horizontal(line):
        y = round(line["start"][1], 3)
        x1 = min(line["start"][0], line["end"][0])
        x2 = max(line["start"][0], line["end"][0])
        return {
            "orientation": "horizontal",
            "coord": y,
            "span_min": round(x1, 3),
            "span_max": round(x2, 3),
            "length": round(x2 - x1, 3),
            "start": line["start"],
            "end": line["end"],
            "layer": line["layer"],
        }

    return None


def nearest_text_to_line_ends(texts, line, max_distance=500.0):
    """
    Looks for label text near either end of a line.
    """
    candidates = []
    p1 = line["start"]
    p2 = line["end"]

    for tx in texts:
        if not is_probable_grid_label(tx["text"]):
            continue

        d1 = dist(tx["point"], p1)
        d2 = dist(tx["point"], p2)
        d = min(d1, d2)

        if d <= max_distance:
            candidates.append((d, tx))

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1] if candidates else None


def extract_grid_map(doc, min_grid_length=1000.0, label_distance=500.0):
    """
    Detect grid lines + labels.
    Arch/Struc does NOT need same origin.
    We use relative coordinates and sorted positions.
    """
    texts = extract_all_text_entities(doc)
    raw_lines = extract_all_line_entities(doc)

    grid_lines = []
    for line in raw_lines:
        n = normalize_grid_line(line)
        if n and n["length"] >= min_grid_length:
            grid_lines.append(n)

    detected = []
    for gl in grid_lines:
        label = nearest_text_to_line_ends(texts, gl, max_distance=label_distance)
        detected.append({
            "orientation": gl["orientation"],
            "coord": gl["coord"],
            "span_min": gl["span_min"],
            "span_max": gl["span_max"],
            "length": gl["length"],
            "label": label["text"] if label else None,
            "label_point": label["point"] if label else None,
            "layer": gl["layer"],
        })

    vertical = sorted([g for g in detected if g["orientation"] == "vertical"], key=lambda x: x["coord"])
    horizontal = sorted([g for g in detected if g["orientation"] == "horizontal"], key=lambda x: x["coord"])

    return {
        "vertical": deduplicate_grid_lines(vertical),
        "horizontal": deduplicate_grid_lines(horizontal),
        "texts": texts
    }


def deduplicate_grid_lines(lines, coord_tol=5.0):
    """
    Merge duplicate lines with nearly same coordinate.
    Prefer labeled line over unlabeled line.
    """
    if not lines:
        return []

    merged = []
    current_group = [lines[0]]

    for item in lines[1:]:
        if abs(item["coord"] - current_group[-1]["coord"]) <= coord_tol:
            current_group.append(item)
        else:
            merged.append(resolve_group(current_group))
            current_group = [item]

    merged.append(resolve_group(current_group))
    return merged


def resolve_group(group):
    labeled = [g for g in group if g["label"]]
    if labeled:
        best = labeled[0]
    else:
        best = group[0]

    avg_coord = round(sum(g["coord"] for g in group) / len(group), 3)
    best = dict(best)
    best["coord"] = avg_coord
    return best


def build_spacing_map(grid_lines):
    spacings = []
    if len(grid_lines) < 2:
        return spacings

    for i in range(len(grid_lines) - 1):
        a = grid_lines[i]
        b = grid_lines[i + 1]
        spacing = round(abs(b["coord"] - a["coord"]), 3)
        spacings.append({
            "index_a": i,
            "index_b": i + 1,
            "label_a": a["label"],
            "label_b": b["label"],
            "coord_a": a["coord"],
            "coord_b": b["coord"],
            "spacing": spacing,
        })
    return spacings


def infer_missing_labels(lines, mode):
    """
    If intermediate grids exist but labels are missing:
    numeric example: 1, [missing], 2 => 1a
    alpha example: A, [missing], B => A'
    """
    if not lines:
        return lines

    lines = [dict(x) for x in lines]

    for i, item in enumerate(lines):
        if item["label"]:
            continue

        prev_label = None
        next_label = None

        for j in range(i - 1, -1, -1):
            if lines[j]["label"]:
                prev_label = lines[j]["label"]
                break

        for j in range(i + 1, len(lines)):
            if lines[j]["label"]:
                next_label = lines[j]["label"]
                break

        if mode == "numeric":
            if prev_label and classify_label(prev_label) == "numeric":
                item["label"] = f"{prev_label}A"
            elif next_label and classify_label(next_label) == "numeric":
                try:
                    n = int(next_label) - 1
                    if n >= 1:
                        item["label"] = f"{n}A"
                except Exception:
                    pass

        elif mode == "alpha":
            if prev_label and classify_label(prev_label) in ["alpha", "alpha_prime"]:
                item["label"] = f"{prev_label}'"
            elif next_label and classify_label(next_label) == "alpha":
                letter = next_label[0]
                if ord(letter) > ord("A"):
                    item["label"] = f"{chr(ord(letter)-1)}'"

    return lines


def detect_axis_mode(lines):
    labels = [x["label"] for x in lines if x["label"]]
    numeric_count = sum(1 for l in labels if classify_label(l) in ["numeric", "numeric_sub"])
    alpha_count = sum(1 for l in labels if classify_label(l) in ["alpha", "alpha_prime"])

    if numeric_count >= alpha_count:
        return "numeric"
    return "alpha"


def compare_grid_maps(arch_map, struc_map, tolerance):
    mismatches = []
    remap_suggestions = []

    for orientation in ["vertical", "horizontal"]:
        arch_lines = arch_map[orientation]
        struc_lines = struc_map[orientation]

        arch_mode = detect_axis_mode(arch_lines) if arch_lines else "unknown"
        struc_mode = detect_axis_mode(struc_lines) if struc_lines else "unknown"

        arch_lines = infer_missing_labels(arch_lines, arch_mode if arch_mode in ["numeric", "alpha"] else "numeric")
        struc_lines = infer_missing_labels(struc_lines, struc_mode if struc_mode in ["numeric", "alpha"] else "numeric")

        arch_sp = build_spacing_map(arch_lines)
        struc_sp = build_spacing_map(struc_lines)

        pair_count = min(len(arch_sp), len(struc_sp))

        for i in range(pair_count):
            a = arch_sp[i]
            s = struc_sp[i]
            delta = round(abs(a["spacing"] - s["spacing"]), 3)

            if delta > tolerance:
                mismatches.append({
                    "type": "grid_spacing",
                    "orientation": orientation,
                    "label": f"{orientation.title()} span {i+1}",
                    "arch_from": a["label_a"],
                    "arch_to": a["label_b"],
                    "struc_from": s["label_a"],
                    "struc_to": s["label_b"],
                    "arch_value": a["spacing"],
                    "struc_value": s["spacing"],
                    "delta": delta,
                    "message": (
                        f"{orientation.title()} span mismatch: "
                        f"{a['label_a']}–{a['label_b']} = {a['spacing']}mm (Arch) vs "
                        f"{s['label_a']}–{s['label_b']} = {s['spacing']}mm (Struc)"
                    )
                })

        if len(arch_lines) != len(struc_lines):
            mismatches.append({
                "type": "grid_count",
                "orientation": orientation,
                "label": f"{orientation.title()} grid count mismatch",
                "arch_from": None,
                "arch_to": None,
                "struc_from": None,
                "struc_to": None,
                "arch_value": len(arch_lines),
                "struc_value": len(struc_lines),
                "delta": abs(len(arch_lines) - len(struc_lines)),
                "message": (
                    f"{orientation.title()} grid count differs: "
                    f"Arch has {len(arch_lines)} while Struc has {len(struc_lines)}"
                )
            })

        if arch_mode != struc_mode:
            remap_suggestions.append(
                f"{orientation.title()} orientation naming differs: Arch uses {arch_mode}, Struc uses {struc_mode}."
            )

    return mismatches, remap_suggestions, arch_map, struc_map


def get_beam_like_lines(doc, min_length=300.0):
    """
    Simplified beam finder:
    long horizontal/vertical lines in modelspace.
    """
    raw_lines = extract_all_line_entities(doc)
    beams = []

    for line in raw_lines:
        n = normalize_grid_line(line)
        if n and n["length"] >= min_length:
            beams.append(n)

    return beams


def nearest_grid_label(coord, grid_lines):
    if not grid_lines:
        return None
    best = min(grid_lines, key=lambda g: abs(g["coord"] - coord))
    return best["label"] if best["label"] else f"@{best['coord']}"


def build_beam_span_map(doc, grid_map):
    """
    Simplified logic:
    - Horizontal beam spans between vertical grids
    - Vertical beam spans between horizontal grids
    """
    beams = get_beam_like_lines(doc)
    beam_records = []

    for b in beams:
        if b["orientation"] == "horizontal":
            start_x = b["span_min"]
            end_x = b["span_max"]
            g1 = nearest_grid_label(start_x, grid_map["vertical"])
            g2 = nearest_grid_label(end_x, grid_map["vertical"])
            beam_records.append({
                "orientation": "horizontal",
                "from_grid": g1,
                "to_grid": g2,
                "length": b["length"],
                "coord": b["coord"],
            })

        elif b["orientation"] == "vertical":
            start_y = b["span_min"]
            end_y = b["span_max"]
            g1 = nearest_grid_label(start_y, grid_map["horizontal"])
            g2 = nearest_grid_label(end_y, grid_map["horizontal"])
            beam_records.append({
                "orientation": "vertical",
                "from_grid": g1,
                "to_grid": g2,
                "length": b["length"],
                "coord": b["coord"],
            })

    return beam_records


def compare_beam_maps(arch_beams, struc_beams, tolerance):
    """
    Match by orientation + from/to grid names.
    """
    mismatches = []

    struc_lookup = {}
    for b in struc_beams:
        key = (b["orientation"], b["from_grid"], b["to_grid"])
        struc_lookup.setdefault(key, []).append(b)

    for ab in arch_beams:
        key = (ab["orientation"], ab["from_grid"], ab["to_grid"])
        candidates = struc_lookup.get(key, [])

        if not candidates:
            mismatches.append({
                "type": "beam_missing",
                "orientation": ab["orientation"],
                "label": f'Beam {ab["from_grid"]}-{ab["to_grid"]}',
                "arch_from": ab["from_grid"],
                "arch_to": ab["to_grid"],
                "arch_value": ab["length"],
                "struc_value": None,
                "delta": None,
                "message": (
                    f'Beam {ab["from_grid"]}-{ab["to_grid"]} found in Arch '
                    f'but not found in Struc by same grid naming.'
                )
            })
            continue

        best = min(candidates, key=lambda x: abs(x["length"] - ab["length"]))
        delta = round(abs(best["length"] - ab["length"]), 3)

        if delta > tolerance:
            mismatches.append({
                "type": "beam_length",
                "orientation": ab["orientation"],
                "label": f'Beam {ab["from_grid"]}-{ab["to_grid"]}',
                "arch_from": ab["from_grid"],
                "arch_to": ab["to_grid"],
                "arch_value": ab["length"],
                "struc_value": best["length"],
                "delta": delta,
                "message": (
                    f'Beam {ab["from_grid"]}-{ab["to_grid"]} mismatch: '
                    f'{ab["length"]}mm (Arch) vs {best["length"]}mm (Struc)'
                )
            })

    return mismatches


def initialize_sync_state():
    defaults = {
        "audit_stage": "idle",
        "mismatches": [],
        "mismatch_index": 0,
        "decisions": [],
        "decision_message": "",
        "remap_suggestions": [],
        "arch_grid_map": {},
        "struc_grid_map": {},
        "arch_beams": [],
        "struc_beams": [],
        "summary": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_sync_state():
    keys = [
        "audit_stage",
        "mismatches",
        "mismatch_index",
        "decisions",
        "decision_message",
        "remap_suggestions",
        "arch_grid_map",
        "struc_grid_map",
        "arch_beams",
        "struc_beams",
        "summary"
    ]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]

    for k in list(st.session_state.keys()):
        if k.startswith("audit_choice_"):
            del st.session_state[k]


def to_csv_bytes(records):
    if not records:
        return b""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(records[0].keys()))
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue().encode("utf-8")


# =========================================================
# TOOL 1: SMART PURGER
# =========================================================
if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload your .DXF file", type=['dxf'], key="purger")

    if uploaded_file is not None:
        tmp_path = save_uploaded_to_temp(uploaded_file)
        try:
            doc = ezdxf.readfile(tmp_path)
            all_layers = sorted([layer.dxf.name for layer in doc.layers])
            st.success(f"Successfully analyzed {uploaded_file.name}")

            layers_to_keep = st.multiselect(
                "Which layers should remain?",
                options=all_layers,
                default=all_layers
            )

            if st.button("🔥 Purge and Prepare Download"):
                msp = doc.modelspace()
                layers_to_delete = set(all_layers) - set(layers_to_keep)

                deleted_count = 0
                for entity in list(msp):
                    try:
                        if entity.dxf.layer in layers_to_delete:
                            msp.delete_entity(entity)
                            deleted_count += 1
                    except Exception:
                        continue

                out_buffer = io.StringIO()
                doc.write(out_buffer)

                st.success(f"Deleted {deleted_count} modelspace entities.")
                st.download_button(
                    "📥 Download Cleaned DXF",
                    out_buffer.getvalue().encode("utf-8"),
                    f"CLEANED_{uploaded_file.name}",
                    mime="application/dxf"
                )
        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            safe_remove_file(tmp_path)


# =========================================================
# TOOL 2: GRID & BEAM SYNC
# =========================================================
elif tool_choice == "2. Grid & Beam Sync":
    st.subheader("Tool 2: Grid, Label & Beam Synchronizer")
    st.caption(
        "Architectural drawing is treated as the source of truth. "
        "The tool detects grid lines, grid labels, naming rhythm, and beam span mismatches."
    )

    initialize_sync_state()

    col_set1, col_set2, col_set3 = st.columns(3)
    with col_set1:
        tolerance = st.slider("Tolerance (mm)", 0.0, 50.0, 10.0, 0.5)
    with col_set2:
        min_grid_length = st.slider("Min Grid Length (mm)", 100.0, 20000.0, 1000.0, 100.0)
    with col_set3:
        label_distance = st.slider("Max Label-to-Grid Distance (mm)", 50.0, 2000.0, 500.0, 50.0)

    col_a, col_b = st.columns(2)
    with col_a:
        arch_file = st.file_uploader("Reference (Architectural DXF)", type=["dxf"], key="arch")
    with col_b:
        struc_file = st.file_uploader("Target (Structural DXF)", type=["dxf"], key="struc")

    if arch_file and struc_file:
        action_col1, action_col2 = st.columns([1, 1])

        with action_col1:
            if st.button("🚀 Run Smart Sync Audit"):
                arch_tmp = None
                struc_tmp = None
                try:
                    arch_tmp = save_uploaded_to_temp(arch_file)
                    struc_tmp = save_uploaded_to_temp(struc_file)

                    arch_doc = ezdxf.readfile(arch_tmp)
                    struc_doc = ezdxf.readfile(struc_tmp)

                    arch_map = extract_grid_map(
                        arch_doc,
                        min_grid_length=min_grid_length,
                        label_distance=label_distance
                    )
                    struc_map = extract_grid_map(
                        struc_doc,
                        min_grid_length=min_grid_length,
                        label_distance=label_distance
                    )

                    grid_mismatches, remap_suggestions, arch_map, struc_map = compare_grid_maps(
                        arch_map, struc_map, tolerance
                    )

                    arch_beams = build_beam_span_map(arch_doc, arch_map)
                    struc_beams = build_beam_span_map(struc_doc, struc_map)
                    beam_mismatches = compare_beam_maps(arch_beams, struc_beams, tolerance)

                    all_mismatches = grid_mismatches + beam_mismatches

                    st.session_state.arch_grid_map = arch_map
                    st.session_state.struc_grid_map = struc_map
                    st.session_state.arch_beams = arch_beams
                    st.session_state.struc_beams = struc_beams
                    st.session_state.remap_suggestions = remap_suggestions
                    st.session_state.mismatches = all_mismatches
                    st.session_state.mismatch_index = 0
                    st.session_state.decisions = []

                    st.session_state.summary = {
                        "arch_v": len(arch_map["vertical"]),
                        "arch_h": len(arch_map["horizontal"]),
                        "struc_v": len(struc_map["vertical"]),
                        "struc_h": len(struc_map["horizontal"]),
                        "arch_beams": len(arch_beams),
                        "struc_beams": len(struc_beams),
                        "mismatches": len(all_mismatches),
                    }

                    if all_mismatches:
                        st.session_state.audit_stage = "reviewing"
                        st.session_state.decision_message = "Review required."
                    else:
                        st.session_state.audit_stage = "finished"
                        st.session_state.decision_message = "✅ No mismatches found within tolerance."

                    st.rerun()

                except Exception as e:
                    st.error(f"Audit failed: {e}")

                finally:
                    safe_remove_file(arch_tmp)
                    safe_remove_file(struc_tmp)

        with action_col2:
            if st.button("🧹 Reset Audit"):
                reset_sync_state()
                st.rerun()

        summary = st.session_state.summary
        if summary:
            c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
            c1.metric("Arch V", summary.get("arch_v", 0))
            c2.metric("Arch H", summary.get("arch_h", 0))
            c3.metric("Struc V", summary.get("struc_v", 0))
            c4.metric("Struc H", summary.get("struc_h", 0))
            c5.metric("Arch Beams", summary.get("arch_beams", 0))
            c6.metric("Struc Beams", summary.get("struc_beams", 0))
            c7.metric("Mismatches", summary.get("mismatches", 0))

        if st.session_state.remap_suggestions:
            st.info("Naming Rhythm Suggestions")
            for msg in st.session_state.remap_suggestions:
                st.write(f"- {msg}")

        tab1, tab2, tab3, tab4 = st.tabs(["Arch Grids", "Struc Grids", "Beam Maps", "Audit"])

        with tab1:
            ag = st.session_state.arch_grid_map
            if ag:
                st.write("### Architectural Vertical Grids")
                st.dataframe(ag["vertical"], use_container_width=True)
                st.write("### Architectural Horizontal Grids")
                st.dataframe(ag["horizontal"], use_container_width=True)

        with tab2:
            sg = st.session_state.struc_grid_map
            if sg:
                st.write("### Structural Vertical Grids")
                st.dataframe(sg["vertical"], use_container_width=True)
                st.write("### Structural Horizontal Grids")
                st.dataframe(sg["horizontal"], use_container_width=True)

        with tab3:
            b1, b2 = st.columns(2)
            with b1:
                st.write("### Arch Beam Span Map")
                st.dataframe(st.session_state.arch_beams, use_container_width=True)
            with b2:
                st.write("### Struc Beam Span Map")
                st.dataframe(st.session_state.struc_beams, use_container_width=True)

        with tab4:
            if st.session_state.audit_stage == "reviewing":
                mismatches = st.session_state.mismatches
                idx = st.session_state.mismatch_index
                total = len(mismatches)

                if idx < total:
                    item = mismatches[idx]
                    st.progress((idx + 1) / total)
                    st.warning(item["message"])

                    if item["type"] in ["grid_spacing", "beam_length"]:
                        choice_list = [
                            f'Force Arch Value ({item["arch_value"]})',
                            "Ignore and Pass",
                            "Reject"
                        ]
                    else:
                        choice_list = [
                            "Use Arch Naming",
                            "Ignore and Pass",
                            "Reject"
                        ]

                    choice = st.radio(
                        "Action:",
                        choice_list,
                        key=f"audit_choice_{idx}"
                    )

                    colx1, colx2 = st.columns([1, 1])

                    with colx1:
                        if st.button("Apply Decision"):
                            st.session_state.decisions.append({
                                "item_no": idx + 1,
                                "type": item["type"],
                                "label": item["label"],
                                "message": item["message"],
                                "decision": choice,
                                "arch_value": item["arch_value"],
                                "struc_value": item["struc_value"],
                                "delta": item["delta"],
                            })

                            st.session_state.mismatch_index += 1

                            if st.session_state.mismatch_index >= total:
                                st.session_state.audit_stage = "finished"
                                st.session_state.decision_message = "✅ Audit complete. All mismatches reviewed."
                            else:
                                st.session_state.decision_message = f"✅ Decision saved for item {idx + 1}."

                            st.rerun()

                    with colx2:
                        if st.button("Finish & Reset"):
                            reset_sync_state()
                            st.rerun()

            elif st.session_state.audit_stage == "finished":
                if st.session_state.decision_message:
                    if st.session_state.decision_message.startswith("✅"):
                        st.success(st.session_state.decision_message)
                    else:
                        st.info(st.session_state.decision_message)

                if st.session_state.decisions:
                    st.write("### Decisions")
                    st.dataframe(st.session_state.decisions, use_container_width=True)

                    st.download_button(
                        "📥 Download Decisions CSV",
                        data=to_csv_bytes(st.session_state.decisions),
                        file_name="grid_beam_sync_decisions.csv",
                        mime="text/csv"
                    )

                if st.session_state.mismatches and not st.session_state.decisions:
                    st.write("Mismatch list exists, but no decisions were recorded.")

                cf1, cf2 = st.columns(2)

                with cf1:
                    if st.button("Run Another Audit"):
                        st.session_state.audit_stage = "idle"
                        st.session_state.mismatches = []
                        st.session_state.mismatch_index = 0
                        st.session_state.decisions = []
                        st.session_state.decision_message = ""
                        st.session_state.remap_suggestions = []
                        st.rerun()

                with cf2:
                    if st.button("Finish & Full Reset"):
                        reset_sync_state()
                        st.rerun()

            else:
                st.info("Ready. Click 'Run Smart Sync Audit' to begin.")
    else:
        st.info("Please upload both the Architectural DXF and the Structural DXF.")
