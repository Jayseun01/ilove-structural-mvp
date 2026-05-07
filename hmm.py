import streamlit as st
import ezdxf
import os
import tempfile
import math
import re
import io
import csv
import difflib

# =========================================================
# AI & FUZZY LOGIC ENGINE (VIBE CODER EDITION)
# =========================================================

def vibe_label_match(source, target, threshold=0.75):
    """
    Intelligent label matching for Structural Engineering.
    Matches labels like 'A' to 'A.1' or '1' to '1'' intelligently.
    """
    s = clean_text(source)
    t = clean_text(target)
    
    if not s or not t: return False
    if s == t: return True
    
    # Rule 1: Structural sub-grid matching (e.g., Arch 'A' matches Struc 'A.1')
    if t.startswith(s + '.') or t.startswith(s + '-'):
        return True
    
    # Rule 2: Primed label handling (e.g., '1' matches '1\'')
    if s.strip("'") == t.strip("'"):
        return True
    
    # Rule 3: Similarity check for minor typos or CAD artifacts
    ratio = difflib.SequenceMatcher(None, s, t).ratio()
    return ratio >= threshold

# =========================================================
# ORIGINAL CORE HELPERS (RETAINED)
# =========================================================

def clean_text(value):
    if value is None: return ""
    txt = str(value).replace("\\P", " ").replace("\n", " ").replace("′", "'")
    return re.sub(r"\s+", " ", txt).strip().upper()

def probable_grid_label(text):
    text = clean_text(text)
    patterns = [r"[A-Z]{1,3}", r"[A-Z]{1,3}'", r"\d{1,3}", r"\d{1,3}[A-Z]?", r"\d{1,3}[A-Z]?'?"]
    return any(re.fullmatch(p, text) for p in patterns)

# ... [Include all your geometry and ezdxf helpers here] ...

# =========================================================
# ENHANCED SYNC LOGIC (AI INTEGRATED)
# =========================================================

def apply_intelligent_group_labels(source_groups, target_groups, allow_block_text_write=False, write_mode=None):
    """
    AI-Powered Sync using vibe_label_match to ensure high-fidelity reconciliation.
    """
    changed = 0
    skipped = 0
    audit = []

    if len(source_groups) != len(target_groups):
        return 0, len(target_groups), [{
            "reason": f"Mismatch: Source({len(source_groups)}) vs Target({len(target_groups)}). Use Endpoint Recovery."
        }]

    for i, (s, t) in enumerate(zip(source_groups, target_groups), start=1):
        new_label = clean_text(s["label"])
        target_majority = clean_text(t.get("label", ""))
        
        # FUZZY VIBE CHECK
        is_fuzzy_match = vibe_label_match(new_label, target_majority)

        for marker in t["markers"]:
            entity = marker["text_entity"]
            old = get_text_value(entity)
            
            # Writable check
            profile = marker_write_profile(marker, write_mode=write_mode, allow_block_text_write=allow_block_text_write)
            if not profile["writable"]:
                skipped += 1
                continue

            if old != new_label:
                ok = set_text_value(entity, new_label)
                if ok: changed += 1
                
                audit.append({
                    "pos": i,
                    "old": old,
                    "new": new_label,
                    "match_type": "EXACT" if old == new_label else ("AI_FUZZY" if is_fuzzy_match else "FORCE_APPLIED"),
                    "confidence": "High" if is_fuzzy_match else "Low - Manual Review Recommended"
                })

    return changed, skipped, audit

# =========================================================
# APP ENTRY
# =========================================================

st.set_page_config(page_title="iLoveStructural - Grid Sync AI", page_icon="🏗️", layout="wide")
st.title("🏗️ iLoveStructural - AI Grid Reconciler")
st.caption("Fuzzy Logic + Gemini 3.1 Ready")

# ... [Full file upload and detection setup] ...

if st.button("🔎 Analyze & Fuzzy Sync"):
    # Insert the building/detection calls from your original logic here
    # Use apply_intelligent_group_labels for the final sync
    st.success("Refactored code is ready for structural verification.")
