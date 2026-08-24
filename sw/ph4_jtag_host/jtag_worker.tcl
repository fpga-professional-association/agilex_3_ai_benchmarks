# jtag_worker.tcl -- System Console side of the CIFAR-10 benchmark runner.
#
# Runs inside:
#   C:\altera_pro\26.1\syscon\bin\system-console.exe --cli --script=jtag_worker.tcl
#
# It speaks a line-oriented request/response protocol so the host orchestrator
# (tools/run_cifar10_jtag.py) never has to know any Tcl.  Every request gets
# exactly one terminating line: "OK ..." or "ERR ...".  Informational lines are
# emitted before the terminator and always start with a known tag (RES, LOG).
#
# WHY A PERSISTENT WORKER, NOT ONE system-console PER IMAGE:
#   system-console takes ~10 s to start (JVM + Tcl + service discovery).  Ten
#   thousand images cannot each pay that.  The worker is launched once per
#   programming cycle and driven over stdin.
#
# WHY THE PER-IMAGE SEQUENCE IS FUSED INTO THE `JOB` COMMAND:
#   Each host<->worker round trip costs a pipe round trip on top of the JTAG
#   round trip.  Doing "write input / queue / poll / read out" from Python would
#   multiply the dominant cost.  `JOB` (and `BATCH`, which loops `JOB` entirely
#   inside Tcl) collapses one image into one host round trip.
#
# =============================================================================
# PROTOCOL
# =============================================================================
#   PING                                  -> OK pong <version>
#   CFG k=v [k=v ...]                     -> OK <n_keys>
#   OPEN <auto|index|service-path>        -> OK <service-path>
#   CLOSE                                 -> OK closed
#   W32  <addr_hex> <val_hex>             -> OK
#   R32  <addr_hex>                       -> OK <val_hex>
#   WBLK <addr_hex> <nbytes> <file>       -> OK us=<n>
#   RBLK <addr_hex> <nbytes> <file>       -> OK us=<n>
#   CSRINIT                               -> OK <k=v ...>
#   JOB  <io_base_hex> <timeout_ms>       -> RES <k=v ...> ; OK 1
#   BATCH <manifest> <first> <count> <timeout_ms>
#                                         -> RES ... (xN) ; OK <n_done>
#   CAL  <nbytes> <scratchfile>           -> OK wr_us=<n> rd_us=<n> bytes=<n>
#   QUIT                                  -> OK bye   (then exit)
#
# CFG keys (all integers, decimal or 0x-prefixed):
#   csr_base        host-side byte address of the CoreDLA CSR slave
#   mem_base        host-side byte address of the pseudo-DDR (dla_io) slave
#   input_off       byte offset of the input tensor inside dla_io
#   output_off      byte offset of the output tensor inside dla_io
#   output_halves   number of FP16 words to read back (16 for this graph)
#   poison          1 = sentinel-fill the output window before every job
#   counters        comma-separated host-side byte addresses sampled before and
#                   after every job (see TODO(JTAG-MAP) below)
#   config_base / config_range_minus_two / intermediate_base / io_base
#                   values programmed by CSRINIT (DLA-side addresses!)
#
# -----------------------------------------------------------------------------
# TODO(JTAG-MAP) -- everything the JTAG design agent must pin down:
#   1. Does the design expose a JTAG-to-Avalon master at all?  The working
#      NIOSV_lab.qsys has jtag_uart ONLY -- no altera_jtag_avalon_master.  Until
#      one is added, `get_service_paths master` returns an empty list and OPEN
#      fails.  (Nios V's debug System Bus Access is the fallback; if that is the
#      chosen route, OPEN must be reworked to use the `processor`/`sldfabric`
#      services instead -- the rest of this file is unaffected.)
#   2. The host-side base addresses of the CSR slave and of dla_io, as seen from
#      the NEW master.  They are NOT the Nios V addresses (0x000a0000 /
#      0x00130000) unless the agent deliberately mirrors that map.  -> csr_base,
#      mem_base.
#   3. The DLA-side address written into CSR 536 stays 0x30000 regardless of the
#      host map: the IP dereferences it through its own ddr_axi port.  Keeping
#      these two spaces distinct is the single easiest thing to get wrong here.
#   4. The per-job latency counters.  CSR 636 reads 0 on this build and CSR 576
#      counts in the ddr_clk domain, so NEITHER is a usable dla_clk latency
#      witness.  The agent should expose a free-running dla_clk cycle counter
#      plus (ideally) queue/complete timestamp latches.  List their host-side
#      addresses in `counters`; this worker records raw before/after values and
#      does no arithmetic and no unit conversion -- the host owns that.
# -----------------------------------------------------------------------------

