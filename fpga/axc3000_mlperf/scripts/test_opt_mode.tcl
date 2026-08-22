# Probe which OPTIMIZATION_MODE spellings Quartus 26.1 accepts for this project.
# Opens read-only and never exports assignments, so the .qsf is untouched.
project_open axc3000_top

foreach v {
    "Balanced"
    "High Performance Effort"
    "Superior Performance"
    "Superior Performance With Maximum Placement Effort"
    "Aggressive Performance"
} {
    if {[catch {
        set_global_assignment -name OPTIMIZATION_MODE $v
        set got [get_global_assignment -name OPTIMIZATION_MODE]
    } err]} {
        puts "PROBE  \"$v\"  -> ERROR: $err"
    } else {
        puts "PROBE  \"$v\"  -> readback \"$got\""
    }
}

project_close -dont_export_assignments
