import streamlit as st
import ezdxf
import io
import os
import tempfile

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️")

st.title("🏗️ iLoveStructural")
st.subheader("Tool 1: DXF Smart Purger")

uploaded_file = st.file_uploader("Upload your .DXF file", type=['dxf'])

if uploaded_file is not None:
    # Save to temp file for stability
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        doc = ezdxf.readfile(tmp_path)
        all_layers = sorted([layer.dxf.name for layer in doc.layers])
        
        st.success(f"Successfully analyzed {uploaded_file.name}")
        st.markdown("---")
        
        st.write("### 🛠️ Step 1: Select Layers to KEEP")
        
        # --- NEW: SELECT ALL / UNSELECT ALL LOGIC ---
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
            "Which layers should remain in the drawing?",
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
                
                # Convert back to bytes for download
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
else:
    st.info("Waiting for a DXF file...")