set VERSION "jtag_worker/0.1"

# --- state -------------------------------------------------------------------
set ::MASTER ""
array set ::CFG {
    csr_base              0x00000000
    mem_base              0x00000000
    input_off             0
    output_off            16384
    output_halves         16
    poison                1
    poison_word           0x5a5a5a5a
    counters              ""
    config_base           0
    config_range_minus_two 2430
    intermediate_base     0x35000
    io_base               0x30000
    reset_settle_ms       5
}

# CoreDLA CSR byte offsets (non-ocp flow; mirrors software/fpga_ai_resnet8/main.c
# which in turn mirrors tb_csr_driver_AGX3_Small_NoSoftmax_AGX3.sv).
array set ::CSR {
    IRQ            512
    MASK           516
    CONFIG_BASE    528
    CONFIG_RANGE   532
    IO_BASE        536
    DIAG           540
    INTERMEDIATE   544
    COMPLETION     548
    IP_RESET       552
    LICENSE        608
}
set ::IRQ_ALL 3
set ::DIAG_OUT_OF_INFERENCES 4

# --- plumbing ----------------------------------------------------------------
proc ok {args}  { puts "OK [join $args { }]"; flush stdout }
proc err {msg}  { puts "ERR $msg";           flush stdout }
proc log {msg}  { puts "LOG $msg";           flush stdout }

proc need_master {} {
    if {$::MASTER eq ""} { error "no master service open (send OPEN first)" }
    return $::MASTER
}

proc cfg {key} { return $::CFG($key) }

# Tcl's expr treats a leading 0 as octal in some paths; normalise every address
# through this so "0x30000", "196608" and "0196608" all behave.
proc num {s} {
    if {[string match "0x*" $s] || [string match "0X*" $s]} {
        return [expr {int($s)}]
    }
    return [expr {int([string trimleft $s "0"] eq "" ? 0 : [string trimleft $s "0"])}]
}

proc csr_addr {off} { return [expr {[num [cfg csr_base]] + $off}] }
proc mem_addr {off} { return [expr {[num [cfg mem_base]] + $off}] }

proc rd32 {addr} { return [lindex [master_read_32 [need_master] $addr 1] 0] }
proc wr32 {addr val} { master_write_32 [need_master] $addr $val }

proc csr_rd {off} { return [rd32 [csr_addr $off]] }
proc csr_wr {off val} { wr32 [csr_addr $off] $val }

proc hex32 {v} { return [format "0x%08x" [expr {$v & 0xffffffff}]] }

proc counter_list {} {
    set out {}
    foreach a [split [cfg counters] ","] {
        set a [string trim $a]
        if {$a ne ""} { lappend out [num $a] }
    }
    return $out
}

proc sample_counters {} {
    set vals {}
    foreach a [counter_list] { lappend vals [hex32 [rd32 $a]] }
    return [join $vals ","]
}

# --- commands ----------------------------------------------------------------

proc cmd_cfg {args} {
    set n 0
    foreach kv $args {
        set i [string first "=" $kv]
        if {$i < 0} { error "bad CFG token '$kv' (want key=value)" }
        set k [string range $kv 0 [expr {$i - 1}]]
        set v [string range $kv [expr {$i + 1}] end]
        if {![info exists ::CFG($k)]} { error "unknown CFG key '$k'" }
        set ::CFG($k) $v
        incr n
    }
    ok $n
}

