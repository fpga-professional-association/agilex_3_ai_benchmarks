# =============================================================================
# probe_debug_network.tcl  -- CoreDLA DDR-free internal-dataflow forensics
# =============================================================================
# Dumps the dla_interface_profiling_counters over the CSR DEBUG_NETWORK ring.
# This is the ONLY way to see whether the clk_dla compute pipeline actually ran,
# because the DLA_DMA_CSR FILTER/FEATURE-read + FEATURE-write counters are
# hard-wired to the DDR LSU AXI channels, which are tied to 0 in DDR-free mode
# (DISABLE_DDR=1) -> those CSR counters are EXPECTED-ZERO and non-diagnostic.
#
# The profiling-counter module (dla_interface_profiling_counters.sv) and the
# whole debug ring run on clk_dla. So this probe is ALSO a clk_dla liveness test:
#   - if every debug read returns TIMEOUT (DEBUG_NETWORK_VALID @0x254 never
#     asserts) => clk_dla is dead or its reset never released.
#   - if reads respond, the per-interface transaction counts localize the break.
#
# Address decode (from RTL, verified):
#   CSR write 0x250 = DEBUG_NETWORK_ADDR = {module_id[31:24], reg[23:0]}
#     module_id 0 = dla_interface_profiling_counters (NUM_MODULES=1, ADDR_LOWER=24)
#   reg[23:22]=2'b10 -> profiling counters block  (base 0x800000)
#     reg[21:5] = interface index (PC_ID_*, 0..18)
#     reg[4:2]  = sub-register:
#        0 steady valid | 1 steady ready
#        2 txn_lo | 3 txn_hi  (accepted = valid&ready)   <-- "did data flow"
#        4 backpressure_lo | 5 hi (valid & ~ready)
#        6 starvation_lo   | 7 hi (~valid & ready, after first txn)
#   reg[23:22]=2'b11 -> freeze block (base 0xC00000); bit2=1 freeze / bit2=0 thaw
#
# Read one debug reg: write 0x250=addr, poll 0x254 until !=0, read 0x258.
#
# Assumes: CSR base 0x38000, ingress onchip @0x200000, egress onchip @0x280000,
#          modular-SGDMA CSR ingress @0x30000 / egress @0x30040 (as in probe_half.tcl).
#
# Env (all optional):
#   DL          -- .sof to design_load first
#   SKIP_INPUT  -- if set to 1, do NOT feed input (control run: pipeline should
#                  then show config flowed but no feature transactions)
#   IMG_HALF1 / IMG_HALF2 -- input halves (default img_hwc_half1/2.bin, 3072B ea)
# =============================================================================

set B        0x38000
set DIR      /workspace/scratch/ddrfree_run
if {[info exists ::env(IMG_HALF1)] && $::env(IMG_HALF1) ne ""} {set H1 $::env(IMG_HALF1)} else {set H1 $DIR/img_hwc_half1.bin}
if {[info exists ::env(IMG_HALF2)] && $::env(IMG_HALF2) ne ""} {set H2 $::env(IMG_HALF2)} else {set H2 $DIR/img_hwc_half2.bin}
set SKIP_INPUT 0
if {[info exists ::env(SKIP_INPUT)] && $::env(SKIP_INPUT) eq "1"} {set SKIP_INPUT 1}

if {[info exists ::env(DL)] && $::env(DL) ne ""} {
  puts "design_load $::env(DL)"
  design_load $::env(DL)
}

# ---- reset pulse via ISSP (releases reset AND clears profiling counters) -----
set issps [get_service_paths issp]
set c [claim_service issp [lindex $issps 0] mylib]
issp_write_source_data $c 0x0
issp_write_source_data $c 0x1
puts "reset pulsed 0->1 (profiling counters cleared)"

set mpaths [get_service_paths master]
set m [claim_service master [lindex $mpaths 0] ""]

# ---- one debug-network read (returns integer, or "TIMEOUT") ------------------
proc dbgrd {m B addr} {
  master_write_32 $m [expr {$B+0x250}] $addr
  set v 0
  for {set i 0} {$i < 40} {incr i} {
    set v [master_read_32 $m [expr {$B+0x254}] 1]
    if {$v != 0} break
  }
  if {$v == 0} { return "TIMEOUT" }
  return [master_read_32 $m [expr {$B+0x258}] 1]
}
# profiling-counter register address for (interface idx, subreg)
proc pcaddr {idx sub} { return [expr {0x800000 + ($idx*32) + ($sub*4)}] }

# ---- initialize CoreDLA + queue egress descriptor (from probe_half.tcl) ------
master_write_32 $m [expr {$B+0x220}] 0
master_write_32 $m [expr {$B+0x204}] 0
master_write_32 $m [expr {$B+0x200}] 3
master_write_32 $m [expr {$B+0x22c}] 1
master_write_32 $m 0x30044 0x2
master_write_32 $m 0x30004 0x2
master_write_32 $m 0x30064 0x00280000
master_write_32 $m 0x30068 32
master_write_32 $m 0x3006c 0x80000000

# ---- feed a full frame (two 3072B halves) unless SKIP_INPUT ------------------
if {$SKIP_INPUT} {
  puts "SKIP_INPUT=1 : no ingress data fed (control run)"
} else {
  foreach f [list $H1 $H2] {
    master_write_from_file $m $f 0x00200000
    master_write_32 $m 0x30020 0x00200000
    master_write_32 $m 0x30028 3072
    master_write_32 $m 0x3002c 0x80000000
    after 1500
  }
}
after 2000

