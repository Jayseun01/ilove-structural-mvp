import streamlit as st
import ezdxf
import io
import os
import tempfile
import math
import re
import matplotlib.pyplot as plt
from matplotlib.patches import Circle as MplCircle

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# =========================================================
# SIDEBAR / HEADER
# =========================================================
st.sidebar.title("Navigation")
tool_choice = st.sidebar.radio(
    "Select a Tool:",
    ["1. DXF Smart Purger", "2. Grid Label Sync"]
)
st.sidebar.markdown("---")
st.sidebar.info("Developed by James Oluwaseun Emmanuel")

st.title("🏗️ iLoveStructural")


# =========================================================
# FILE HELPERS
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


# =========================================================
# GENERAL HELPERS
# =========================================================
def clean_text(value):
    if value is None:
        return ""
    return str(value).strip().upper()


def euclidean(p1, p2):
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def is_vertical(x1, y1, x2, y2, tol=1.0):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def is_horizontal(x1, y1, x2, y2, tol=1.0):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


def probable_grid_label(text):
    text = clean_text(text)
    patterns = [
        r"^[A-Z]{1,2}$",       # A, B, AA
        r"^\d{1,2}$",          # 1, 2, 10
        r"^[A-Z]{1,2}'$",      # A'
        r"^\d{1,2}[A-Z]?$",    # 1A / 2A / 1
    ]
    return any(re.match(p, text) for p in patterns)


def probable_grid_ref(text):
    text = clean_text(text)
    patterns = [
        r"^[A-Z]{1,2}\-[A-Z]{1,2}$",
        r"^\d{1,2}\-\d{1,2}$",
        r"^[A-Z]{1,2}\-\d{1,2}$",
        r"^\d{1,2}\-[A-Z]{1,2}$",
    ]
    return any(re.match(p, text) for p in patterns)


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


# =========================================================
# TOOL 1 HELPERS
# =========================================================
def purge_layers_from_modelspace(doc, layers_to_keep):
    all_layers = get_layer_names(doc)
    layers_to_delete = set(all_layers) - set(layers_to_keep)
    msp = doc.modelspace()
    deleted_count = 0

    for entity in list(msp):
        try:
            if entity.dxf.layer in layers_to_delete:
                msp.delete_entity(entity)
                deleted_count += 1
        except Exception:
            continue

    return deleted_count


# =========================================================
# ENTITY EXTRACTION
# =========================================================
def extract_texts(doc):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "TEXT":
                txt = clean_text(e.dxf.text)
                ins = e.dxf.insert
                texts.append({
                    "entity": e,
                    "text": txt,
                    "point": (float(ins.x), float(ins.y)),
                    "layer": e.dxf.layer,
                    "type": "TEXT",
                })
            elif e.dxftype() == "MTEXT":
                txt = clean_text(e.text)
                ins = e.dxf.insert
                texts.append({
                    "entity": e,
                    "text": txt,
                    "point": (float(ins.x), float(ins.y)),
                    "layer": e.dxf.layer,
                    "type": "MTEXT",
                })
        except Exception:
            continue

    return texts


def extract_circles(doc, layer_name=None):
    circles = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "CIRCLE":
                if layer_name and e.dxf.layer != layer_name:
                    continue
                c = e.dxf.center
                circles.append({
                    "entity": e,
                    "center": (float(c.x), float(c.y)),
                    "radius": float(e.dxf.radius),
                    "layer": e.dxf.layer,
                })
        except Exception:
            continue

    return circles


