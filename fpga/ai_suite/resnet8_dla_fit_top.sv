// Standalone synthesis harness for measuring the exact generated DLA RTL.
// It is not programmed onto the board; the final board top keeps HyperRAM idle.
module resnet8_dla_fit_top (
    input  wire         clk,
    input  wire         reset_n,

    input  wire         csr_arvalid,
    input  wire [10:0]  csr_araddr,
    output wire         csr_arready,
    output wire         csr_rvalid,
    output wire [31:0]  csr_rdata,
    input  wire         csr_rready,
    input  wire         csr_awvalid,
    input  wire [10:0]  csr_awaddr,
    output wire         csr_awready,
    input  wire         csr_wvalid,
    input  wire [31:0]  csr_wdata,
    output wire         csr_wready,
    output wire         csr_bvalid,
    input  wire         csr_bready,

    input  wire         input_valid,
    output wire         input_ready,
    input  wire [95:0]  input_data,
    output wire         output_valid,
    input  wire         output_ready,
    output wire         output_last,
    output wire [127:0] output_data,
    output wire [15:0]  output_strb,
    output wire         interrupt
);
    wire [31:0]  unused_ddr_araddr;
    wire [7:0]   unused_ddr_arlen;
    wire [2:0]   unused_ddr_arsize;
    wire [1:0]   unused_ddr_arburst;
    wire [1:0]   unused_ddr_arid;
    wire         unused_ddr_arvalid;
    wire         unused_ddr_rready;
    wire [31:0]  unused_ddr_awaddr;
    wire [7:0]   unused_ddr_awlen;
    wire [2:0]   unused_ddr_awsize;
    wire [1:0]   unused_ddr_awburst;
    wire [4:0]   unused_ddr_awid;
    wire         unused_ddr_awvalid;
    wire [255:0] unused_ddr_wdata;
    wire [31:0]  unused_ddr_wstrb;
    wire         unused_ddr_wlast;
    wire         unused_ddr_wvalid;
    wire         unused_ddr_bready;

    dla_top_wrapper_resnet8_agx3_logits_AGX3 dla (
        .ddr_clk(clk),
        .dla_clk(clk),
        .axi_clk(clk),
        .irq_clk(clk),
        .dla_resetn(reset_n),
        .o_interrupt_level(interrupt),

        .i_csr_arvalid(csr_arvalid),
        .i_csr_araddr(csr_araddr),
        .o_csr_arready(csr_arready),
        .o_csr_rvalid(csr_rvalid),
        .o_csr_rdata(csr_rdata),
        .i_csr_rready(csr_rready),
        .i_csr_awvalid(csr_awvalid),
        .i_csr_awaddr(csr_awaddr),
        .o_csr_awready(csr_awready),
        .i_csr_wvalid(csr_wvalid),
        .i_csr_wdata(csr_wdata),
        .o_csr_wready(csr_wready),
        .o_csr_bvalid(csr_bvalid),
        .i_csr_bready(csr_bready),

        .o_ddr_arvalid(unused_ddr_arvalid),
        .o_ddr_araddr(unused_ddr_araddr),
        .o_ddr_arlen(unused_ddr_arlen),
        .o_ddr_arsize(unused_ddr_arsize),
        .o_ddr_arburst(unused_ddr_arburst),
        .o_ddr_arid(unused_ddr_arid),
        .i_ddr_arready(1'b0),
        .i_ddr_rvalid(1'b0),
        .i_ddr_rdata(256'b0),
        .i_ddr_rid(2'b0),
        .o_ddr_rready(unused_ddr_rready),
        .o_ddr_awvalid(unused_ddr_awvalid),
        .o_ddr_awaddr(unused_ddr_awaddr),
        .o_ddr_awlen(unused_ddr_awlen),
        .o_ddr_awsize(unused_ddr_awsize),
        .o_ddr_awburst(unused_ddr_awburst),
        .o_ddr_awid(unused_ddr_awid),
        .i_ddr_awready(1'b0),
        .o_ddr_wvalid(unused_ddr_wvalid),
        .o_ddr_wdata(unused_ddr_wdata),
        .o_ddr_wstrb(unused_ddr_wstrb),
        .o_ddr_wlast(unused_ddr_wlast),
        .i_ddr_wready(1'b0),
        .i_ddr_bvalid(1'b0),
        .o_ddr_bready(unused_ddr_bready),

        .i_istream_axi_t_valid(input_valid),
        .o_istream_axi_t_ready(input_ready),
        .i_istream_axi_t_data(input_data),
        .o_ostream_axi_t_valid(output_valid),
        .i_ostream_axi_t_ready(output_ready),
        .o_ostream_axi_t_last(output_last),
        .o_ostream_axi_t_data(output_data),
        .o_ostream_axi_t_strb(output_strb)
    );
endmodule
