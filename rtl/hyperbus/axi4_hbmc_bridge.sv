// axi4_hbmc_bridge — CoreDLA AXI4 "DDR" master -> HyperRAM Avalon-MM adapter (PH3).
//
// Front-end: reduced AXI4 slave matching the FPGA AI Suite CoreDLA DDR port (DATA=256, ADDR=32,
// WRITE_ID=5, READ_ID=2; AxSIZE const 3'd5, AxBURST const INCR, AxLEN<=15). Back-end: Avalon-MM
// master driving rtl/hyperbus/hbmc_core.sv (16-bit words, word address, linear bursts). See
// docs/ph3_bridge_design.md for the full contract and FSM; docs/ph3_interfaces.md for provenance.
//
// v3 (2026-07-12): WRITE-COMBINING. The DDIO controller (submodule) corrupts any 32-byte beat that is
// written more than once — proven on silicon (scratch/hyperram_retest/wstrb_abc.tcl: two writes to the
// SAME beat clobber the first word; two writes to DIFFERENT beats are independent). A 32-bit host/JTAG
// write is a partial-strobe beat, and a contiguous load does 8 such writes per beat -> corruption.
// This version buffers the partial-strobe writes of one 32-byte beat and flushes them as ONE
// full-strobe beat write (the proven one-write-per-beat pattern), so a contiguous host load lands
// correctly without the DDIO fix. The buffer flushes when a write moves to a different beat, or when a
// read arrives (read-your-writes). FULL-strobe writes — CoreDLA's own 256-bit datapath, already one
// write per beat — flush any pending buffer then pass straight through, bit-identically to v1.
// (This supersedes v2's read-modify-write, which did a read THEN a write to the same beat -- two
// accesses -- and so was itself corrupted by the same DDIO defect on silicon.)
//
// LIMITATION: correct for beat-aligned contiguous writes (config/weights, CoreDLA). A partial beat
// that is flushed before all 32 bytes are written (non-contiguous / interrupted run) writes its
// un-accumulated bytes as 0; do not rely on sub-beat scatter writes.
//
// RTL discipline (AGENTS.md / PLAN §3 LV1): synchronous reset only for architectural state (FSM,
// counters, sticky status, running address, buffer-valid); beat data buffers and latched AXI
// attributes are reset-less datapath registers (written before read). No async reset, no clock gating.
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
    output logic                 wstrb_partial_seen,  // a non-all-ones WSTRB was seen (-> combined)
    output logic                 hi_addr_seen         // an address above the 16 MB window was seen
);
  // ---- derived widths / constants ----
  localparam int WORDS_PER_BEAT = DATA_W / 16;                 // 256/16 = 16 hbmc words per AXI beat
  localparam int IDX_W          = $clog2(WORDS_PER_BEAT);      // 4-bit word index within a beat
  localparam int WSTRB_W        = DATA_W / 8;                  // 32
  localparam int DEC_ADDR_W     = HB_ADDR_W + 1;               // 24 decoded byte-address bits (16 MB)
  localparam logic [1:0] RESP_OKAY = 2'b00;

  // after-flush routing (what to do once a buffer flush completes)
  localparam logic [1:0] AF_NONE = 2'd0, AF_LOAD = 2'd1, AF_FULL = 2'd2, AF_READ = 2'd3;

  // ---- FSM ----
  typedef enum logic [2:0] {
    S_IDLE,        // wait for AW (write priority) or AR
    S_W_DATA,      // accept a W beat (256b): combine (partial) or set up pass-through (full)
    S_W_FLUSH,     // drain the combine buffer (cbuf) as one 16-word hbmc write at cbaddr
    S_W_XFER,      // drain a full-strobe wbeat as 16 hbmc words (pass-through fast path)
    S_W_RESP,      // emit B
    S_R_XFER,      // issue hbmc read command
    S_R_COLLECT,   // gather 16 hbmc words into rbeat
    S_R_RESP       // emit R
  } state_t;
  state_t state;

  // ---- architectural state (synchronous reset) ----
  logic [HB_ADDR_W-1:0] word_addr;      // running hbmc word address (current read/write burst)
  logic [LEN_W-1:0]     beat_cnt;       // beats emitted/consumed in the current AXI burst
  logic [IDX_W-1:0]     wword_idx;      // word within the current write/flush beat (0..15)
  logic [IDX_W-1:0]     rword_idx;      // word within the current read beat (0..15)
  logic                 chas;           // combine buffer holds a pending (not-yet-flushed) beat
  logic [1:0]           after_flush;    // what to do when S_W_FLUSH completes

  // ---- datapath (reset-less: written before read) ----
  logic [DATA_W-1:0]    wbeat;          // latched AXI write beat (full-strobe pass-through / AF_LOAD src)
  logic [WSTRB_W-1:0]   wstrb_r;        // latched AXI write strobes
  logic [DATA_W-1:0]    rbeat;          // assembled AXI read beat
  logic [DATA_W-1:0]    cbuf;           // combine buffer: accumulated beat data
  logic [WSTRB_W-1:0]   cstrb;          // combine buffer: accumulated strobes (all-ones => full beat)
  logic [HB_ADDR_W-1:0] cbaddr;         // combine buffer: hbmc word-address of the buffered beat base
  logic [WID_W-1:0]     awid_r;         // latched write ID -> bid
  logic [RID_W-1:0]     arid_r;         // latched read ID  -> rid (echo, required)
  logic [LEN_W-1:0]     awlen_r;        // latched write burst length
  logic [LEN_W-1:0]     arlen_r;        // latched read burst length

  wire last_word  = (wword_idx == IDX_W'(WORDS_PER_BEAT-1));
  wire last_rword = (rword_idx == IDX_W'(WORDS_PER_BEAT-1));
  // A 32-bit AXI write to a 256-bit slave arrives with the ACTUAL byte address (e.g. 0x..04) and a
  // WSTRB that selects the addressed lanes -- NOT a beat-aligned address. Combining must key on the
  // 32-byte-BEAT base, so mask the low IDX_W word bits (= low 5 byte bits) to get the beat's word addr.
  wire [HB_ADDR_W-1:0] aw_beat = {awaddr[DEC_ADDR_W-1 : 1+IDX_W], {IDX_W{1'b0}}};
  wire [HB_ADDR_W-1:0] ar_beat = {araddr[DEC_ADDR_W-1 : 1+IDX_W], {IDX_W{1'b0}}};

  // per-byte expansion of a 32-bit WSTRB into a 256-bit byte mask
  function automatic logic [DATA_W-1:0] strb_mask(input logic [WSTRB_W-1:0] s);
    logic [DATA_W-1:0] m;
    for (int i = 0; i < WSTRB_W; i++) m[8*i +: 8] = {8{s[i]}};
    return m;
  endfunction

  // ---- AXI handshake / response outputs (combinational from state) ----
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

  // ---- Avalon-MM master outputs ----
  //   S_W_FLUSH writes the combine buffer (cbuf @ cbaddr); S_W_XFER writes the pass-through beat
  //   (wbeat @ word_addr); S_R_XFER reads.
  assign av_burstcount = HB_BURST_W'(WORDS_PER_BEAT);
  assign av_address    = (state == S_W_FLUSH) ? cbaddr : word_addr;
  assign av_read       = (state == S_R_XFER);
  assign av_write      = (state == S_W_XFER) || (state == S_W_FLUSH);
  assign av_writedata  = (state == S_W_FLUSH) ? cbuf [16*wword_idx +: 16]
                                              : wbeat[16*wword_idx +: 16];

  // ---- sequential FSM ----
  always_ff @(posedge clk) begin
    if (rst) begin
      state              <= S_IDLE;
      word_addr          <= '0;
      beat_cnt           <= '0;
      wword_idx          <= '0;
      rword_idx          <= '0;
      chas               <= 1'b0;
      after_flush        <= AF_NONE;
      wstrb_partial_seen <= 1'b0;
      hi_addr_seen       <= 1'b0;
    end else begin
      unique case (state)
        // -------- accept a new AXI transaction (serialized: one at a time) --------
        S_IDLE: begin
          if (awvalid) begin
            awid_r    <= awid;
            awlen_r   <= awlen;
            word_addr <= aw_beat;
            beat_cnt  <= '0;
            if (|awaddr[ADDR_W-1:DEC_ADDR_W]) hi_addr_seen <= 1'b1;
            state     <= S_W_DATA;
          end else if (arvalid) begin
            arid_r    <= arid;
            arlen_r   <= arlen;
            word_addr <= ar_beat;
            beat_cnt  <= '0;
            if (|araddr[ADDR_W-1:DEC_ADDR_W]) hi_addr_seen <= 1'b1;
            // read-your-writes: flush any pending combine buffer before the read
            if (chas) begin after_flush <= AF_READ; wword_idx <= '0; state <= S_W_FLUSH; end
            else                                                     state <= S_R_XFER;
          end
        end

        // -------- WRITE data beat: combine (partial) or pass through (full) --------
        S_W_DATA: begin
          if (wvalid) begin                             // wready asserted in this state
            wbeat   <= wdata;
            wstrb_r <= wstrb;
            if (wstrb == {WSTRB_W{1'b1}}) begin
              // FULL beat (CoreDLA). If the buffer holds a DIFFERENT beat, flush it first; a buffer
              // for the SAME beat is superseded by this full write, so just drop it.
              if (chas && (cbaddr != word_addr)) begin
                after_flush <= AF_FULL; wword_idx <= '0; state <= S_W_FLUSH;
              end else begin
                chas <= 1'b0; wword_idx <= '0; state <= S_W_XFER;
              end
            end else begin
              // PARTIAL beat (host load): accumulate.
              wstrb_partial_seen <= 1'b1;
              if (chas && (cbaddr == word_addr)) begin
                cbuf  <= (cbuf & ~strb_mask(wstrb)) | (wdata & strb_mask(wstrb));
                cstrb <= cstrb | wstrb;
                state <= S_W_RESP;
              end else if (!chas) begin
                cbuf   <= wdata & strb_mask(wstrb);
                cstrb  <= wstrb;
                cbaddr <= word_addr;
                chas   <= 1'b1;
                state  <= S_W_RESP;
              end else begin
                // buffer holds a different beat -> flush it, then load this write (AF_LOAD)
                after_flush <= AF_LOAD; wword_idx <= '0; state <= S_W_FLUSH;
              end
            end
          end
        end

        // -------- FLUSH: drain the combine buffer as one 16-word write at cbaddr --------
        S_W_FLUSH: begin
          if (!av_waitrequest) begin
            if (last_word) begin
              chas <= 1'b0;
              unique case (after_flush)
                AF_LOAD: begin                          // load the deferred partial write into buffer
                  cbuf   <= wbeat & strb_mask(wstrb_r);
                  cstrb  <= wstrb_r;
                  cbaddr <= word_addr;                  // the new write's beat base
                  chas   <= 1'b1;
                  state  <= S_W_RESP;
                end
                AF_FULL: begin wword_idx <= '0; state <= S_W_XFER; end  // then pass-through the full beat
                AF_READ: state <= S_R_XFER;                             // then service the read
                default: state <= S_IDLE;
              endcase
              after_flush <= AF_NONE;
            end else begin
              wword_idx <= wword_idx + IDX_W'(1);
            end
          end
        end

        // -------- WRITE: drain a full-strobe beat into one hbmc write burst (fast path) --------
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
  //   awaddr[IDX_W:1]/araddr[IDX_W:1] (the word-within-beat bits) are intentionally unused: WSTRB is
  //   authoritative for byte placement within the beat, and combining keys on the beat base only.
  /* verilator lint_off UNUSEDSIGNAL */
  wire _unused_ok = &{1'b0, awsize, awburst, arsize, arburst, wlast,
                      awaddr[IDX_W:0], araddr[IDX_W:0]};
  /* verilator lint_on UNUSEDSIGNAL */
endmodule
`endif
