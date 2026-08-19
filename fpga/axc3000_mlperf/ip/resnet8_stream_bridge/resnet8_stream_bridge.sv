module resnet8_stream_bridge (
    input  wire         clk,
    input  wire         reset_n,

    input  wire [3:0]   av_address,
    input  wire         av_read,
    output logic [31:0] av_readdata,
    input  wire         av_write,
    input  wire [31:0]  av_writedata,
    input  wire [3:0]   av_byteenable,
    output wire         av_waitrequest,

    output wire [95:0]  m_axis_tdata,
    output wire         m_axis_tvalid,
    input  wire         m_axis_tready,

    input  wire [127:0] s_axis_tdata,
    input  wire         s_axis_tvalid,
    output wire         s_axis_tready,
    input  wire         s_axis_tlast,
    input  wire [15:0]  s_axis_tstrb
);
    logic [31:0] input_word0;
    logic [31:0] input_word1;
    logic [95:0] input_payload;
    logic        input_pending;
    logic        input_overflow;

    logic [127:0] output_payload;
    logic [15:0]  output_strobe;
    logic         output_last;
    logic         output_pending;
    logic         output_overflow;

    logic [31:0] input_beat_count;
    logic [31:0] output_beat_count;

    wire input_fire = input_pending && m_axis_tready;
    wire output_fire = s_axis_tvalid && s_axis_tready;
    wire output_ack = av_read && (av_address == 4'd7);

    assign av_waitrequest = 1'b0;
    assign m_axis_tdata = input_payload;
    assign m_axis_tvalid = input_pending;
    assign s_axis_tready = !output_pending;

    always_comb begin
        case (av_address)
            4'd0: av_readdata = {
                input_beat_count[7:0],
                output_strobe,
                4'b0,
                output_overflow,
                input_overflow,
                output_pending,
                !input_pending
            };
            4'd1: av_readdata = input_payload[31:0];
            4'd2: av_readdata = input_payload[63:32];
            4'd3: av_readdata = input_payload[95:64];
            4'd4: av_readdata = output_payload[31:0];
            4'd5: av_readdata = output_payload[63:32];
            4'd6: av_readdata = output_payload[95:64];
            4'd7: av_readdata = output_payload[127:96];
            4'd8: av_readdata = {31'b0, output_last};
            4'd9: av_readdata = input_beat_count;
            4'd10: av_readdata = output_beat_count;
            default: av_readdata = 32'b0;
        endcase
    end

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            input_word0 <= 32'b0;
            input_word1 <= 32'b0;
            input_payload <= 96'b0;
            input_pending <= 1'b0;
            input_overflow <= 1'b0;
            output_payload <= 128'b0;
            output_strobe <= 16'b0;
            output_last <= 1'b0;
            output_pending <= 1'b0;
            output_overflow <= 1'b0;
            input_beat_count <= 32'b0;
            output_beat_count <= 32'b0;
        end else begin
            if (input_fire) begin
                input_pending <= 1'b0;
                input_beat_count <= input_beat_count + 1'b1;
            end

            if (output_ack)
                output_pending <= 1'b0;

            if (output_fire) begin
                output_payload <= s_axis_tdata;
                output_strobe <= s_axis_tstrb;
                output_last <= s_axis_tlast;
                output_pending <= 1'b1;
                output_beat_count <= output_beat_count + 1'b1;
            end

            if (av_write) begin
                case (av_address)
                    4'd1: begin
                        if (av_byteenable[0]) input_word0[7:0] <= av_writedata[7:0];
                        if (av_byteenable[1]) input_word0[15:8] <= av_writedata[15:8];
                        if (av_byteenable[2]) input_word0[23:16] <= av_writedata[23:16];
                        if (av_byteenable[3]) input_word0[31:24] <= av_writedata[31:24];
                    end
                    4'd2: begin
                        if (av_byteenable[0]) input_word1[7:0] <= av_writedata[7:0];
                        if (av_byteenable[1]) input_word1[15:8] <= av_writedata[15:8];
                        if (av_byteenable[2]) input_word1[23:16] <= av_writedata[23:16];
                        if (av_byteenable[3]) input_word1[31:24] <= av_writedata[31:24];
                    end
                    4'd3: begin
                        if (!input_pending || input_fire) begin
                            input_payload <= {av_writedata, input_word1, input_word0};
                            input_pending <= 1'b1;
                        end else begin
                            input_overflow <= 1'b1;
                        end
                    end
                    4'd11: begin
                        if (av_writedata[0]) begin
                            input_overflow <= 1'b0;
                            output_overflow <= 1'b0;
                        end
                        if (av_writedata[1]) begin
                            input_beat_count <= 32'b0;
                            output_beat_count <= 32'b0;
                        end
                    end
                    default: begin end
                endcase
            end
        end
    end
endmodule
