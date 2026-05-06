import ezdxf
import datetime
import math


# =========================================================
# SETTINGS
# =========================================================

INPUT_DXF = "plan.dxf"

# Detect these wall thicknesses.
# You can use [225] only if you want external walls first.
WALL_THICKNESSES = [225, 150]

# Allowed drafting error when checking if two wall face lines are 225/150 apart.
WALL_THICKNESS_TOLERANCE = 10

# A line is considered horizontal/vertical if the coordinate difference is below this.
ORTHO_TOLERANCE = 1.0

# Minimum overlap between two wall face lines before we accept them as a wall.
# Increase this if it detects tiny false walls.
MIN_WALL_FACE_OVERLAP = 100

# This is the important one:
# If two centerline fragments are collinear and the gap between them is below this,
# they will be joined. Use this to bridge doors/windows/openings.
#
# Typical:
# - Door gap: 900-1200
# - Large opening/window: 1500-2400
BRIDGE_GAP_TOLERANCE = 1200

# If a horizontal and vertical centerline almost meet, extend them to intersect.
# This helps create closed rectangles/slab panels.
INTERSECTION_EXTEND_TOLERANCE = 300

# If centerlines are almost on the same X or Y axis, treat them as same axis.
AXIS_MERGE_TOLERANCE = 20

# Minimum slab panel size to draw.
MIN_PANEL_WIDTH = 500
MIN_PANEL_HEIGHT = 500

# Draw debug/raw layers.
DRAW_RAW_CENTERLINES = True
DRAW_HEALED_CENTERLINES = True
DRAW_SLAB_PANELS = True
DRAW_JUNCTION_NODES = True


# =========================================================
# BASIC HELPERS
# =========================================================

def safe_layer(doc, name, color=7):
    """Create a layer if it does not already exist."""
    try:
        if name not in doc.layers:
            doc.layers.new(name=name, dxfattribs={"color": color})
    except Exception:
        pass


def get_xy(point):
    return float(point.x), float(point.y)


def is_horizontal_line(entity):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return abs(float(p1.y) - float(p2.y)) <= ORTHO_TOLERANCE


def is_vertical_line(entity):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return abs(float(p1.x) - float(p2.x)) <= ORTHO_TOLERANCE


def line_x_range(entity):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return sorted([float(p1.x), float(p2.x)])


def line_y_range(entity):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return sorted([float(p1.y), float(p2.y)])


def overlap_range(a1, a2, b1, b2):
    start = max(min(a1, a2), min(b1, b2))
    end = min(max(a1, a2), max(b1, b2))
    return start, end


def overlap_length(a1, a2, b1, b2):
    start, end = overlap_range(a1, a2, b1, b2)
    return max(0, end - start)


def make_segment(orientation, const_coord, start_coord, end_coord, thickness):
    """
    Segment format:
    - H: const_coord = y, start/end = x1/x2
    - V: const_coord = x, start/end = y1/y2
    """
    a = float(min(start_coord, end_coord))
    b = float(max(start_coord, end_coord))

    return {
        "orientation": orientation,
        "c": float(const_coord),
        "a": a,
        "b": b,
        "thickness": thickness,
    }


def segment_length(seg):
    return abs(seg["b"] - seg["a"])


def copy_segment(seg):
    return {
        "orientation": seg["orientation"],
        "c": float(seg["c"]),
        "a": float(seg["a"]),
        "b": float(seg["b"]),
        "thickness": seg["thickness"],
    }


# =========================================================
# STEP 1: EXTRACT WALL FACE LINES
# =========================================================

def extract_axis_aligned_lines(msp):
    horizontal_lines = []
    vertical_lines = []

    for entity in msp:
        try:
            if entity.dxftype() != "LINE":
                continue

            if is_horizontal_line(entity):
                horizontal_lines.append(entity)

            elif is_vertical_line(entity):
                vertical_lines.append(entity)

        except Exception:
            continue

    return horizontal_lines, vertical_lines


# =========================================================
# STEP 2: DETECT RAW CENTERLINES FROM WALL FACE PAIRS
# =========================================================

