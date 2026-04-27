import streamlit as st
import ezdxf
import io
import os
import tempfile
import math

st.set_page_config(page_title="iLoveStructural", page_icon="🏗️", layout="wide")

# =========================================================
# SIDEBAR / HEADER
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
# FILE HELPERS
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


# =========================================================
# GENERAL GEOMETRY HELPERS
# =========================================================
def dist(p1, p2):
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def is_vertical_line_coords(x1, y1, x2, y2, tol=1.0):
    return abs(x1 - x2) <= tol and abs(y2 - y1) > tol


def is_horizontal_line_coords(x1, y1, x2, y2, tol=1.0):
    return abs(y1 - y2) <= tol and abs(x2 - x1) > tol


# =========================================================
# DXF ENTITY EXTRACTORS
# =========================================================
def get_layer_names(doc):
    return sorted([layer.dxf.name for layer in doc.layers])


def extract_grid_lines_from_layer(doc, layer_name, min_length=1000.0):
    """
    Extract horizontal and vertical grid-like lines from a chosen layer.
    Supports LINE and LWPOLYLINE segment decomposition.
    """
    msp = doc.modelspace()
    found = []

    for e in msp:
        try:
            if e.dxf.layer != layer_name:
                continue

            t = e.dxftype()

            if t == "LINE":
                x1, y1, _ = e.dxf.start
                x2, y2, _ = e.dxf.end
                length = math.dist((x1, y1), (x2, y2))

                if length < min_length:
                    continue

                if is_vertical_line_coords(x1, y1, x2, y2):
                    found.append({
                        "orientation": "vertical",
                        "coord": round(float(x1), 3),
                        "start": (float(x1), float(y1)),
                        "end": (float(x2), float(y2)),
                        "length": round(float(length), 3),
                    })

                elif is_horizontal_line_coords(x1, y1, x2, y2):
                    found.append({
                        "orientation": "horizontal",
                        "coord": round(float(y1), 3),
                        "start": (float(x1), float(y1)),
                        "end": (float(x2), float(y2)),
                        "length": round(float(length), 3),
                    })

            elif t == "LWPOLYLINE":
                pts = list(e.get_points())
                for i in range(len(pts) - 1):
                    x1, y1 = float(pts[i][0]), float(pts[i][1])
                    x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
                    length = math.dist((x1, y1), (x2, y2))

                    if length < min_length:
                        continue

                    if is_vertical_line_coords(x1, y1, x2, y2):
                        found.append({
                            "orientation": "vertical",
                            "coord": round(float(x1), 3),
                            "start": (float(x1), float(y1)),
                            "end": (float(x2), float(y2)),
                            "length": round(float(length), 3),
                        })

                    elif is_horizontal_line_coords(x1, y1, x2, y2):
                        found.append({
                            "orientation": "horizontal",
                            "coord": round(float(y1), 3),
                            "start": (float(x1), float(y1)),
                            "end": (float(x2), float(y2)),
                            "length": round(float(length), 3),
                        })
        except Exception:
            continue

    return deduplicate_grid_lines(found)


def deduplicate_grid_lines(lines, tol=5.0):
    if not lines:
        return []

    grouped = {"vertical": [], "horizontal": []}

    for ori in ["vertical", "horizontal"]:
        subset = sorted([x for x in lines if x["orientation"] == ori], key=lambda x: x["coord"])
        if not subset:
            continue

        current = [subset[0]]
        for item in subset[1:]:
            if abs(item["coord"] - current[-1]["coord"]) <= tol:
                current.append(item)
            else:
                grouped[ori].append(merge_grid_group(current))
                current = [item]
        grouped[ori].append(merge_grid_group(current))

    return grouped["vertical"] + grouped["horizontal"]


def merge_grid_group(group):
    avg_coord = round(sum(x["coord"] for x in group) / len(group), 3)
    best = max(group, key=lambda x: x["length"])
    out = dict(best)
    out["coord"] = avg_coord
    return out


