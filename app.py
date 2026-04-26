import streamlit as st
import ezdxf
import io
import os
import tempfile
import math

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# =========================================================
# APP HEADER / SIDEBAR
# =========================================================
st.sidebar.title("Navigation")
tool_choice = st.sidebar.radio(
    "Select a Tool:",
    ["1. DXF Smart Purger", "2. Grid & Beam Sync"]
)
st.sidebar.markdown("---")
st.sidebar.info("Developed by James Oluwaseun Emmanuel")

st.title("🏗️ iLoveStructural")


# =========================================================
# HELPERS
# =========================================================
def save_uploaded_to_temp(uploaded_file):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    tmp.write(uploaded_file.getvalue())
    tmp.close()
    return tmp.name


def safe_remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def get_all_layouts(doc):
    layouts = []
    try:
        layouts.append(doc.modelspace())
    except Exception:
        pass

    try:
        for layout in doc.layouts:
            if layout.name != "Model":
                layouts.append(layout)
    except Exception:
        pass

    return layouts


def collect_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def delete_entities_on_layers(doc, layers_to_delete):
    deleted_count = 0
    layouts = get_all_layouts(doc)

    for layout in layouts:
        for entity in list(layout):
            try:
                if entity.dxf.layer in layers_to_delete:
                    layout.delete_entity(entity)
                    deleted_count += 1
            except Exception:
                continue

    return deleted_count


def collect_line_entities(doc):
    """
    Collect simple LINE entities from modelspace.
    This is a practical MVP approach for grid comparison.
    """
    lines = []
    msp = doc.modelspace()

    for e in msp:
        try:
            if e.dxftype() == "LINE":
                x1, y1, _ = e.dxf.start
                x2, y2, _ = e.dxf.end
                lines.append({
                    "layer": e.dxf.layer,
                    "start": (float(x1), float(y1)),
                    "end": (float(x2), float(y2)),
                })
        except Exception:
            continue

    return lines


def is_vertical(line, angle_tol=1e-6):
    x1, y1 = line["start"]
    x2, y2 = line["end"]
    return abs(x2 - x1) <= angle_tol and abs(y2 - y1) > angle_tol


def is_horizontal(line, angle_tol=1e-6):
    x1, y1 = line["start"]
    x2, y2 = line["end"]
    return abs(y2 - y1) <= angle_tol and abs(x2 - x1) > angle_tol


def normalize_line(line):
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    if is_vertical(line):
        x = round(x1, 3)
        y_min = round(min(y1, y2), 3)
        y_max = round(max(y1, y2), 3)
        return {
            "type": "vertical",
            "coord": x,
            "span_min": y_min,
            "span_max": y_max,
            "length": round(abs(y2 - y1), 3),
            "layer": line["layer"],
        }

    if is_horizontal(line):
        y = round(y1, 3)
        x_min = round(min(x1, x2), 3)
        x_max = round(max(x1, x2), 3)
        return {
            "type": "horizontal",
            "coord": y,
            "span_min": x_min,
            "span_max": x_max,
            "length": round(abs(x2 - x1), 3),
            "layer": line["layer"],
        }

    return None


def extract_grid_candidates(doc, min_length=1000):
    """
    Very simple heuristic:
    - only LINE entities
    - only vertical/horizontal
    - minimum length threshold
    """
    raw_lines = collect_line_entities(doc)
    candidates = []

    for line in raw_lines:
        n = normalize_line(line)
        if n and n["length"] >= min_length:
            candidates.append(n)

    return candidates


def group_by_orientation(candidates):
    verticals = [c for c in candidates if c["type"] == "vertical"]
    horizontals = [c for c in candidates if c["type"] == "horizontal"]

    verticals = sorted(verticals, key=lambda x: x["coord"])
    horizontals = sorted(horizontals, key=lambda x: x["coord"])

    return verticals, horizontals


def build_spacing_list(lines, axis_name):
    """
    Converts sorted grid lines into spacing checks between adjacent lines.
    """
    spacings = []
    if len(lines) < 2:
        return spacings

    for i in range(len(lines) - 1):
        a = lines[i]
        b = lines[i + 1]
        spacing = round(abs(b["coord"] - a["coord"]), 3)

        spacings.append({
            "axis": axis_name,
            "index_a": i + 1,
            "index_b": i + 2,
            "coord_a": a["coord"],
            "coord_b": b["coord"],
            "spacing": spacing,
        })

    return spacings


