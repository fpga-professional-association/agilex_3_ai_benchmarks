# l2_read.tcl — run the on-chip L2 aggregate-M20K-bandwidth microbench over JTAG and print GB/s
# (issue #12, PLAN §7 L2 + §3 LV3). Modeled on fpga/axc3000/sysconsole/bw_read.tcl.
#
# Control plane ONLY over JTAG (PLAN §8 method E): this reads DIMS, programs K, pulses CTRL.start,
# polls STATUS.done, and reads the cycle count + per-bank/aggregate checksums. The measured
# CYCLES_LO/HI cover only the on-chip M20K read datapath, NOT the JTAG access time, so the reported
# GB/s is the real on-chip aggregate bandwidth for whichever GEOMETRY/OUTPUT_REG config was
# synthesized into the loaded .sof (see README.md's config matrix — this script does not know which
# config is loaded; it reads NUM_BANKS/WORD_BYTES/GEOMETRY/OUTPUT_REG back from DIMS and reports them).
#
# Run inside Quartus System Console (headless):
#   system-console --script=sysconsole/l2_read.tcl <K> <fclk_MHz>
# K defaults to 100000; fclk_MHz MUST match the IOPLL clk frequency actually built into the loaded
# .sof (qsys/make_l2_sys.tcl's CLK_MHZ, default 300.0 — post-fit Fmax may differ; use the value you
# actually constrained/achieved, not a wish).
#
# CSR map (m20k_bw_pkg::L2_ADDR_*, rtl/microbench/l2_m20k_bw/README.md). The JTAG-to-Avalon master
# is BYTE addressed; m20k_bw's CSR slave is ALREADY byte-addressed (no >>2 shift, unlike bw_read.tcl's
# hyperram_bw_test target — see quartus/l2_m20k_bw/top.sv header comment):
#   0x00 CTRL(w, bit0=START)   0x04 K            0x08 CYCLES_LO     0x0C CYCLES_HI
#   0x10 STATUS(bit0=RUNNING,bit1=DONE)          0x14 CS_ADDR(w)    0x18 CS_DATA(r)
#   0x1C AGG_CS(r)             0x20 DIMS(r)

# ---- configuration -------------------------------------------------------
set K        100000       ;# reads per reader for this run (override: arg 1)
set FCLK_MHZ 300.0         ;# IOPLL clk (outclk0) MHz -- MUST match the loaded .sof (override: arg 2)

if {$argc >= 1} { set K        [lindex $argv 0] }
if {$argc >= 2} { set FCLK_MHZ [expr {double([lindex $argv 1])}] }

# CSR byte offsets
set CTRL    0x00
set K_REG   0x04
set CYC_LO  0x08
set CYC_HI  0x0C
set STATUS  0x10
set CS_ADDR 0x14
set CS_DATA 0x18
set AGG_CS  0x1C
set DIMS    0x20

# ---- helpers -------------------------------------------------------------
proc rd32 {m a} {
    # master_read_32 returns a list of one 32-bit value; normalise to an unsigned integer.
    return [expr {[lindex [master_read_32 $m $a 1] 0] & 0xffffffff}]
}

# ---- open the JTAG-to-Avalon master service ------------------------------
set paths [get_service_paths master]
if {[llength $paths] == 0} {
    puts "ERROR: no Avalon 'master' service found. Is the board programmed and USB-Blaster III attached?"
    exit 1
}
if {[llength $paths] > 1} {
    puts "NOTE: multiple master services found; using the first:"
    foreach p $paths { puts "   $p" }
}
set m [lindex $paths 0]
open_service master $m
puts "Opened master service: $m"

# ---- DIMS: compile-time geometry (see m20k_bw_pkg.sv's DIMS field layout) ------------------------
set dims        [rd32 $m $DIMS]
set num_banks   [expr {$dims & 0xFFFF}]
set word_bytes  [expr {($dims >> 16) & 0xFF}]
set geometry    [expr {($dims >> 24) & 0x1}]
set output_reg  [expr {($dims >> 25) & 0x1}]
set addr_width  [expr {($dims >> 26) & 0x3F}]
set geom_name   [expr {$geometry ? "SHARED_ROUND_ROBIN" : "BANKED_PER_READER"}]

puts [format "DIMS = 0x%08X -> NUM_BANKS=%d WORD_BYTES=%d GEOMETRY=%s(%d) OUTPUT_REG=%d ADDR_WIDTH=%d" \
        $dims $num_banks $word_bytes $geom_name $geometry $output_reg $addr_width]

