	char_clkgen u0 (
		.clk_ref_clk        (_connected_to_clk_ref_clk_),        //   input,  width = 1,      clk_ref.clk
		.clk_clk            (_connected_to_clk_clk_),            //  output,  width = 1,          clk.clk
		.clk2x_clk          (_connected_to_clk2x_clk_),          //  output,  width = 1,        clk2x.clk
		.locked_export      (_connected_to_locked_export_),      //  output,  width = 1,       locked.export
		.reset_in_reset     (_connected_to_reset_in_reset_),     //   input,  width = 1,     reset_in.reset
		.fabric_reset_reset (_connected_to_fabric_reset_reset_)  //  output,  width = 1, fabric_reset.reset
	);

