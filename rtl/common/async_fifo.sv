// async_fifo — dual-clock FIFO with Gray-code pointers (issue #15).
// The only sanctioned primitive for multi-bit CDC (AGENTS.md). Classic Cummings structure:
// binary+Gray pointers, each synchronized into the opposite domain through 2 flops, with `full` and
// `empty` REGISTERED — the pointer-next equations use the registered flags, which breaks the
// combinational loop that arises when a producer gates its write with `full` (as sb_frontend does).
// Synchronous resets in each domain reset that domain's pointers/flag; assert both only while the
// FIFO is quiescent (our clear happens at run boundaries with no traffic).
`ifndef ASYNC_FIFO_SV
`define ASYNC_FIFO_SV
module async_fifo #(
    parameter int WIDTH  = 32,
    parameter int ADDR_W = 4          // depth = 2**ADDR_W
) (
    // write domain
    input  logic              wr_clk,
    input  logic              wr_rst,
    input  logic              wr_en,
    input  logic [WIDTH-1:0]  wr_data,
    output logic              full,
    // read domain
    input  logic              rd_clk,
    input  logic              rd_rst,
    input  logic              rd_en,
    output logic [WIDTH-1:0]  rd_data,
    output logic              empty
);
  localparam int DEPTH = 1 << ADDR_W;

  logic [WIDTH-1:0] mem [DEPTH];

  logic [ADDR_W:0] wr_bin, wr_gray;
  logic [ADDR_W:0] rd_bin, rd_gray;
  logic [ADDR_W:0] rd_gray_wsync1, rd_gray_wsync2; // rd_gray into wr domain
  logic [ADDR_W:0] wr_gray_rsync1, wr_gray_rsync2; // wr_gray into rd domain

  function automatic [ADDR_W:0] bin2gray(input logic [ADDR_W:0] b);
    return b ^ (b >> 1);
  endfunction

  // ---------------- write domain ----------------
  wire do_wr = wr_en && !full;
  wire [ADDR_W:0] wr_bin_next  = wr_bin + (ADDR_W+1)'(do_wr);
  wire [ADDR_W:0] wr_gray_next = bin2gray(wr_bin_next);
  // full when the next write Gray equals the read Gray with the top two bits inverted
  wire full_val = (wr_gray_next == {~rd_gray_wsync2[ADDR_W:ADDR_W-1], rd_gray_wsync2[ADDR_W-2:0]});

  always_ff @(posedge wr_clk) begin
    if (wr_rst) begin
      wr_bin  <= '0;
      wr_gray <= '0;
      full    <= 1'b0;
    end else begin
      wr_bin  <= wr_bin_next;
      wr_gray <= wr_gray_next;
      full    <= full_val;
    end
    if (do_wr) mem[wr_bin[ADDR_W-1:0]] <= wr_data;
  end

  always_ff @(posedge wr_clk) begin
    if (wr_rst) {rd_gray_wsync2, rd_gray_wsync1} <= '0;
    else        {rd_gray_wsync2, rd_gray_wsync1} <= {rd_gray_wsync1, rd_gray};
  end

  // ---------------- read domain ----------------
  wire do_rd = rd_en && !empty;
  wire [ADDR_W:0] rd_bin_next  = rd_bin + (ADDR_W+1)'(do_rd);
  wire [ADDR_W:0] rd_gray_next = bin2gray(rd_bin_next);
  wire empty_val = (rd_gray_next == wr_gray_rsync2);

  always_ff @(posedge rd_clk) begin
    if (rd_rst) begin
      rd_bin  <= '0;
      rd_gray <= '0;
      empty   <= 1'b1;
    end else begin
      rd_bin  <= rd_bin_next;
      rd_gray <= rd_gray_next;
      empty   <= empty_val;
    end
  end

  always_ff @(posedge rd_clk) begin
    if (rd_rst) {wr_gray_rsync2, wr_gray_rsync1} <= '0;
    else        {wr_gray_rsync2, wr_gray_rsync1} <= {wr_gray_rsync1, wr_gray};
  end

  assign rd_data = mem[rd_bin[ADDR_W-1:0]];
endmodule
`endif