def split_grids(lines):
    vertical = sorted([x for x in lines if x["orientation"] == "vertical"], key=lambda x: x["coord"])
    horizontal = sorted([x for x in lines if x["orientation"] == "horizontal"], key=lambda x: x["coord"])
    return vertical, horizontal


def build_spacing_records(lines, orientation):
    records = []
    if len(lines) < 2:
        return records

    for i in range(len(lines) - 1):
        a = lines[i]
        b = lines[i + 1]
        spacing = round(abs(b["coord"] - a["coord"]), 3)
        records.append({
            "orientation": orientation,
            "index": i,
            "from_idx": i + 1,
            "to_idx": i + 2,
            "coord_a": a["coord"],
            "coord_b": b["coord"],
            "spacing": spacing,
        })
    return records


# =========================================================
# MASTER ORIGIN ALIGNMENT
# =========================================================
def get_master_origin(verticals, horizontals):
    """
    Master Origin = first vertical + first horizontal grid intersection.
    """
    if not verticals or not horizontals:
        return None
    return (verticals[0]["coord"], horizontals[0]["coord"])


def translate_doc(doc, dx=0.0, dy=0.0):
    """
    Translate all supported entities in modelspace.
    """
    msp = doc.modelspace()
    moved = 0

    for e in msp:
        try:
            shift_entity(e, dx=dx, dy=dy)
            moved += 1
        except Exception:
            continue

    return moved


def align_structural_to_arch(arch_doc, struc_doc, arch_grid_layer, struc_grid_layer, min_grid_length):
    arch_lines = extract_grid_lines_from_layer(arch_doc, arch_grid_layer, min_grid_length)
    struc_lines = extract_grid_lines_from_layer(struc_doc, struc_grid_layer, min_grid_length)

    arch_v, arch_h = split_grids(arch_lines)
    struc_v, struc_h = split_grids(struc_lines)

    arch_origin = get_master_origin(arch_v, arch_h)
    struc_origin = get_master_origin(struc_v, struc_h)

    if arch_origin is None or struc_origin is None:
        return None, None, None, None, None

    dx = arch_origin[0] - struc_origin[0]
    dy = arch_origin[1] - struc_origin[1]

    translate_doc(struc_doc, dx=dx, dy=dy)

    return dx, dy, arch_origin, struc_origin, struc_doc


# =========================================================
# SHIFT ENGINE
# =========================================================
def shift_point_xy(x, y, axis, delta):
    if axis == "x":
        return x + delta, y
    return x, y + delta


def shift_entity(e, dx=0.0, dy=0.0):
    """
    Generic translation.
    """
    t = e.dxftype()

    if t == "LINE":
        s = e.dxf.start
        en = e.dxf.end
        e.dxf.start = (float(s.x) + dx, float(s.y) + dy, float(getattr(s, "z", 0.0)))
        e.dxf.end = (float(en.x) + dx, float(en.y) + dy, float(getattr(en, "z", 0.0)))

    elif t == "LWPOLYLINE":
        pts = list(e.get_points())
        new_pts = []
        for p in pts:
            x = float(p[0]) + dx
            y = float(p[1]) + dy
            rest = list(p[2:]) if len(p) > 2 else []
            new_pts.append((x, y, *rest))
        e.set_points(new_pts)

    elif t == "TEXT":
        ins = e.dxf.insert
        e.dxf.insert = (float(ins.x) + dx, float(ins.y) + dy, float(getattr(ins, "z", 0.0)))

    elif t == "MTEXT":
        ins = e.dxf.insert
        e.dxf.insert = (float(ins.x) + dx, float(ins.y) + dy, float(getattr(ins, "z", 0.0)))

    elif t == "INSERT":
        ins = e.dxf.insert
        e.dxf.insert = (float(ins.x) + dx, float(ins.y) + dy, float(getattr(ins, "z", 0.0)))

    elif t == "CIRCLE":
        c = e.dxf.center
        e.dxf.center = (float(c.x) + dx, float(c.y) + dy, float(getattr(c, "z", 0.0)))

    elif t == "ARC":
        c = e.dxf.center
        e.dxf.center = (float(c.x) + dx, float(c.y) + dy, float(getattr(c, "z", 0.0)))

    # Unsupported entities are silently ignored