# ---- top-level status --------------------------------------------------------
puts "----------------------------------------------------------------------"
puts [format "COMPLETION_COUNT (0x224)          = 0x%08x" [master_read_32 $m [expr {$B+0x224}] 1]]
puts [format "DESC_DIAGNOSTICS (0x21c)          = 0x%08x" [master_read_32 $m [expr {$B+0x21c}] 1]]
puts [format "CORE_CLOCKS_ACTIVE_LO (0x27c)     = 0x%08x  (nonzero => clk_dla core saw an active job)" [master_read_32 $m [expr {$B+0x27c}] 1]]
puts [format "CLOCKS_ACTIVE_LO (0x240)          = 0x%08x" [master_read_32 $m [expr {$B+0x240}] 1]]
puts "--- DDR-LSU CSR counters (EXPECTED 0 in DDR-free -- NON-diagnostic) ---"
puts [format "INPUT_FEATURE_READ_LO (0x264)     = 0x%08x" [master_read_32 $m [expr {$B+0x264}] 1]]
puts [format "INPUT_FILTER_READ_LO  (0x26c)     = 0x%08x" [master_read_32 $m [expr {$B+0x26c}] 1]]
puts [format "OUTPUT_FEATURE_WRITE_LO (0x274)   = 0x%08x" [master_read_32 $m [expr {$B+0x274}] 1]]

# ---- clk_dla liveness gate ---------------------------------------------------
puts "----------------------------------------------------------------------"
set probe [dbgrd $m $B [pcaddr 0 0]]
if {$probe eq "TIMEOUT"} {
  puts "*** DEBUG NETWORK TIMEOUT on first read ***"
  puts "*** => clk_dla (kernel_clk) is NOT toggling, OR the clk_dla reset never"
  puts "***    released. The entire compute domain is frozen. This alone explains"
  puts "***    constant output + zero compute. Check kernel_pll lock / clk_dla SDC /"
  puts "***    global_pll_resetn into the clk_dla domain. STOP: fix clocking first."
  return
}
puts "debug network RESPONDS -> clk_dla is alive and out of reset. Reading pipeline."

# ---- dump every profiling interface -----------------------------------------
# order == PC_ID_* enum in dla_top.sv (index 0..18)
set names {
  "0  DMA_TO_CONFIG              (config bytes INTO config net; may be 0 in ocp mode*)"
  "1  DMA_TO_FILTER             "
  "2  DMA_TO_INPUT_FEEDER        (DDR feature reader; ~0 when input-streaming)"
  "3  CONFIG_TO_INPUT_FEEDER_IN "
  "4  CONFIG_TO_INPUT_FEEDER_OUT"
  "5  CONFIG_TO_XBAR            "
  "6  CONFIG_TO_ACTIVATION      "
  "7  CONFIG_TO_POOL            "
  "8  CONFIG_TO_SOFTMAX         "
  "9  INPUT_FEEDER_TO_SEQUENCER  <== features reach sequencer (drives PE start)"
  "10 PE_ARRAY_TO_XBAR          <== *** PE ARRAY PRODUCED OUTPUT (did compute run) ***"
  "11 XBAR_TO_ACTIVATION        "
  "12 ACTIVATION_TO_XBAR        "
  "13 XBAR_TO_POOL              "
  "14 POOL_TO_XBAR              "
  "15 XBAR_TO_SOFTMAX           "
  "16 SOFTMAX_TO_XBAR           "
  "17 XBAR_TO_INPUT_FEEDER      "
  "18 XBAR_TO_DMA                (feeds output streamer / feature writer)"
}
puts "----------------------------------------------------------------------"
puts "idx  interface                                                    Vld Rdy   txn_lo   backp_lo  starv_lo"
puts "----------------------------------------------------------------------"
for {set i 0} {$i < 19} {incr i} {
  set vld  [dbgrd $m $B [pcaddr $i 0]]
  set rdy  [dbgrd $m $B [pcaddr $i 1]]
  set txn  [dbgrd $m $B [pcaddr $i 2]]
  set bp   [dbgrd $m $B [pcaddr $i 4]]
  set st   [dbgrd $m $B [pcaddr $i 6]]
  set nm [lindex $names $i]
  if {$txn eq "TIMEOUT"} {
    puts [format "%-62s TIMEOUT" $nm]
  } else {
    puts [format "%-62s  %d   %d   %8d  %8d  %8d" $nm $vld $rdy $txn $bp $st]
  }
}
puts "----------------------------------------------------------------------"
puts "READING GUIDE:"
puts " * ocp mode: on-chip config enters via the intercept port, which is NOT"
puts "   snooped, so idx0 (DMA_TO_CONFIG) may legitimately read 0. Use idx3-8"
puts "   (CONFIG_TO_*) to confirm config was DISTRIBUTED to the consumers."
puts " HEALTHY (compute ran): idx3-8 txn_lo > 0 (config distributed),"
puts "   idx9 txn_lo > 0 (features reached sequencer), idx10 txn_lo > 0 (PE"
puts "   produced results), idx18 txn_lo > 0 (results head to output)."
puts " BROKEN patterns:"
puts "   - idx3-8 all 0            => config never distributed on clk_dla"
puts "                               (on-chip config intercept / ddrfree_config"
puts "                                _data_read never dispatched). PE can't start."
puts "   - idx3-8 >0 but idx9 = 0  => sequencer never fed; stream-buffer read /"
puts "                                input-feeder handshake dead."
puts "   - idx9 >0 but idx10 = 0   => sequencer ran but PE array never emitted"
puts "                                (PE-start / filter-scratchpad / DSP issue);"
puts "                                idx10 backp/starv shows which side stalls."
puts "   - idx10 >0 but constant   => PE ran; constant egress is an OUTPUT-PATH /"
puts "     host-side output-buffer-address problem, NOT dead compute."
puts "======================================================================"
