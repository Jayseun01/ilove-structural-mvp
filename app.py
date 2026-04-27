import streamlit as st
import ezdxf
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
        r"^[A-Z]{1,2}$",
        r"^\d{1,2}$",
        r"^[A-Z]{1,2}'$",
        r"^\d{1,2}[A-Z]?$",
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
def extract_texts(doc, layer_name):
    texts = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxf.layer != layer_name:
                continue

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


def extract_circles(doc, layer_name):
    circles = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() != "CIRCLE":
                continue
            if e.dxf.layer != layer_name:
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


def extract_axis_lines(doc, layer_name, min_length=1000.0):
    lines = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxf.layer != layer_name:
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
# TWO-WAY AUTHENTICATED MARKER DETECTION
# =========================================================
def text_inside_circle(text_point, circle_center, circle_radius, extra_gap=120.0):
    d = euclidean(text_point, circle_center)
    return d <= (circle_radius + extra_gap)


def line_attached_to_circle(line, circle_center, circle_radius, attach_gap=120.0):
    """
    Check if one endpoint of the line is near the circle boundary and aligned with the circle center.
    """
    cx, cy = circle_center
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    if line["orientation"] == "vertical":
        # x alignment to circle center
        if abs(line["coord"] - cx) > attach_gap:
            return False

        d1 = abs(euclidean((x1, y1), circle_center) - circle_radius)
        d2 = abs(euclidean((x2, y2), circle_center) - circle_radius)
        return min(d1, d2) <= attach_gap

    elif line["orientation"] == "horizontal":
        # y alignment to circle center
        if abs(line["coord"] - cy) > attach_gap:
            return False

        d1 = abs(euclidean((x1, y1), circle_center) - circle_radius)
        d2 = abs(euclidean((x2, y2), circle_center) - circle_radius)
        return min(d1, d2) <= attach_gap

    return False


def build_trusted_markers(doc, line_layer, text_layer, circle_layer, min_grid_length, text_gap, attach_gap):
    """
    Two-way authentication:
    1. Layer authentication
    2. Pattern authentication
    Only trusted markers are returned.
    """
    texts = extract_texts(doc, text_layer)
    circles = extract_circles(doc, circle_layer)
    lines = extract_axis_lines(doc, line_layer, min_length=min_grid_length)

    trusted_markers = []
    rejected = []

    for c in circles:
        circle_center = c["center"]
        circle_radius = c["radius"]

        candidate_texts = []
        for t in texts:
            if not probable_grid_label(t["text"]):
                continue
            if text_inside_circle(t["point"], circle_center, circle_radius, extra_gap=text_gap):
                candidate_texts.append(t)

        candidate_lines = []
        for line in lines:
            if line_attached_to_circle(line, circle_center, circle_radius, attach_gap=attach_gap):
                candidate_lines.append(line)

        if len(candidate_texts) == 1 and len(candidate_lines) >= 1:
            # choose nearest attached line
            best_line = min(
                candidate_lines,
                key=lambda ln: abs(ln["coord"] - circle_center[0]) if ln["orientation"] == "vertical"
                else abs(ln["coord"] - circle_center[1])
            )

            trusted_markers.append({
                "label": candidate_texts[0]["text"],
                "text_entity": candidate_texts[0]["entity"],
                "text_point": candidate_texts[0]["point"],
                "circle_entity": c["entity"],
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "line_entity": best_line["entity"],
                "orientation": best_line["orientation"],
                "coord": best_line["coord"],
                "line_start": best_line["start"],
                "line_end": best_line["end"],
            })
        else:
            rejected.append({
                "circle_center": circle_center,
                "circle_radius": circle_radius,
                "text_matches": len(candidate_texts),
                "line_matches": len(candidate_lines),
            })

    vertical = sorted(deduplicate_markers([m for m in trusted_markers if m["orientation"] == "vertical"]), key=lambda x: x["coord"])
    horizontal = sorted(deduplicate_markers([m for m in trusted_markers if m["orientation"] == "horizontal"]), key=lambda x: x["coord"])

    return {
        "texts": texts,
        "circles": circles,
        "lines": lines,
        "trusted_markers": trusted_markers,
        "rejected_markers": rejected,
        "vertical": vertical,
        "horizontal": horizontal,
    }


