import streamlit as st
import ezdxf
import os
import tempfile
import math
import re
import io

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
# FILE & GENERAL HELPERS
# =========================================================
def save_uploaded_to_temp(uploaded_file):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp.write(uploaded_file.getvalue())
    tmp.close()
    return tmp.name

def safe_remove_file(path):
    try:
        if path and os.path.exists(path): os.remove(path)
    except Exception: pass

def write_doc_to_temp_bytes(doc):
    out_buffer = io.StringIO()
    doc.write(out_buffer)
    return out_buffer.getvalue().encode("utf-8")

def clean_text(value):
    if value is None: return ""
    txt = str(value).replace("\\P", " ").replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()

def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))

def is_vertical(x1, y1, x2, y2, tol=2.0):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol

def is_horizontal(x1, y1, x2, y2, tol=2.0):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol

def probable_grid_label(text):
    text = clean_text(text)
    patterns = [r"^[A-Z]{1,3}$", r"^\d{1,3}$", r"^[A-Z]{1,3}'$", r"^\d{1,3}[A-Z]?$"]
    return any(re.match(p, text) for p in patterns)

def is_numeric_label(text):
    return bool(re.match(r"^\d{1,3}[A-Z]?$", clean_text(text)))

def is_alpha_label(text):
    return bool(re.match(r"^[A-Z]{1,3}'?$", clean_text(text)))

def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])

# =========================================================
# THE SHIFT ENGINE (Physical Stretching)
# =========================================================
def shift_geometry(doc, axis, threshold, delta, tol=5.0):
    """Physically moves CAD entities to fix spacing mismatches."""
    msp = doc.modelspace()
    count = 0
    for e in msp:
        try:
            if e.dxftype() == "LINE":
                start, end = list(e.dxf.start), list(e.dxf.end)
                if axis == "x":
                    if start[0] >= threshold - tol: start[0] += delta
                    if end[0] >= threshold - tol: end[0] += delta
                else:
                    if start[1] >= threshold - tol: start[1] += delta
                    if end[1] >= threshold - tol: end[1] += delta
                e.dxf.start, e.dxf.end = start, end
                count += 1
            elif e.dxftype() in ("TEXT", "MTEXT", "CIRCLE", "INSERT"):
                point_attr = "center" if e.dxftype() == "CIRCLE" else "insert"
                p = list(getattr(e.dxf, point_attr))
                if axis == "x" and p[0] >= threshold - tol: p[0] += delta
                elif axis == "y" and p[1] >= threshold - tol: p[1] += delta
                setattr(e.dxf, point_attr, p)
                count += 1
        except Exception: continue
    return count

# =========================================================
# MULTI-PLAN CLUSTERING ENGINE
# =========================================================
def cluster_markers_by_plan(markers, threshold=20000.0):
    """Groups trusted markers into separate floor plans based on distance."""
    if not markers: return []
    clusters = []
    sorted_markers = sorted(markers, key=lambda m: (m['circle_center'][0], m['circle_center'][1]))
    for marker in sorted_markers:
        pos = marker['circle_center']
        assigned = False
        for cluster in clusters:
            avg_x = sum(m['circle_center'][0] for m in cluster) / len(cluster)
            avg_y = sum(m['circle_center'][1] for m in cluster) / len(cluster)
            if math.dist(pos, (avg_x, avg_y)) < threshold:
                cluster.append(marker); assigned = True; break
        if not assigned: clusters.append([marker])
    return clusters

# =========================================================
# TOOL 1 LOGIC
# =========================================================
if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload DXF", type=["dxf"])
    if uploaded_file:
        tmp_path = save_uploaded_to_temp(uploaded_file)
        doc = ezdxf.readfile(tmp_path)
        layers = get_layer_names(doc)
        keep = st.multiselect("Keep Layers:", layers, default=layers)
        if st.button("🔥 Purge"):
            msp = doc.modelspace()
            delete = set(layers) - set(keep)
            for e in list(msp):
                if e.dxf.layer in delete: msp.delete_entity(e)
            st.success("Drawing Cleaned!")
            st.download_button("📥 Download", write_doc_to_temp_bytes(doc), f"PURGED_{uploaded_file.name}")
        os.remove(tmp_path)

