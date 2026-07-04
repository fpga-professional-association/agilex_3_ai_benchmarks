// hbmc_core — HyperBus (HyperRAM) memory controller, protocol layer (issue #13).
//
// Avalon-MM 16-bit data-path slave + CSR slave -> HyperBus beats (byte-per-beat model, see
// hyperbus_pkg). PHY-agnostic: exposes raw dq/rwds/cs tristate signals for a thin Agilex DDR-IO PHY
// (synthesis only) so this module stays fully simulable (AGENTS.md / issue #13 "do not bury IO
// primitives in protocol logic"). `cs_n` is driven combinationally and `beat` is derived from it, so
// the controller and device BFM stay in exact lockstep.
//
// Reads capture data gated by RWDS (so a mid-burst row-crossing latency gap is handled for free).
// Writes drive RWDS as a byte mask and simply mask (stall) beats until Avalon delivers the next word.
`ifndef HBMC_CORE_SV
`define HBMC_CORE_SV
module hbmc_core
  import hyperbus_pkg::*;
#(
    parameter int LAT_BEATS_DEFAULT = 6
) (
    input  logic        clk,
    input  logic        rst,

    // ---- CSR slave (config/status/device-register access) ----
    input  logic [5:0]  csr_address,
    input  logic        csr_read,
    output logic [31:0] csr_readdata,
    input  logic        csr_write,
    input  logic [31:0] csr_writedata,

    // ---- Avalon-MM data-path slave (16-bit words, word-addressed, linear bursts) ----
    input  logic [22:0] av_address,       // HyperRAM word address
    input  logic [7:0]  av_burstcount,    // words
    input  logic        av_read,
    input  logic        av_write,
    input  logic [15:0] av_writedata,
    output logic [15:0] av_readdata,
    output logic        av_readdatavalid,
    output logic        av_waitrequest,

    // ---- HyperBus PHY-facing signals ----
    output logic        hb_cs_n,
    output logic [7:0]  hb_dq_o,
    output logic        hb_dq_oe,
    input  logic [7:0]  hb_dq_i,
    output logic        hb_rwds_o,
    output logic        hb_rwds_oe,
    input  logic        hb_rwds_i,
    output logic [7:0]  hb_capture_delay  // to PHY delay taps (#14 training hook)
);
  // ---- CSR registers ----
  logic        cfg_fixed;
  logic [7:0]  cfg_lat;
  logic [7:0]  cfg_capdelay;
  logic [31:0] dev_addr, dev_wdata;
  logic [15:0] dev_rdata;
  logic        dev_start, dev_rw;
  assign hb_capture_delay = cfg_capdelay;

  // ---- transaction state ----
  typedef enum logic [0:0] {IDLE, RUN} state_t;
  state_t st;
  logic        src_dev;
  logic        cur_rw, cur_as;
  logic [47:0] cur_ca;
  logic [15:0] cur_words;
  logic [15:0] eff;
  logic        rwds_ca_seen;
  logic [15:0] words_rx, words_tx;
  logic        rd_lo_valid;
  logic [7:0]  rd_lo;
  logic [15:0] wbuf;
  logic        wbuf_valid;
  logic        wbyte_hi;      // 0 = drive low byte next, 1 = drive high byte next

  logic        busy;
  logic [15:0] beat;
  assign busy   = (st == RUN);
  assign hb_cs_n = ~busy;

  wire in_ca      = busy && (beat < 6);
  wire data_phase = busy && (beat >= 6 + eff);

  function automatic logic [15:0] calc_eff(input logic as_, input logic rw, input logic dbl);
    if (as_ && !rw) return 16'd0;                      // register write: no latency
    if (cfg_fixed)  return 16'({8'd0, cfg_lat});
    return dbl ? 16'({7'd0, cfg_lat, 1'b0}) : 16'({8'd0, cfg_lat});
  endfunction

  // accept a new Avalon command only when idle and no device op is queued
  wire can_cmd = (st == IDLE) && !dev_start;
  wire cmd_fire = can_cmd && (av_read || av_write);
  // during a normal write, pull the next word when the buffer is empty and more remain
  wire need_word = (st == RUN) && !src_dev && !cur_rw && data_phase &&
                   !wbuf_valid && (words_tx < cur_words);
  assign av_waitrequest = ~(cmd_fire || need_word);

  // ---- CSR read (0-wait; readdata valid when csr_read asserted) ----
  always_comb begin
    csr_readdata = 32'd0;
    if (csr_read) begin
      unique case (csr_address)
        CSR_CONFIG:   csr_readdata = {31'd0, cfg_fixed};
        CSR_LATENCY:  csr_readdata = {24'd0, cfg_lat};
        CSR_CAPDELAY: csr_readdata = {24'd0, cfg_capdelay};
        CSR_STATUS:   csr_readdata = {31'd0, (st != IDLE) || dev_start};
        CSR_DEV_ADDR: csr_readdata = dev_addr;
        CSR_DEV_WDAT: csr_readdata = dev_wdata;
        CSR_DEV_CTRL: csr_readdata = {30'd0, dev_rw, 1'b0};
        CSR_DEV_RDAT: csr_readdata = {16'd0, dev_rdata};
        default:      csr_readdata = 32'hDEAD_C0DE;
      endcase
    end
  end

  // ---- main FSM ----
  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE; src_dev <= 1'b0; dev_start <= 1'b0; dev_rw <= 1'b0;
      cfg_fixed <= 1'b1; cfg_lat <= 8'(LAT_BEATS_DEFAULT); cfg_capdelay <= 8'd0;
      dev_addr <= '0; dev_wdata <= '0; dev_rdata <= '0;
      av_readdatavalid <= 1'b0;
      beat <= '0; rwds_ca_seen <= 1'b0;
      words_rx <= '0; words_tx <= '0; rd_lo_valid <= 1'b0;
      wbuf <= '0; wbuf_valid <= 1'b0; wbyte_hi <= 1'b0;
      cur_rw <= 1'b0; cur_as <= 1'b0; cur_ca <= '0; cur_words <= '0; eff <= '0;
    end else begin
      av_readdatavalid <= 1'b0;
      beat <= busy ? (beat + 16'd1) : 16'd0;

      // CSR writes
      if (csr_write) begin
        unique case (csr_address)
          CSR_CONFIG:   cfg_fixed    <= csr_writedata[CFG_FIXED_LATENCY];
          CSR_LATENCY:  cfg_lat      <= csr_writedata[7:0];
          CSR_CAPDELAY: cfg_capdelay <= csr_writedata[7:0];
          CSR_DEV_ADDR: dev_addr     <= csr_writedata;
          CSR_DEV_WDAT: dev_wdata    <= csr_writedata;
          CSR_DEV_CTRL: begin
            if (csr_writedata[DEV_GO] && st == IDLE) begin
              dev_start <= 1'b1;
              dev_rw    <= csr_writedata[DEV_RW];
            end
          end
          default: ;
        endcase
      end

      unique case (st)
        IDLE: begin
          rwds_ca_seen <= 1'b0; words_rx <= '0; words_tx <= '0;
          rd_lo_valid <= 1'b0; wbyte_hi <= 1'b0;
          if (dev_start) begin
            // device register-space op takes priority
            src_dev   <= 1'b1;
            cur_rw    <= dev_rw;
            cur_as    <= 1'b1;
            cur_ca    <= pack_ca(dev_rw, 1'b1, dev_addr);
            cur_words <= 16'd1;
            wbuf      <= dev_wdata[15:0];
            wbuf_valid <= ~dev_rw;         // write needs data staged; read does not
            dev_start <= 1'b0;
            st <= RUN;
          end else if (cmd_fire) begin
            src_dev   <= 1'b0;
            cur_rw    <= av_read;
            cur_as    <= 1'b0;
            cur_ca    <= pack_ca(av_read, 1'b0, {9'd0, av_address});
            cur_words <= {8'd0, av_burstcount};
            wbuf      <= av_writedata;      // first write word arrives with the command
            wbuf_valid <= av_write;
            st <= RUN;
          end
        end

        RUN: begin
          // sample RWDS during CA to detect variable-latency doubling
          if (in_ca && hb_rwds_i) rwds_ca_seen <= 1'b1;
          if (beat == 5)
            eff <= calc_eff(cur_as, cur_rw, rwds_ca_seen | hb_rwds_i);

          if (data_phase && cur_rw) begin
            // READ: capture on RWDS strobe, assemble little-endian words
            if (hb_rwds_i) begin
              if (!rd_lo_valid) begin
                rd_lo <= hb_dq_i; rd_lo_valid <= 1'b1;
              end else begin
                rd_lo_valid <= 1'b0;
                if (src_dev) dev_rdata <= {hb_dq_i, rd_lo};
                else begin
                  av_readdata      <= {hb_dq_i, rd_lo};
                  av_readdatavalid <= 1'b1;
                end
                words_rx <= words_rx + 16'd1;
                if (words_rx + 16'd1 == cur_words) st <= IDLE;
              end
            end
          end else if (data_phase && !cur_rw) begin
            // WRITE: drive masked bytes; mask (stall) until a word is buffered
            if (wbuf_valid) begin
              if (!wbyte_hi) wbyte_hi <= 1'b1;
              else begin
                wbyte_hi   <= 1'b0;
                wbuf_valid <= 1'b0;
                words_tx   <= words_tx + 16'd1;
                if (words_tx + 16'd1 == cur_words) st <= IDLE;
              end
            end else if (need_word) begin
              wbuf <= av_writedata; wbuf_valid <= 1'b1;
            end
          end
        end
      endcase
    end
  end

  // ---- combinational bus drive ----
  always_comb begin
    hb_dq_o = 8'h00; hb_dq_oe = 1'b0; hb_rwds_o = 1'b0; hb_rwds_oe = 1'b0;
    if (in_ca) begin
      hb_dq_o  = cur_ca[8*(5 - beat[2:0]) +: 8];
      hb_dq_oe = 1'b1;
    end else if (data_phase && !cur_rw) begin
      // WRITE data: drive byte with RWDS=0 (write), or RWDS=1 (mask/stall) when no word ready
      hb_dq_oe = 1'b1; hb_rwds_oe = 1'b1;
      if (wbuf_valid) begin
        hb_rwds_o = 1'b0;
        hb_dq_o   = wbyte_hi ? wbuf[15:8] : wbuf[7:0];
      end else begin
        hb_rwds_o = 1'b1;    // mask this beat
      end
    end
  end
endmodule
`endif
