// (C) 2001-2026 Altera Corporation. All rights reserved.
// Your use of Altera Corporation's design tools, logic functions and other 
// software and tools, and its AMPP partner logic functions, and any output 
// files from any of the foregoing (including device programming or simulation 
// files), and any associated documentation or information are expressly subject 
// to the terms and conditions of the Altera Program License Subscription 
// Agreement, Altera IP License Agreement, or other applicable 
// license agreement, including, without limitation, that your use is for the 
// sole purpose of programming logic devices manufactured by Altera and sold by 
// Altera or its authorized distributors.  Please refer to the applicable 
// agreement for further details.


// $Id: //acds/rel/26.1/ip/iconnect/merlin/altera_reset_controller/altera_reset_synchronizer.v#1 $
// $Revision: #1 $
// $Date: 2026/02/05 $

// -----------------------------------------------
// Reset Synchronizer
// -----------------------------------------------
`timescale 1 ns / 1 ns

module altera_reset_synchronizer
#(
    parameter ASYNC_RESET = 1,
    parameter DEPTH       = 2
)
(
    input   reset_in /* synthesis ALTERA_ATTRIBUTE = "SUPPRESS_DA_RULE_INTERNAL=R101" */,

    input   clk,
    output  reset_out
);

    // -----------------------------------------------
    // Synchronizer register chain. We cannot reuse the
    // standard synchronizer in this implementation 
    // because our timing constraints are different.
    //
    // Instead of cutting the timing path to the d-input 
    // on the first flop we need to cut the aclr input.
    // 
    // We omit the "preserve" attribute on the final
    // output register, so that the synthesis tool can
    // duplicate it where needed.
    // -----------------------------------------------
    (*preserve*) reg [DEPTH-1:0] altera_reset_synchronizer_int_chain;
    reg altera_reset_synchronizer_int_chain_out;

    generate if (ASYNC_RESET) begin

        // -----------------------------------------------
        // Assert asynchronously, deassert synchronously.
        // -----------------------------------------------
        always @(posedge clk or posedge reset_in) begin
            if (reset_in) begin
                altera_reset_synchronizer_int_chain <= {DEPTH{1'b1}};
                altera_reset_synchronizer_int_chain_out <= 1'b1;
            end
            else begin
                altera_reset_synchronizer_int_chain[DEPTH-2:0] <= altera_reset_synchronizer_int_chain[DEPTH-1:1];
                altera_reset_synchronizer_int_chain[DEPTH-1] <= 0;
                altera_reset_synchronizer_int_chain_out <= altera_reset_synchronizer_int_chain[0];
            end
        end

        assign reset_out = altera_reset_synchronizer_int_chain_out;
     
    end else begin

        // -----------------------------------------------
        // Assert synchronously, deassert synchronously.
        // -----------------------------------------------
        always @(posedge clk) begin
            altera_reset_synchronizer_int_chain[DEPTH-2:0] <= altera_reset_synchronizer_int_chain[DEPTH-1:1];
            altera_reset_synchronizer_int_chain[DEPTH-1] <= reset_in;
            altera_reset_synchronizer_int_chain_out <= altera_reset_synchronizer_int_chain[0];
        end

        assign reset_out = altera_reset_synchronizer_int_chain_out;
 
    end
    endgenerate

endmodule

`ifdef QUESTA_INTEL_OEM
`pragma questa_oem_00 "YTu29eqC0KhfAXeRYUICwrAy8f9dNBV8Yu+dcGvIzwUfS9I5ptCmbZm2uCuZOPVBwtVKcWxCI/+NeoaHntIaySHYiXfBS0rjxyGQ1PvQXHCWij1r9uR37Xhl/QPx0bCXnHcqTISdKzkt8Ln8KkNYH7nrnMsVsPv0qdFfytVvHaZA2ymERVJWzYTBStzr1wFRdvdc4EFdV8tOn6e9D/1x/55lNPxUJFwXEnW21qrq43m3wnlehhyl/tvDkBTp1EtPpbSB7YBxMa/I8ZSI1dsWR/qcwyHhk27aoKzAbN2oGgXxnkwGf/9jPkx/jeL/VKbKIEY/78RjwxdNSCxTIRDJqQC4btrU936foYfcH4GIkOj1Pb8nFcEnnGY2UJoXMYcN51IJ1jy971+TxnUYP9B6ARYUmle9XtPoHHUKAYDS/tpju+pAaSva2iqW+gYvhSLYW1152f8xap7C5X60qVk8Bs11aIz0KJPZdF+f8bl9mwoAU39/rZQ6WcxvYEj1WnTK9f+aUGqcmmR+vUNBXXNN0C54sq0s5+6Nl8rpoXhs5VLdHpQr7Hat1GKzvMbNFmVBKP7BMC8ZPrGL63Oy6brcicq+7H7Ba23PxPyxRFJRiTAzutT9yIp3fNKDF8Mxfa18JediNCf2ZaBWrSnyppAn3tdQhF/5EzJyKO4hnV97MohBYYq5jOYf9FAlLGWdPlUgTCu3eUDmAhDMLjL1gJ3Ra4I6eUQfUcGeV3naPYko4wBX4pUYdx5CfIVAObW/RPsXGvLBU/6Ua16JjWk9+HRLtyu1oF+tSyHwHSe6GTbtVimKfBv+Z9iq9fw3/pchuMmvOQ0erCRAWsGe1gVpq3THnpq2rsWByBDOpaxfSLEyW/s5dLZy5F77rEz3L5gN6hPD1Ip/9hRQqCswu8HpGj2nu8SfFiMcD7SGKBiuaXTY5Cjib0cZWywM7PTWyZf2PACVBwRn7evdMFX0LLrqaC0VbIgjapuMBfJ9UfmsFIdeEDpIFZUwIkWqsKmeF1BIPe1C"
`endif