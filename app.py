import streamlit as st
import ezdxf
import io

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️")

st.title("🏗️ iLoveStructural")
st.subheader("Tool 1: DXF Smart Purger")
st.write("Upload a messy drawing, keep only the structural layers, and download the clean version.")

uploaded_file = st.file_uploader("Upload your .DXF file", type=['dxf'])

if uploaded_file is not None:
    try:
        # Load the DXF from the upload
        file_bytes = io.BytesIO(uploaded_file.getvalue())
        doc = ezdxf.read(file_bytes)
        msp = doc.modelspace()
        
        # Get all unique layers that actually have stuff drawn on them
        all_layers = sorted([layer.dxf.name for layer in doc.layers])
        
        st.success(f"Successfully analyzed {uploaded_file.name}")
        
        st.markdown("---")
        st.write("### 🛠️ Step 1: Select Layers to KEEP")
        st.write("Uncheck the layers you want to delete (e.g., Furniture, Plumbing, Hatching).")
        
        # Create a multi-select box for the user
        layers_to_keep = st.multiselect(
            "Which layers should remain in the drawing?",
            options=all_layers,
            default=all_layers # By default, all are selected
        )

        if st.button("🔥 Purge and Prepare Download"):
            # Logic to delete unwanted layers
            layers_to_delete = set(all_layers) - set(layers_to_keep)
            
            for layer_name in layers_to_delete:
                if layer_name in doc.layers:
                    # Delete all entities on that layer
                    entities = doc.modelspace().query(f'*[layer=="{layer_name}"]')
                    for entity in entities:
                        doc.modelspace().delete_entity(entity)
            
            # Save the new file to a memory buffer
            out_buffer = io.StringIO()
            doc.write(out_buffer)
            
            st.balloons() # Just for fun!
            st.success("Drawing Cleaned!")
            
            # Create a download button
            st.download_button(
                label="📥 Download Cleaned DXF",
                data=out_buffer.getvalue(),
                file_name=f"CLEANED_{uploaded_file.name}",
                mime="application/dxf"
            )

    except Exception as e:
        st.error(f"Something went wrong: {e}")
