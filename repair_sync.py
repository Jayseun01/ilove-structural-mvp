from pathlib import Path

path = Path("sync.py")
text = path.read_text(encoding="utf-8")

start_marker = "# =========================================================\n# ENDPOINT RECOVERY LOGIC\n# ========================================================="
end_marker = "# =========================================================\n# SESSION STATE\n# ========================================================="

start = text.index(start_marker)
end = text.index(end_marker)

endpoint_recovery_section = r'''# =========================================================
# ENDPOINT RECOVERY LOGIC
# =========================================================

def source_label_set(source_groups):
    return {
        clean_text(g.get("label", ""))
        for g in source_groups
        if clean_text(g.get("label", ""))
    }


def label_matches_family(label, family_name):
    label = clean_text(label)

    if family_name == "numeric":
        return is_numeric_label(label)

    if family_name == "alphabetic":
        return is_alpha_label(label)

    return False


def axis_distance_for_marker(marker, orientation, coord):
    x, y = marker["circle_center"]

    if orientation == "vertical":
        return abs(float(x) - float(coord))

    if orientation == "horizontal":
        return abs(float(y) - float(coord))

    return 999999999.0


def median_value(values):
    values = sorted([float(v) for v in values if v is not None])

    if not values:
        return None

    n = len(values)

    if n % 2 == 1:
        return values[n // 2]

    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def source_grid_bubble_radius_range(source_numeric, source_alpha, min_ratio=0.55, max_ratio=1.80):
    radii = []

    for group in source_numeric + source_alpha:
        for marker in group.get("markers", []):
            r = marker.get("circle_radius")

            if r and r > 0:
                radii.append(float(r))

    med = median_value(radii)

    if med is None:
        return None, None, None

    return med * min_ratio, med * max_ratio, med


def marker_radius_ok(marker, min_radius, max_radius):
    if min_radius is None or max_radius is None:
        return True

    r = marker.get("circle_radius")

    if r is None:
        return True

    return min_radius <= float(r) <= max_radius


def build_grid_frame_from_target_groups(target_numeric_groups, target_alpha_groups, fallback_bbox):
    vertical_coords = []
    horizontal_coords = []

    for g in target_numeric_groups + target_alpha_groups:
        if g.get("orientation") == "vertical":
            vertical_coords.append(float(g["coord"]))

        elif g.get("orientation") == "horizontal":
            horizontal_coords.append(float(g["coord"]))

    if len(vertical_coords) >= 2 and len(horizontal_coords) >= 2:
        return {
            "min_x": min(vertical_coords),
            "max_x": max(vertical_coords),
            "min_y": min(horizontal_coords),
            "max_y": max(horizontal_coords),
            "width": max(vertical_coords) - min(vertical_coords),
            "height": max(horizontal_coords) - min(horizontal_coords),
            "source": "target_axis_groups",
        }

    frame = dict(fallback_bbox)
    frame["source"] = "region_bbox_fallback"
    return frame


def endpoint_points_for_axis_group_using_frame(axis_group, frame):
    coord = float(axis_group["coord"])

    if axis_group["orientation"] == "vertical":
        return [
            (coord, frame["min_y"]),
            (coord, frame["max_y"]),
        ]

    return [
        (frame["min_x"], coord),
        (frame["max_x"], coord),
    ]


def source_numeric_range(source_numeric, extra=5):
    vals = []

    for g in source_numeric:
        v = numeric_label_value(g.get("label", ""))

        if v is not None:
            vals.append(v)

    if not vals:
        return 1, 999

    return max(1, min(vals) - extra), max(vals) + extra


def is_zero_padded_detail_number(label):
    label = clean_text(label)
    return bool(re.fullmatch(r"0\d{1,3}", label))


def plausible_recovery_label(label, family, source_numeric, source_alpha, numeric_extra=5):
    label = clean_text(label)

    if is_zero_padded_detail_number(label):
        return False

    if family == "numeric":
        v = numeric_label_value(label)

        if v is None:
            return False

        lo, hi = source_numeric_range(source_numeric, extra=numeric_extra)

        return lo <= v <= hi

    if family == "alphabetic":
        if not is_alpha_label(label):
            return False

        return bool(re.fullmatch(r"[A-Z]{1,2}'?", label))

    return False


def strict_slab_recovery_label_ok(
    label,
    family_name,
    source_numeric,
    source_alpha,
    numeric_extra=5,
    strict_source_labels=True,
    allowed_old_labels=None,
):
    label = clean_text(label)

    if is_zero_padded_detail_number(label):
        return False

    allowed_old_labels = {
        clean_text(x)
        for x in (allowed_old_labels or [])
        if clean_text(x)
    }

    # Strict mode uses detected old target axis labels only.
    # This allows old numeric labels like 10, 35, or 72 to become
    # alphabetic source labels if those old labels were detected on that axis.
    if strict_source_labels:
        if allowed_old_labels:
            return label in allowed_old_labels

        if not label_matches_family(label, family_name):
            return False

        if family_name == "numeric":
            return label in source_label_set(source_numeric)

        if family_name == "alphabetic":
            return label in source_label_set(source_alpha)

        return False

    if not label_matches_family(label, family_name):
        return False

    return plausible_recovery_label(
        label,
        family_name,
        source_numeric,
        source_alpha,
        numeric_extra=numeric_extra,
    )


def axis_group_recovery_quality(group):
    markers = group.get("markers", []) or []

    marker_count = len(markers)
    avg_conf = float(group.get("avg_confidence", 0) or 0)

    attached_count = len([
        m for m in markers
        if m.get("detection_mode") == "attached_gridline"
    ])

    closest_count = len([
        m for m in markers
        if m.get("detection_mode") == "closest_gridline"
    ])

    virtual_count = len([
        m for m in markers
        if m.get("detection_mode") == "same_label_virtual_axis"
    ])

    writable_count = len([
        m for m in markers
        if m.get("writable")
    ])

    label_count = int(group.get("label_count", 1) or 1)

    return (
        marker_count * 1000.0
        + attached_count * 350.0
        + closest_count * 175.0
        + writable_count * 75.0
        + avg_conf * 5.0
        - virtual_count * 500.0
        - max(0, label_count - 1) * 50.0
    )


def axis_group_label_summary(group):
    labels = []

    main = clean_text(group.get("label", ""))

    if main:
        labels.append(main)

    for lab in group.get("mixed_labels", []):
        lab = clean_text(lab)

        if lab and lab not in labels:
            labels.append(lab)

    for lab in (group.get("label_counts", {}) or {}).keys():
        lab = clean_text(lab)

        if lab and lab not in labels:
            labels.append(lab)

    return ", ".join(labels)


def select_axis_groups_for_recovery(
    source_groups,
    target_groups,
    family_name,
    max_extra_groups=2,
):
    expected = len(source_groups)
    found = len(target_groups)

    blockers = []
    warnings = []

    if expected == 0:
        blockers.append(f"No source {family_name} axes were detected.")
        return [], blockers, warnings

    if found == expected:
        return target_groups, blockers, warnings

    if found < expected:
        blockers.append(
            f"Recovery {family_name} axis-group count mismatch: found {found}, expected {expected}. "
            f"Missing target axis groups cannot be safely inferred automatically."
        )
        return [], blockers, warnings

    extra = found - expected

    if extra > max_extra_groups:
        blockers.append(
            f"Recovery {family_name} axis-group count mismatch: found {found}, expected {expected}. "
            f"There are {extra} extra detected groups, which is more than the safe auto-trim limit "
            f"of {max_extra_groups}."
        )
        return [], blockers, warnings

    scored = []

    for idx, group in enumerate(target_groups):
        scored.append({
            "idx": idx,
            "group": group,
            "score": axis_group_recovery_quality(group),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    chosen_items = scored[:expected]
    dropped_items = scored[expected:]

    chosen_items.sort(key=lambda x: x["idx"])
    dropped_items.sort(key=lambda x: x["idx"])

    chosen_groups = [x["group"] for x in chosen_items]

    dropped_desc = []

    for item in dropped_items:
        g = item["group"]
        dropped_desc.append(
            f"coord={g.get('coord')}, labels=[{axis_group_label_summary(g)}], "
            f"markers={g.get('marker_count')}, score={round(item['score'], 1)}"
        )

    warnings.append(
        f"Recovery {family_name}: found {found} target groups but expected {expected}. "
        f"Auto-trimmed {extra} lower-confidence extra group(s): "
        + " | ".join(dropped_desc)
    )

    return chosen_groups, blockers, warnings


def marker_endpoint_distance(marker, endpoints):
    p = marker["circle_center"]

    distances = [
        euclidean(p, ep)
        for ep in endpoints
    ]

    best = min(distances)

    return best, distances.index(best)


def build_endpoint_recovery_plan(
    region,
    source_numeric,
    source_alpha,
    numeric_orientation,
    alpha_orientation,
    numeric_order,
    alpha_order,
    all_lines,
    endpoint_radius,
    allow_block_text_write,
    numeric_extra=5,
    axis_snap_tolerance=250.0,
    strict_source_labels=True,
    allow_virtual_axis_recovery=False,
    write_mode=None,
):
    blockers = []
    warnings = []
    plan_rows = []

    candidate_markers = region.get("all_markers", region.get("markers", []))

    raw_target_numeric_groups, raw_target_alpha_groups = get_family_groups(
        region,
        numeric_orientation,
        alpha_orientation,
        numeric_order,
        alpha_order,
    )

    target_numeric_groups, num_blockers, num_warnings = select_axis_groups_for_recovery(
        source_numeric,
        raw_target_numeric_groups,
        "numeric",
        max_extra_groups=2,
    )

    target_alpha_groups, alpha_blockers, alpha_warnings = select_axis_groups_for_recovery(
        source_alpha,
        raw_target_alpha_groups,
        "alphabetic",
        max_extra_groups=2,
    )

    blockers.extend(num_blockers)
    blockers.extend(alpha_blockers)
    warnings.extend(num_warnings)
    warnings.extend(alpha_warnings)

    if blockers:
        return False, blockers, warnings, plan_rows

    frame = build_grid_frame_from_target_groups(
        target_numeric_groups,
        target_alpha_groups,
        region["bbox"],
    )

    min_radius, max_radius, source_median_radius = source_grid_bubble_radius_range(
        source_numeric,
        source_alpha,
    )

    families = [
        ("numeric", source_numeric, target_numeric_groups),
        ("alphabetic", source_alpha, target_alpha_groups),
    ]

    seen_handles = set()

    for family_name, source_groups, target_groups in families:
        for pos, (source_group, target_group) in enumerate(zip(source_groups, target_groups), start=1):
            new_label = clean_text(source_group.get("label", ""))

            orientation = target_group["orientation"]
            coord = float(target_group["coord"])

            allowed_old_labels = set()
            group_labels = []

            main_old_label = clean_text(target_group.get("label", ""))

            if main_old_label:
                group_labels.append(main_old_label)

            for lab in target_group.get("mixed_labels", []):
                lab = clean_text(lab)

                if lab:
                    group_labels.append(lab)

            for lab in (target_group.get("label_counts", {}) or {}).keys():
                lab = clean_text(lab)

                if lab:
                    group_labels.append(lab)

            for lab in group_labels:
                lab = clean_text(lab)

                if not lab:
                    continue

                if is_zero_padded_detail_number(lab):
                    continue

                if not probable_grid_label(lab):
                    continue

                allowed_old_labels.add(lab)

            endpoints = endpoint_points_for_axis_group_using_frame(target_group, frame)

            endpoint_candidates = {
                0: [],
                1: [],
            }

            for marker in candidate_markers:
                old_label = clean_text(marker.get("label", ""))

                if not strict_slab_recovery_label_ok(
                    old_label,
                    family_name,
                    source_numeric,
                    source_alpha,
                    numeric_extra=numeric_extra,
                    strict_source_labels=strict_source_labels,
                    allowed_old_labels=allowed_old_labels,
                ):
                    continue

                if marker.get("orientation") != orientation:
                    continue

                detection_mode = marker.get("detection_mode", "")

                if not allow_virtual_axis_recovery and detection_mode == "same_label_virtual_axis":
                    continue

                profile = marker_write_profile(
                    marker,
                    write_mode=write_mode,
                    allow_block_text_write=allow_block_text_write,
                )

                if not profile.get("writable"):
                    continue

                if not marker_radius_ok(marker, min_radius, max_radius):
                    continue

                axis_distance = axis_distance_for_marker(marker, orientation, coord)

                if axis_distance > axis_snap_tolerance:
                    continue

                endpoint_distance, endpoint_index = marker_endpoint_distance(marker, endpoints)

                if endpoint_distance > endpoint_radius:
                    continue

                confidence = float(marker.get("confidence", 0) or 0)

                if detection_mode == "attached_gridline":
                    mode_penalty = 0.0
                elif detection_mode == "closest_gridline":
                    mode_penalty = 250.0
                elif detection_mode == "same_label_virtual_axis":
                    mode_penalty = 1000.0
                else:
                    mode_penalty = 500.0

                score = (
                    endpoint_distance
                    + axis_distance * 5.0
                    + mode_penalty
                    - confidence * 2.0
                )

                endpoint_candidates[endpoint_index].append({
                    "score": score,
                    "endpoint_distance": endpoint_distance,
                    "axis_distance": axis_distance,
                    "marker": marker,
                    "detection_mode": detection_mode,
                    "confidence": confidence,
                    "write_profile": profile,
                })

            selected = []

            for endpoint_index in [0, 1]:
                cands = endpoint_candidates[endpoint_index]

                if not cands:
                    warnings.append(
                        f"No safe endpoint candidate found for {family_name} axis position {pos}, "
                        f"source label {new_label}, endpoint {'A' if endpoint_index == 0 else 'B'}."
                    )
                    continue

                cands.sort(key=lambda x: x["score"])
                best = cands[0]

                if len(cands) > 1:
                    second = cands[1]

                    if second["score"] <= best["score"] + 300.0:
                        warnings.append(
                            f"Ambiguous candidates for {family_name} axis position {pos}, "
                            f"source label {new_label}, endpoint {'A' if endpoint_index == 0 else 'B'}. "
                            f"Best score={round(best['score'], 1)}, second={round(second['score'], 1)}."
                        )

                selected.append((endpoint_index, best))

            for endpoint_index, best in selected:
                marker = best["marker"]
                entity = marker["text_entity"]

                handle = marker_entity_handle(marker)
                handle_key = handle or f"entity_{id(entity)}"

                if handle_key in seen_handles:
                    continue

                seen_handles.add(handle_key)

                old_label = get_text_value(entity)
                endpoint_name = "A" if endpoint_index == 0 else "B"
                approval_id = f"{region['name']}|{family_name}|{pos}|{endpoint_name}|{handle_key}"

                profile = best.get("write_profile") or marker_write_profile(
                    marker,
                    write_mode=write_mode,
                    allow_block_text_write=allow_block_text_write,
                )

                plan_rows.append({
                    "approval_id": approval_id,
                    "region": region["name"],
                    "sync_mode": "endpoint_recovery",
                    "family": family_name,
                    "axis_position": pos,
                    "region_axis_position": pos,
                    "source_label": new_label,
                    "old_label": old_label,
                    "new_label": new_label,
                    "endpoint": endpoint_name,
                    "distance_to_endpoint": round(best["endpoint_distance"], 3),
                    "axis_distance": round(best["axis_distance"], 3),
                    "axis_coord": coord,
                    "grid_frame_source": frame.get("source", ""),
                    "source_median_radius": source_median_radius,
                    "text_handle": handle,
                    "entity_handle": handle,
                    "text_source": marker.get("text_source"),
                    "write_source": profile.get("write_source", ""),
                    "write_risk": profile.get("write_risk", ""),
                    "write_reason": profile.get("write_reason", ""),
                    "text_type": marker.get("text_type", ""),
                    "layer": marker_layer(marker),
                    "detection_mode": marker.get("detection_mode"),
                    "confidence": marker.get("confidence"),
                    "recovery_score": round(best["score"], 3),
                    "writable": profile.get("writable", False),
                    "entity": entity,
                    "marker": marker,
                })

    if not plan_rows:
        blockers.append(
            "Recovery found no safe endpoint candidates. Try increasing Endpoint Search Radius "
            "or Axis Snap Tolerance slightly."
        )

    ready = len(blockers) == 0

    return ready, blockers, warnings, plan_rows


def recovery_preview_rows(plan_rows):
    rows = []

    for r in plan_rows:
        rows.append({
            "apply": True,
            "approval_id": r.get("approval_id", ""),
            "region": r["region"],
            "family": r["family"],
            "axis_position": r["axis_position"],
            "old_label": r["old_label"],
            "new_label": r["new_label"],
            "endpoint": r["endpoint"],
            "distance_to_endpoint": r["distance_to_endpoint"],
            "axis_distance": r.get("axis_distance", ""),
            "axis_coord": r["axis_coord"],
            "grid_frame_source": r.get("grid_frame_source", ""),
            "recovery_score": r.get("recovery_score", ""),
            "detection_mode": r.get("detection_mode", ""),
            "text_handle": r["text_handle"],
            "text_source": r["text_source"],
            "write_source": r.get("write_source", ""),
            "write_risk": r.get("write_risk", ""),
            "write_reason": r.get("write_reason", ""),
            "layer": r.get("layer", ""),
            "confidence": r["confidence"],
            "writable": r["writable"],
        })

    return rows


def editor_result_to_rows(editor_result):
    if editor_result is None:
        return []

    if isinstance(editor_result, list):
        return editor_result

    try:
        return editor_result.to_dict("records")
    except Exception:
        pass

    try:
        return list(editor_result)
    except Exception:
        return []


def checkbox_truthy(value):
    if value is True:
        return True

    if value is False or value is None:
        return False

    return str(value).strip().lower() in ("true", "1", "yes", "y", "checked")


def approved_ids_from_editor(editor_result):
    rows = editor_result_to_rows(editor_result)

    approved = set()

    for row in rows:
        if checkbox_truthy(row.get("apply", False)):
            approval_id = row.get("approval_id", "")
            if approval_id:
                approved.add(approval_id)

    return approved


def recovery_plan_apply_counts(plan_rows):
    approved_count = len(plan_rows)
    will_change_count = 0
    already_match_count = 0
    risky_count = 0
    unwritable_count = 0

    for r in plan_rows:
        old = clean_text(r.get("old_label", ""))
        new = clean_text(r.get("new_label", ""))

        if old != new:
            will_change_count += 1
        else:
            already_match_count += 1

        if r.get("write_risk") == "high":
            risky_count += 1

        if not r.get("writable"):
            unwritable_count += 1

    return {
        "approved_rows": approved_count,
        "will_change": will_change_count,
        "already_match": already_match_count,
        "high_risk": risky_count,
        "unwritable": unwritable_count,
    }


def build_unapproved_recovery_audit_rows(plan_rows):
    audit = []

    for r in plan_rows:
        audit.append({
            "region": r.get("region", ""),
            "sync_mode": "endpoint_recovery",
            "approved_by_user": False,
            "family": r.get("family", ""),
            "axis_position": r.get("axis_position", ""),
            "region_axis_position": r.get("region_axis_position", r.get("axis_position", "")),
            "old_label": r.get("old_label", ""),
            "new_label": r.get("new_label", ""),
            "changed": False,
            "skipped": True,
            "reason": "user_unchecked_in_manual_approval_table",
            "endpoint": r.get("endpoint", ""),
            "distance_to_endpoint": r.get("distance_to_endpoint", ""),
            "axis_distance": r.get("axis_distance", ""),
            "axis_coord": r.get("axis_coord", ""),
            "detection_mode": r.get("detection_mode", ""),
            "confidence": r.get("confidence", ""),
            "recovery_score": r.get("recovery_score", ""),
            "text_source": r.get("text_source", ""),
            "write_source": r.get("write_source", ""),
            "write_risk": r.get("write_risk", ""),
            "write_reason": r.get("write_reason", ""),
            "entity_handle": r.get("entity_handle", r.get("text_handle", "")),
            "layer": r.get("layer", ""),
            "writable": r.get("writable", ""),
        })

    return audit


def apply_endpoint_recovery_plan(plan_rows, approved_by_user=True):
    changed = 0
    skipped = 0
    audit = []

    for r in plan_rows:
        entity = r["entity"]
        old = get_text_value(entity)
        new = clean_text(r["new_label"])

        base = {
            "region": r.get("region", ""),
            "sync_mode": "endpoint_recovery",
            "approved_by_user": approved_by_user,
            "family": r.get("family", ""),
            "axis_position": r.get("axis_position", ""),
            "region_axis_position": r.get("region_axis_position", r.get("axis_position", "")),
            "old_label": old,
            "new_label": new,
            "endpoint": r.get("endpoint", ""),
            "distance_to_endpoint": r.get("distance_to_endpoint", ""),
            "axis_distance": r.get("axis_distance", ""),
            "axis_coord": r.get("axis_coord", ""),
            "detection_mode": r.get("detection_mode", ""),
            "confidence": r.get("confidence", ""),
            "recovery_score": r.get("recovery_score", ""),
            "text_source": r.get("text_source", ""),
            "write_source": r.get("write_source", ""),
            "write_risk": r.get("write_risk", ""),
            "write_reason": r.get("write_reason", ""),
            "entity_handle": r.get("entity_handle", r.get("text_handle", "")),
            "layer": r.get("layer", ""),
            "writable": r.get("writable", ""),
        }

        if not r.get("writable"):
            skipped += 1

            row = dict(base)
            row.update({
                "changed": False,
                "skipped": True,
                "reason": "recovery_not_writable",
            })
            audit.append(row)
            continue

        if old != new:
            ok = set_text_value(entity, new)

            if ok:
                changed += 1

            row = dict(base)
            row.update({
                "changed": ok,
                "skipped": False,
                "reason": "endpoint_recovery_updated" if ok else "endpoint_recovery_write_failed",
            })
            audit.append(row)

        else:
            row = dict(base)
            row.update({
                "changed": False,
                "skipped": False,
                "reason": "already_matches",
            })
            audit.append(row)

    return changed, skipped, audit


'''

