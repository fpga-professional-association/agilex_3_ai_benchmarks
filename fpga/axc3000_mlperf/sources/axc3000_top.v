// 
// SPDX-FileCopyrightText: Copyright (C) 2025 Arrow Electronics, Inc. 
// SPDX-License-Identifier: MIT-0 
//
// This licensed-IP-free derivative implements a Nios V/m soft microcontroller
// in the Arrow AXC3000 Agilex 3 design. HyperRAM is deliberately quiescent.


`timescale 1ns/10ps

module axc3000_top (

   input          CLK_25M_C,       // 25MHz input clock
   input          USER_BTN,        // active-low reset from RST button
   input          DBG_RX,          // Arrow FTDI UART receive (reserved)
   output         DBG_TX,          // Arrow FTDI UART transmit (idle high)
   output         VSEL_1V3,        // select the Arrow VADJ rail
   output         LED1,            // heartbeat
   output         RLED,
   output         GLED,
   output         BLED,
   output         HR_HRESETn,      // asserted reset: HyperRAM inactive
   output         HR_CLK,          // held low: effective HyperRAM clock 0 MHz
   output         HR_CSn,          // deasserted chip select
   inout  [7:0]   HR_DQ,
   inout          HR_RWDS
   
);


/* * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * */

// Instantiate the Nios V Platform Designer system
	NIOSV_lab u0 (
		.clk_clk       (CLK_25M_C),
		.reset_reset_n (USER_BTN)
	);

   // Keep the external memory interface electrically inactive. No SLL,
   // xSPI, QSPI, or HyperRAM controller is instantiated in this project.
   assign VSEL_1V3   = 1'b1;
   assign HR_HRESETn = 1'b0;
   assign HR_CLK     = 1'b0;
   assign HR_CSn     = 1'b1;
   assign HR_DQ      = 8'bz;
   assign HR_RWDS    = 1'bz;
   assign DBG_TX     = 1'b1;
   assign RLED       = 1'b0;
   assign GLED       = 1'b0;
   assign BLED       = 1'b0;

   reg [24:0] heartbeat_counter;
   always @(posedge CLK_25M_C or negedge USER_BTN) begin
      if (!USER_BTN)
        heartbeat_counter <= 25'd0;
      else
        heartbeat_counter <= heartbeat_counter + 25'd1;
   end
   assign LED1 = heartbeat_counter[24];
	
endmodule