def compare_spacing_lists(arch_spacings, struc_spacings, tolerance):
    mismatches = []

    pair_count = min(len(arch_spacings), len(struc_spacings))

    for i in range(pair_count):
        a = arch_spacings[i]
        s = struc_spacings[i]
        delta = round(abs(a["spacing"] - s["spacing"]), 3)

        if delta > tolerance:
            mismatches.append({
                "axis": a["axis"],
                "label": f'{a["axis"]} Grid {a["index_a"]}-{a["index_b"]}',
                "arch_spacing": a["spacing"],
                "struc_spacing": s["spacing"],
                "delta": delta,
                "arch_coord_a": a["coord_a"],
                "arch_coord_b": a["coord_b"],
                "struc_coord_a": s["coord_a"],
                "struc_coord_b": s["coord_b"],
            })

    if len(arch_spacings) != len(struc_spacings):
        mismatches.append({
            "axis": "Count",
            "label": "Grid count mismatch",
            "arch_spacing": len(arch_spacings),
            "struc_spacing": len(struc_spacings),
            "delta": abs(len(arch_spacings) - len(struc_spacings)),
            "arch_coord_a": None,
            "arch_coord_b": None,
            "struc_coord_a": None,
            "struc_coord_b": None,
        })

    return mismatches


def analyze_grid_mismatches(arch_doc, struc_doc, tolerance):
    arch_candidates = extract_grid_candidates(arch_doc)
    struc_candidates = extract_grid_candidates(struc_doc)

    arch_v, arch_h = group_by_orientation(arch_candidates)
    struc_v, struc_h = group_by_orientation(struc_candidates)

    arch_v_sp = build_spacing_list(arch_v, "Vertical")
    struc_v_sp = build_spacing_list(struc_v, "Vertical")

    arch_h_sp = build_spacing_list(arch_h, "Horizontal")
    struc_h_sp = build_spacing_list(struc_h, "Horizontal")

    mismatches = []
    mismatches.extend(compare_spacing_lists(arch_v_sp, struc_v_sp, tolerance))
    mismatches.extend(compare_spacing_lists(arch_h_sp, struc_h_sp, tolerance))

    summary = {
        "arch_vertical_count": len(arch_v),
        "arch_horizontal_count": len(arch_h),
        "struc_vertical_count": len(struc_v),
        "struc_horizontal_count": len(struc_h),
        "total_mismatches": len(mismatches),
    }

    return mismatches, summary