def deduplicate_markers(markers, tol=5.0):
    if not markers:
        return []

    markers = sorted(markers, key=lambda x: x["coord"])
    result = []
    current = [markers[0]]

    for item in markers[1:]:
        if abs(item["coord"] - current[-1]["coord"]) <= tol:
            current.append(item)
        else:
            result.append(current[0])
            current = [item]

    result.append(current[0])
    return result


# =========================================================
# GEOMETRY QA
# =========================================================
def build_spacings(markers, orientation):
    data = []
    if len(markers) < 2:
        return data

    for i in range(len(markers) - 1):
        a = markers[i]
        b = markers[i + 1]
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


def compare_spacings(arch_markers, struc_markers, orientation, tolerance):
    arch_sp = build_spacings(arch_markers, orientation)
    struc_sp = build_spacings(struc_markers, orientation)

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
# ARCH -> STRUC MAPPING
# =========================================================
def build_mapping_by_position(arch_markers, struc_markers):
    preview = []
    count = min(len(arch_markers), len(struc_markers))

    for i in range(count):
        preview.append({
            "position": i + 1,
            "arch_label": arch_markers[i]["label"],
            "struc_old_label": struc_markers[i]["label"],
            "arch_coord": arch_markers[i]["coord"],
            "struc_coord": struc_markers[i]["coord"],
            "difference": round(abs(arch_markers[i]["coord"] - struc_markers[i]["coord"]), 3),
        })

    return preview


def apply_arch_labels_to_structural_markers(arch_markers, struc_markers):
    """
    Safe rename:
    Update only the specific trusted structural marker text entities.
    No global find/replace.
    """
    count = min(len(arch_markers), len(struc_markers))
    changed = 0

    for i in range(count):
        new_label = arch_markers[i]["label"]
        marker = struc_markers[i]
        entity = marker["text_entity"]

        try:
            if entity.dxftype() == "TEXT":
                old = clean_text(entity.dxf.text)
                if old != new_label:
                    entity.dxf.text = new_label
                    changed += 1

            elif entity.dxftype() == "MTEXT":
                old = clean_text(entity.text)
                if old != new_label:
                    entity.text = new_label
                    changed += 1
        except Exception:
            continue

    return changed


# =========================================================
# VISUALIZATION
# =========================================================
def draw_preview(title, detection_result, issues=None):
    fig, ax = plt.subplots(figsize=(8, 8))

    for line in detection_result["lines"]:
        x1, y1 = line["start"]
        x2, y2 = line["end"]
        ax.plot([x1, x2], [y1, y2], color="lightgray", linewidth=0.8)

    for c in detection_result["circles"]:
        cx, cy = c["center"]
        circ = MplCircle((cx, cy), c["radius"], fill=False, color="silver", linewidth=0.8)
        ax.add_patch(circ)

    for m in detection_result["trusted_markers"]:
        cx, cy = m["circle_center"]
        color = "blue" if m["orientation"] == "vertical" else "green"
        circ = MplCircle((cx, cy), m["circle_radius"], fill=False, color=color, linewidth=2)
        ax.add_patch(circ)
        ax.text(cx, cy, m["label"], ha="center", va="center", color=color, fontsize=8)

    if issues:
        for issue in issues:
            if issue["status"] != "FAIL":
                continue
            if issue["orientation"] == "vertical":
                ax.axvline(issue["coord_a_struc"], color="red", linestyle="--", linewidth=1)
                ax.axvline(issue["coord_b_struc"], color="red", linestyle="--", linewidth=1)
            elif issue["orientation"] == "horizontal":
                ax.axhline(issue["coord_a_struc"], color="red", linestyle="--", linewidth=1)
                ax.axhline(issue["coord_b_struc"], color="red", linestyle="--", linewidth=1)

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
        "arch_detection": {},
        "struc_detection": {},
        "vertical_preview": [],
        "horizontal_preview": [],
        "geometry_issues": [],
        "geometry_continue": False,
        "apply_ready": False,
        "labels_changed_count": 0,
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
        "arch_detection",
        "struc_detection",
        "vertical_preview",
        "horizontal_preview",
        "geometry_issues",
        "geometry_continue",
        "apply_ready",
        "labels_changed_count",
    ]
    for key in keys:
        if key in st.session_state:
            del st.session_state[key]