def extract_axis_lines(doc, layer_name=None, min_length=1000.0):
    lines = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if layer_name and e.dxf.layer != layer_name:
                continue

            if e.dxftype() == "LINE":
                x1, y1, _ = e.dxf.start
                x2, y2, _ = e.dxf.end
                length = math.dist((x1, y1), (x2, y2))
                if length < min_length:
                    continue

                if is_vertical(x1, y1, x2, y2):
                    lines.append({
                        "entity": e,
                        "orientation": "vertical",
                        "coord": round(float(x1), 3),
                        "start": (float(x1), float(y1)),
                        "end": (float(x2), float(y2)),
                        "length": round(float(length), 3),
                        "layer": e.dxf.layer,
                    })
                elif is_horizontal(x1, y1, x2, y2):
                    lines.append({
                        "entity": e,
                        "orientation": "horizontal",
                        "coord": round(float(y1), 3),
                        "start": (float(x1), float(y1)),
                        "end": (float(x2), float(y2)),
                        "length": round(float(length), 3),
                        "layer": e.dxf.layer,
                    })

            elif e.dxftype() == "LWPOLYLINE":
                pts = list(e.get_points())
                for i in range(len(pts) - 1):
                    x1, y1 = float(pts[i][0]), float(pts[i][1])
                    x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
                    length = math.dist((x1, y1), (x2, y2))
                    if length < min_length:
                        continue

                    if is_vertical(x1, y1, x2, y2):
                        lines.append({
                            "entity": e,
                            "orientation": "vertical",
                            "coord": round(float(x1), 3),
                            "start": (float(x1), float(y1)),
                            "end": (float(x2), float(y2)),
                            "length": round(float(length), 3),
                            "layer": e.dxf.layer,
                        })
                    elif is_horizontal(x1, y1, x2, y2):
                        lines.append({
                            "entity": e,
                            "orientation": "horizontal",
                            "coord": round(float(y1), 3),
                            "start": (float(x1), float(y1)),
                            "end": (float(x2), float(y2)),
                            "length": round(float(length), 3),
                            "layer": e.dxf.layer,
                        })
        except Exception:
            continue

    return deduplicate_axis_lines(lines)


def deduplicate_axis_lines(lines, tol=5.0):
    if not lines:
        return []

    lines = sorted(lines, key=lambda x: (x["orientation"], x["coord"]))
    result = []
    current = [lines[0]]

    for item in lines[1:]:
        same_band = (
            item["orientation"] == current[-1]["orientation"]
            and abs(item["coord"] - current[-1]["coord"]) <= tol
        )
        if same_band:
            current.append(item)
        else:
            result.append(resolve_line_group(current))
            current = [item]

    result.append(resolve_line_group(current))
    return result


def resolve_line_group(group):
    best = max(group, key=lambda x: x["length"])
    out = dict(best)
    out["coord"] = round(sum(g["coord"] for g in group) / len(group), 3)
    return out


# =========================================================
# GRID BUBBLE / LABEL DETECTION
# =========================================================
def detect_grid_bubbles(doc, grid_layer=None, min_grid_length=1000.0, text_circle_gap=150.0):
    texts = extract_texts(doc)
    circles = extract_circles(doc, layer_name=grid_layer)
    lines = extract_axis_lines(doc, layer_name=grid_layer, min_length=min_grid_length)

    bubbles = []
    for c in circles:
        best_text = None
        best_dist = None

        for t in texts:
            if not probable_grid_label(t["text"]):
                continue

            d = euclidean(c["center"], t["point"])
            if d <= max(c["radius"] * 0.9, text_circle_gap):
                if best_dist is None or d < best_dist:
                    best_dist = d
                    best_text = t

        if best_text:
            bubbles.append({
                "label": best_text["text"],
                "center": c["center"],
                "radius": c["radius"],
                "circle_entity": c["entity"],
                "text_entity": best_text["entity"],
                "layer": c["layer"],
            })

    vertical_grids = []
    horizontal_grids = []

    for bubble in bubbles:
        bx, by = bubble["center"]
        nearest_line = None
        nearest_dist = None

        for line in lines:
            if line["orientation"] == "vertical":
                d = abs(bx - line["coord"])
            else:
                d = abs(by - line["coord"])

            if nearest_dist is None or d < nearest_dist:
                nearest_dist = d
                nearest_line = line

        if nearest_line:
            record = {
                "label": bubble["label"],
                "coord": nearest_line["coord"],
                "center": bubble["center"],
                "radius": bubble["radius"],
                "text_entity": bubble["text_entity"],
                "circle_entity": bubble["circle_entity"],
                "line_entity": nearest_line["entity"],
            }

            if nearest_line["orientation"] == "vertical":
                vertical_grids.append(record)
            else:
                horizontal_grids.append(record)

    vertical_grids = sorted(deduplicate_grids(vertical_grids), key=lambda x: x["coord"])
    horizontal_grids = sorted(deduplicate_grids(horizontal_grids), key=lambda x: x["coord"])

    return vertical_grids, horizontal_grids, lines