def detect_raw_centerlines(horizontal_lines, vertical_lines, wall_thicknesses):
    raw_segments = []

    print("Scanning for horizontal wall patterns...")

    for thickness in wall_thicknesses:
        for i, l1 in enumerate(horizontal_lines):
            for l2 in horizontal_lines[i + 1:]:
                try:
                    y1 = float(l1.dxf.start.y)
                    y2 = float(l2.dxf.start.y)

                    y_dist = abs(y1 - y2)

                    if abs(y_dist - thickness) > WALL_THICKNESS_TOLERANCE:
                        continue

                    l1_x1, l1_x2 = line_x_range(l1)
                    l2_x1, l2_x2 = line_x_range(l2)

                    start_x, end_x = overlap_range(l1_x1, l1_x2, l2_x1, l2_x2)
                    overlap = end_x - start_x

                    if overlap < MIN_WALL_FACE_OVERLAP:
                        continue

                    center_y = (y1 + y2) / 2.0

                    raw_segments.append(
                        make_segment(
                            orientation="H",
                            const_coord=center_y,
                            start_coord=start_x,
                            end_coord=end_x,
                            thickness=thickness,
                        )
                    )

                except Exception:
                    continue

    print("Scanning for vertical wall patterns...")

    for thickness in wall_thicknesses:
        for i, l1 in enumerate(vertical_lines):
            for l2 in vertical_lines[i + 1:]:
                try:
                    x1 = float(l1.dxf.start.x)
                    x2 = float(l2.dxf.start.x)

                    x_dist = abs(x1 - x2)

                    if abs(x_dist - thickness) > WALL_THICKNESS_TOLERANCE:
                        continue

                    l1_y1, l1_y2 = line_y_range(l1)
                    l2_y1, l2_y2 = line_y_range(l2)

                    start_y, end_y = overlap_range(l1_y1, l1_y2, l2_y1, l2_y2)
                    overlap = end_y - start_y

                    if overlap < MIN_WALL_FACE_OVERLAP:
                        continue

                    center_x = (x1 + x2) / 2.0

                    raw_segments.append(
                        make_segment(
                            orientation="V",
                            const_coord=center_x,
                            start_coord=start_y,
                            end_coord=end_y,
                            thickness=thickness,
                        )
                    )

                except Exception:
                    continue

    return raw_segments


# =========================================================
# STEP 3: MERGE COLLINEAR BROKEN CENTERLINES
# =========================================================

def group_segments_by_axis(segments, axis_tol):
    """
    Groups segments that are:
    - same orientation
    - same thickness
    - nearly same X/Y centerline axis
    """
    groups = []

    sorted_segments = sorted(
        segments,
        key=lambda s: (
            s["orientation"],
            s["thickness"],
            s["c"],
            s["a"],
            s["b"],
        )
    )

    for seg in sorted_segments:
        placed = False

        for group in groups:
            g0 = group[0]

            if seg["orientation"] != g0["orientation"]:
                continue

            if seg["thickness"] != g0["thickness"]:
                continue

            group_c = sum(x["c"] for x in group) / len(group)

            if abs(seg["c"] - group_c) <= axis_tol:
                group.append(seg)
                placed = True
                break

        if not placed:
            groups.append([seg])

    return groups


def merge_collinear_segments(segments, bridge_gap):
    """
    Merge same-axis segments if they overlap or have a small gap.

    This is what bridges wall centerlines across doors/windows.
    """
    if not segments:
        return []

    groups = group_segments_by_axis(segments, AXIS_MERGE_TOLERANCE)
    merged = []

    for group in groups:
        if not group:
            continue

        orientation = group[0]["orientation"]
        thickness = group[0]["thickness"]
        avg_c = sum(s["c"] for s in group) / len(group)

        intervals = sorted([(s["a"], s["b"]) for s in group], key=lambda x: x[0])

        cur_a, cur_b = intervals[0]

        for a, b in intervals[1:]:
            # If the next piece overlaps or is close enough, merge it.
            if a <= cur_b + bridge_gap:
                cur_b = max(cur_b, b)
            else:
                merged.append(
                    make_segment(
                        orientation=orientation,
                        const_coord=avg_c,
                        start_coord=cur_a,
                        end_coord=cur_b,
                        thickness=thickness,
                    )
                )

                cur_a, cur_b = a, b

        merged.append(
            make_segment(
                orientation=orientation,
                const_coord=avg_c,
                start_coord=cur_a,
                end_coord=cur_b,
                thickness=thickness,
            )
        )

    return merged


# =========================================================
# STEP 4: EXTEND CENTERLINES TO NEARBY INTERSECTIONS
# =========================================================

def split_hv_segments(segments):
    h_segments = [s for s in segments if s["orientation"] == "H"]
    v_segments = [s for s in segments if s["orientation"] == "V"]
    return h_segments, v_segments


