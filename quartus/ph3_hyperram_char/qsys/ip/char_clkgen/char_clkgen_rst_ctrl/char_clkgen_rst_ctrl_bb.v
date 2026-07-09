module char_clkgen_rst_ctrl #(
		parameter NUM_RESET_INPUTS          = 1,
		parameter OUTPUT_RESET_SYNC_EDGES   = "deassert",
		parameter SYNC_DEPTH                = 3,
		parameter RESET_REQUEST_PRESENT     = 0,
		parameter RESET_REQ_WAIT_TIME       = 1,
		parameter MIN_RST_ASSERTION_TIME    = 3,
		parameter RESET_REQ_EARLY_DSRT_TIME = 1,
		parameter USE_RESET_REQUEST_IN0     = 0,
		parameter USE_RESET_REQUEST_IN1     = 0,
		parameter USE_RESET_REQUEST_IN2     = 0,
		parameter USE_RESET_REQUEST_IN3     = 0,
		parameter USE_RESET_REQUEST_IN4     = 0,
		parameter USE_RESET_REQUEST_IN5     = 0,
		parameter USE_RESET_REQUEST_IN6     = 0,
		parameter USE_RESET_REQUEST_IN7     = 0,
		parameter USE_RESET_REQUEST_IN8     = 0,
		parameter USE_RESET_REQUEST_IN9     = 0,
		parameter USE_RESET_REQUEST_IN10    = 0,
		parameter USE_RESET_REQUEST_IN11    = 0,
		parameter USE_RESET_REQUEST_IN12    = 0,
		parameter USE_RESET_REQUEST_IN13    = 0,
		parameter USE_RESET_REQUEST_IN14    = 0,
		parameter USE_RESET_REQUEST_IN15    = 0,
		parameter ADAPT_RESET_REQUEST       = 0
	) (
		input  wire  reset_in0, // reset_in0.reset
		input  wire  clk,       //       clk.clk
		output wire  reset_out  // reset_out.reset
	);
endmodule