def deduplicate_grids(grids, tol=5.0):
    if not grids:
        return []

    grids = sorted(grids, key=lambda x: x["coord"])
    result = []
    current = [grids[0]]

    for item in grids[1:]:
        if abs(item["coord"] - current[-1]["coord"]) <= tol:
            current.append(item)
        else:
            result.append(current[0])
            current = [item]

    result.append(current[0])
    return result


# =========================================================
# GEOMETRY CHECK / SPACING CHECK
# =========================================================
def build_spacings(grids, orientation):
    data = []
    if len(grids) < 2:
        return data

    for i in range(len(grids) - 1):
        a = grids[i]
        b = grids[i + 1]
        spacing = round(abs(b["coord"] - a["coord"]), 3)
        data.append({
            "orientation": orientation,
            "index_a": i + 1,
            "index_b": i + 2,
            "label_a": a["label"],
            "label_b": b["label"],
            "coord_a": a["coord"],
            "coord_b": b["coord"],
            "spacing": spacing,
        })
    return data


def compare_spacings(arch_grids, struc_grids, orientation, tolerance):
    arch_sp = build_spacings(arch_grids, orientation)
    struc_sp = build_spacings(struc_grids, orientation)

    pair_count = min(len(arch_sp), len(struc_sp))
    issues = []

    for i in range(pair_count):
        a = arch_sp[i]
        s = struc_sp[i]
        diff = round(abs(a["spacing"] - s["spacing"]), 3)
        status = "PASS" if diff <= tolerance else "FAIL"

        issues.append({
            "orientation": orientation,
            "span_position": i + 1,
            "arch_span": f"{a['label_a']}-{a['label_b']}",
            "struc_span": f"{s['label_a']}-{s['label_b']}",
            "arch_spacing": a["spacing"],
            "struc_spacing": s["spacing"],
            "difference": diff,
            "tolerance": tolerance,
            "status": status,
            "coord_a_arch": a["coord_a"],
            "coord_b_arch": a["coord_b"],
            "coord_a_struc": s["coord_a"],
            "coord_b_struc": s["coord_b"],
        })

    return issues


def summarize_geometry_issues(all_issues):
    passes = [x for x in all_issues if x["status"] == "PASS"]
    fails = [x for x in all_issues if x["status"] == "FAIL"]
    return passes, fails


# =========================================================
# LABEL MAPPING
# =========================================================
def build_mapping_by_position(arch_grids, struc_grids):
    mapping = {}
    preview = []

    count = min(len(arch_grids), len(struc_grids))
    for i in range(count):
        old_label = struc_grids[i]["label"]
        new_label = arch_grids[i]["label"]
        mapping[old_label] = new_label
        preview.append({
            "position": i + 1,
            "structural_old": old_label,
            "architectural_new": new_label,
            "arch_coord": arch_grids[i]["coord"],
            "struc_coord": struc_grids[i]["coord"],
            "difference": round(abs(arch_grids[i]["coord"] - struc_grids[i]["coord"]), 3),
        })

    return mapping, preview


def replace_single_label(text, mapping):
    t = clean_text(text)
    return mapping.get(t, t)


def replace_pair_label(text, mapping):
    t = clean_text(text)
    if "-" not in t:
        return mapping.get(t, t)

    parts = t.split("-")
    if len(parts) == 2:
        left = mapping.get(parts[0], parts[0])
        right = mapping.get(parts[1], parts[1])
        return f"{left}-{right}"
    return t


def apply_mapping_to_structural_texts(doc, vertical_map, horizontal_map):
    combined = {}
    combined.update(vertical_map)
    combined.update(horizontal_map)

    changed = 0
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "TEXT":
                old = clean_text(e.dxf.text)
                new = old

                if probable_grid_label(old):
                    new = replace_single_label(old, combined)
                elif probable_grid_ref(old):
                    new = replace_pair_label(old, combined)

                if new != old:
                    e.dxf.text = new
                    changed += 1

            elif e.dxftype() == "MTEXT":
                old = clean_text(e.text)
                new = old

                if probable_grid_label(old):
                    new = replace_single_label(old, combined)
                elif probable_grid_ref(old):
                    new = replace_pair_label(old, combined)

                if new != old:
                    e.text = new
                    changed += 1
        except Exception:
            continue

    return changed