# =========================================================
# TOOL 2 LOGIC (Multi-Plan + Shift Engine)
# =========================================================
elif tool_choice == "2. Grid Label Sync":
    st.subheader("Tool 2: Multi-Plan Grid & Label Synchronizer")
    
    # ⚙️ SETTINGS
    with st.expander("Advanced Engineering Settings"):
        tolerance = st.slider("Tolerance (mm)", 0.0, 50.0, 10.0)
        sep_dist = st.number_input("Plan Separation Distance (mm)", value=25000, help="How far apart are your plans in Model Space?")
        do_stretch = st.checkbox("Apply Physical Stretch (Modify Coordinates)", value=True)

    col_a, col_b = st.columns(2)
    with col_a: arch_file = st.file_uploader("Reference (Arch)", type=["dxf"], key="a")
    with col_b: struc_file = st.file_uploader("Target (Struc - Multi-Plan)", type=["dxf"], key="s")

    if arch_file and struc_file:
        if 'struc_doc' not in st.session_state:
            t1, t2 = save_uploaded_to_temp(arch_file), save_uploaded_to_temp(struc_file)
            st.session_state.arch_doc, st.session_state.struc_doc = ezdxf.readfile(t1), ezdxf.readfile(t2)
            st.session_state.struc_name = struc_file.name
            os.remove(t1); os.remove(t2)

        al, sl = st.columns(2)
        arch_layer = al.selectbox("Arch Grid Layer", get_layer_names(st.session_state.arch_doc))
        struc_layer = sl.selectbox("Struc Grid Layer", get_layer_names(st.session_state.struc_doc))

        if st.button("🚀 Analyze All Plans"):
            # 1. Use your robust detection on both files
            from __main__ import build_trusted_markers # Self-reference helper
            arch_m = build_trusted_markers(st.session_state.arch_doc, arch_layer, arch_layer, arch_layer, 1000, 180, 180)["trusted_markers"]
            struc_m = build_trusted_markers(st.session_state.struc_doc, struc_layer, struc_layer, struc_layer, 1000, 180, 180)["trusted_markers"]
            
            # 2. Store Arch Master
            st.session_state.arch_v = sorted([m for m in arch_m if m['orientation'] == 'vertical'], key=lambda x: x['coord'])
            st.session_state.arch_h = sorted([m for m in arch_m if m['orientation'] == 'horizontal'], key=lambda x: x['coord'])
            
            # 3. Cluster Structural Plans
            st.session_state.struc_plans = cluster_markers_by_plan(struc_m, threshold=sep_dist)
            st.success(f"Found {len(st.session_state.struc_plans)} separate plans in structural drawing!")

        if 'struc_plans' in st.session_state:
            st.write("### Review Detected Plans")
            for i, plan in enumerate(st.session_state.struc_plans):
                plan_v = sorted([m for m in plan if m['orientation'] == 'vertical'], key=lambda x: x['coord'])
                plan_h = sorted([m for m in plan if m['orientation'] == 'horizontal'], key=lambda x: x['coord'])
                with st.expander(f"Plan {i+1}: Found {len(plan_v)}V and {len(plan_h)}H grids"):
                    st.write(f"Syncing labels to Arch Master: {st.session_state.arch_v[0]['label']} to {st.session_state.arch_v[-1]['label']}")

            if st.button("✍️ Sync All Plans & Physical Stretch"):
                total_text, total_moved = 0, 0
                for plan in st.session_state.struc_plans:
                    plan_v = sorted([m for m in plan if m['orientation'] == 'vertical'], key=lambda x: x['coord'])
                    plan_h = sorted([m for m in plan if m['orientation'] == 'horizontal'], key=lambda x: x['coord'])
                    
                    # Sync Vertical
                    for j in range(min(len(plan_v), len(st.session_state.arch_v))):
                        # Label Update
                        from __main__ import set_text_value
                        set_text_value(plan_v[j]['text_entity'], st.session_state.arch_v[j]['label'])
                        total_text += 1
                        # Physical Stretch (Only if enabled and mismatch exists)
                        if do_stretch:
                            delta = (st.session_state.arch_v[j]['coord'] - st.session_state.arch_v[0]['coord']) - (plan_v[j]['coord'] - plan_v[0]['coord'])
                            if abs(delta) > tolerance:
                                total_moved += shift_geometry(st.session_state.struc_doc, "x", plan_v[j]['coord'], delta)

                st.success(f"Sync Complete! Updated {total_text} labels and adjusted {total_moved} geometry entities.")
                st.download_button("📥 Download Synced Structural DXF", write_doc_to_temp_bytes(st.session_state.struc_doc), f"SYNCED_{st.session_state.struc_name}")
