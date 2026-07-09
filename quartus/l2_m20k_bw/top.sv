// top — AXC3000 board top level for the L2 aggregate-M20K-bandwidth microbench (issue #12,
// PLAN §7 L2 + §3 LV3). Agilex-3 A3CY100BM16AE7S.
//
// This harness is an ON-CHIP-ONLY benchmark: NO external memory, NO HyperBus pins. It is
// deliberately simpler than fpga/axc3000/top.sv (the HyperBus bandwidth-test template this
// mirrors): one clock domain, no clk2x, no I/O periphery beyond the JTAG-Avalon master + 3 LEDs.
//
// Data path:
//   l2_sys (Qsys)                                quartus/l2_m20k_bw/qsys/
//     * IOPLL: clk (~300 MHz, 0deg) from the 25 MHz board XO — PLAN §4's "M20K on-chip ...
//       ~330 GB/s aggregate @300 MHz" operating point.
//     * reset controller -> synchronous active-high fabric reset (clk domain)
//     * JTAG-to-Avalon-MM master bridge --- exported Avalon-MM master (byte addressed)
//         |
//         v
//   m20k_bw (NUM_BANKS parallel M20K readers + checksum sinks, CSR slave)
//
// m20k_bw's CSR slave is ALREADY byte-addressed (m20k_bw_pkg::L2_ADDR_* are byte offsets 0x00,
// 0x04, .., 0x20 compared directly against the 8-bit csr_address port — see m20k_bw.sv), so the
// byte-addressed JTAG-Avalon master's low address bits wire straight to csr_address with no
// word-shift adapter (unlike fpga/axc3000/top.sv's hyperram_bw_test, whose CSR slave IS
// word-addressed and needs one).
//
// GEOMETRY/OUTPUT_REG select the hardware config (issue #12 step 3/4's three named configs):
//   a) BANKED  + OUTPUT_REG=1   (default — PLAN §3 LV3 "good" geometry)
//   b) SHARED  + OUTPUT_REG=1   (round-robin anti-pattern)
//   c) BANKED  + OUTPUT_REG=0   (output-registers-off fmax-cost geometry)
// Selected at synthesis time via the L2_GEOMETRY / L2_OUTPUT_REG top parameters, set in
// l2_m20k_bw.qsf with `set_parameter -name L2_GEOMETRY ...` / `set_parameter -name
// L2_OUTPUT_REG ...` (one .qsf/config per config, same convention as quartus/l0_tensor_chain's
// l0_tensor_chain_n*.qsf variants — see README.md's config matrix).
//
// Read back with sysconsole/l2_read.tcl.
//
// Board signal names below match quartus/constraints/axc3000_board.tcl / pins.tcl (copied from the
// same vetted source as fpga/axc3000/pins.tcl).

`timescale 1ns/1ps

module top
  import m20k_bw_pkg::L2_ADDR_STATUS;
#(
    parameter bit L2_GEOMETRY   = 1'b0,   // m20k_bw_pkg::GEOM_BANKED (0) / GEOM_SHARED (1)
    parameter bit L2_OUTPUT_REG = 1'b1
) (
    // ---- board clock / reset ----
    input  wire CLK_25M_C,   // 25 MHz fixed XO (PIN_A7, 1.2 V)
    input  wire USER_BTN,    // S2, active-low, weak pull-up (PIN_A12, 1.2 V)

    // ---- user LEDs (active-low, 3.3-V LVCMOS) — quick visual STATUS ----
    output wire LED1,        // STATUS.done  (lit = done)
    output wire RLED,        // tied off: m20k_bw has no error/fault status to report
    output wire GLED         // PLL locked   (lit = locked)
);

  localparam int NUM_BANKS  = 32;
  localparam int DATA_WIDTH = 32;
  localparam int ADDR_WIDTH = 9;

  // =========================================================================
  // Qsys backbone: clock, reset, JTAG-Avalon master
  // =========================================================================
  wire        clk;          // ~300 MHz fabric clock (single domain: controller + M20K banks)
  wire        pll_locked;
  wire        sys_rst;      // synchronous, active-high fabric reset (clk domain)

  // Exported (byte-addressed) Avalon-MM master from the JTAG bridge
  wire [31:0] m_address;
  wire [31:0] m_readdata;
  wire        m_read;
  wire        m_write;
  wire [31:0] m_writedata;
  wire        m_waitrequest;
  wire        m_readdatavalid;
  wire [3:0]  m_byteenable;

  l2_sys u_sys (
    .clk_25_clk           (CLK_25M_C),
    .reset_reset          (~USER_BTN),      // button pressed (low) => assert active-high reset
    .clk_clk              (clk),
    .locked_export        (pll_locked),
    .sys_reset_reset      (sys_rst),
    .master_address       (m_address),
    .master_readdata      (m_readdata),
    .master_read          (m_read),
    .master_write         (m_write),
    .master_writedata     (m_writedata),
    .master_waitrequest   (m_waitrequest),
    .master_readdatavalid (m_readdatavalid),
    .master_byteenable    (m_byteenable)
  );

  // =========================================================================
  // Byte-address (JTAG master) -> m20k_bw CSR slave (already byte-addressed, see header comment).
  // m20k_bw reads combinationally and ties waitrequest low; the JTAG-Avalon master is pipelined
  // (expects readdatavalid) and single-outstanding, so a registered readdata/valid pipe (read-
  // latency-1 contract) is the only adapter needed — same idiom as fpga/axc3000/top.sv.
  // =========================================================================
  wire [7:0]  csr_address   = m_address[7:0];
  wire        csr_read      = m_read;
  wire        csr_write     = m_write;
  wire [31:0] csr_writedata = m_writedata;
  wire [31:0] csr_readdata;

  assign m_waitrequest = 1'b0;   // m20k_bw's CSR slave has no wait states

  logic [31:0] rd_hold;
  logic        rdv_q;
  always_ff @(posedge clk) begin
    if (sys_rst) begin
      rd_hold <= 32'd0;
      rdv_q   <= 1'b0;
    end else begin
      rd_hold <= csr_readdata;   // m20k_bw reads combinationally off the address
      rdv_q   <= m_read;        // accepted this cycle (waitrequest is always 0)
    end
  end
  assign m_readdata      = rd_hold;
  assign m_readdatavalid = rdv_q;

  m20k_bw #(
      .NUM_BANKS  (NUM_BANKS),
      .DATA_WIDTH (DATA_WIDTH),
      .ADDR_WIDTH (ADDR_WIDTH),
      .GEOMETRY   (L2_GEOMETRY),
      .OUTPUT_REG (L2_OUTPUT_REG)
  ) u_l2 (
      .clk           (clk),
      .rst           (sys_rst),
      .csr_address   (csr_address),
      .csr_read      (csr_read),
      .csr_readdata  (csr_readdata),
      .csr_write     (csr_write),
      .csr_writedata (csr_writedata)
  );

  // =========================================================================
  // LED status snoop — latch STATUS.DONE whenever the host polls STATUS (m20k_bw_pkg::L2_ADDR_STATUS).
  // =========================================================================
  logic led_done_q;
  always_ff @(posedge clk) begin
    if (sys_rst) begin
      led_done_q <= 1'b0;
    end else if (csr_read && (csr_address == L2_ADDR_STATUS)) begin
      led_done_q <= csr_readdata[1];   // STATUS bit1 = DONE
    end
  end

  assign LED1 = ~led_done_q;         // active-low: lit when done
  assign RLED = 1'b1;                // active-low: always off — m20k_bw has no error status
  assign GLED = ~pll_locked;         // active-low: lit while PLL locked

endmodule
