// smoke — trivial headless-compile smoke test for issue #1 (toolchain install/verify).
// One synchronous counter (architectural state, per AGENTS.md RTL conventions) driving one
// LED-style output. Proves the Agilex 3 device/license path compiles clean, nothing more.
`default_nettype none
module smoke (
    input  wire clk,
    input  wire rst_n,
    output wire led
);

    logic [23:0] counter_q;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            counter_q <= '0;
        end else begin
            counter_q <= counter_q + 1'b1;
        end
    end

    assign led = counter_q[23];

endmodule
`default_nettype wire