# =========================================================
# VISUALIZATION
# =========================================================
def draw_preview(title, grids_v, grids_h, lines, issues=None):
    fig, ax = plt.subplots(figsize=(8, 8))

    # draw grid lines
    for line in lines:
        x1, y1 = line["start"]
        x2, y2 = line["end"]
        ax.plot([x1, x2], [y1, y2], color="lightgray", linewidth=0.8)

    # draw vertical grid bubbles
    for g in grids_v:
        cx, cy = g["center"]
        r = max(g.get("radius", 150.0), 50.0)
        circ = MplCircle((cx, cy), r, fill=False, color="blue", linewidth=1.5)
        ax.add_patch(circ)
        ax.text(cx, cy, g["label"], ha="center", va="center", color="blue", fontsize=8)

    # draw horizontal grid bubbles
    for g in grids_h:
        cx, cy = g["center"]
        r = max(g.get("radius", 150.0), 50.0)
        circ = MplCircle((cx, cy), r, fill=False, color="green", linewidth=1.5)
        ax.add_patch(circ)
        ax.text(cx, cy, g["label"], ha="center", va="center", color="green", fontsize=8)

    # highlight failed spacing issues
    if issues:
        for issue in issues:
            if issue["status"] != "FAIL":
                continue

            if issue["orientation"] == "vertical":
                x1 = issue["coord_a_struc"]
                x2 = issue["coord_b_struc"]
                ax.axvline(x=x1, color="red", linestyle="--", linewidth=1)
                ax.axvline(x=x2, color="red", linestyle="--", linewidth=1)
            elif issue["orientation"] == "horizontal":
                y1 = issue["coord_a_struc"]
                y2 = issue["coord_b_struc"]
                ax.axhline(y=y1, color="red", linestyle="--", linewidth=1)
                ax.axhline(y=y2, color="red", linestyle="--", linewidth=1)

    ax.set_title(title)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle=":", linewidth=0.3)
    return fig