# =========================================================
# TOOL 1
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
                dxf_bytes = write_doc_to_temp_bytes(doc)

                st.success(f"Deleted {deleted_count} modelspace entities.")
                st.download_button(
                    "📥 Download Cleaned DXF",
                    data=dxf_bytes,
                    file_name=f"CLEANED_{uploaded_file.name}",
                    mime="application/dxf"
                )

        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            safe_remove_file(tmp_path)


# =========================================================
# TOOL 2
# =========================================================
elif tool_choice == "2. Grid Label Sync":
    st.subheader("Tool 2: Strict Arch → Struc Grid Label Sync")
    st.caption(
        "Two-way authentication: only markers that pass both layer validation and geometry-pattern validation are renamed. "
        "Architectural labels are copied onto trusted Structural markers only."
    )

    init_sync_state()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        tolerance = st.slider("Tolerance (mm)", 0.0, 50.0, 10.0, 0.5)
    with c2:
        min_grid_length = st.slider("Min Grid Line Length (mm)", 100.0, 20000.0, 1000.0, 100.0)
    with c3:
        text_gap = st.slider("Text-in-Circle Gap (mm)", 20.0, 1000.0, 120.0, 10.0)
    with c4:
        attach_gap = st.slider("Line-to-Circle Attach Gap (mm)", 20.0, 1000.0, 120.0, 10.0)

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

            st.markdown("### Architectural Layer Setup")
            a1, a2, a3 = st.columns(3)
            with a1:
                arch_line_layer = st.selectbox("Arch Grid Line Layer", arch_layers, index=0)
            with a2:
                arch_text_layer = st.selectbox("Arch Grid Text Layer", arch_layers, index=0)
            with a3:
                arch_circle_layer = st.selectbox("Arch Grid Circle Layer", arch_layers, index=0)

            st.markdown("### Structural Layer Setup")
            s1, s2, s3 = st.columns(3)
            with s1:
                struc_line_layer = st.selectbox("Struc Grid Line Layer", struc_layers, index=0)
            with s2:
                struc_text_layer = st.selectbox("Struc Grid Text Layer", struc_layers, index=0)
            with s3:
                struc_circle_layer = st.selectbox("Struc Grid Circle Layer", struc_layers, index=0)

            b1, b2 = st.columns(2)
            with b1:
                if st.button("🚀 Analyze, Check, and Preview"):
                    try:
                        arch_detection = build_trusted_markers(
                            st.session_state.arch_doc,
                            line_layer=arch_line_layer,
                            text_layer=arch_text_layer,
                            circle_layer=arch_circle_layer,
                            min_grid_length=min_grid_length,
                            text_gap=text_gap,
                            attach_gap=attach_gap,
                        )

                        struc_detection = build_trusted_markers(
                            st.session_state.struc_doc,
                            line_layer=struc_line_layer,
                            text_layer=struc_text_layer,
                            circle_layer=struc_circle_layer,
                            min_grid_length=min_grid_length,
                            text_gap=text_gap,
                            attach_gap=attach_gap,
                        )

                        st.session_state.arch_detection = arch_detection
                        st.session_state.struc_detection = struc_detection

                        vertical_preview = build_mapping_by_position(
                            arch_detection["vertical"],
                            struc_detection["vertical"]
                        )
                        horizontal_preview = build_mapping_by_position(
                            arch_detection["horizontal"],
                            struc_detection["horizontal"]
                        )

                        st.session_state.vertical_preview = vertical_preview
                        st.session_state.horizontal_preview = horizontal_preview

                        issues = []
                        issues.extend(compare_spacings(
                            arch_detection["vertical"],
                            struc_detection["vertical"],
                            "vertical",
                            tolerance
                        ))
                        issues.extend(compare_spacings(
                            arch_detection["horizontal"],
                            struc_detection["horizontal"],
                            "horizontal",
                            tolerance
                        ))

                        st.session_state.geometry_issues = issues
                        _, fails = summarize_geometry_issues(issues)

                        if not fails:
                            st.session_state.geometry_continue = True
                            st.session_state.apply_ready = True
                        else:
                            st.session_state.geometry_continue = False
                            st.session_state.apply_ready = False

                        st.success("Strict detection complete. Review results before apply.")

                    except Exception as e:
                        st.error(f"Analysis failed: {e}")

            with b2:
                if st.button("🧹 Reset Tool 2"):
                    reset_sync_state()
                    st.rerun()

            arch_v = len(st.session_state.arch_detection.get("vertical", []))
            arch_h = len(st.session_state.arch_detection.get("horizontal", []))
            struc_v = len(st.session_state.struc_detection.get("vertical", []))
            struc_h = len(st.session_state.struc_detection.get("horizontal", []))

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Arch Vertical", arch_v)
            m2.metric("Arch Horizontal", arch_h)
            m3.metric("Struc Vertical", struc_v)
            m4.metric("Struc Horizontal", struc_h)

            tabs = st.tabs(["Grid Mapping", "Geometry Check", "Visual Preview", "Apply Labels", "Download"])

            with tabs[0]:
                st.write("### Vertical Arch → Struc Mapping")
                if st.session_state.vertical_preview:
                    st.dataframe(st.session_state.vertical_preview, use_container_width=True)
                else:
                    st.info("No vertical mapping yet.")

                st.write("### Horizontal Arch → Struc Mapping")
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
                            "Some structural grid spacings differ from the architectural reference beyond tolerance."
                        )

                        continue_choice = st.radio(
                            "Do you want to continue anyway?",
                            ["No, go back and review", "Yes, continue and apply labels"],
                            key="qa_gate"
                        )

                        if continue_choice == "Yes, continue and apply labels":
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
                pv1, pv2 = st.columns(2)

                with pv1:
                    if st.session_state.arch_detection:
                        fig_arch = draw_preview(
                            "Architectural Trusted Markers",
                            st.session_state.arch_detection
                        )
                        st.pyplot(fig_arch)
                    else:
                        st.info("No architectural preview yet.")

                with pv2:
                    if st.session_state.struc_detection:
                        fig_struc = draw_preview(
                            "Structural Trusted Markers + Fail Highlights",
                            st.session_state.struc_detection,
                            issues=st.session_state.geometry_issues
                        )
                        st.pyplot(fig_struc)
                    else:
                        st.info("No structural preview yet.")

                st.caption(
                    "Only markers that pass layer authentication and geometry-pattern authentication are shown as trusted."
                )

                if st.session_state.arch_detection:
                    st.write("#### Architectural Rejected Marker Count")
                    st.write(len(st.session_state.arch_detection.get("rejected_markers", [])))

                if st.session_state.struc_detection:
                    st.write("#### Structural Rejected Marker Count")
                    st.write(len(st.session_state.struc_detection.get("rejected_markers", [])))

            with tabs[3]:
                st.write("### Apply Architectural Labels to Trusted Structural Markers")
                if st.session_state.apply_ready:
                    st.success("Geometry gate passed or continuation approved.")

                    if st.button("✍️ Apply Arch → Struc Label Sync"):
                        try:
                            changed = 0
                            changed += apply_arch_labels_to_structural_markers(
                                st.session_state.arch_detection.get("vertical", []),
                                st.session_state.struc_detection.get("vertical", [])
                            )
                            changed += apply_arch_labels_to_structural_markers(
                                st.session_state.arch_detection.get("horizontal", []),
                                st.session_state.struc_detection.get("horizontal", [])
                            )

                            st.session_state.labels_changed_count = changed
                            st.success(f"Sync applied. {changed} trusted structural marker texts updated.")
                        except Exception as e:
                            st.error(f"Failed to apply label sync: {e}")
                else:
                    st.warning("Geometry check has not passed. Review Geometry Check tab first.")

            with tabs[4]:
                st.write("### Download Modified Structural DXF")
                if st.session_state.struc_doc is not None:
                    st.info(f"Updated trusted marker texts: {st.session_state.labels_changed_count}")
                    dxf_bytes = write_doc_to_temp_bytes(st.session_state.struc_doc)

                    st.download_button(
                        "📥 Download Relabeled Structural DXF",
                        data=dxf_bytes,
                        file_name=f"RELABELED_{st.session_state.struc_name}",
                        mime="application/dxf"
                    )
                else:
                    st.info("No structural DXF available.")
    else:
        st.info("Please upload both the Architectural DXF and Structural DXF.")
