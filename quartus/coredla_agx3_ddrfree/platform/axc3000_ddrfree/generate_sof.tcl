# generate_sof.tcl
# Compiles the DDR-free CoreDLA example design for the Arrow AXC3000 (Agilex 3 C100,
# A3CY100BM16AE7S) and generates top.sof. Reconstructed from the vendor
# agx5e_modular_ddrfree/generate_sof.tcl -- ONLY the family_name/device_name are changed to the
# AXC3000 part; the DDR-free flow (board.qsys from board.tcl, kernel_pll clock path, the
# dla_adjust_pll.tcl PLL-retune pass) is identical to the vendor DDR-free reference.

proc compile_script {project_name revision_name family_name device_name} {
    qexec "qsys-generate -syn --family=\"$family_name\" --part=$device_name board.qsys 2>&1"
    qexec "quartus_syn --read_settings_files=off --write_settings_files=off $project_name -c $revision_name 2>&1"
    qexec "quartus_fit --read_settings_files=on --write_settings_files=off $project_name -c $revision_name 2>&1"
    qexec "quartus_sta $project_name -c $revision_name --mode=finalize --do_report_timing 2>&1"
    qexec "quartus_cdb -t dla_adjust_pll.tcl 2>&1 | tee dla_adjust_pll.log"
    qexec "quartus_asm --read_settings_files=on --write_settings_files=off $project_name -c $revision_name 2>&1"
}

proc main {} {
    set project_name top
    set revision_name top
    set family_name "Agilex 3"
    set device_name A3CY100BM16AE7S

    # Setup QSYS project (generates board.qsys + ip/board/*.ip from ddrfree_common/board.tcl)
    qexec "echo \"INFO: Creating Platform Designer System\""
    qexec "qsys-script --cmd=\"set system_name shell;\" --script=board.tcl --quartus_project=top --rev=top 2>&1"

    # Compile the project and generate bitstream
    compile_script $project_name $revision_name $family_name $device_name

    # Generates QoR JSON (runs AFTER quartus_asm, i.e. after top.sof already exists -- non-fatal)
    set project_ip_clock "board_inst|kernel_pll|kernel_pll_outclk0"
    source dla_parse_report.tcl
    dla_parse_report -project top -ip-clock ${project_ip_clock} -platform-clock ${project_ip_clock}
}

main
