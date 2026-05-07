import streamlit as st
import ezdxf
import os
import tempfile
import math
import re
import io
import csv
import difflib

# =========================================================
# 1. AI VIBE RECONCILER (The Logic Bridge)
# =========================================================

class StructuralAI:
    """
    Intelligent logic to handle the 'pain' of Arch vs Structural drift.
    Optimized for Gemini 3.1 Vibe-Logic.
    """
    @staticmethod
    def get_match_confidence(source, target):
        s = StructuralAI.clean(source)
        t = StructuralAI.clean(target)
        if not s or not t: return 0
        if s == t: return 100
        # Sync sub-grids: A -> A.1
        if t.startswith(s + '.') or t.startswith(s + '-'): return 95
        # Sync primed: 1 -> 1'
        if s.strip("'") == t.strip("'"): return 90
        # Fuzzy string check
        ratio = difflib.SequenceMatcher(None, s, t).ratio()
        return round(ratio * 100) if ratio > 0.8 else 0

    @staticmethod
    def clean(value):
        if value is None: return ""
        txt = str(value).replace("\\P", " ").replace("\n", " ").replace("′", "'")
        return re.sub(r"\s+", " ", txt).strip().upper()

# =========================================================
# 2. CORE GEOMETRY & DXF HELPERS
# =========================================================

def save_uploaded_to_temp(uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name

def write_doc_to_temp_bytes(doc):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp_path = tmp.name
    tmp.close()
    try:
        doc.saveas(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

def euclidean(p1, p2):
    return math.dist((p1[0], p1[1]), (p2[0], p2[1]))

def probable_grid_label(text):
    text = StructuralAI.clean(text)
    return any(re.fullmatch(p, text) for p in [r"[A-Z]{1,3}", r"[A-Z]{1,3}'", r"\d{1,3}", r"\d{1,3}[A-Z]?", r"\d{1,3}[A-Z]?'?"])

# =========================================================
# 3. EXTRACTION LOGIC (The Structural Engine)
# =========================================================

def extract_markers(doc, text_layer, circle_layer):
    """Simplified for the Vibe Refactor."""
    msp = doc.modelspace()
    found = []
    texts = [e for e in msp if e.dxftype() in ('TEXT', 'MTEXT') and e.dxf.layer == text_layer]
    circles = [e for e in msp if e.dxftype() == 'CIRCLE' and e.dxf.layer == circle_layer]
    
    for c in circles:
        cp = (c.dxf.center.x, c.dxf.center.y)
        cr = c.dxf.radius
        best_t = None
        min_d = 999999
        for t in texts:
            tp = (t.dxf.insert.x, t.dxf.insert.y)
            d = euclidean(cp, tp)
            if d < cr * 1.5 and d < min_d:
                val = t.dxf.text if t.dxftype() == 'TEXT' else t.text
                if probable_grid_label(val):
                    min_d = d
                    best_t = t
        if best_t:
            found.append({"label": best_t.dxf.text if best_t.dxftype() == 'TEXT' else best_t.text, "entity": best_t, "pos": cp})
    return found

# =========================================================
# 4. APP INTERFACE
# =========================================================

st.set_page_config(page_title="iLoveStructural: AI Edition", page_icon="🏗️", layout="wide")
st.title("🏗️ iLoveStructural: AI Edition")
st.subheader("Tool 2: Grid Label Sync (Enhanced with Fuzzy Logic)")

u1, u2 = st.columns(2)
with u1: arch_file = st.file_uploader("Reference (Arch) DXF", type=["dxf"])
with u2: struc_file = st.file_uploader("Target (Struc) DXF", type=["dxf"])

if arch_file and struc_file:
    # 1. Load Docs
    arch_doc = ezdxf.readfile(save_uploaded_to_temp(arch_file))
    struc_doc = ezdxf.readfile(save_uploaded_to_temp(struc_file))
    
    # 2. Config Layers (Defaults for Engineering)
    st.sidebar.markdown("### Layer Settings")
    txt_lay = st.sidebar.text_input("Grid Text Layer", "S-GRID-IDEN")
    circ_lay = st.sidebar.text_input("Grid Bubble Layer", "S-GRID-IDEN")

    if st.button("🔥 Run AI Optimal Sync", type="primary"):
        with st.spinner("Processing labels with Gemini-Fuzzy logic..."):
            
            # Extract
            arch_markers = extract_markers(arch_doc, txt_lay, circ_lay)
            struc_markers = extract_markers(struc_doc, txt_lay, circ_lay)
            
            if not arch_markers or not struc_markers:
                st.error("No markers found on those layers. Check your layer names.")
            else:
                changed = 0
                # AI Matching Loop
                for sm in struc_markers:
                    best_match = None
                    max_conf = 0
                    
                    # Match by spatial vibe (closest coordinate first) then fuzzy text
                    for am in arch_markers:
                        dist = euclidean(sm["pos"], am["pos"])
                        if dist < 1000: # Standard tolerance for grid jumps
                            conf = StructuralAI.get_match_confidence(am["label"], sm["label"])
                            if conf > max_conf:
                                max_conf = conf
                                best_match = am
                    
                    if best_match and max_conf > 50:
                        entity = sm["entity"]
                        new_val = StructuralAI.clean(best_match["label"])
                        if hasattr(entity.dxf, 'text'): 
                            entity.dxf.text = new_val
                        else: 
                            entity.text = new_val
                        changed += 1

                st.success(f"AI Sync complete! Processed {changed} labels.")
                
                # =========================================================
                # THE MISSING DOWNLOAD BUTTON
                # =========================================================
                st.markdown("### 2. Output Download")
                processed_data = write_doc_to_temp_bytes(struc_doc)
                
                st.download_button(
                    label="📥 DOWNLOAD SYNCED DXF",
                    data=processed_data,
                    file_name=f"AI_SYNCED_{struc_file.name}",
                    mime="application/dxf",
                    key="download_final"
                )
                st.balloons()

else:
    st.info("Upload Architecture and Structural DXFs to begin AI optimization.")

st.caption("Terminal Status: Online | Powered by Gemini 3.1 Vibe-Logic")
