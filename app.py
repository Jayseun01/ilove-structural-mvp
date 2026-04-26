import streamlit as st
import ezdxf
import io

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️")

st.title("🏗️ iLoveStructural")
st.subheader("Tool 1: DXF Smart Purger")

uploaded_file = st.file_uploader("Upload your .DXF file", type=['dxf'])

if uploaded_file is not None:
    try:
        # 1. Convert the uploaded file into a text string
        # This fixes the 'bytes-like object' error
        file_content = uploaded_file.getvalue().decode("utf-8", errors="ignore")
        text_stream = io.StringIO(file_content)
        
        # 2. Load the drawing
        doc = ezdxf.read(text_stream)
        
        # 3. Get list of layers
        all_layers = sorted([layer.dxf.name for layer in doc.layers])
        
        st.success(f"Successfully analyzed {uploaded_file.name}")
        
        st.markdown("---")
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
            out_buffer = io.StringIO()
            doc.write(out_buffer)
            
            st.balloons()
            st.download_button(
                label="📥 Download Cleaned DXF",
                data=out_buffer.getvalue(),
                file_name=f"CLEANED_{uploaded_file.name}",
                mime="application/dxf"
            )

    except Exception as e:
        st.error(f"Error: {e}")
