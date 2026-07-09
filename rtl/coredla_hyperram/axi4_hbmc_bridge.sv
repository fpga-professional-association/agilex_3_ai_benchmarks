// axi4_hbmc_bridge — CoreDLA AXI4 "DDR" master -> HyperRAM Avalon-MM adapter (PH3).
//
// Lives in rtl/coredla_hyperram/ (moved from rtl/hyperbus/ during the CoreDLA-HyperRAM rename
// cleanup) alongside axc3000_hyperram_axi4.sv/axc3000_hyperram_pads.sv, the modules that wire it
// into the production PH3 datapath onto the third_party/hyperram submodule's hyperram_avalon.
//
// Front-end: reduced AXI4 slave matching the FPGA AI Suite CoreDLA DDR port (DATA=256, ADDR=32,
// WRITE_ID=5, READ_ID=2; AxSIZE const 3'd5, AxBURST const INCR, AxLEN<=15). Back-end: a generic
// 16-bit-word, word-addressed, linear-burst Avalon-MM master — driving hyperram_avalon in
// production (axc3000_hyperram_axi4.sv), and driving the golden sim/replay/hbmc_core.sv model in
// this module's own standalone regression history. See docs/ph3_bridge_design.md for the full
// contract and FSM; docs/ph3_interfaces.md for provenance.
//
// v1 (documented in the design doc): single clock (no CDC), full-width writes only (partial WSTRB is
// DETECTED via wstrb_partial_seen, not read-modify-written), serialized/one-outstanding, one 16-word
// hbmc burst per 256-bit AXI beat (no CA amortization). This is a datapath proof, not a HW result.
//
// RTL discipline (AGENTS.md / PLAN §3 LV1): synchronous reset only for architectural state (FSM,
// counters, sticky status, running address); the beat data buffers and latched AXI attributes are
// reset-less datapath registers (always written before read). No async reset, no clock gating.
`ifndef AXI4_HBMC_BRIDGE_SV
`define AXI4_HBMC_BRIDGE_SV
module axi4_hbmc_bridge #(
    parameter int DATA_W    = 256,   // CoreDLA AXI data width (bits)
    parameter int ADDR_W    = 32,    // CoreDLA AXI byte-address width
    parameter int WID_W     = 5,     // AXI write-ID width (awid/bid)
    parameter int RID_W     = 2,     // AXI read-ID width (arid/rid)
    parameter int LEN_W     = 8,     // AXI AxLEN width (AXI4)
    parameter int HB_ADDR_W = 23,    // hbmc word-address width
    parameter int HB_BURST_W = 8     // hbmc av_burstcount width
) (
    input  logic                 clk,
    input  logic                 rst,

    // ---- AXI4 slave: write address (AW) ----
    input  logic [WID_W-1:0]     awid,
    input  logic [ADDR_W-1:0]    awaddr,
    input  logic [LEN_W-1:0]     awlen,
    input  logic [2:0]           awsize,     // const 3'd5 per contract (accepted, unused)
    input  logic [1:0]           awburst,    // const INCR per contract (accepted, unused)
    input  logic                 awvalid,
    output logic                 awready,

    // ---- AXI4 slave: write data (W) ----
    input  logic [DATA_W-1:0]    wdata,
    input  logic [DATA_W/8-1:0]  wstrb,
    input  logic                 wlast,      // driven by master; bridge counts beats (unused)
    input  logic                 wvalid,
    output logic                 wready,

    // ---- AXI4 slave: write response (B) ----
    output logic [WID_W-1:0]     bid,
    output logic [1:0]           bresp,
    output logic                 bvalid,
    input  logic                 bready,

    // ---- AXI4 slave: read address (AR) ----
    input  logic [RID_W-1:0]     arid,
    input  logic [ADDR_W-1:0]    araddr,
    input  logic [LEN_W-1:0]     arlen,
    input  logic [2:0]           arsize,     // const 3'd5 per contract (accepted, unused)
    input  logic [1:0]           arburst,    // const INCR per contract (accepted, unused)
    input  logic                 arvalid,
    output logic                 arready,

    // ---- AXI4 slave: read data (R) ----
    output logic [RID_W-1:0]     rid,
    output logic [DATA_W-1:0]    rdata,
    output logic [1:0]           rresp,
    output logic                 rlast,
    output logic                 rvalid,
    input  logic                 rready,

    // ---- Avalon-MM master to hbmc_core (16-bit data path) ----
    output logic [HB_ADDR_W-1:0] av_address,
    output logic [HB_BURST_W-1:0] av_burstcount,
    output logic                 av_read,
    output logic                 av_write,
    output logic [15:0]          av_writedata,
    input  logic [15:0]          av_readdata,
    input  logic                 av_readdatavalid,
    input  logic                 av_waitrequest,

    // ---- sticky status (integration hooks; see docs/ph3_bridge_design.md) ----
    output logic                 wstrb_partial_seen,  // a non-all-ones WSTRB was seen (RMW needed!)
    output logic                 hi_addr_seen         // an address above the 16 MB window was seen
);
  // ---- derived widths / constants ----
  localparam int WORDS_PER_BEAT = DATA_W / 16;                 // 256/16 = 16 hbmc words per AXI beat
  localparam int IDX_W          = $clog2(WORDS_PER_BEAT);      // 4-bit word index within a beat
  localparam int WSTRB_W        = DATA_W / 8;                  // 32
  localparam int DEC_ADDR_W     = HB_ADDR_W + 1;               // 24 decoded byte-address bits (16 MB)
  localparam logic [1:0] RESP_OKAY = 2'b00;

  // ---- FSM ----
  typedef enum logic [2:0] {
    S_IDLE,        // wait for AW (write priority) or AR
    S_W_DATA,      // accept a W beat (256b) into wbeat
    S_W_XFER,      // drain wbeat as 16 hbmc words
    S_W_RESP,      // emit B
    S_R_XFER,      // issue hbmc read command
    S_R_COLLECT,   // gather 16 hbmc words into rbeat
    S_R_RESP       // emit R
  } state_t;
  state_t state;

  // ---- architectural state (synchronous reset) ----
  logic [HB_ADDR_W-1:0] word_addr;      // running hbmc word address
  logic [LEN_W-1:0]     beat_cnt;       // beats emitted/consumed in the current AXI burst
  logic [IDX_W-1:0]     wword_idx;      // word within the current write beat (0..15)
  logic [IDX_W-1:0]     rword_idx;      // word within the current read beat (0..15)

  // ---- datapath (reset-less: written before read) ----
  logic [DATA_W-1:0]    wbeat;          // latched AXI write beat
  logic [DATA_W-1:0]    rbeat;          // assembled AXI read beat
  logic [WID_W-1:0]     awid_r;         // latched write ID -> bid
  logic [RID_W-1:0]     arid_r;         // latched read ID  -> rid (echo, required)
  logic [LEN_W-1:0]     awlen_r;        // latched write burst length
  logic [LEN_W-1:0]     arlen_r;        // latched read burst length

  wire last_word = (wword_idx == IDX_W'(WORDS_PER_BEAT-1));
  wire last_rword = (rword_idx == IDX_W'(WORDS_PER_BEAT-1));

  // ---- AXI handshake / response outputs (combinational from state) ----
  // Write priority: whenever AW is valid in IDLE we take the write; AR waits.
  assign awready = (state == S_IDLE) && awvalid;
  assign arready = (state == S_IDLE) && !awvalid && arvalid;
  assign wready  = (state == S_W_DATA);

  assign bvalid  = (state == S_W_RESP);
  assign bid     = awid_r;
  assign bresp   = RESP_OKAY;

  assign rvalid  = (state == S_R_RESP);
  assign rid     = arid_r;
  assign rdata   = rbeat;
  assign rresp   = RESP_OKAY;
  assign rlast   = (beat_cnt == arlen_r);

  // ---- Avalon-MM master outputs (combinational from state + datapath) ----
  assign av_burstcount = HB_BURST_W'(WORDS_PER_BEAT);
  assign av_address    = word_addr;
  assign av_read       = (state == S_R_XFER);
  assign av_write      = (state == S_W_XFER);
  assign av_writedata  = wbeat[16*wword_idx +: 16];

  // ---- sequential FSM ----
  always_ff @(posedge clk) begin
    if (rst) begin
      state              <= S_IDLE;
      word_addr          <= '0;
      beat_cnt           <= '0;
      wword_idx          <= '0;
      rword_idx          <= '0;
      wstrb_partial_seen <= 1'b0;
      hi_addr_seen       <= 1'b0;
    end else begin
      unique case (state)
        // -------- accept a new AXI transaction (serialized: one at a time) --------
        S_IDLE: begin
          if (awvalid) begin
            awid_r    <= awid;
            awlen_r   <= awlen;
            word_addr <= awaddr[DEC_ADDR_W-1:1];        // byte addr -> word addr
            beat_cnt  <= '0;
            if (|awaddr[ADDR_W-1:DEC_ADDR_W]) hi_addr_seen <= 1'b1;
            state     <= S_W_DATA;
          end else if (arvalid) begin
            arid_r    <= arid;
            arlen_r   <= arlen;
            word_addr <= araddr[DEC_ADDR_W-1:1];
            beat_cnt  <= '0;
            if (|araddr[ADDR_W-1:DEC_ADDR_W]) hi_addr_seen <= 1'b1;
            state     <= S_R_XFER;
          end
        end

        // -------- WRITE: latch one 256-bit beat --------
        S_W_DATA: begin
          if (wvalid) begin                             // wready is asserted in this state
            wbeat     <= wdata;
            if (wstrb != {WSTRB_W{1'b1}}) wstrb_partial_seen <= 1'b1;  // detect, do not corrupt
            wword_idx <= '0;
            state     <= S_W_XFER;
          end
        end

        // -------- WRITE: drain 16 words into one hbmc write burst --------
        // av_write held high; word 0 rides the command, words 1..15 ride each need_word. A word is
        // consumed on every cycle av_waitrequest is low.
        S_W_XFER: begin
          if (!av_waitrequest) begin
            if (last_word) begin
              word_addr <= word_addr + HB_ADDR_W'(WORDS_PER_BEAT);
              if (beat_cnt == awlen_r) begin
                state <= S_W_RESP;
              end else begin
                beat_cnt <= beat_cnt + LEN_W'(1);
                state    <= S_W_DATA;
              end
            end else begin
              wword_idx <= wword_idx + IDX_W'(1);
            end
          end
        end

        // -------- WRITE: response --------
        S_W_RESP: begin
          if (bready) state <= S_IDLE;                  // bvalid asserted in this state
        end

        // -------- READ: issue one hbmc read burst (16 words) --------
        S_R_XFER: begin
          if (!av_waitrequest) begin                    // command accepted -> drop av_read
            rword_idx <= '0;
            state     <= S_R_COLLECT;
          end
        end

        // -------- READ: gather 16 words into one 256-bit beat --------
        S_R_COLLECT: begin
          if (av_readdatavalid) begin
            rbeat[16*rword_idx +: 16] <= av_readdata;
            if (last_rword) state <= S_R_RESP;
            else            rword_idx <= rword_idx + IDX_W'(1);
          end
        end

        // -------- READ: emit R beat --------
        S_R_RESP: begin
          if (rready) begin                             // rvalid asserted in this state
            word_addr <= word_addr + HB_ADDR_W'(WORDS_PER_BEAT);
            if (beat_cnt == arlen_r) begin
              state <= S_IDLE;
            end else begin
              beat_cnt <= beat_cnt + LEN_W'(1);
              state    <= S_R_XFER;
            end
          end
        end

        default: state <= S_IDLE;
      endcase
    end
  end

  // Contract signals that are constant per docs/ph3_interfaces.md and intentionally not decoded.
  /* verilator lint_off UNUSEDSIGNAL */
  wire _unused_ok = &{1'b0, awsize, awburst, arsize, arburst, wlast, awaddr[0], araddr[0]};
  /* verilator lint_on UNUSEDSIGNAL */
endmodule
`endif
