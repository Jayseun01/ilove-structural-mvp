import streamlit as st
import ezdxf
import io
import os
import tempfile
import math
import re
import csv

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# =========================================================
# SIDEBAR / NAVIGATION
# =========================================================
st.sidebar.title("Navigation")
tool_choice = st.sidebar.radio(
    "Select a Tool:",
    ["1. DXF Smart Purger", "2. Grid Label Sync"]
)
st.sidebar.markdown("---")
st.sidebar.info("Developed by James Oluwaseun Emmanuel")

# =========================================================
# GENERAL HELPERS
# =========================================================
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

def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])

# =========================================================
# SPATIAL CLUSTERING ENGINE (The "Plan Detector")
# =========================================================
def cluster_markers_by_plan(markers, threshold=15000.0):
    """Groups trusted markers into separate 'Plans' based on physical distance."""
    if not markers: return []
    
    clusters = []
    # Sort markers spatially to improve clustering efficiency
    sorted_markers = sorted(markers, key=lambda m: (m['circle_center'][0], m['circle_center'][1]))
    
    for marker in sorted_markers:
        pos = marker['circle_center']
        assigned = False
        for cluster in clusters:
            # Check distance to the average center of the existing cluster
            avg_x = sum(m['circle_center'][0] for m in cluster) / len(cluster)
            avg_y = sum(m['circle_center'][1] for m in cluster) / len(cluster)
            
            if math.dist(pos, (avg_x, avg_y)) < threshold:
                cluster.append(marker)
                assigned = True
                break
        if not assigned:
            clusters.append([marker])
    return clusters

# =========================================================
# ENTITY EXTRACTION & RE-LABELING
# =========================================================
def get_text_value(entity):
    if entity.dxftype() == "TEXT": return clean_text(entity.dxf.text)
    elif entity.dxftype() == "MTEXT": return clean_text(entity.text)
    elif entity.dxftype() == "ATTRIB": return clean_text(entity.dxf.text)
    return ""

def set_text_value(entity, val):
    if entity.dxftype() == "TEXT": entity.dxf.text = val
    elif entity.dxftype() == "MTEXT": entity.text = val
    elif entity.dxftype() == "ATTRIB": entity.dxf.text = val

def build_trusted_markers(doc, line_layer, text_layer, circle_layer, text_gap=180.0):
    """Finds circles that contain valid grid text and are attached to lines."""
    msp = doc.modelspace()
    texts = []
    circles = []
    lines = []

    for e in msp:
        if e.dxf.layer == text_layer and e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
            texts.append({"entity": e, "text": get_text_value(e), "point": (e.dxf.insert.x, e.dxf.insert.y)})
        elif e.dxf.layer == circle_layer and e.dxftype() == "CIRCLE":
            circles.append({"entity": e, "center": (e.dxf.center.x, e.dxf.center.y), "radius": e.dxf.radius})
        elif e.dxf.layer == line_layer and e.dxftype() == "LINE":
            x1, y1, _ = e.dxf.start
            x2, y2, _ = e.dxf.end
            orient = "vertical" if is_vertical(x1, y1, x2, y2) else "horizontal" if is_horizontal(x1, y1, x2, y2) else None
            if orient:
                lines.append({"entity": e, "orientation": orient, "coord": x1 if orient == "vertical" else y1, "start": (x1, y1), "end": (x2, y2)})

    trusted = []
    for c in circles:
        # Find text inside circle
        matching_text = [t for t in texts if probable_grid_label(t["text"]) and euclidean(t["point"], c["center"]) <= (c["radius"] + text_gap)]
        # Find lines near circle
        matching_lines = [l for l in lines if abs(l["coord"] - (c["center"][0] if l["orientation"] == "vertical" else c["center"][1])) < 200]
        
        if matching_text and matching_lines:
            best_line = max(matching_lines, key=lambda l: math.dist(l["start"], l["end"]))
            trusted.append({
                "label": matching_text[0]["text"],
                "text_entity": matching_text[0]["entity"],
                "circle_center": c["center"],
                "orientation": best_line["orientation"],
                "coord": best_line["coord"]
            })
    return trusted

# =========================================================
# APP LOGIC
# =========================================================
st.title("🏗️ iLoveStructural")