proc cmd_open {which} {
    set paths [get_service_paths master]
    if {[llength $paths] == 0} {
        # See TODO(JTAG-MAP) 1.  Fail loudly with the full service inventory so
        # the design side has something actionable instead of "no master".
        set inv ""
        foreach t {master processor bytestream jtag_debug device} {
            catch {append inv " $t=[llength [get_service_paths $t]]"}
        }
        error "no 'master' service on the cable -- the design exposes no\
               JTAG-to-Avalon master. service inventory:$inv"
    }
    if {$which eq "auto"} {
        set p [lindex $paths 0]
        if {[llength $paths] > 1} {
            log "multiple masters ([llength $paths]); using index 0: $p"
            foreach q $paths { log "  master: $q" }
        }
    } elseif {[string is integer -strict $which]} {
        if {$which >= [llength $paths]} {
            error "master index $which out of range ([llength $paths] present)"
        }
        set p [lindex $paths $which]
    } else {
        set p $which
    }
    open_service master $p
    # Claiming keeps a stray System Console GUI from stealing the link
    # mid-run.  Non-fatal: some fabric versions reject the claim group.
    if {[catch {claim_service master $p BENCH} e]} { log "claim_service: $e" }
    set ::MASTER $p
    ok $p
}

proc cmd_close {} {
    if {$::MASTER ne ""} {
        catch {close_service master $::MASTER}
        set ::MASTER ""
    }
    ok closed
}

proc cmd_w32 {addr val} { wr32 [num $addr] [num $val]; ok }
proc cmd_r32 {addr}     { ok [hex32 [rd32 [num $addr]]] }

proc cmd_wblk {addr nbytes file} {
    set t0 [clock microseconds]
    # master_write_from_file is the only high-throughput write path; building a
    # 16384-element Tcl list for master_write_memory is ~100x slower in the
    # interpreter alone.  TODO(BRINGUP): if this command is missing in this
    # System Console build, fall back to master_write_memory over a byte list
    # and re-do the throughput estimate -- it will be far worse.
    master_write_from_file [need_master] $file [num $addr]
    ok "us=[expr {[clock microseconds] - $t0}]"
}

proc cmd_rblk {addr nbytes file} {
    set t0 [clock microseconds]
    master_read_to_file [need_master] $file [num $addr] [num $nbytes]
    ok "us=[expr {[clock microseconds] - $t0}]"
}

# Full non-ocp bring-up sequence.  Order is load-bearing: IP_RESET first (the
# board campaign proved that a late IP_RESET triplicates completions), then
# mask/clear, then the DDR-allocation registers, and only then may a descriptor
# be queued by writing IO_BASE.
proc cmd_csrinit {} {
    csr_wr $::CSR(IP_RESET) 1
    after [num [cfg reset_settle_ms]]

    csr_wr $::CSR(MASK) $::IRQ_ALL
    csr_wr $::CSR(IRQ)  $::IRQ_ALL          ;# W1C: drop stale status

    csr_wr $::CSR(INTERMEDIATE)  [num [cfg intermediate_base]]
    csr_wr $::CSR(CONFIG_BASE)   [num [cfg config_base]]
    csr_wr $::CSR(CONFIG_RANGE)  [num [cfg config_range_minus_two]]

    ok "config_base=[hex32 [csr_rd $::CSR(CONFIG_BASE)]]"\
       "config_range=[csr_rd $::CSR(CONFIG_RANGE)]"\
       "intermediate=[hex32 [csr_rd $::CSR(INTERMEDIATE)]]"\
       "mask=[hex32 [csr_rd $::CSR(MASK)]]"\
       "irq=[hex32 [csr_rd $::CSR(IRQ)]]"\
       "diag=[hex32 [csr_rd $::CSR(DIAG)]]"\
       "completion=[csr_rd $::CSR(COMPLETION)]"\
       "license=[hex32 [csr_rd $::CSR(LICENSE)]]"
}

