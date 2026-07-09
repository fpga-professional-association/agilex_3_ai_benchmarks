// w957d8nb_bfm — behavioral Winbond W957D8NB HyperRAM model (issue #13, sim only).
//
// Test infrastructure only (moved here from sim/hyperbus/ during the CoreDLA-HyperRAM rename
// cleanup): pairs with sim/replay/hbmc_core.sv as the golden HyperBus device model used by
// sim/replay/tb_replay_integ.sv's integration TB; the production PH3 datapath
// (rtl/coredla_hyperram/) is instead verified against third_party/hyperram's own golden device
// model (third_party/hyperram/sim/model/hyperram_model.sv).
//
// Byte-per-beat protocol model (see hbmc_pkg): CA=6 beats, latency counted in beats, read data
// RWDS-strobed, writes RWDS-masked. Protocol-accurate, not AC-timing-accurate (that's the .sdc).
//
// Alignment: `cs_n` is driven combinationally by the controller; both sides derive `beat` from it
// with the identical `beat <= cs_n ? 0 : beat+1` register, so their beat counters are equal every
// cycle. Each side drives its bus outputs combinationally from the current beat and samples the
// resolved bus at the posedge (using the pre-increment beat), which keeps them in lockstep.
//
// Models memory + register spaces, fixed/variable latency (variable doubles on `collision`, signaled
// by driving RWDS during CA), and a mid-burst latency gap when a linear read crosses a row boundary.
`timescale 1ns/1ps
module w957d8nb_bfm
  import hbmc_pkg::*;
#(
    parameter int MEM_BYTES   = 65536,
    parameter int LAT_BEATS   = 6,     // base latency in beats (host programs the controller to match)
    parameter int ROW_BYTES   = 128,   // linear-burst row size (small, to exercise crossings)
    parameter int ROW_PENALTY = 4      // extra latency beats inserted at a row crossing
) (
    input  logic       clk,
    input  logic       cs_n,
    input  logic [7:0] dq_i,
    input  logic       rwds_i,
    output logic [7:0] dq_o,
    output logic       dq_oe,
    output logic       rwds_o,
    output logic       rwds_oe,
    input  logic       collision   // test-controlled: variable-latency refresh collision
);
  localparam int AW = $clog2(MEM_BYTES);

  // device register-space addresses (W957D8NB-style)
  localparam logic [31:0] REG_ID0 = 32'h0000_0000;
  localparam logic [31:0] REG_ID1 = 32'h0000_0001;
  localparam logic [31:0] REG_CR0 = 32'h0000_0800;
  localparam logic [31:0] REG_CR1 = 32'h0000_0801;

  logic [7:0]  mem [MEM_BYTES];
  logic [15:0] id0, id1, cr0, cr1;
  wire         cr_fixed = cr0[3];

  initial begin
    for (int i = 0; i < MEM_BYTES; i++) mem[i] = 8'((i * 13 + 7));
    id0 = 16'h0C81; id1 = 16'h0000;
    cr0 = 16'h0008;   // bit3 = 1 -> fixed latency by default
    cr1 = 16'h0000;
  end

  wire         busy = !cs_n;
  logic [15:0] beat;
  logic [47:0] ca_sr;
  logic        cur_rw, cur_as;
  logic [15:0] eff_lat;
  logic [31:0] data_addr;         // running byte address
  logic [15:0] data_idx;          // bytes transferred in the data phase
  logic [15:0] pen;               // row-crossing penalty countdown

  function automatic logic [15:0] calc_eff(input logic as_, input logic rw);
    if (as_ && !rw) return 16'd0;                              // register write: no latency
    if (cr_fixed)   return 16'(LAT_BEATS);
    return collision ? 16'(2 * LAT_BEATS) : 16'(LAT_BEATS);
  endfunction

  wire [47:0] ca_full      = {ca_sr[39:0], dq_i};              // valid when beat==5
  wire [31:0] ca_full_addr = ca_addr(ca_full);
  wire [31:0] reg_addr     = ca_addr(ca_sr);
  wire [15:0] reg_val   = (reg_addr == REG_ID0) ? id0 :
                          (reg_addr == REG_ID1) ? id1 :
                          (reg_addr == REG_CR0) ? cr0 : cr1;

  wire in_ca      = busy && (beat < 6);
  wire data_phase = busy && (beat >= 6 + eff_lat);

  always_ff @(posedge clk) begin
    beat <= busy ? (beat + 16'd1) : 16'd0;
    if (!busy) begin
      pen <= 16'd0; data_idx <= 16'd0; ca_sr <= '0;
    end else if (beat < 6) begin
      ca_sr <= {ca_sr[39:0], dq_i};
      if (beat == 5) begin
        cur_rw    <= ca_full[47];
        cur_as    <= ca_full[46];
        eff_lat   <= calc_eff(ca_full[46], ca_full[47]);
        data_addr <= {ca_full_addr[30:0], 1'b0};               // word addr -> byte addr (x2)
      end
    end else if (data_phase) begin
      if (cur_rw) begin                                        // READ
        if (pen != 0) begin
          pen <= pen - 16'd1;
        end else begin
          data_idx  <= data_idx + 16'd1;
          data_addr <= data_addr + 32'd1;
          if (!cur_as && (((data_addr + 1) % ROW_BYTES) == 0))
            pen <= 16'(ROW_PENALTY);
        end
      end else if (!rwds_i) begin                              // WRITE (rwds low = write byte)
        if (cur_as) begin
          if (data_idx == 0) begin
            if (reg_addr == REG_CR0) cr0[7:0]  <= dq_i; else cr1[7:0]  <= dq_i;
          end else begin
            if (reg_addr == REG_CR0) cr0[15:8] <= dq_i; else cr1[15:8] <= dq_i;
          end
        end else begin
          mem[data_addr[AW-1:0]] <= dq_i;
        end
        data_idx  <= data_idx + 16'd1;
        data_addr <= data_addr + 32'd1;
      end
    end
  end

  // ---- combinational bus drive ----
  always_comb begin
    dq_o = 8'h00; dq_oe = 1'b0; rwds_o = 1'b0; rwds_oe = 1'b0;
    if (in_ca) begin
      rwds_o  = 1'b1;
      rwds_oe = (!cr_fixed) && collision;                      // signal variable-latency doubling
    end
    if (data_phase && cur_rw) begin
      dq_oe = 1'b1; rwds_oe = 1'b1;
      if (pen != 0) begin
        rwds_o = 1'b0;                                         // gap: no strobe during row penalty
      end else begin
        rwds_o = 1'b1;
        dq_o   = cur_as ? (data_idx == 0 ? reg_val[7:0] : reg_val[15:8])
                        : mem[data_addr[AW-1:0]];
      end
    end
  end
endmodule
