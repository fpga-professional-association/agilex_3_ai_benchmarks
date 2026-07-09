// axc3000_blink — real, flashable LED-blink bring-up for the Arrow AXC3000 board
// (Altera Agilex 3 A3CY100BM16AE7S). This is the honest "verify you can blink lights" artifact:
// it runs off the board's REAL 25 MHz oscillator (CLK_25M_C @ PIN_A7) and drives the REAL user
// LED pins, per the vetted pinout in quartus/constraints/axc3000_board.tcl (issue #7). Unlike
// quartus/smoke — whose pins are Fitter-auto-placed and wired to no physical LED — flashing this
// bitstream makes actual board LEDs blink.
//
// RTL conventions (AGENTS.md): synchronous reset is used ONLY for architectural state (the
// counter); there is no asynchronous reset anywhere. USER_BTN is an asynchronous, mechanical
// input, so it is passed through the sanctioned 2-FF synchronizer (rtl/common/cdc_bit_sync.sv)
// before being used as reset — never a hand-rolled synchronizer, never an async reset.
`default_nettype none
module axc3000_blink (
    input  wire CLK_25M_C,  // 25 MHz fixed oscillator, PIN_A7  (axc3000_board.tcl / .sdc)
    input  wire USER_BTN,   // active-low push button S2, PIN_A12, weak pullup (=1 when idle)
    output wire LED1,       // single user LED D10, PIN_AG21, ACTIVE-LOW (0 = lit)
    output wire RLED,       // RGB LED D2 red,      PIN_AH22, ACTIVE-LOW (0 = lit)
    output wire GLED,       // RGB LED D2 green,    PIN_AK21, ACTIVE-LOW (0 = lit)
    output wire BLED        // RGB LED D2 blue,     PIN_AK20, ACTIVE-LOW (0 = lit)
);

    // 25 MHz clock (axc3000_board.sdc: 40.000 ns period). A 27-bit free-running counter yields:
    //   counter_q[23] toggles at 25e6 / 2^24 ≈ 1.49 Hz  -> LED1 blink, clearly visible by eye.
    //   counter_q[25:24] steps a 2-bit phase every 2^24 / 25e6 ≈ 0.67 s, full 4-phase walk every
    //   2^26 / 25e6 ≈ 2.68 s -> obviously-alive slow RGB colour cycle.
    localparam int COUNTER_W = 27;
    localparam int BLINK_BIT = 23;  // ≈ 1.49 Hz toggle at 25 MHz (see above)
    localparam int PHASE_HI  = 25;  // RGB phase field is counter_q[25:24]
    localparam int PHASE_LO  = 24;

    // Synchronize the asynchronous active-low button into CLK_25M_C to form rst_n.
    // Idle (released) = weak-pullup high = 1 = run; pressed = low = 0 = reset.
    logic rst_n;
    cdc_bit_sync #(.STAGES(2)) u_btn_sync (
        .dst_clk (CLK_25M_C),
        .d       (USER_BTN),
        .q       (rst_n)
    );

    // Architectural state: synchronous reset only (AGENTS.md RTL conventions).
    logic [COUNTER_W-1:0] counter_q;
    always_ff @(posedge CLK_25M_C) begin
        if (!rst_n) counter_q <= '0;
        else        counter_q <= counter_q + 1'b1;
    end

    // Slow RGB colour walk: light exactly one of R/G/B per phase; all dark on the 4th phase.
    logic [1:0] phase;
    assign phase = counter_q[PHASE_HI:PHASE_LO];

    // All LEDs are ACTIVE-LOW: drive 0 to light. LED1 blinks at ~1.49 Hz; RGB steps R->G->B->off.
    assign LED1 = ~counter_q[BLINK_BIT];  // lit for half of each ~1.49 Hz period
    assign RLED = ~(phase == 2'd0);
    assign GLED = ~(phase == 2'd1);
    assign BLED = ~(phase == 2'd2);

endmodule
`default_nettype wire