def entity_min_coordinate(e, axis):
    """
    Get threshold coordinate for deciding whether entity lies at/after K.
    """
    t = e.dxftype()

    try:
        if t == "LINE":
            s = e.dxf.start
            en = e.dxf.end
            vals = [float(s.x), float(en.x)] if axis == "x" else [float(s.y), float(en.y)]
            return min(vals)

        elif t == "LWPOLYLINE":
            pts = list(e.get_points())
            vals = [float(p[0]) for p in pts] if axis == "x" else [float(p[1]) for p in pts]
            return min(vals)

        elif t in ["TEXT", "MTEXT", "INSERT"]:
            ins = e.dxf.insert
            return float(ins.x) if axis == "x" else float(ins.y)

        elif t in ["CIRCLE", "ARC"]:
            c = e.dxf.center
            return float(c.x) if axis == "x" else float(c.y)
    except Exception:
        return None

    return None


def stretch_structural_doc(doc, axis, threshold_k, delta):
    """
    Recursive shift:
    Every entity with coordinate >= K is shifted by delta.
    """
    msp = doc.modelspace()
    shifted = 0

    for e in msp:
        try:
            min_coord = entity_min_coordinate(e, axis)
            if min_coord is None:
                continue

            if min_coord >= threshold_k:
                if axis == "x":
                    shift_entity(e, dx=delta, dy=0.0)
                else:
                    shift_entity(e, dx=0.0, dy=delta)
                shifted += 1
        except Exception:
            continue

    return shifted


# =========================================================
# AUDIT FUNCTIONS
# =========================================================
def compare_spacing(arch_lines, struc_lines, tolerance, orientation):
    arch_sp = build_spacing_records(arch_lines, orientation)
    struc_sp = build_spacing_records(struc_lines, orientation)

    mismatches = []
    pair_count = min(len(arch_sp), len(struc_sp))

    for i in range(pair_count):
        a = arch_sp[i]
        s = struc_sp[i]
        delta = round(a["spacing"] - s["spacing"], 3)

        if abs(delta) > tolerance:
            mismatches.append({
                "type": "spacing",
                "orientation": orientation,
                "span_label": f"{orientation.title()} Span {a['from_idx']}-{a['to_idx']}",
                "arch_spacing": a["spacing"],
                "struc_spacing": s["spacing"],
                "difference": round(abs(delta), 3),
                "signed_delta": delta,
                "arch_coord_target": a["coord_b"],
                "struc_coord_target": s["coord_b"],
            })

    return mismatches


def refresh_analysis_from_docs(arch_doc, struc_doc, arch_grid_layer, struc_grid_layer, min_grid_length, tolerance):
    arch_lines = extract_grid_lines_from_layer(arch_doc, arch_grid_layer, min_grid_length)
    struc_lines = extract_grid_lines_from_layer(struc_doc, struc_grid_layer, min_grid_length)

    arch_v, arch_h = split_grids(arch_lines)
    struc_v, struc_h = split_grids(struc_lines)

    mismatches = []
    mismatches.extend(compare_spacing(arch_v, struc_v, tolerance, "vertical"))
    mismatches.extend(compare_spacing(arch_h, struc_h, tolerance, "horizontal"))

    summary = {
        "arch_v": len(arch_v),
        "arch_h": len(arch_h),
        "struc_v": len(struc_v),
        "struc_h": len(struc_h),
        "mismatches": len(mismatches),
    }

    return {
        "arch_v": arch_v,
        "arch_h": arch_h,
        "struc_v": struc_v,
        "struc_h": struc_h,
        "mismatches": mismatches,
        "summary": summary,
    }