# =========================================================
# SESSION STATE
# =========================================================
def init_sync_state():
    defaults = {
        "docs_loaded": False,
        "arch_doc": None,
        "struc_doc": None,
        "arch_name": "",
        "struc_name": "",
        "vertical_map": {},
        "horizontal_map": {},
        "vertical_preview": [],
        "horizontal_preview": [],
        "geometry_issues": [],
        "geometry_continue": False,
        "apply_ready": False,
        "labels_changed_count": 0,
        "arch_vertical": [],
        "arch_horizontal": [],
        "struc_vertical": [],
        "struc_horizontal": [],
        "arch_lines": [],
        "struc_lines": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_sync_state():
    keys = [
        "docs_loaded",
        "arch_doc",
        "struc_doc",
        "arch_name",
        "struc_name",
        "vertical_map",
        "horizontal_map",
        "vertical_preview",
        "horizontal_preview",
        "geometry_issues",
        "geometry_continue",
        "apply_ready",
        "labels_changed_count",
        "arch_vertical",
        "arch_horizontal",
        "struc_vertical",
        "struc_horizontal",
        "arch_lines",
        "struc_lines",
    ]
    for key in keys:
        if key in st.session_state:
            del st.session_state[key]


# =========================================================
# TOOL 1: DXF SMART PURGER
# =========================================================
if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload your .DXF file", type=["dxf"], key="purger")

    if uploaded_file is not None:
        tmp_path = save_uploaded_to_temp(uploaded_file)
        try:
            doc = ezdxf.readfile(tmp_path)
            all_layers = get_layer_names(doc)
            st.success(f"Successfully analyzed {uploaded_file.name}")

            layers_to_keep = st.multiselect(
                "Which layers should remain?",
                options=all_layers,
                default=all_layers
            )

            if st.button("🔥 Purge and Prepare Download"):
                deleted_count = purge_layers_from_modelspace(doc, layers_to_keep)

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
# TOOL 2: GRID LABEL SYNC + GEOMETRY QA + VISUAL PREVIEW
# =========================================================
elif tool_choice == "2. Grid Label Sync":
    st.subheader("Tool 2: Grid Label Sync + Geometry QA + Visual Preview")
    st.caption(
        "Uses architectural grid naming as source of truth, checks structural spacing against tolerance, "
        "and provides visual confirmation before relabeling the structural DXF."
    )

    init_sync_state()

    c_cfg1, c_cfg2, c_cfg3 = st.columns(3)
    with c_cfg1:
        tolerance = st.slider("Tolerance (mm)", 0.0, 50.0, 10.0, 0.5)
    with c_cfg2:
        min_grid_length = st.slider("Minimum Grid Line Length (mm)", 100.0, 20000.0, 1000.0, 100.0)
    with c_cfg3:
        text_circle_gap = st.slider("Text-to-Circle Capture Gap (mm)", 20.0, 500.0, 150.0, 10.0)

    col_a, col_b = st.columns(2)
    with col_a:
        arch_file = st.file_uploader("Reference (Architectural DXF)", type=["dxf"], key="arch")
    with col_b:
        struc_file = st.file_uploader("Target (Structural DXF)", type=["dxf"], key="struc")

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

            except Exception as e:
                st.error(f"Failed to load DXF files: {e}")
            finally:
                safe_remove_file(arch_tmp)
                safe_remove_file(struc_tmp)

        if st.session_state.arch_doc and st.session_state.struc_doc:
            arch_layers = get_layer_names(st.session_state.arch_doc)
            struc_layers = get_layer_names(st.session_state.struc_doc)

            l1, l2 = st.columns(2)
            with l1:
                arch_grid_layer = st.selectbox(
                    "Architectural Grid Layer",
                    options=["<ALL LAYERS>"] + arch_layers,
                    index=0
                )
            with l2:
                struc_grid_layer = st.selectbox(
                    "Structural Grid Layer",
                    options=["<ALL LAYERS>"] + struc_layers,
                    index=0
                )

            arch_grid_layer_val = None if arch_grid_layer == "<ALL LAYERS>" else arch_grid_layer
            struc_grid_layer_val = None if struc_grid_layer == "<ALL LAYERS>" else struc_grid_layer

            b1, b2 = st.columns(2)

            with b1:
                if st.button("🚀 Analyze, Check, and Preview"):
                    try:
                        arch_vertical, arch_horizontal, arch_lines = detect_grid_bubbles(
                            st.session_state.arch_doc,
                            grid_layer=arch_grid_layer_val,
                            min_grid_length=min_grid_length,
                            text_circle_gap=text_circle_gap
                        )

                        struc_vertical, struc_horizontal, struc_lines = detect_grid_bubbles(
                            st.session_state.struc_doc,
                            grid_layer=struc_grid_layer_val,
                            min_grid_length=min_grid_length,
                            text_circle_gap=text_circle_gap
                        )

                        st.session_state.arch_vertical = arch_vertical
                        st.session_state.arch_horizontal = arch_horizontal
                        st.session_state.struc_vertical = struc_vertical
                        st.session_state.struc_horizontal = struc_horizontal
                        st.session_state.arch_lines = arch_lines
                        st.session_state.struc_lines = struc_lines

                        vertical_map, vertical_preview = build_mapping_by_position(arch_vertical, struc_vertical)
                        horizontal_map, horizontal_preview = build_mapping_by_position(arch_horizontal, struc_horizontal)

                        st.session_state.vertical_map = vertical_map
                        st.session_state.horizontal_map = horizontal_map
                        st.session_state.vertical_preview = vertical_preview
                        st.session_state.horizontal_preview = horizontal_preview

                        issues = []
                        issues.extend(compare_spacings(arch_vertical, struc_vertical, "vertical", tolerance))
                        issues.extend(compare_spacings(arch_horizontal, struc_horizontal, "horizontal", tolerance))

                        st.session_state.geometry_issues = issues

                        _, fails = summarize_geometry_issues(issues)
                        if not fails:
                            st.session_state.geometry_continue = True
                            st.session_state.apply_ready = True
                        else:
                            st.session_state.geometry_continue = False
                            st.session_state.apply_ready = False

                        st.success("Analysis complete. Check mapping, geometry, and visual preview tabs.")

                    except Exception as e:
                        st.error(f"Analysis failed: {e}")

            with b2:
                if st.button("🧹 Reset Tool 2"):
                    reset_sync_state()
                    st.rerun()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Arch Vertical", len(st.session_state.arch_vertical))
            m2.metric("Arch Horizontal", len(st.session_state.arch_horizontal))
            m3.metric("Struc Vertical", len(st.session_state.struc_vertical))
            m4.metric("Struc Horizontal", len(st.session_state.struc_horizontal))

            tabs = st.tabs(["Grid Mapping", "Geometry Check", "Visual Preview", "Apply Labels", "Download"])

            with tabs[0]:
                st.write("### Vertical Grid Mapping")
                if st.session_state.vertical_preview:
                    st.dataframe(st.session_state.vertical_preview, use_container_width=True)
                else:
                    st.info("No vertical mapping yet.")

                st.write("### Horizontal Grid Mapping")
                if st.session_state.horizontal_preview:
                    st.dataframe(st.session_state.horizontal_preview, use_container_width=True)
                else:
                    st.info("No horizontal mapping yet.")

            with tabs[1]:
                st.write("### Geometry Tolerance Check")
                if st.session_state.geometry_issues:
                    st.dataframe(st.session_state.geometry_issues, use_container_width=True)

                    passes, fails = summarize_geometry_issues(st.session_state.geometry_issues)
                    st.success(f"Within tolerance: {len(passes)}")
                    if fails:
                        st.error(f"Outside tolerance: {len(fails)}")
                        st.warning(
                            "Some structural spans differ from architectural spans beyond the allowed tolerance."
                        )

                        continue_choice = st.radio(
                            "What do you want to do?",
                            ["Go back and review", "Continue anyway and apply labels"],
                            key="qa_gate"
                        )

                        if continue_choice == "Continue anyway and apply labels":
                            st.session_state.geometry_continue = True
                            st.session_state.apply_ready = True
                        else:
                            st.session_state.geometry_continue = False
                            st.session_state.apply_ready = False
                    else:
                        st.info("All checked spans are within tolerance.")
                        st.session_state.geometry_continue = True
                        st.session_state.apply_ready = True
                else:
                    st.info("Run analysis first.")

            with tabs[2]:
                st.write("### Visual Preview")

                pv1, pv2 = st.columns(2)

                with pv1:
                    if st.session_state.arch_lines:
                        fig_arch = draw_preview(
                            "Architectural Grid Detection",
                            st.session_state.arch_vertical,
                            st.session_state.arch_horizontal,
                            st.session_state.arch_lines,
                            issues=None
                        )
                        st.pyplot(fig_arch)
                    else:
                        st.info("No architectural preview yet.")

                with pv2:
                    if st.session_state.struc_lines:
                        fig_struc = draw_preview(
                            "Structural Grid Detection + Fail Highlights",
                            st.session_state.struc_vertical,
                            st.session_state.struc_horizontal,
                            st.session_state.struc_lines,
                            issues=st.session_state.geometry_issues
                        )
                        st.pyplot(fig_struc)
                    else:
                        st.info("No structural preview yet.")

                st.caption(
                    "Blue circles = detected vertical-grid bubble labels. "
                    "Green circles = detected horizontal-grid bubble labels. "
                    "Red dashed lines = structural spans failing tolerance."
                )

            with tabs[3]:
                st.write("### Apply Architectural Labels to Structural DXF")
                if st.session_state.apply_ready:
                    st.success("Geometry gate passed or continuation approved.")

                    if st.button("✍️ Apply Label Sync"):
                        try:
                            changed = apply_mapping_to_structural_texts(
                                st.session_state.struc_doc,
                                st.session_state.vertical_map,
                                st.session_state.horizontal_map
                            )
                            st.session_state.labels_changed_count = changed
                            st.success(f"Label sync applied successfully. {changed} text entities updated.")
                        except Exception as e:
                            st.error(f"Failed to apply label sync: {e}")
                else:
                    st.warning("Geometry check has not passed. Review Geometry Check tab first.")

            with tabs[4]:
                st.write("### Download Modified Structural DXF")
                if st.session_state.struc_doc is not None:
                    st.info(f"Updated text entities: {st.session_state.labels_changed_count}")

                    out_buffer = io.StringIO()
                    st.session_state.struc_doc.write(out_buffer)

                    st.download_button(
                        "📥 Download Relabeled Structural DXF",
                        data=out_buffer.getvalue().encode("utf-8"),
                        file_name=f"RELABELED_{st.session_state.struc_name}",
                        mime="application/dxf"
                    )
                else:
                    st.info("No structural DXF available.")
    else:
        st.info("Please upload both the Architectural DXF and Structural DXF.")
