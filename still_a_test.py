import streamlit as st
import ezdxf
import os

# --- 1. THE FRONTEND UI (The Client Brief) ---
st.set_page_config(page_title="iLoveStructural Generator", layout="wide")
st.title("🏗️ iLoveStructural: DXF Grid Generator")
st.markdown("Enter the architectural brief parameters below to instantly generate a workable DXF grid.")

with st.sidebar:
    st.header("The Client Brief")
    
    st.subheader("Grid Dimensions (mm)")
    # Taking inputs as comma-separated strings to allow multiple spans
    x_input = st.text_input("X-Axis Spans (e.g., 4000, 3500, 4000)", "4000, 3500")
    y_input = st.text_input("Y-Axis Spans (e.g., 5000, 4500)", "5000, 4500")
    
    st.subheader("Architectural Rules")
    wall_thickness = st.selectbox("Standard Wall Thickness (mm)", [225, 150])
    add_cantilever = st.checkbox("Include 1.5m Front Cantilever")

# --- 2. THE ENGINE (Processing the Inputs) ---
def generate_dxf(x_spans, y_spans, wall_thk, cantilever):
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # Calculate cumulative coordinates from spans
    x_coords = [0]
    for span in x_spans: x_coords.append(x_coords[-1] + span)
        
    y_coords = [0]
    for span in y_spans: y_coords.append(y_coords[-1] + span)

    max_y = max(y_coords) + 2000
    max_x = max(x_coords) + 2000

    # Draw the lines
    for x in x_coords:
        msp.add_line((x, -2000), (x, max_y), dxfattribs={'layer': 'STR_GRID'})
    for y in y_coords:
        msp.add_line((-2000, y), (max_x, y), dxfattribs={'layer': 'STR_GRID'})
        
    # Just a placeholder for how you'd add a cantilever rule
    if cantilever:
        msp.add_text("1.5m Cantilever Zone", dxfattribs={'height': 250}).set_placement((0, max_y - 1000))

    # Save to a temporary file
    filename = "Generated_Brief.dxf"
    doc.saveas(filename)
    return filename

# --- 3. THE OUTPUT (Execution) ---
if st.button("Generate AutoCAD File"):
    try:
        # Convert string inputs to lists of integers
        x_list = [int(i.strip()) for i in x_input.split(',')]
        y_list = [int(i.strip()) for i in y_input.split(',')]
        
        # Run the engine
        output_file = generate_dxf(x_list, y_list, wall_thickness, add_cantilever)
        
        # Create download button
        with open(output_file, "rb") as file:
            st.success("Drafting Complete! 🚀")
            st.download_button(
                label="Download .DXF File",
                data=file,
                file_name="iLoveStructural_Grid.dxf",
                mime="application/dxf"
            )
    except Exception as e:
        st.error(f"Error processing dimensions: Ensure you use numbers separated by commas. Details: {e}")
