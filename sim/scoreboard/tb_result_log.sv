// tb_result_log — self-checking test of result_log_writer (issue #15).
// Drives (index, predicted-class) entries and a simple Avalon-MM slave memory model with random
// waitrequest stalls; checks each predicted byte lands at LOG_BASE + index. Also checks the FIFO
// backpressure (in_ready) path by offering entries faster than the stalled slave accepts them.
`timescale 1ns/1ps
module tb_result_log;

  localparam int MEM_WORDS = 4096;
  localparam logic [31:0] LOG_BASE = 32'h0000_1000;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  logic        in_valid, in_ready;
  logic [31:0] in_index;
  logic [7:0]  in_class;
  logic [31:0] mst_address, mst_writedata;
  logic        mst_write;
  logic [3:0]  mst_byteenable;
  logic        mst_waitrequest;

  result_log_writer #(.FIFO_AW(4)) dut (
      .clk(clk), .rst(rst),
      .log_base(LOG_BASE),
      .in_valid(in_valid), .in_index(in_index), .in_class(in_class), .in_ready(in_ready),
      .mst_address(mst_address), .mst_write(mst_write), .mst_writedata(mst_writedata),
      .mst_byteenable(mst_byteenable), .mst_waitrequest(mst_waitrequest)
  );

  // ---- Avalon-MM slave: word memory with byteenable, random waitrequest ----
  logic [7:0] mem [0:MEM_WORDS*4-1];
  int errors = 0;

  // random-ish waitrequest
  logic [3:0] lfsr = 4'hB;
  always_ff @(posedge clk) lfsr <= {lfsr[2:0], lfsr[3]^lfsr[2]};
  assign mst_waitrequest = lfsr[0];      // stalls ~half the time

  always_ff @(posedge clk) begin
    if (mst_write && !mst_waitrequest) begin
      logic [31:0] word_byte_addr;
      word_byte_addr = {mst_address[31:2], 2'b00};
      for (int b = 0; b < 4; b++)
        if (mst_byteenable[b]) mem[word_byte_addr + b] <= mst_writedata[b*8 +: 8];
    end
  end

  // ---- golden shadow ----
  logic [7:0] gold [0:MEM_WORDS*4-1];

  task automatic send(input int idx, input int cls);
    // offer until accepted (honor in_ready backpressure)
    @(posedge clk);
    in_valid <= 1'b1; in_index <= idx; in_class <= cls[7:0];
    @(posedge clk);
    while (!in_ready) @(posedge clk);
    in_valid <= 1'b0;
    gold[LOG_BASE + idx] = cls[7:0];
  endtask

  int N = 300;
  initial begin
    rst = 1'b1; in_valid = 0; in_index = 0; in_class = 0;
    for (int i = 0; i < MEM_WORDS*4; i++) begin mem[i] = 8'hEE; gold[i] = 8'hEE; end
    repeat (4) @(posedge clk);
    rst = 1'b0;
    @(posedge clk);

    for (int i = 0; i < N; i++) send(i, $urandom_range(0, 255));

    // let the write FIFO drain fully
    repeat (200) @(posedge clk);

    for (int i = 0; i < N; i++) begin
      logic [31:0] a = LOG_BASE + i;
      if (mem[a] !== gold[a]) begin
        $display("FAIL: byte %0d addr %h mem=%h gold=%h", i, a, mem[a], gold[a]);
        errors++;
      end
    end

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #3_000_000; $display("FAIL: timeout"); $finish; end
endmodule
