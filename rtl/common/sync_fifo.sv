// sync_fifo — single-clock show-ahead FIFO (issue #15).
// Used for the issue-timestamp queue that pairs issue strobes to retire events in the hot domain.
// Synchronous reset. `rd_data` shows the head whenever !empty; assert `rd_en` to pop.
`ifndef SYNC_FIFO_SV
`define SYNC_FIFO_SV
module sync_fifo #(
    parameter int WIDTH  = 32,
    parameter int ADDR_W = 4          // depth = 2**ADDR_W
) (
    input  logic              clk,
    input  logic              rst,    // synchronous
    input  logic              wr_en,
    input  logic [WIDTH-1:0]  wr_data,
    output logic              full,
    input  logic              rd_en,
    output logic [WIDTH-1:0]  rd_data,
    output logic              empty
);
  localparam int DEPTH = 1 << ADDR_W;

  logic [WIDTH-1:0] mem [DEPTH];
  logic [ADDR_W:0]  wr_ptr, rd_ptr;

  wire do_wr = wr_en && !full;
  wire do_rd = rd_en && !empty;

  assign empty   = (wr_ptr == rd_ptr);
  assign full    = (wr_ptr[ADDR_W-1:0] == rd_ptr[ADDR_W-1:0]) && (wr_ptr[ADDR_W] != rd_ptr[ADDR_W]);
  assign rd_data = mem[rd_ptr[ADDR_W-1:0]];

  always_ff @(posedge clk) begin
    if (rst) begin
      wr_ptr <= '0;
      rd_ptr <= '0;
    end else begin
      if (do_wr) wr_ptr <= wr_ptr + 1'b1;
      if (do_rd) rd_ptr <= rd_ptr + 1'b1;
    end
    if (do_wr) mem[wr_ptr[ADDR_W-1:0]] <= wr_data;
  end
endmodule
`endif
