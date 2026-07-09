module char_clkgen (
		input  wire  clk_ref_clk,        //      clk_ref.clk
		output wire  clk_clk,            //          clk.clk
		output wire  clk2x_clk,          //        clk2x.clk
		output wire  locked_export,      //       locked.export
		input  wire  reset_in_reset,     //     reset_in.reset
		output wire  fabric_reset_reset  // fabric_reset.reset
	);
endmodule