def maybe_extend_to_intersections(segments, extend_tol):
    """
    If a horizontal and vertical centerline almost meet, extend both to touch.

    This helps convert:
        ----     |
    into:
        ---------|
    """
    working = [copy_segment(s) for s in segments]

    # Iterate a few times because one extension can create another connection.
    for _ in range(3):
        h_segments, v_segments = split_hv_segments(working)

        for h in h_segments:
            hy = h["c"]

            for v in v_segments:
                vx = v["c"]

                # Intersection candidate is (vx, hy).
                # Check if vx is near/on the horizontal segment,
                # and hy is near/on the vertical segment.
                vx_near_h = (h["a"] - extend_tol) <= vx <= (h["b"] + extend_tol)
                hy_near_v = (v["a"] - extend_tol) <= hy <= (v["b"] + extend_tol)

                if not (vx_near_h and hy_near_v):
                    continue

                # Extend horizontal segment to include vertical x.
                if vx < h["a"]:
                    h["a"] = vx
                if vx > h["b"]:
                    h["b"] = vx

                # Extend vertical segment to include horizontal y.
                if hy < v["a"]:
                    v["a"] = hy
                if hy > v["b"]:
                    v["b"] = hy

        # After extension, merge again in case pieces now touch.
        working = merge_collinear_segments(working, bridge_gap=AXIS_MERGE_TOLERANCE)

    return working


# =========================================================
# STEP 5: DETECT RECTANGULAR SLAB PANELS
# =========================================================

def cluster_values(values, tol):
    if not values:
        return []

    values = sorted(values)
    clusters = []

    current = [values[0]]

    for v in values[1:]:
        avg = sum(current) / len(current)

        if abs(v - avg) <= tol:
            current.append(v)
        else:
            clusters.append(sum(current) / len(current))
            current = [v]

    clusters.append(sum(current) / len(current))
    return clusters


def horizontal_covers(h_segments, y, x1, x2, tol):
    for h in h_segments:
        if abs(h["c"] - y) > tol:
            continue

        if h["a"] <= x1 + tol and h["b"] >= x2 - tol:
            return True

    return False


def vertical_covers(v_segments, x, y1, y2, tol):
    for v in v_segments:
        if abs(v["c"] - x) > tol:
            continue

        if v["a"] <= y1 + tol and v["b"] >= y2 - tol:
            return True

    return False


def detect_rectangular_panels(segments):
    """
    Simple rectangular panel detector for orthogonal centerline networks.

    It checks adjacent X and Y centerline axes and confirms that all four
    rectangle sides exist.
    """
    h_segments, v_segments = split_hv_segments(segments)

    xs = cluster_values([v["c"] for v in v_segments], AXIS_MERGE_TOLERANCE)
    ys = cluster_values([h["c"] for h in h_segments], AXIS_MERGE_TOLERANCE)

    panels = []

    if len(xs) < 2 or len(ys) < 2:
        return panels

    for i in range(len(xs) - 1):
        x1 = xs[i]
        x2 = xs[i + 1]

        if abs(x2 - x1) < MIN_PANEL_WIDTH:
            continue

        for j in range(len(ys) - 1):
            y1 = ys[j]
            y2 = ys[j + 1]

            if abs(y2 - y1) < MIN_PANEL_HEIGHT:
                continue

            bottom_exists = horizontal_covers(h_segments, y1, x1, x2, AXIS_MERGE_TOLERANCE)
            top_exists = horizontal_covers(h_segments, y2, x1, x2, AXIS_MERGE_TOLERANCE)
            left_exists = vertical_covers(v_segments, x1, y1, y2, AXIS_MERGE_TOLERANCE)
            right_exists = vertical_covers(v_segments, x2, y1, y2, AXIS_MERGE_TOLERANCE)

            if bottom_exists and top_exists and left_exists and right_exists:
                panels.append({
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "width": abs(x2 - x1),
                    "height": abs(y2 - y1),
                    "area": abs(x2 - x1) * abs(y2 - y1),
                })

    return panels


# =========================================================
# STEP 6: DRAW OUTPUT DXF
# =========================================================

def draw_segment(msp, seg, layer):
    if seg["orientation"] == "H":
        y = seg["c"]
        msp.add_line(
            (seg["a"], y),
            (seg["b"], y),
            dxfattribs={"layer": layer},
        )

    elif seg["orientation"] == "V":
        x = seg["c"]
        msp.add_line(
            (x, seg["a"]),
            (x, seg["b"]),
            dxfattribs={"layer": layer},
        )


def draw_centerlines(msp, segments, layer_prefix):
    for seg in segments:
        thickness = seg["thickness"]
        layer = f"{layer_prefix}_{thickness}"
        draw_segment(msp, seg, layer)


def draw_panels(msp, panels, layer="ILS_SLAB_PANEL"):
    for p in panels:
        x1 = p["x1"]
        y1 = p["y1"]
        x2 = p["x2"]
        y2 = p["y2"]

        points = [
            (x1, y1),
            (x2, y1),
            (x2, y2),
            (x1, y2),
            (x1, y1),
        ]

        msp.add_lwpolyline(
            points,
            close=True,
            dxfattribs={"layer": layer},
        )


