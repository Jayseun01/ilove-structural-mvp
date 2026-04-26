if arch_file and struc_file:
        st.success("Both drawings loaded into memory. Ready to initiate synchronization protocol.")
        
        # --- ADDING MEMORY TO THE APP ---
        if 'audit_started' not in st.session_state:
            st.session_state.audit_started = False
        if 'audit_completed' not in st.session_state:
            st.session_state.audit_completed = False

        # When clicked, update the memory to True
        if st.button("🚀 Run Grid Audit"):
            st.session_state.audit_started = True
            st.session_state.audit_completed = False

        # Because we use session_state, this stays visible!
        if st.session_state.audit_started:
            st.info("Scanner Activated...")
            st.write("1. Finding Anchor Points...")
            st.write("2. Scanning for Grid Labels (ignoring dimensions)...")
            st.write("3. Checking tolerances...")
            
            st.warning(f"Simulated Error: Grid 1-2 distance mismatch! (Arch: 2500mm, Struct: 2498mm. Tolerance: {tolerance}mm)")
            
            # Save the user's choice to a variable
            action = st.radio("How to proceed?", ["Force Structural to match Arch (2500mm)", "Reject and Abort", "Ignore and Pass"])
            
            if st.button("Apply Fix"):
                st.session_state.audit_completed = True

        # Show the final result based on what they picked
        if st.session_state.audit_completed:
            st.markdown("---")
            if action == "Ignore and Pass":
                st.success("✅ Bypassed mismatch. Proceeding with Structural coordinates.")
            elif action == "Force Structural to match Arch (2500mm)":
                st.success("✅ Grid topological sub-scripting applied. Structural grid forced to 2500mm.")
            else:
                st.error("❌ Audit aborted by user.")
                
            if st.button("Reset Audit"):
                st.session_state.audit_started = False
                st.session_state.audit_completed = False
                st.rerun()