if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload DXF", type=["dxf"])
    if uploaded_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name
        doc = ezdxf.readfile(tmp_path)
        layers = get_layer_names(doc)
        keep = st.multiselect("Keep Layers:", layers, default=layers)
        if st.button("🔥 Purge"):
            msp = doc.modelspace()
            delete = set(layers) - set(keep)
            for e in list(msp):
                if e.dxf.layer in delete: msp.delete_entity(e)
            out = io.StringIO()
            doc.write(out)
            st.download_button("📥 Download", out.getvalue().encode("utf-8"), f"PURGED_{uploaded_file.name}")
        os.remove(tmp_path)

elif tool_choice == "2. Grid Label Sync":
    st.subheader("Tool 2: Multi-Plan Grid Label Sync")
    st.info("Syncs multiple plans (Foundation, FF, etc.) to one Arch Master.")
    
    col_a, col_b = st.columns(2)
    with col_a: arch_file = st.file_uploader("Architectural Master", type=["dxf"])
    with col_b: struc_file = st.file_uploader("Structural (Multi-Plan)", type=["dxf"])
    
    sep_dist = st.number_input("Plan Separation Distance (mm)", value=15000)

    if arch_file and struc_file:
        if 'struc_doc' not in st.session_state:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as t1, tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as t2:
                t1.write(arch_file.getvalue()); t2.write(struc_file.getvalue())
                st.session_state.arch_doc = ezdxf.readfile(t1.name)
                st.session_state.struc_doc = ezdxf.readfile(t2.name)
                os.remove(t1.name); os.remove(t2.name)

        arch_layers = get_layer_names(st.session_state.arch_doc)
        struc_layers = get_layer_names(st.session_state.struc_doc)
        
        la, lb = st.columns(2)
        arch_l = la.selectbox("Arch Grid Layer", arch_layers)
        struc_l = lb.selectbox("Struc Grid Layer", struc_layers)

        if st.button("🔎 Analyze Multi-Plan Layout"):
            arch_markers = build_trusted_markers(st.session_state.arch_doc, arch_l, arch_l, arch_l)
            struc_markers = build_trusted_markers(st.session_state.struc_doc, struc_l, struc_l, struc_l)
            
            # Master Arch groups
            st.session_state.arch_v = sorted([m for m in arch_markers if m['orientation'] == 'vertical'], key=lambda x: x['coord'])
            st.session_state.arch_h = sorted([m for m in arch_markers if m['orientation'] == 'horizontal'], key=lambda x: x['coord'])
            
            # Cluster Structural markers into separate plans
            st.session_state.struc_plans = cluster_markers_by_plan(struc_markers, threshold=sep_dist)
            st.success(f"Detected {len(st.session_state.struc_plans)} separate plans in structural file!")

        if 'struc_plans' in st.session_state:
            st.write("### Audit Summary")
            all_mappings = []
            for i, plan in enumerate(st.session_state.struc_plans):
                plan_v = sorted([m for m in plan if m['orientation'] == 'vertical'], key=lambda x: x['coord'])
                plan_h = sorted([m for m in plan if m['orientation'] == 'horizontal'], key=lambda x: x['coord'])
                
                with st.expander(f"📦 Plan {i+1} Details ({len(plan)} markers)"):
                    st.write(f"Verticals: {len(plan_v)}, Horizontals: {len(plan_h)}")
                    # Preview mapping for first few
                    for j in range(min(len(plan_v), len(st.session_state.arch_v))):
                        all_mappings.append({"Plan": i+1, "Struc_Old": plan_v[j]['label'], "Arch_New": st.session_state.arch_v[j]['label']})
            
            st.table(all_mappings[:10]) # Show snippet

            if st.button("✍️ Apply Labels to ALL Plans"):
                total_changed = 0
                for plan in st.session_state.struc_plans:
                    plan_v = sorted([m for m in plan if m['orientation'] == 'vertical'], key=lambda x: x['coord'])
                    plan_h = sorted([m for m in plan if m['orientation'] == 'horizontal'], key=lambda x: x['coord'])
                    
                    # Sync Vertical
                    for j in range(min(len(plan_v), len(st.session_state.arch_v))):
                        set_text_value(plan_v[j]['text_entity'], st.session_state.arch_v[j]['label'])
                        total_changed += 1
                    # Sync Horizontal
                    for j in range(min(len(plan_h), len(st.session_state.arch_h))):
                        set_text_value(plan_h[j]['text_entity'], st.session_state.arch_h[j]['label'])
                        total_changed += 1
                
                st.success(f"Successfully updated {total_changed} grid labels across all plans!")
                
                # Final Download
                out = io.StringIO()
                st.session_state.struc_doc.write(out)
                st.download_button("📥 Download Synced Structural DXF", out.getvalue().encode("utf-8"), f"SYNCED_{struc_file.name}")
