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
# TOOL 1: SMART PURGER (Already Working)
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
            st.markdown("---")
            
            st.write("### 🛠️ Step 1: Select Layers to KEEP")
            
            if 'selected_layers' not in st.session_state:
                st.session_state.selected_layers = all_layers

            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("✅ Select All"):
                    st.session_state.selected_layers = all_layers
                    st.rerun()
            with col2:
                if st.button("❌ Unselect All"):
                    st.session_state.selected_layers = []
                    st.rerun()

            layers_to_keep = st.multiselect(
                "Which layers should remain?",
                options=all_layers,
                key="selected_layers"
            )

            if st.button("🔥 Purge and Prepare Download"):
                if not layers_to_keep:
                    st.warning("Please select at least one layer to keep!")
                else:
                    layers_to_delete = set(all_layers) - set(layers_to_keep)
                    msp = doc.modelspace()
                    for layer_name in layers_to_delete:
                        entities = msp.query(f'*[layer=="{layer_name}"]')
                        for entity in entities:
                            msp.delete_entity(entity)
                    
                    out_buffer = io.StringIO()
                    doc.write(out_buffer)
                    byte_content = out_buffer.getvalue().encode("utf-8")
                    
                    st.balloons()
                    st.download_button(
                        label="📥 Download Cleaned DXF",
                        data=byte_content,
                        file_name=f"CLEANED_{uploaded_file.name}",
                        mime="application/dxf"
                    )

        except Exception as e:
            st.error(f"Analysis Error: {e}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

# ==========================================
# TOOL 2: GRID & BEAM SYNC (The New Brain)
# ==========================================
elif tool_choice == "2. Grid & Beam Sync":
    st.subheader("Tool 2: Architectural vs. Structural Synchronizer")
    st.write("Establish a 'Source of Truth' and audit grid/beam alignment.")

    # 1. SET TOLERANCE
    st.markdown("### ⚙️ Settings")
    tolerance = st.slider("Select Acceptable Tolerance (mm)", min_value=0, max_value=20, value=5)
    
    st.markdown("---")
    
    # 2. FILE UPLOADS
    col_a, col_b = st.columns(2)
    with col_a:
        st.write("#### 🏛️ Master (Architectural)")
        arch_file = st.file_uploader("Reference DXF", type=['dxf'], key="arch")
    with col_b:
        st.write("#### 🏗️ Target (Structural)")
        struc_file = st.file_uploader("Drawing to Audit", type=['dxf'], key="struc")

    if arch_file and struc_file:
        st.success("Both drawings loaded into memory. Ready to initiate synchronization protocol.")
        
        if st.button("🚀 Run Grid Audit"):
            # This is where we will put the deep math logic next!
            st.info("Scanner Activated...")
            st.write("1. Finding Anchor Points...")
            st.write("2. Scanning for Grid Labels (ignoring dimensions)...")
            st.write("3. Checking tolerances...")
            
            # Placeholder for the UI you will see once the math is built
            st.warning(f"Simulated Error: Grid 1-2 distance mismatch! (Arch: 2500mm, Struct: 2498mm. Tolerance: {tolerance}mm)")
            
            action = st.radio("How to proceed?", ["Force Structural to match Arch (2500mm)", "Reject and Abort", "Ignore and Pass"])
            
            if st.button("Apply Fix"):
                st.success("Grid topological sub-scripting applied. Example: Inserted Grid '1a' between 1 and 2.")