def initialize_sync_state():
    defaults = {
        "audit_stage": "idle",              # idle | reviewing | finished
        "mismatches": [],
        "mismatch_index": 0,
        "decisions": [],
        "decision_message": "",
        "analysis_summary": {},
        "arch_filename": None,
        "struc_filename": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_sync_state():
    keys_to_reset = [
        "audit_stage",
        "mismatches",
        "mismatch_index",
        "decisions",
        "decision_message",
        "analysis_summary",
        "arch_filename",
        "struc_filename",
        "audit_choice",
    ]

    for key in keys_to_reset:
        if key in st.session_state:
            del st.session_state[key]


def render_summary_box(summary):
    if not summary:
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Arch Vertical Grids", summary.get("arch_vertical_count", 0))
    c2.metric("Arch Horizontal Grids", summary.get("arch_horizontal_count", 0))
    c3.metric("Struc Vertical Grids", summary.get("struc_vertical_count", 0))
    c4.metric("Struc Horizontal Grids", summary.get("struc_horizontal_count", 0))
    c5.metric("Mismatches", summary.get("total_mismatches", 0))


# =========================================================
# TOOL 1: DXF SMART PURGER
# =========================================================
if tool_choice == "1. DXF Smart Purger":
    st.subheader("Tool 1: DXF Smart Purger")
    uploaded_file = st.file_uploader("Upload your .DXF file", type=["dxf"], key="purger")

    if uploaded_file is not None:
        tmp_path = save_uploaded_to_temp(uploaded_file)

        try:
            doc = ezdxf.readfile(tmp_path)
            all_layers = collect_layer_names(doc)
            st.success(f"Successfully analyzed {uploaded_file.name}")

            if not all_layers:
                st.warning("No layers found in this DXF.")
            else:
                st.write(f"Detected **{len(all_layers)}** layers.")
                protected_layers = {"0", "Defpoints"}

                default_layers = all_layers.copy()

                layers_to_keep = st.multiselect(
                    "Which layers should remain?",
                    options=all_layers,
                    default=default_layers
                )

                removed_layers_preview = sorted(set(all_layers) - set(layers_to_keep))
                if removed_layers_preview:
                    st.warning(f"Layers marked for removal: {', '.join(removed_layers_preview)}")
                else:
                    st.info("No layers selected for removal.")

                if st.button("🔥 Purge and Prepare Download"):
                    layers_to_delete = set(all_layers) - set(layers_to_keep)

                    # Optional protection against deleting layer 0 / Defpoints entities
                    protected_hit = layers_to_delete.intersection(protected_layers)
                    if protected_hit:
                        st.warning(
                            f"Protected layer(s) excluded from deletion: {', '.join(sorted(protected_hit))}"
                        )
                        layers_to_delete = layers_to_delete - protected_layers

                    deleted_count = delete_entities_on_layers(doc, layers_to_delete)

                    out_buffer = io.StringIO()
                    doc.write(out_buffer)

                    st.success(f"Done. Deleted {deleted_count} entities from selected layers.")
                    st.balloons()

                    st.download_button(
                        "📥 Download Cleaned DXF",
                        data=out_buffer.getvalue().encode("utf-8"),
                        file_name=f"CLEANED_{uploaded_file.name}",
                        mime="application/dxf"
                    )

        except Exception as e:
            st.error(f"Error: {e}")

        finally:
            safe_remove_file(tmp_path)


# =========================================================
# TOOL 2: GRID & BEAM SYNC
# =========================================================
elif tool_choice == "2. Grid & Beam Sync":
    st.subheader("Tool 2: Architectural vs. Structural Synchronizer")
    st.caption("Smart prototype: compares simple horizontal/vertical LINE-based grid spacing from both DXF files.")

    initialize_sync_state()

    tolerance = st.slider("Select Acceptable Tolerance (mm)", 0.0, 50.0, 10.0, 0.5)
    min_grid_length = st.slider("Minimum Grid Line Length to Consider (mm)", 100.0, 10000.0, 1000.0, 100.0)

    col_a, col_b = st.columns(2)
    with col_a:
        arch_file = st.file_uploader("Reference (Arch)", type=["dxf"], key="arch")
    with col_b:
        struc_file = st.file_uploader("Target (Struc)", type=["dxf"], key="struc")

    if arch_file and struc_file:
        st.session_state.arch_filename = arch_file.name
        st.session_state.struc_filename = struc_file.name

        top_col1, top_col2 = st.columns([1, 1])

        with top_col1:
            if st.button("🚀 Run Smart Grid Audit"):
                arch_tmp = None
                struc_tmp = None

                try:
                    arch_tmp = save_uploaded_to_temp(arch_file)
                    struc_tmp = save_uploaded_to_temp(struc_file)

                    arch_doc = ezdxf.readfile(arch_tmp)
                    struc_doc = ezdxf.readfile(struc_tmp)

                    # Use the chosen min length in a local analysis path
                    def local_extract(doc):
                        raw_lines = collect_line_entities(doc)
                        candidates = []
                        for line in raw_lines:
                            n = normalize_line(line)
                            if n and n["length"] >= min_grid_length:
                                candidates.append(n)
                        return candidates

                    arch_candidates = local_extract(arch_doc)
                    struc_candidates = local_extract(struc_doc)

                    arch_v, arch_h = group_by_orientation(arch_candidates)
                    struc_v, struc_h = group_by_orientation(struc_candidates)

                    arch_v_sp = build_spacing_list(arch_v, "Vertical")
                    struc_v_sp = build_spacing_list(struc_v, "Vertical")
                    arch_h_sp = build_spacing_list(arch_h, "Horizontal")
                    struc_h_sp = build_spacing_list(struc_h, "Horizontal")

                    mismatches = []
                    mismatches.extend(compare_spacing_lists(arch_v_sp, struc_v_sp, tolerance))
                    mismatches.extend(compare_spacing_lists(arch_h_sp, struc_h_sp, tolerance))

                    summary = {
                        "arch_vertical_count": len(arch_v),
                        "arch_horizontal_count": len(arch_h),
                        "struc_vertical_count": len(struc_v),
                        "struc_horizontal_count": len(struc_h),
                        "total_mismatches": len(mismatches),
                    }

                    st.session_state.mismatches = mismatches
                    st.session_state.analysis_summary = summary
                    st.session_state.mismatch_index = 0
                    st.session_state.decisions = []
                    st.session_state.decision_message = ""

                    if mismatches:
                        st.session_state.audit_stage = "reviewing"
                    else:
                        st.session_state.audit_stage = "finished"
                        st.session_state.decision_message = "✅ No grid spacing mismatches found within tolerance."

                    st.rerun()

                except Exception as e:
                    st.error(f"Audit failed: {e}")

                finally:
                    safe_remove_file(arch_tmp)
                    safe_remove_file(struc_tmp)

        with top_col2:
            if st.button("🧹 Reset Audit State"):
                reset_sync_state()
                st.rerun()

        render_summary_box(st.session_state.analysis_summary)

        # -------------------------------------------------
        # REVIEWING STAGE
        # -------------------------------------------------
        if st.session_state.audit_stage == "reviewing":
            mismatches = st.session_state.mismatches
            idx = st.session_state.mismatch_index
            total = len(mismatches)

            if total > 0 and idx < total:
                mismatch = mismatches[idx]

                progress = (idx + 1) / total
                st.progress(progress)

                st.info(f"Reviewing mismatch {idx + 1} of {total}")
                st.warning(
                    f'{mismatch["label"]}: '
                    f'{mismatch["arch_spacing"]}mm (Arch) vs '
                    f'{mismatch["struc_spacing"]}mm (Struc) | '
                    f'Δ = {mismatch["delta"]}mm'
                )

                with st.expander("See technical details"):
                    st.write({
                        "Axis": mismatch["axis"],
                        "Label": mismatch["label"],
                        "Arch Spacing (mm)": mismatch["arch_spacing"],
                        "Struc Spacing (mm)": mismatch["struc_spacing"],
                        "Difference (mm)": mismatch["delta"],
                        "Arch Coords": [mismatch["arch_coord_a"], mismatch["arch_coord_b"]],
                        "Struc Coords": [mismatch["struc_coord_a"], mismatch["struc_coord_b"]],
                        "Tolerance (mm)": tolerance,
                    })

                choice = st.radio(
                    "Action:",
                    [
                        f'Force Arch Value ({mismatch["arch_spacing"]}mm)',
                        "Ignore and Pass",
                        "Reject"
                    ],
                    key=f"audit_choice_{idx}"
                )

                col1, col2 = st.columns([1, 1])

                with col1:
                    if st.button("Apply Decision"):
                        record = {
                            "mismatch_no": idx + 1,
                            "label": mismatch["label"],
                            "choice": choice,
                            "arch_spacing": mismatch["arch_spacing"],
                            "struc_spacing": mismatch["struc_spacing"],
                            "delta": mismatch["delta"],
                        }
                        st.session_state.decisions.append(record)

                        st.session_state.mismatch_index += 1

                        if st.session_state.mismatch_index >= total:
                            st.session_state.audit_stage = "finished"
                            st.session_state.decision_message = "✅ Audit complete. All mismatches reviewed."
                        else:
                            st.session_state.decision_message = f"✅ Decision saved for mismatch {idx + 1}. Moving to next item."

                        st.rerun()

                with col2:
                    if st.button("Finish & Reset"):
                        reset_sync_state()
                        st.rerun()

        # -------------------------------------------------
        # FINISHED STAGE
        # -------------------------------------------------
        elif st.session_state.audit_stage == "finished":
            if st.session_state.decision_message:
                if st.session_state.decision_message.startswith("✅"):
                    st.success(st.session_state.decision_message)
                else:
                    st.error(st.session_state.decision_message)

            decisions = st.session_state.decisions
            mismatches = st.session_state.mismatches

            if decisions:
                st.subheader("Audit Decisions")
                st.dataframe(decisions, use_container_width=True)

                csv_lines = ["mismatch_no,label,choice,arch_spacing,struc_spacing,delta"]
                for d in decisions:
                    csv_lines.append(
                        f'{d["mismatch_no"]},"{d["label"]}","{d["choice"]}",{d["arch_spacing"]},{d["struc_spacing"]},{d["delta"]}'
                    )
                csv_data = "\n".join(csv_lines).encode("utf-8")

                st.download_button(
                    "📥 Download Audit Decisions (CSV)",
                    data=csv_data,
                    file_name="grid_audit_decisions.csv",
                    mime="text/csv"
                )

            elif not mismatches:
                st.info("No mismatches to review.")

            colf1, colf2 = st.columns([1, 1])

            with colf1:
                if st.button("Run Another Audit"):
                    st.session_state.audit_stage = "idle"
                    st.session_state.mismatches = []
                    st.session_state.mismatch_index = 0
                    st.session_state.decisions = []
                    st.session_state.decision_message = ""
                    st.session_state.analysis_summary = {}
                    st.rerun()

            with colf2:
                if st.button("Finish & Full Reset"):
                    reset_sync_state()
                    st.rerun()

        # -------------------------------------------------
        # IDLE STAGE
        # -------------------------------------------------
        else:
            st.info("Upload both files and click 'Run Smart Grid Audit' to begin.")

    else:
        st.info("Please upload both the Architectural and Structural DXF files to continue.")
