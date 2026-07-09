module l2_sys (
		input  wire        clk_25_clk,           //    clk_25.clk
		output wire        clk_clk,              //       clk.clk
		output wire        locked_export,        //    locked.export
		output wire [31:0] master_address,       //    master.address
		input  wire [31:0] master_readdata,      //          .readdata
		output wire        master_read,          //          .read
		output wire        master_write,         //          .write
		output wire [31:0] master_writedata,     //          .writedata
		input  wire        master_waitrequest,   //          .waitrequest
		input  wire        master_readdatavalid, //          .readdatavalid
		output wire [3:0]  master_byteenable,    //          .byteenable
		input  wire        reset_reset,          //     reset.reset
		output wire        sys_reset_reset       // sys_reset.reset
	);
endmodule