# =========================================================
# SESSION STATE
# =========================================================
def init_sync_state():
    defaults = {
        "sync_initialized": False,
        "audit_stage": "idle",   # idle | reviewing | done
        "arch_doc": None,
        "struc_doc": None,
        "arch_name": "",
        "struc_name": "",
        "arch_grid_layer": "",
        "struc_grid_layer": "",
        "analysis": {},
        "current_index": 0,
        "decision_log": [],
        "alignment_done": False,
        "alignment_dx": 0.0,
        "alignment_dy": 0.0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_sync_state():
    keys = [
        "sync_initialized",
        "audit_stage",
        "arch_doc",
        "struc_doc",
        "arch_name",
        "struc_name",
        "arch_grid_layer",
        "struc_grid_layer",
        "analysis",
        "current_index",
        "decision_log",
        "alignment_done",
        "alignment_dx",
        "alignment_dy",
    ]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]


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
            all_layers = get_layer_names(doc)
            st.success(f"Successfully analyzed {uploaded_file.name}")

            layers_to_keep = st.multiselect(
                "Which layers should remain?",
                options=all_layers,
                default=all_layers
            )

            if st.button("🔥 Purge and Prepare Download"):
                msp = doc.modelspace()
                layers_to_delete = set(all_layers) - set(layers_to_keep)

                deleted_count = 0
                for entity in list(msp):
                    try:
                        if entity.dxf.layer in layers_to_delete:
                            msp.delete_entity(entity)
                            deleted_count += 1
                    except Exception:
                        continue

                out_buffer = io.StringIO()
                doc.write(out_buffer)

                st.success(f"Deleted {deleted_count} modelspace entities.")
                st.download_button(
                    "📥 Download Cleaned DXF",
                    out_buffer.getvalue().encode("utf-8"),
                    f"CLEANED_{uploaded_file.name}",
                    mime="application/dxf"
                )
        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            safe_remove_file(tmp_path)


