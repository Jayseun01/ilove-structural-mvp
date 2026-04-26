import streamlit as st
import ezdxf
import io

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️")

st.title("🏗️ iLoveStructural")
st.subheader("Tool 1: DXF Smart Purger")

uploaded_file = st.file_uploader("Upload your .DXF file", type=['dxf'])

if uploaded_file is not None:
    try:
        # --- THE MASTER KEY FIX ---
        # We pass the RAW bytes directly to ezdxf. 
        # It will automatically detect if it's Binary or ASCII and handle the encoding.
        file_bytes = uploaded_file.read()
        file_stream = io.BytesIO(file_bytes)
        doc = ezdxf.read(file_stream)
        
        # Get all layers
        all_layers = sorted([layer.dxf.name for layer in doc.layers])
        
        st.success(f"Successfully analyzed {uploaded_file.name}")
        
        st.markdown("---")
        st.write("### 🛠️ Step 1: Select Layers to KEEP")
        layers_to_keep = st.multiselect(
            "Which layers should remain?",
            options=all_layers,
            default=all_layers
        )

        if st.button("🔥 Purge and Prepare Download"):
            layers_to_delete = set(all_layers) - set(layers_to_keep)
            
            # Remove entities on unwanted layers
            for layer_name in layers_to_delete:
                entities = doc.modelspace().query(f'*[layer=="{layer_name}"]')
                for entity in entities:
                    doc.modelspace().delete_entity(entity)
            
            # Prepare the final file for download
            out_buffer = io.BytesIO()
            doc.write(out_buffer)
            
            st.balloons()
            st.download_button(
                label="📥 Download Cleaned DXF",
                data=out_buffer.getvalue(),
                file_name=f"CLEANED_{uploaded_file.name}",
                mime="application/dxf"
            )

    except Exception as e:
        # If there's a specific encoding error, we catch it here
        st.error(f"Analysis Error: {e}")
        st.info("Tip: Try saving your drawing as 'AutoCAD 2018 DXF' or 'ASCII DXF' and re-uploading.")