text = text[:start] + endpoint_recovery_section + "\n\n" + text[end:]

# Small cleanup fixes.
text = text.replace(
    "def marker_entity_handle(marker):\n"
    "    entity = marker.get(\"text_entity\")\n"
    "    return marker.get(\"text_handle\") or get_entity_handle(entity)\n"
    "def get_insert_transform",
    "def marker_entity_handle(marker):\n"
    "    entity = marker.get(\"text_entity\")\n"
    "    return marker.get(\"text_handle\") or get_entity_handle(entity)\n\n\n"
    "def get_insert_transform",
)

text = text.replace(
    "                 write_mode=write_mode,\n",
    "                write_mode=write_mode,\n",
)

# Add write_mode to endpoint recovery call if missing.
text = text.replace(
    "                strict_source_labels=recovery_strict_source_labels,\n"
    "                allow_virtual_axis_recovery=recovery_allow_virtual_axis,\n"
    "            )",
    "                strict_source_labels=recovery_strict_source_labels,\n"
    "                allow_virtual_axis_recovery=recovery_allow_virtual_axis,\n"
    "                write_mode=write_mode,\n"
    "            )",
)

# Add write_mode to apply_group_labels calls if missing.
text = text.replace(
    "                ch1, sk1, au1 = apply_group_labels(\n"
    "                    st.session_state.source_numeric,\n"
    "                    num,\n"
    "                    allow_block_text_write=allow_block_text_write,\n"
    "                )",
    "                ch1, sk1, au1 = apply_group_labels(\n"
    "                    st.session_state.source_numeric,\n"
    "                    num,\n"
    "                    allow_block_text_write=allow_block_text_write,\n"
    "                    write_mode=write_mode,\n"
    "                )",
)

