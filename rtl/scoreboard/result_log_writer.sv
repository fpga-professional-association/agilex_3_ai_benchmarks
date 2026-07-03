// result_log_writer — optional per-record prediction log (issue #15).
//
// Writes one byte (the predicted class) per retired inference into the reserved top-64 KB of
// HyperRAM, at LOG_BASE + record_index. The accuracy parity gate (#21) reads this back and compares
// per record against the OpenVINO CPU-INT8 reference. Instantiated behind a generate/ENABLE guard so
// designs that don't need it pay nothing.
//
// 32-bit Avalon-MM write master with byteenable so each transaction lands exactly one byte
// (byte-per-record layout, matching docs/record_format.md's result-log reserve). A small FIFO
// absorbs retire bursts; this path is off the timed measurement loop.
`ifndef RESULT_LOG_WRITER_SV
`define RESULT_LOG_WRITER_SV
module result_log_writer #(
    parameter int FIFO_AW = 4          // absorb burst depth = 2**FIFO_AW
) (
    input  logic        clk,
    input  logic        rst,

    input  logic [31:0] log_base,      // byte base address of the result log
    // one entry per retired inference (in order): predicted class for record `index`
    input  logic        in_valid,
    input  logic [31:0] in_index,
    input  logic [7:0]  in_class,
    output logic        in_ready,      // deasserts if the absorb FIFO is full

    // Avalon-MM write master
    output logic [31:0] mst_address,   // byte address, word-aligned (low 2 bits 0)
    output logic        mst_write,
    output logic [31:0] mst_writedata,
    output logic [3:0]  mst_byteenable,
    input  logic        mst_waitrequest
);
  localparam int EW = 32 + 8;   // {index, class}

  logic [EW-1:0] fifo_wr, fifo_rd;
  logic          fifo_full, fifo_empty, fifo_pop;
  assign fifo_wr  = {in_index, in_class};
  assign in_ready = !fifo_full;

  sync_fifo #(.WIDTH(EW), .ADDR_W(FIFO_AW)) u_fifo (
      .clk(clk), .rst(rst),
      .wr_en(in_valid && !fifo_full), .wr_data(fifo_wr), .full(fifo_full),
      .rd_en(fifo_pop), .rd_data(fifo_rd), .empty(fifo_empty)
  );

  wire [31:0] q_index = fifo_rd[39:8];
  wire [7:0]  q_class = fifo_rd[7:0];
  wire [31:0] byte_addr = log_base + q_index;
  wire [1:0]  lane      = byte_addr[1:0];

  // present a write whenever the FIFO has an entry; hold until accepted (!waitrequest)
  assign mst_write      = !fifo_empty;
  assign mst_address    = {byte_addr[31:2], 2'b00};
  assign mst_byteenable = 4'b0001 << lane;
  assign mst_writedata  = {24'd0, q_class} << (8 * lane);
  assign fifo_pop       = mst_write && !mst_waitrequest;
endmodule
`endif
