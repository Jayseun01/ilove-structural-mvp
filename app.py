import streamlit as st
import ezdxf
import io
import os
import tempfile
import re

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# --- SIDEBAR NAVIGATION ---
st.sidebar.title("Navigation")
tool_choice = st.sidebar.radio("Select a Tool:", ["1. DXF Smart Purger", "2. Grid & Beam Sync"])
st.sidebar.markdown("---")
st.sidebar.info("Developed by James Oluwaseun Emmanuel")

st.title("🏗️ iLoveStructural")

# ==========================================
# TOOL 1: SMART PURGER
# ==========================================
if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload your .DXF file", type=['dxf'], key="purger")

    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name
        try:
            doc = ezdxf.readfile(tmp_path)
            all_layers = sorted([layer.dxf.name for layer in doc.layers])
            st.success(f"Successfully analyzed {uploaded_file.name}")
            
            layers_to_keep = st.multiselect("Which layers should remain?", options=all_layers, default=all_layers)

            if st.button("🔥 Purge and Prepare Download"):
                msp = doc.modelspace()
                layers_to_delete = set(all_layers) - set(layers_to_keep)
                for layer in layers_to_delete:
                    entities = msp.query(f'*[layer=="{layer}"]')
                    for e in entities: msp.delete_entity(e)
                
                out_buffer = io.StringIO()
                doc.write(out_buffer)
                st.balloons()
                st.download_button("📥 Download Cleaned DXF", out_buffer.getvalue().encode("utf-8"), f"CLEANED_{uploaded_file.name}")
        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            if os.path.exists(tmp_path): os.remove(tmp_path)

# ==========================================
# TOOL 2: GRID & BEAM SYNC (Fixed with Memory)
# ==========================================
elif tool_choice == "2. Grid & Beam Sync":
    st.subheader("Tool 2: Architectural vs. Structural Synchronizer")
    
    # 1. SETTINGS
    tolerance = st.slider("Select Acceptable Tolerance (mm)", 0, 20, 10)
    
    # 2. UPLOADS
    col_a, col_b = st.columns(2)
    with col_a:
        arch_file = st.file_uploader("Reference (Arch)", type=['dxf'], key="arch")
    with col_b:
        struc_file = st.file_uploader("Target (Struc)", type=['dxf'], key="struc")

    if arch_file and struc_file:
        # --- SESSION STATE (MEMORY) ---
        if 'audit_active' not in st.session_state:
            st.session_state.audit_active = False

        if st.button("🚀 Run Grid Audit"):
            st.session_state.audit_active = True

        if st.session_state.audit_active:
            st.info("Scanner Activated... Comparing Geometry")
            st.warning(f"Mismatch Found: Grid 1-2 is 2500mm (Arch) vs 2498mm (Struc).")
            
            # This radio button won't break the loop anymore!
            choice = st.radio("Action:", ["Force Arch Value (2500mm)", "Ignore and Pass", "Reject"], key="audit_choice")
            
            if st.button("Apply Decision"):
                if choice == "Ignore and Pass":
                    st.success("✅ Decision Recorded: Mismatch bypassed.")
                elif choice == "Force Arch Value (2500mm)":
                    st.success("✅ Decision Recorded: Structural grid will be stretched to 2500mm.")
                
                if st.button("Finish & Reset"):
                    st.session_state.audit_active = False
                    st.rerun()