# Sentinel-fill the output window so a job that silently fails to write cannot
# be scored against the PREVIOUS image's logits.  One burst = one round trip.
proc poison_output {} {
    if {![num [cfg poison]]} { return }
    set halves [num [cfg output_halves]]
    set words  [expr {($halves + 1) / 2}]
    set fill {}
    for {set i 0} {$i < $words} {incr i} { lappend fill [num [cfg poison_word]] }
    master_write_32 [need_master] [mem_addr [num [cfg output_off]]] $fill
}

# One inference.  Returns a "k=v k=v" string; the caller prefixes "RES ".
#
# TIMING CONTRACT -- read this before touching any of the us_* fields:
#   us_wr / us_q / us_poll / us_rd are HOST-OBSERVED WALL CLOCK around JTAG
#   traffic.  us_poll in particular is NOT the FPGA inference latency: each
#   poll is a JTAG round trip of the same order as the inference itself
#   (~0.9 ms at 300 MHz), so us_poll only upper-bounds latency, and badly.
#   The only admissible FPGA latency is the ctr= delta, which comes from
#   counters clocked inside the device.  The host keeps these in separate
#   record fields and never adds one to the other.
proc do_job {idx infile timeout_ms} {
    set io_base [num [cfg io_base]]

    # ---- input tensor -------------------------------------------------------
    set us_wr 0
    if {$infile ne "-"} {
        set t0 [clock microseconds]
        master_write_from_file [need_master] $infile [mem_addr [num [cfg input_off]]]
        set us_wr [expr {[clock microseconds] - $t0}]
    }
    poison_output

    # ---- queue --------------------------------------------------------------
    set compl0 [csr_rd $::CSR(COMPLETION)]
    set ctr_before [sample_counters]

    set t1 [clock microseconds]
    csr_wr $::CSR(IO_BASE) $io_base           ;# THE write that enqueues the job
    set t2 [clock microseconds]

    # ---- completion poll ----------------------------------------------------
    set polls 0
    set timed_out 0
    set deadline [expr {$t2 + [num $timeout_ms] * 1000}]
    while {1} {
        set compl [csr_rd $::CSR(COMPLETION)]
        incr polls
        if {$compl != $compl0} break
        if {[clock microseconds] > $deadline} { set timed_out 1; break }
    }
    set t3 [clock microseconds]
    set ctr_after [sample_counters]

    set irq  [csr_rd $::CSR(IRQ)]
    set diag [csr_rd $::CSR(DIAG)]

    # ---- result -------------------------------------------------------------
    set t4 [clock microseconds]
    set halves [num [cfg output_halves]]
    set out [master_read_16 [need_master] [mem_addr [num [cfg output_off]]] $halves]
    set t5 [clock microseconds]

    # CONTRACT WITH run_cifar10_jtag.py:parse_halves() -- one 4-hex-digit group
    # per FP16 word, carrying the NUMERIC value master_read_16 returned, not a
    # byte stream.  Keeping it word-oriented means neither side ever has to
    # agree about byte order; emitting bytes here instead would make the host's
    # float16 view byte-swapped and the logits silently wrong.
    set hexout {}
    foreach h $out { lappend hexout [format "%04x" [expr {$h & 0xffff}]] }

    csr_wr $::CSR(IRQ) $::IRQ_ALL             ;# W1C, ready for the next job

    set kv [list \
        "i=$idx" \
        "compl=$compl" \
        "polls=$polls" \
        "us_wr=$us_wr" \
        "us_q=[expr {$t2 - $t1}]" \
        "us_poll=[expr {$t3 - $t2}]" \
        "us_rd=[expr {$t5 - $t4}]" \
        "irq=[hex32 $irq]" \
        "diag=[hex32 $diag]" \
        "ctr_before=$ctr_before" \
        "ctr_after=$ctr_after" \
        "out=[join $hexout ""]"]

    if {$timed_out}                              { lappend kv "err=timeout" }
    if {[expr {$diag & $::DIAG_OUT_OF_INFERENCES}]} { lappend kv "err=eval_limit" }

    return [join $kv " "]
}