def collect_junction_nodes(segments):
    h_segments, v_segments = split_hv_segments(segments)
    nodes = []

    for h in h_segments:
        hy = h["c"]

        for v in v_segments:
            vx = v["c"]

            if h["a"] - AXIS_MERGE_TOLERANCE <= vx <= h["b"] + AXIS_MERGE_TOLERANCE:
                if v["a"] - AXIS_MERGE_TOLERANCE <= hy <= v["b"] + AXIS_MERGE_TOLERANCE:
                    nodes.append((vx, hy))

    # Deduplicate nodes.
    deduped = []
    for x, y in nodes:
        exists = False

        for dx, dy in deduped:
            if math.dist((x, y), (dx, dy)) <= AXIS_MERGE_TOLERANCE:
                exists = True
                break

        if not exists:
            deduped.append((x, y))

    return deduped


def draw_nodes(msp, nodes, radius=50, layer="ILS_JUNCTION_NODE"):
    for x, y in nodes:
        msp.add_circle(
            center=(x, y),
            radius=radius,
            dxfattribs={"layer": layer},
        )


# =========================================================
# MAIN
# =========================================================

def main():
    try:
        doc = ezdxf.readfile(INPUT_DXF)
        msp = doc.modelspace()
    except Exception as e:
        print(f"Agent: Could not read '{INPUT_DXF}'. Error: {e}")
        return

    print("Agent: DXF loaded.")
    print("Agent: Extracting horizontal and vertical wall face lines...")

    horizontal_lines, vertical_lines = extract_axis_aligned_lines(msp)

    print(f"Agent: Horizontal LINE entities found: {len(horizontal_lines)}")
    print(f"Agent: Vertical LINE entities found: {len(vertical_lines)}")

    print("Agent: Detecting raw wall centerlines from wall face pairs...")

    raw_segments = detect_raw_centerlines(
        horizontal_lines=horizontal_lines,
        vertical_lines=vertical_lines,
        wall_thicknesses=WALL_THICKNESSES,
    )

    print(f"Agent: Raw centerline fragments found: {len(raw_segments)}")

    print("Agent: Merging collinear centerline fragments and bridging openings...")

    merged_segments = merge_collinear_segments(
        raw_segments,
        bridge_gap=BRIDGE_GAP_TOLERANCE,
    )

    print(f"Agent: Centerlines after gap bridging: {len(merged_segments)}")

    print("Agent: Extending centerlines to nearby intersections...")

    healed_segments = maybe_extend_to_intersections(
        merged_segments,
        extend_tol=INTERSECTION_EXTEND_TOLERANCE,
    )

    healed_segments = merge_collinear_segments(
        healed_segments,
        bridge_gap=AXIS_MERGE_TOLERANCE,
    )

    print(f"Agent: Healed centerlines after intersection extension: {len(healed_segments)}")

    print("Agent: Detecting rectangular slab panels...")

    panels = detect_rectangular_panels(healed_segments)

    print(f"Agent: Rectangular slab panels detected: {len(panels)}")

    nodes = collect_junction_nodes(healed_segments)

    print(f"Agent: Junction nodes detected: {len(nodes)}")

    # Create output DXF.
    new_doc = ezdxf.new()
    new_msp = new_doc.modelspace()

    # Layers.
    safe_layer(new_doc, "ILS_RAW_CENTERLINE_225", color=8)
    safe_layer(new_doc, "ILS_RAW_CENTERLINE_150", color=9)
    safe_layer(new_doc, "ILS_HEALED_CENTERLINE_225", color=1)
    safe_layer(new_doc, "ILS_HEALED_CENTERLINE_150", color=3)
    safe_layer(new_doc, "ILS_SLAB_PANEL", color=5)
    safe_layer(new_doc, "ILS_JUNCTION_NODE", color=2)

    if DRAW_RAW_CENTERLINES:
        draw_centerlines(
            new_msp,
            raw_segments,
            layer_prefix="ILS_RAW_CENTERLINE",
        )

    if DRAW_HEALED_CENTERLINES:
        draw_centerlines(
            new_msp,
            healed_segments,
            layer_prefix="ILS_HEALED_CENTERLINE",
        )

    if DRAW_SLAB_PANELS:
        draw_panels(
            new_msp,
            panels,
            layer="ILS_SLAB_PANEL",
        )

    if DRAW_JUNCTION_NODES:
        draw_nodes(
            new_msp,
            nodes,
            radius=50,
            layer="ILS_JUNCTION_NODE",
        )

    timestamp = datetime.datetime.now().strftime("%H%M%S")
    output_name = f"structural_centerline_result_{timestamp}.dxf"

    new_doc.saveas(output_name)

    print("")
    print("--- COMPLETE ---")
    print(f"Raw centerline fragments: {len(raw_segments)}")
    print(f"Healed centerlines: {len(healed_segments)}")
    print(f"Slab panels detected: {len(panels)}")
    print(f"Junction nodes detected: {len(nodes)}")
    print(f"Saved to: {output_name}")


if __name__ == "__main__":
    main()