# =========================================================
# TOOL 2: GRID & BEAM SYNC WITH SHIFT ENGINE
# =========================================================
elif tool_choice == "2. Grid & Beam Sync":
    st.subheader("Tool 2: Grid & Beam Sync — Commit Mode")
    st.caption("Architectural DXF is the source of truth. 'Force Arch Value' physically transforms the Structural DXF.")

    init_sync_state()

    tolerance = st.slider("Tolerance (mm)", 0.0, 50.0, 10.0, 0.5)
    min_grid_length = st.slider("Minimum Grid Line Length (mm)", 100.0, 20000.0, 1000.0, 100.0)

    col_a, col_b = st.columns(2)
    with col_a:
        arch_file = st.file_uploader("Reference (Architectural DXF)", type=["dxf"], key="arch")
    with col_b:
        struc_file = st.file_uploader("Target (Structural DXF)", type=["dxf"], key="struc")

    if arch_file and struc_file:
        # Load docs only once per upload change cycle
        if not st.session_state.sync_initialized:
            arch_tmp = None
            struc_tmp = None

            try:
                arch_tmp = save_uploaded_to_temp(arch_file)
                struc_tmp = save_uploaded_to_temp(struc_file)

                arch_doc = ezdxf.readfile(arch_tmp)
                struc_doc = ezdxf.readfile(struc_tmp)

                st.session_state.arch_doc = arch_doc
                st.session_state.struc_doc = struc_doc
                st.session_state.arch_name = arch_file.name
                st.session_state.struc_name = struc_file.name
                st.session_state.sync_initialized = True

            except Exception as e:
                st.error(f"Failed to load files: {e}")

            finally:
                safe_remove_file(arch_tmp)
                safe_remove_file(struc_tmp)

        if st.session_state.arch_doc is not None and st.session_state.struc_doc is not None:
            arch_layers = get_layer_names(st.session_state.arch_doc)
            struc_layers = get_layer_names(st.session_state.struc_doc)

            col_l1, col_l2 = st.columns(2)
            with col_l1:
                arch_grid_layer = st.selectbox(
                    "Architectural Grid Layer",
                    options=arch_layers,
                    index=0 if arch_layers else None
                )
            with col_l2:
                struc_grid_layer = st.selectbox(
                    "Structural Grid Layer",
                    options=struc_layers,
                    index=0 if struc_layers else None
                )

            st.session_state.arch_grid_layer = arch_grid_layer
            st.session_state.struc_grid_layer = struc_grid_layer

            cbtn1, cbtn2, cbtn3 = st.columns(3)

            with cbtn1:
                if st.button("🧭 Align to Master Origin"):
                    result = align_structural_to_arch(
                        st.session_state.arch_doc,
                        st.session_state.struc_doc,
                        arch_grid_layer,
                        struc_grid_layer,
                        min_grid_length
                    )

                    if result[0] is None:
                        st.error("Could not determine master origin from selected grid layers.")
                    else:
                        dx, dy, arch_origin, struc_origin, updated_doc = result
                        st.session_state.struc_doc = updated_doc
                        st.session_state.alignment_done = True
                        st.session_state.alignment_dx = dx
                        st.session_state.alignment_dy = dy
                        st.success(
                            f"Structural DXF aligned to Arch master origin. "
                            f"Shift applied: dx={round(dx,3)} mm, dy={round(dy,3)} mm"
                        )

            with cbtn2:
                if st.button("🚀 Run Audit"):
                    analysis = refresh_analysis_from_docs(
                        st.session_state.arch_doc,
                        st.session_state.struc_doc,
                        arch_grid_layer,
                        struc_grid_layer,
                        min_grid_length,
                        tolerance
                    )
                    st.session_state.analysis = analysis
                    st.session_state.current_index = 0
                    st.session_state.decision_log = []

                    if analysis["mismatches"]:
                        st.session_state.audit_stage = "reviewing"
                    else:
                        st.session_state.audit_stage = "done"

                    st.rerun()

            with cbtn3:
                if st.button("🧹 Reset Tool 2"):
                    reset_sync_state()
                    st.rerun()

            if st.session_state.alignment_done:
                st.info(
                    f"Master Origin alignment active: "
                    f"dx={round(st.session_state.alignment_dx,3)} mm, "
                    f"dy={round(st.session_state.alignment_dy,3)} mm"
                )

            analysis = st.session_state.analysis
            if analysis:
                s = analysis["summary"]
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Arch V", s["arch_v"])
                m2.metric("Arch H", s["arch_h"])
                m3.metric("Struc V", s["struc_v"])
                m4.metric("Struc H", s["struc_h"])
                m5.metric("Mismatches", s["mismatches"])

                tabs = st.tabs(["Arch Grids", "Struc Grids", "Audit", "Commit Log", "Download"])

                with tabs[0]:
                    st.write("### Architectural Vertical Grids")
                    st.dataframe(analysis["arch_v"], use_container_width=True)
                    st.write("### Architectural Horizontal Grids")
                    st.dataframe(analysis["arch_h"], use_container_width=True)

                with tabs[1]:
                    st.write("### Structural Vertical Grids")
                    st.dataframe(analysis["struc_v"], use_container_width=True)
                    st.write("### Structural Horizontal Grids")
                    st.dataframe(analysis["struc_h"], use_container_width=True)

                with tabs[2]:
                    mismatches = analysis["mismatches"]

                    if st.session_state.audit_stage == "reviewing" and mismatches:
                        idx = st.session_state.current_index
                        total = len(mismatches)

                        if idx < total:
                            item = mismatches[idx]
                            st.progress((idx + 1) / total)

                            st.warning(
                                f"{item['span_label']}: "
                                f"{item['arch_spacing']}mm (Arch) vs "
                                f"{item['struc_spacing']}mm (Struc) | "
                                f"Δ = {item['difference']}mm"
                            )

                            st.write("### Commit Options")
                            choice = st.radio(
                                "Action",
                                [
                                    f"Force Arch Value ({item['arch_spacing']}mm)",
                                    "Ignore and Pass",
                                    "Reject"
                                ],
                                key=f"decision_{idx}"
                            )

                            col_d1, col_d2 = st.columns(2)

                            with col_d1:
                                if st.button("Apply Decision"):
                                    if choice.startswith("Force Arch Value"):
                                        axis = "x" if item["orientation"] == "vertical" else "y"
                                        threshold_k = item["struc_coord_target"]
                                        delta = item["arch_coord_target"] - item["struc_coord_target"]

                                        shifted_count = stretch_structural_doc(
                                            st.session_state.struc_doc,
                                            axis=axis,
                                            threshold_k=threshold_k,
                                            delta=delta
                                        )

                                        # Re-run analysis on modified in-memory doc
                                        new_analysis = refresh_analysis_from_docs(
                                            st.session_state.arch_doc,
                                            st.session_state.struc_doc,
                                            arch_grid_layer,
                                            struc_grid_layer,
                                            min_grid_length,
                                            tolerance
                                        )
                                        st.session_state.analysis = new_analysis

                                        st.session_state.decision_log.append({
                                            "item_no": idx + 1,
                                            "action": "Force Arch Value",
                                            "orientation": item["orientation"],
                                            "axis": axis,
                                            "threshold_k": threshold_k,
                                            "delta_applied": round(delta, 3),
                                            "entities_shifted": shifted_count,
                                            "span_label": item["span_label"],
                                        })

                                    elif choice == "Ignore and Pass":
                                        st.session_state.decision_log.append({
                                            "item_no": idx + 1,
                                            "action": "Ignore and Pass",
                                            "orientation": item["orientation"],
                                            "axis": None,
                                            "threshold_k": None,
                                            "delta_applied": 0.0,
                                            "entities_shifted": 0,
                                            "span_label": item["span_label"],
                                        })

                                    else:
                                        st.session_state.decision_log.append({
                                            "item_no": idx + 1,
                                            "action": "Reject",
                                            "orientation": item["orientation"],
                                            "axis": None,
                                            "threshold_k": None,
                                            "delta_applied": 0.0,
                                            "entities_shifted": 0,
                                            "span_label": item["span_label"],
                                        })

                                    # Move next
                                    st.session_state.current_index += 1

                                    # If modified analysis has fewer mismatches, re-anchor index safely
                                    if st.session_state.current_index >= len(st.session_state.analysis["mismatches"]):
                                        if st.session_state.analysis["mismatches"]:
                                            st.session_state.current_index = min(
                                                st.session_state.current_index,
                                                len(st.session_state.analysis["mismatches"]) - 1
                                            )
                                        else:
                                            st.session_state.audit_stage = "done"

                                    if not st.session_state.analysis["mismatches"]:
                                        st.session_state.audit_stage = "done"

                                    st.rerun()

                            with col_d2:
                                if st.button("Finish Review"):
                                    st.session_state.audit_stage = "done"
                                    st.rerun()

                    elif st.session_state.audit_stage == "done":
                        if not mismatches:
                            st.success("✅ No remaining mismatches within tolerance.")
                        else:
                            st.info(f"Review paused. Remaining mismatches: {len(mismatches)}")

                with tabs[3]:
                    st.write("### Commit Log")
                    if st.session_state.decision_log:
                        st.dataframe(st.session_state.decision_log, use_container_width=True)
                    else:
                        st.info("No committed decisions yet.")

                with tabs[4]:
                    st.write("### Download Modified Structural DXF")
                    if st.session_state.struc_doc is not None:
                        out_buffer = io.StringIO()
                        st.session_state.struc_doc.write(out_buffer)

                        st.download_button(
                            "📥 Download Modified Structural DXF",
                            data=out_buffer.getvalue().encode("utf-8"),
                            file_name=f"SHIFTED_{st.session_state.struc_name}",
                            mime="application/dxf"
                        )
                    else:
                        st.info("No modified structural document available yet.")
    else:
        st.info("Please upload both the Architectural DXF and Structural DXF.")
