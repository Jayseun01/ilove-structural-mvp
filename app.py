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
    # --- THE "TOTAL RESILIENCE" FIX ---
    # We save the file to a temporary location so ezdxf can use its full power
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        # Load the drawing from the temp path
        doc = ezdxf.readfile(tmp_path)
        
        # Get list of layers
        all_layers = sorted([layer.dxf.name for layer in doc.layers])
        
        st.success(f"Successfully analyzed {uploaded_file.name}")
        
        st.markdown("---")
        st.write("### 🛠️ Step 1: Select Layers to KEEP")
        layers_to_keep = st.multiselect(
            "Which layers should remain in the drawing?",
            options=all_layers,
            default=all_layers
        )

        if st.button("🔥 Purge and Prepare Download"):
            layers_to_delete = set(all_layers) - set(layers_to_keep)
            
            # Remove entities on unwanted layers
            msp = doc.modelspace()
            for layer_name in layers_to_delete:
                entities = msp.query(f'*[layer=="{layer_name}"]')
                for entity in entities:
                    msp.delete_entity(entity)
            
            # Prepare the final file for download using a Byte Stream
            out_buffer = io.StringIO()
            doc.write(out_buffer)
            byte_content = out_buffer.getvalue().encode("utf-8")
            
            st.balloons()
            st.success("Drawing Cleaned!")
            
            st.download_button(
                label="📥 Download Cleaned DXF",
                data=byte_content,
                file_name=f"CLEANED_{uploaded_file.name}",
                mime="application/dxf"
            )

    except Exception as e:
        st.error(f"Analysis Error: {e}")
    
    finally:
        # Clean up the temporary file from the server
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

else:
    st.info("Waiting for a DXF file... Upload your plan to begin.")
