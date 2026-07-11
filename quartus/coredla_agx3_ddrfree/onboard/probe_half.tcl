design_load /workspace/scratch/ddrfree_run/top_ddrfree_resnet8_nofoldcfg.sof
set issps [get_service_paths issp]
set c [claim_service issp [lindex $issps 0] mylib]
issp_write_source_data $c 0x0
issp_write_source_data $c 0x1
set mpaths [get_service_paths master]
set m [claim_service master [lindex $mpaths 0] ""]
set B 0x38000
master_write_32 $m [expr {$B+0x220}] 0
master_write_32 $m [expr {$B+0x204}] 0
master_write_32 $m [expr {$B+0x200}] 3
master_write_32 $m [expr {$B+0x22c}] 1
master_write_32 $m 0x30044 0x2
master_write_32 $m 0x30004 0x2
# egress descriptor for 32 B
master_write_32 $m 0x30064 0x00280000
master_write_32 $m 0x30068 32
master_write_32 $m 0x3006c 0x80000000
# HALF 1: 3072 B
master_write_from_file $m /workspace/scratch/ddrfree_run/img_hwc_half1.bin 0x00200000
master_write_32 $m 0x30020 0x00200000
master_write_32 $m 0x30028 3072
master_write_32 $m 0x3002c 0x80000000
after 3000
puts [format "completion after half1 = 0x%08x (expect 0 if frame=3072 elements)" [master_read_32 $m [expr {$B+0x224}] 1]]
# HALF 2
master_write_from_file $m /workspace/scratch/ddrfree_run/img_hwc_half2.bin 0x00200000
master_write_32 $m 0x30020 0x00200000
master_write_32 $m 0x30028 3072
master_write_32 $m 0x3002c 0x80000000
after 3000
puts [format "completion after half2 = 0x%08x (expect 1)" [master_read_32 $m [expr {$B+0x224}] 1]]