text = text.replace(
    "                ch2, sk2, au2 = apply_group_labels(\n"
    "                    st.session_state.source_alpha,\n"
    "                    alp,\n"
    "                    allow_block_text_write=allow_block_text_write,\n"
    "                )",
    "                ch2, sk2, au2 = apply_group_labels(\n"
    "                    st.session_state.source_alpha,\n"
    "                    alp,\n"
    "                    allow_block_text_write=allow_block_text_write,\n"
    "                    write_mode=write_mode,\n"
    "                )",
)

# Add write_mode to get_region_sync_plan calls where exact old endings exist.
text = text.replace(
    "            expected_reference_marker_count=len(st.session_state.arch_detection.get(\"trusted_markers\", [])),\n"
    "            max_region_marker_ratio=max_region_marker_ratio,\n"
    "        )",
    "            expected_reference_marker_count=len(st.session_state.arch_detection.get(\"trusted_markers\", [])),\n"
    "            max_region_marker_ratio=max_region_marker_ratio,\n"
    "            write_mode=write_mode,\n"
    "        )",
)

text = text.replace(
    "                    expected_reference_marker_count=len(st.session_state.arch_detection.get(\"trusted_markers\", [])),\n"
    "                    max_region_marker_ratio=max_region_marker_ratio,\n"
    "                )",
    "                    expected_reference_marker_count=len(st.session_state.arch_detection.get(\"trusted_markers\", [])),\n"
    "                    max_region_marker_ratio=max_region_marker_ratio,\n"
    "                    write_mode=write_mode,\n"
    "                )",
)

path.write_text(text, encoding="utf-8")
print("sync.py repaired successfully.")
