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
`pragma questa_oem_00 "mfufSZOYfNPxmSfS+FkCl32e9bHGnpakqHX1nHXvrhE4J6x8qGVCEAJRqHIBXSp/FcyWPq9/UmljeMey51E9VTMH3OCNEvZdEUSPU4/Vf6DgtU26zIfR3Ws2AtmUBXEHD5yEeHuHpEMedz5PLIdzFsBxJ/9OiyM+8A3UVG3icM2FQlZPfaXli71zX1obNH4Hi4tyLUZvYQoJv7xSQMqW1lxant16XP5C2V2aZfTOCZm98qIL7ekOBsgi3Jk6JINlBn1MPss/MY/onstD48jcwiif8Gs4IWdQ2N3ArmvPwztGaa9pFUdrqFC/ySuZse43QXHTSLktppbb5RWP6kUriuoicmo6krAD1dbg/gJD2CPBniH3lvaXSbq4NXuXvaIi5JFjsHvqYH5AYGEBbEYIvX50OtmvM7MmdXWz8yMt/2x5MXTWbyba6bfKDJO6u5VlcHp85j3oXhLyoSx6ddNAgFJy9rjF3Z+u9E7PhkTCkebQtUzC8lIU9rG1+dwJE98atxbf1BPMRyiFcpFny635Ygt4p5jSQaXJnjSr6IUlFMLGpbYxPn1FcSkyMGPl2hZ7QtQENnQ3zejAwBl3Ze7ED8vOSzEBB93wGiKKZGd9hNwx5qeE0sgSNJa00qx6RLsg5tdDdzTo2bNXHlCtfYMIt2/YE75hzYCE//Zmb1mMx8nElfiU7/CpeYigjPvinVN5SqMGjIfMFWJasqdSm9Kw1UAN9s0LK8S6K3T1TGkHThOkJWDfpad7wvhxSkVJ99h9FdBLMlmkBYEwI/u/MKtNrzgKPsLz1FZ5EHB/JZti7aWbfhXQS1FoZYSLodk3NCTCmYgyghebDnmxCjIcHta/kmoyhMwLpM8ogUx6J3QA9oOxI+AvWy9A+id8Pj2XJjjQZxXzlg9JOVaXvn88zi6LVC88csY6kT+ANYw3OMfSjw9C2F6wme2VCibcI+DhqZCNXC41h1m9pqNYMUyyrBy5GMRZpEvb5yXb0+fS85rTPw4BgZkbkOxcmoA92mjOky5L"
`endif