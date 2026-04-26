import streamlit as st

# Configure the page
st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="centered")

# The Header
st.title("🏗️ iLoveStructural")
st.subheader("The Engineer's Workflow Automation Suite")
st.write("Stop wasting hours on manual CAD cleanup. Upload your drawing and let the engine do the work.")

# The Dashboard Grid (For now, just one tool)
st.markdown("---")
st.markdown("### 🛠️ Tool 1: DXF Layer Analyzer")

# The File Uploader
uploaded_file = st.file_uploader("Drag and drop your messy .DXF file here", type=['dxf'])

if uploaded_file is not None:
    st.success("Drawing uploaded successfully! The server is ready to process it.")
    st.write(f"**Filename:** {uploaded_file.name}")
    st.info("The logic to purge non-structural layers will go here next.")
