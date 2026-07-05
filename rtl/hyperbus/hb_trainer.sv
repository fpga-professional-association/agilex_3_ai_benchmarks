// hb_trainer — HyperBus capture-delay training (issue #14, PLAN §3 LV6 / §7 L3).
//
// Sweeps the PHY capture-delay tap (hbmc_core CSR_CAPDELAY) across [0, DELAY_TAPS-1], writing then
// read-verifying a known fixed word pattern at TEST_ADDR for every tap, and records a pass/fail bit
// per tap. After the sweep it finds the widest contiguous run of passing taps, exposes the window
// edges/width/center via its own CSR block, and (if the window meets MIN_WINDOW) leaves hbmc_core's
// CAPDELAY parked at the computed center — the "set center" step in the issue body.
//
// This module is a direct Avalon-MM + CSR MASTER onto hbmc_core (rtl/hyperbus/hbmc_core.sv): it
// drives hbmc_core.csr_*/av_* exactly like a JTAG-Avalon host would, but autonomously runs the whole
// sweep so it isn't gated by per-tap round trips over JTAG. In a full Platform Designer system the
// host's own JTAG-Avalon master and this module both target hbmc_core's single Avalon/CSR slave
// ports; Platform Designer's generated interconnect arbitrates multiple masters onto one slave
// automatically (standard Avalon-MM system-integration behavior, not hand-rolled here) — the
// operational contract is simply that the host does not drive hbmc_core directly while
// t_csr STATUS.BUSY is set.
//
// DELAY_TAPS=32 is a placeholder for the real Agilex DDR-IO delay-chain tap count, which is fixed
// once the PHY (docs/hyperbus.md "Hardware handoff") is implemented during board bring-up; override
// the parameter once that number is known.
//
// Simulation honesty note: hbmc_core's BFM (sim/hyperbus/w957d8nb_bfm.sv) is a byte-per-beat
// protocol model with NO analog/AC-timing behavior, so nothing about hb_capture_delay actually
// changes what a simulated read returns — real bit errors here come from RWDS/DQ skew that only
// exists on real silicon (docs/hyperbus.md). sim/hyperbus/tb_hb_trainer.sv adds a TESTBENCH-ONLY
// synthetic error injector keyed off hb_capture_delay so the window-search ALGORITHM below can be
// exercised deterministically under Verilator; it is not a timing model and must not be read as one.
`ifndef HB_TRAINER_SV
`define HB_TRAINER_SV
module hb_trainer #(
    parameter int DELAY_TAPS = 32,             // capture-delay taps to sweep: [0, DELAY_TAPS-1]
    parameter int MIN_WINDOW = 2,              // issue #14 step 1: window must be >= this many taps
    parameter int TEST_WORDS = 8,               // known-pattern length written/verified per tap
    parameter logic [22:0] TEST_ADDR = 23'h0    // HyperRAM scratch word address used for training
) (
    input  logic clk,
    input  logic rst,

    // ---- host-facing trainer CSR slave (own register block, docs/hyperbus.md #14 addendum) ----
    input  logic [4:0]  t_csr_address,
    input  logic        t_csr_read,
    output logic [31:0] t_csr_readdata,
    input  logic        t_csr_write,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [31:0] t_csr_writedata,  // only bit0 (CTRL.START) is meaningful; rest reserved
    /* verilator lint_on UNUSEDSIGNAL */

    // ---- CSR master onto hbmc_core (only active CSR_CAPDELAY writes during a sweep) ----
    output logic [5:0]  csr_address,
    output logic        csr_write,
    output logic [31:0] csr_writedata,

    // ---- Avalon-MM master onto hbmc_core's data slave ----
    output logic [22:0] av_address,
    output logic [7:0]  av_burstcount,
    output logic        av_read,
    output logic        av_write,
    output logic [15:0] av_writedata,
    input  logic [15:0] av_readdata,
    input  logic        av_readdatavalid,
    input  logic        av_waitrequest
);
  localparam int TAP_W  = (DELAY_TAPS <= 1) ? 1 : $clog2(DELAY_TAPS);
  localparam int WORD_W = $clog2(TEST_WORDS + 1);

  // ---- trainer CSR map ----
  localparam logic [4:0] T_CTRL       = 5'h00;  // bit0 START (self-clearing)
  localparam logic [4:0] T_STATUS     = 5'h04;  // bit0 BUSY, bit1 DONE, bit2 WINDOW_VALID
  localparam logic [4:0] T_WIN_LO     = 5'h08;  // RO
  localparam logic [4:0] T_WIN_HI     = 5'h0C;  // RO
  localparam logic [4:0] T_WIN_WIDTH  = 5'h10;  // RO
  localparam logic [4:0] T_WIN_CENTER = 5'h14;  // RO — also the value parked in hbmc_core CAPDELAY
  localparam logic [4:0] T_NUM_TAPS   = 5'h18;  // RO, = DELAY_TAPS
  localparam logic [4:0] T_LAST_TAP   = 5'h1C;  // RO — current/last tap the sweep processed

  function automatic logic [15:0] known_pattern(input logic [WORD_W-1:0] idx);
    return 16'hC300 ^ {8'(idx), ~8'(idx)};
  endfunction

  typedef enum logic [3:0] {
    IDLE, SET_DELAY, SET_DELAY_WAIT, WBODY, RD_WAIT, RBODY,
    TAP_DONE, COMPUTE, SET_CENTER, SET_CENTER_WAIT, DONE
  } state_t;
  state_t st;

  logic [TAP_W-1:0]  tap;
  logic [DELAY_TAPS-1:0] tap_pass;
  logic               tap_ok;
  logic [WORD_W-1:0]  widx;

  logic [TAP_W-1:0]  win_lo, win_hi, win_center;
  logic [TAP_W:0]    win_width;
  logic               window_valid;

  logic [TAP_W:0]    scan_i;
  logic               run_active;
  logic [TAP_W-1:0]  run_start;
  logic [TAP_W:0]    run_len;
  logic [TAP_W-1:0]  best_lo, best_hi;
  logic [TAP_W:0]    best_width;

  wire busy = (st != IDLE) && (st != DONE);
  wire done = (st == DONE);

  // ---- trainer CSR read ----
  always_comb begin
    t_csr_readdata = 32'd0;
    if (t_csr_read) begin
      unique case (t_csr_address)
        T_STATUS:     t_csr_readdata = {29'd0, window_valid, done, busy};
        T_WIN_LO:     t_csr_readdata = {{(32 - TAP_W){1'b0}}, win_lo};
        T_WIN_HI:     t_csr_readdata = {{(32 - TAP_W){1'b0}}, win_hi};
        T_WIN_WIDTH:  t_csr_readdata = {{(31 - TAP_W){1'b0}}, win_width};
        T_WIN_CENTER: t_csr_readdata = {{(32 - TAP_W){1'b0}}, win_center};
        T_NUM_TAPS:   t_csr_readdata = 32'(DELAY_TAPS);
        T_LAST_TAP:   t_csr_readdata = {{(32 - TAP_W){1'b0}}, tap};
        default:      t_csr_readdata = 32'hDEAD_C0DE;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE;
      tap <= '0; tap_pass <= '0; tap_ok <= 1'b1; widx <= '0;
      win_lo <= '0; win_hi <= '0; win_center <= '0; win_width <= '0; window_valid <= 1'b0;
      scan_i <= '0; run_active <= 1'b0; run_start <= '0; run_len <= '0;
      best_lo <= '0; best_hi <= '0; best_width <= '0;
      csr_address <= '0; csr_write <= 1'b0; csr_writedata <= '0;
      av_address <= '0; av_burstcount <= '0; av_read <= 1'b0; av_write <= 1'b0; av_writedata <= '0;
    end else begin
      csr_write <= 1'b0;  // single-cycle CSR write pulses; default low each cycle

      if (t_csr_write && t_csr_address == T_CTRL && t_csr_writedata[0] && !busy) begin
        tap <= '0; tap_pass <= '0; tap_ok <= 1'b1; window_valid <= 1'b0;
        st <= SET_DELAY;
      end

      unique case (st)
        IDLE, DONE: ;

        SET_DELAY: begin
          csr_address <= hyperbus_pkg::CSR_CAPDELAY; csr_writedata <= {24'd0, {(8 - TAP_W){1'b0}}, tap};
          csr_write <= 1'b1;
          st <= SET_DELAY_WAIT;
        end

        SET_DELAY_WAIT: begin
          av_write <= 1'b1; av_address <= TEST_ADDR; av_burstcount <= TEST_WORDS[7:0];
          av_writedata <= known_pattern('0);
          widx <= '0;
          st <= WBODY;
        end

        WBODY: begin
          if (!av_waitrequest) begin
            if (widx + 1'b1 == WORD_W'(TEST_WORDS)) begin
              av_write <= 1'b0;
              av_read  <= 1'b1; av_address <= TEST_ADDR; av_burstcount <= TEST_WORDS[7:0];
              widx <= '0;
              st <= RD_WAIT;
            end else begin
              av_writedata <= known_pattern(widx + 1'b1);
              widx <= widx + 1'b1;
            end
          end
        end

        RD_WAIT: begin
          if (av_read && !av_waitrequest) av_read <= 1'b0;
          if (av_readdatavalid) begin
            if (av_readdata != known_pattern(widx)) tap_ok <= 1'b0;
            if (widx + 1'b1 == WORD_W'(TEST_WORDS)) st <= TAP_DONE;
            else st <= RBODY;
            widx <= widx + 1'b1;
          end
        end

        RBODY: begin
          if (av_readdatavalid) begin
            if (av_readdata != known_pattern(widx)) tap_ok <= 1'b0;
            if (widx + 1'b1 == WORD_W'(TEST_WORDS)) st <= TAP_DONE;
            widx <= widx + 1'b1;
          end
        end

        TAP_DONE: begin
          tap_pass[tap] <= tap_ok;
          tap_ok <= 1'b1;
          if (tap == TAP_W'(DELAY_TAPS - 1)) begin
            scan_i <= '0; run_active <= 1'b0; run_len <= '0;
            best_width <= '0; best_lo <= '0; best_hi <= '0;
            st <= COMPUTE;
          end else begin
            tap <= tap + 1'b1;
            st <= SET_DELAY;
          end
        end

        COMPUTE: begin
          if (scan_i == (TAP_W + 1)'(DELAY_TAPS)) begin
            win_lo <= best_lo; win_hi <= best_hi; win_width <= best_width;
            win_center <= best_lo + TAP_W'(best_width >> 1);
            window_valid <= (best_width >= (TAP_W + 1)'(MIN_WINDOW));
            st <= SET_CENTER;
          end else begin
            if (tap_pass[scan_i[TAP_W-1:0]]) begin
              if (!run_active) begin
                run_active <= 1'b1; run_start <= scan_i[TAP_W-1:0]; run_len <= (TAP_W+1)'(1);
                if ((TAP_W+1)'(1) > best_width) begin
                  best_width <= (TAP_W+1)'(1); best_lo <= scan_i[TAP_W-1:0]; best_hi <= scan_i[TAP_W-1:0];
                end
              end else begin
                run_len <= run_len + 1'b1;
                if (run_len + 1'b1 > best_width) begin
                  best_width <= run_len + 1'b1; best_lo <= run_start; best_hi <= scan_i[TAP_W-1:0];
                end
              end
            end else begin
              run_active <= 1'b0; run_len <= '0;
            end
            scan_i <= scan_i + 1'b1;
          end
        end

        SET_CENTER: begin
          csr_address <= hyperbus_pkg::CSR_CAPDELAY; csr_writedata <= {24'd0, {(8 - TAP_W){1'b0}}, win_center};
          csr_write <= 1'b1;
          st <= SET_CENTER_WAIT;
        end

        SET_CENTER_WAIT: st <= DONE;

        default: st <= IDLE;
      endcase
    end
  end
endmodule
`endif
