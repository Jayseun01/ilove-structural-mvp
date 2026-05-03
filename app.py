import streamlit as st
import ezdxf
import os
import tempfile


# =========================================================
# APP CONFIG
# =========================================================

st.set_page_config(
    page_title="iLoveStructural - DXF Smart Purger",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ iLoveStructural")
st.subheader("Tool 1: DXF Smart Purger")
st.caption("Upload a DXF, choose the layers to keep, purge other modelspace entities, and download a cleaned DXF.")


# =========================================================
# FILE HELPERS
# =========================================================

def save_uploaded_to_temp(uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def write_doc_to_temp_bytes(doc):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp_path = tmp.name
    tmp.close()

    try:
        doc.saveas(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        safe_remove_file(tmp_path)


def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def uploaded_file_signature(uploaded_file):
    if uploaded_file is None:
        return None
    return uploaded_file.name, len(uploaded_file.getvalue())


# =========================================================
# PURGE LOGIC
# =========================================================

def purge_layers_from_modelspace(doc, layers_to_keep):
    """
    Deletes modelspace entities whose entity.dxf.layer is not in layers_to_keep.
    Does not delete layer definitions.
    Does not modify paperspace/layout entities.
    Does not purge blocks, linetypes, styles, etc.
    """
    keep_set = set(layers_to_keep)
    msp = doc.modelspace()

    deleted = 0
    skipped = 0
    deleted_by_layer = {}

    for entity in list(msp):
        try:
            layer = entity.dxf.layer

            if layer not in keep_set:
                msp.delete_entity(entity)
                deleted += 1
                deleted_by_layer[layer] = deleted_by_layer.get(layer, 0) + 1

        except Exception:
            skipped += 1
            continue

    return {
        "deleted": deleted,
        "skipped": skipped,
        "deleted_by_layer": deleted_by_layer,
    }


# =========================================================
# SESSION STATE
# =========================================================

def init_state():
    defaults = {
        "uploaded_sig": None,
        "doc": None,
        "file_name": "",
        "layers": [],
        "prepared_data": None,
        "purge_result": None,
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_loaded_file_state():
    for k in [
        "doc",
        "file_name",
        "layers",
        "prepared_data",
        "purge_result",
    ]:
        if k in st.session_state:
            del st.session_state[k]

    init_state()


init_state()


# =========================================================
# UI
# =========================================================

uploaded_file = st.file_uploader(
    "Upload your .DXF file",
    type=["dxf"],
    key="purger_upload",
)

sig = uploaded_file_signature(uploaded_file)

if sig != st.session_state.uploaded_sig:
    reset_loaded_file_state()
    st.session_state.uploaded_sig = sig

if uploaded_file is None:
    st.info("Upload a DXF file to begin.")
    st.stop()


# Load DXF once per uploaded file.
if st.session_state.doc is None:
    tmp = None

    try:
        tmp = save_uploaded_to_temp(uploaded_file)
        doc = ezdxf.readfile(tmp)

        st.session_state.doc = doc
        st.session_state.file_name = uploaded_file.name
        st.session_state.layers = get_layer_names(doc)

        st.success(f"Successfully analyzed `{uploaded_file.name}`.")

    except Exception as e:
        st.error(f"Failed to read DXF file: {e}")
        st.stop()

    finally:
        safe_remove_file(tmp)


doc = st.session_state.doc
layers = st.session_state.layers


if not layers:
    st.error("No layers found in this DXF.")
    st.stop()


st.markdown("### Layer Selection")

c1, c2 = st.columns([3, 1])

with c1:
    layers_to_keep = st.multiselect(
        "Choose layers to keep",
        options=layers,
        default=layers,
        help="Entities on unselected layers will be deleted from modelspace.",
    )

with c2:
    st.metric("Total Layers", len(layers))
    st.metric("Layers Selected", len(layers_to_keep))


if not layers_to_keep:
    st.warning("You must select at least one layer to keep before purging.")
    st.stop()


with st.expander("Selected Layers"):
    st.write(layers_to_keep)


st.markdown("### Purge")

st.warning(
    "This tool deletes modelspace entities on unselected layers. "
    "Always keep a backup of the original DXF."
)

b1, b2 = st.columns(2)

with b1:
    purge_clicked = st.button(
        "🔥 Purge and Prepare Download",
        type="primary",
    )

with b2:
    if st.button("🔄 Reset File"):
        reset_loaded_file_state()
        st.rerun()


if purge_clicked:
    try:
        # Work on the loaded document in memory.
        result = purge_layers_from_modelspace(doc, layers_to_keep)
        data = write_doc_to_temp_bytes(doc)

        st.session_state.purge_result = result
        st.session_state.prepared_data = data

        st.success(f"Deleted {result['deleted']} modelspace entities.")

        if result["skipped"]:
            st.warning(f"Skipped {result['skipped']} entities due to read/delete issues.")

    except Exception as e:
        st.error(f"Purge failed: {e}")


if st.session_state.purge_result:
    st.markdown("### Purge Summary")

    result = st.session_state.purge_result

    st.write({
        "deleted_entities": result["deleted"],
        "skipped_entities": result["skipped"],
        "deleted_by_layer": result["deleted_by_layer"],
    })


if st.session_state.prepared_data:
    st.markdown("### Download")

    st.download_button(
        "📥 Download Cleaned DXF",
        data=st.session_state.prepared_data,
        file_name=f"CLEANED_{st.session_state.file_name}",
        mime="application/dxf",
    )
