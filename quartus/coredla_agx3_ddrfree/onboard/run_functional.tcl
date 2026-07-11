puts "WRAPPER: start"
if {[catch {
  if {[info exists ::env(DL)] && $::env(DL) ne ""} {
    puts "WRAPPER: design_load $::env(DL)"
    design_load $::env(DL)
    puts "WRAPPER: design_load done"
  }
  set img $::env(IMG)
  set ninf $::env(NINF)
  set ::argv [list --functional=1 --num_inferences=$ninf \
    --arch=/workspace/scratch/ddrfree_run/AGX3_Ddrfree_Fit.arch \
    --input=$img {--output_shape=[10 1 1]}]
  set ::argc [llength $::argv]
  set ::argv0 /workspace/scratch/ddrfree_run/ed0/system_console_script.tcl
  source /workspace/scratch/ddrfree_run/ed0/system_console_script.tcl
} err]} {
  puts "WRAPPER ERROR: $err"
  puts $::errorInfo
}