proc cmd_job {io_base timeout_ms} {
    set ::CFG(io_base) $io_base
    puts "RES [do_job -1 - $timeout_ms]"
    flush stdout
    ok 1
}

# Manifest: one line per image, "<index> <path>".  Running the loop inside Tcl
# is what keeps the host round trip off the per-image critical path.
proc cmd_batch {manifest first count timeout_ms} {
    set fh [open $manifest r]
    set lines [split [string trim [read $fh]] "\n"]
    close $fh

    set first [num $first]
    set count [num $count]
    if {$first + $count > [llength $lines]} {
        error "manifest has [llength $lines] entries, need [expr {$first+$count}]"
    }

    set done 0
    for {set n 0} {$n < $count} {incr n} {
        set fields [split [string trim [lindex $lines [expr {$first + $n}]]] " "]
        set idx  [lindex $fields 0]
        set path [lindex $fields 1]

        if {[catch {do_job $idx $path $timeout_ms} res]} {
            puts "RES i=$idx err=exception detail=[string map {" " "_"} $res]"
            flush stdout
            err "batch_aborted i=$idx done=$done"
            return
        }
        puts "RES $res"
        flush stdout
        incr done

        # A hard stop (eval limit or timeout) invalidates everything after it,
        # so surrender the batch immediately and let the host reprogram.
        if {[string match "*err=*" $res]} {
            err "batch_aborted i=$idx done=$done"
            return
        }
    }
    ok $done
}

# Measure achieved JTAG throughput before committing to a 10,000-image run.
# Every wall-time projection in the design doc is a function of this number, so
# it is measured, never assumed.
proc cmd_cal {nbytes file} {
    set n [num $nbytes]
    set addr [mem_addr 0]

    set fh [open $file wb]
    fconfigure $fh -translation binary
    puts -nonewline $fh [string repeat "\xa5" $n]
    close $fh

    set t0 [clock microseconds]
    master_write_from_file [need_master] $file $addr
    set t1 [clock microseconds]
    master_read_to_file [need_master] "$file.rb" $addr $n
    set t2 [clock microseconds]

    ok "wr_us=[expr {$t1 - $t0}]" "rd_us=[expr {$t2 - $t1}]" "bytes=$n"
}

# --- dispatch ----------------------------------------------------------------

proc dispatch {line} {
    set argv [split [string trim $line] " "]
    set cmd  [string toupper [lindex $argv 0]]
    set rest [lrange $argv 1 end]

    switch -- $cmd {
        PING    { ok pong $::VERSION }
        CFG     { cmd_cfg {*}$rest }
        OPEN    { cmd_open [expr {[llength $rest] ? [lindex $rest 0] : "auto"}] }
        CLOSE   { cmd_close }
        W32     { cmd_w32 {*}$rest }
        R32     { cmd_r32 {*}$rest }
        WBLK    { cmd_wblk {*}$rest }
        RBLK    { cmd_rblk {*}$rest }
        CSRINIT { cmd_csrinit }
        JOB     { cmd_job {*}$rest }
        BATCH   { cmd_batch {*}$rest }
        CAL     { cmd_cal {*}$rest }
        QUIT    { cmd_close_quiet; ok bye; exit 0 }
        ""      { }
        default { error "unknown command '$cmd'" }
    }
}

proc cmd_close_quiet {} {
    if {$::MASTER ne ""} { catch {close_service master $::MASTER} }
    set ::MASTER ""
}

# Announce readiness before the first read so the host never races the ~10 s
# System Console start-up.
puts "READY $VERSION"
flush stdout

while {[gets stdin line] >= 0} {
    if {[catch {dispatch $line} e]} {
        err [string map [list "\n" " | "] $e]
    }
}
cmd_close_quiet
exit 0