# ---- program the run -----------------------------------------------------
puts [format "Programming K=%d reads/reader ..." $K]
master_write_32 $m $K_REG $K

# ---- pulse CTRL.start (self-clearing strobe) -----------------------------
master_write_32 $m $CTRL 0x1

# ---- poll STATUS.done (bit1), with a timeout -----------------------------
set done 0
set st 0
for {set i 0} {$i < 1000000} {incr i} {
    set st [rd32 $m $STATUS]
    if {($st & 0x2) != 0} { set done 1; break }   ;# STATUS.done
}
if {!$done} {
    puts "ERROR: run did not complete (STATUS.done never asserted). Last STATUS = [format 0x%08X $st]"
    close_service master $m
    exit 1
}

# ---- read back the atomic cycle-count snapshot ---------------------------
set cyc_lo [rd32 $m $CYC_LO]
set cyc_hi [rd32 $m $CYC_HI]
set cycles [expr {($cyc_hi << 32) | $cyc_lo}]

# ---- per-bank checksum readback (CS_ADDR select -> CS_DATA) + aggregate --
set bank_cs {}
for {set b 0} {$b < $num_banks} {incr b} {
    master_write_32 $m $CS_ADDR $b
    lappend bank_cs [rd32 $m $CS_DATA]
}
set agg_cs [rd32 $m $AGG_CS]

# ---- achieved GB/s: NUM_BANKS * K * bytes/read / (cycles / fclk_hz) ------
# Same total bytes move in every GEOMETRY config (only elapsed cycles differ), so this number is
# directly comparable across configs and against PLAN §3 LV3's banks*bytes/port/cycle*fclk bound
# (scripts/l2_golden.py:theoretical_gbps -- run that offline with the same NUM_BANKS/WORD_BYTES to
# get the ceiling this achieved figure should approach for GEOMETRY=BANKED).
proc gbps {num_banks k word_bytes fclk_mhz cycles} {
    if {$cycles == 0} { return 0.0 }
    set fclk_hz [expr {$fclk_mhz * 1.0e6}]
    set bytes [expr {double($num_banks) * double($k) * double($word_bytes)}]
    return [expr {$bytes / (double($cycles) / $fclk_hz) / 1.0e9}]
}
set achieved_gbps [gbps $num_banks $K $word_bytes $FCLK_MHZ $cycles]
set theoretical_gbps [expr {double($num_banks) * double($word_bytes) * ($FCLK_MHZ * 1.0e6) / 1.0e9}]

puts "---------------------------------------------------------------"
puts [format "STATUS        = 0x%08X (busy=%d done=%d)" $st [expr {$st & 1}] [expr {($st>>1)&1}]]
puts [format "f_clk         = %.3f MHz" $FCLK_MHZ]
puts [format "K             = %d reads/reader   NUM_BANKS=%d   WORD_BYTES=%d   GEOMETRY=%s   OUTPUT_REG=%d" \
        $K $num_banks $word_bytes $geom_name $output_reg]
puts [format "CYCLES        = %d" $cycles]
puts [format "AGG_CHECKSUM  = 0x%08X" $agg_cs]
puts -nonewline "PER-BANK CS   = "
foreach cs $bank_cs { puts -nonewline [format "0x%08X " $cs] }
puts ""
puts [format "ACHIEVED      = %.3f GB/s" $achieved_gbps]
puts [format "THEORETICAL   = %.3f GB/s  (banks*bytes/port/cycle*fclk, PLAN §3 LV3 ceiling for BANKED)" \
        $theoretical_gbps]
puts [format "EFFICIENCY    = %.1f%% of theoretical" \
        [expr {$theoretical_gbps > 0 ? (100.0 * $achieved_gbps / $theoretical_gbps) : 0.0}]]
puts "---------------------------------------------------------------"
puts "NOTE: cross-check AGG_CHECKSUM / PER-BANK CS against:"
puts "  python3 scripts/l2_golden.py --num-banks $num_banks --addr-width $addr_width --k $K \\"
puts "      --geometry [expr {$geometry ? {shared} : {banked}}] --output-reg $output_reg"
puts "before trusting the GB/s number above (issue #12 do-not: never report bandwidth from a run"
puts "whose checksum failed)."

close_service master $m
