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


// --------------------------------------------------------------------------------
//| Avalon ST Idle Inserter 
// --------------------------------------------------------------------------------

`timescale 1ns / 100ps
module altera_avalon_st_idle_inserter (

      // Interface: clk
      input              clk,
      input              reset_n,
      // Interface: ST in
      output reg         in_ready,
      input              in_valid,
      input      [7: 0]  in_data,

      // Interface: ST out 
      input              out_ready,
      output reg         out_valid,
      output reg [7: 0]  out_data
);

   // ---------------------------------------------------------------------
   //| Signal Declarations
   // ---------------------------------------------------------------------

   reg  received_esc;
   wire escape_char, idle_char;

   // ---------------------------------------------------------------------
   //| Thingofamagick
   // ---------------------------------------------------------------------

   assign idle_char = (in_data == 8'h4a);
   assign escape_char = (in_data == 8'h4d);

   always @(posedge clk or negedge reset_n) begin
      if (!reset_n) begin
         received_esc <= 0; 
      end else begin
         if (in_valid & out_ready) begin
            if ((idle_char | escape_char) & ~received_esc & out_ready) begin
                 received_esc <= 1;
            end else begin
                 received_esc <= 0;
            end
         end
      end
   end

   always @* begin
      //we are always valid
      out_valid = 1'b1;
      in_ready = out_ready & (~in_valid | ((~idle_char & ~escape_char) | received_esc));
      out_data = (~in_valid) ? 8'h4a :    //if input is not valid, insert idle
                 (received_esc) ? in_data ^ 8'h20 : //escaped once, send data XOR'd
                 (idle_char | escape_char) ? 8'h4d : //input needs escaping, send escape_char
                 in_data; //send data
   end
endmodule
`ifdef QUESTA_INTEL_OEM
`pragma questa_oem_00 "YTu29eqC0KhfAXeRYUICwrAy8f9dNBV8Yu+dcGvIzwUfS9I5ptCmbZm2uCuZOPVBwtVKcWxCI/+NeoaHntIaySHYiXfBS0rjxyGQ1PvQXHCWij1r9uR37Xhl/QPx0bCXnHcqTISdKzkt8Ln8KkNYH7nrnMsVsPv0qdFfytVvHaZA2ymERVJWzYTBStzr1wFRdvdc4EFdV8tOn6e9D/1x/55lNPxUJFwXEnW21qrq43kCRpW4r+SV5JNpaTWRJVwAk9Te9/4uDK/9P0jpNRt3FOTzSu+eCkD8vGTJ5ECpem121uVEsoaBkVw0tq6Pggh2yrt9+q2ZO2tCEdipsY9N2PU9Z9Nj4iWsLLcDXmWgIAHWGO4T2+T7mbqEU5LjSrAMcn2L1vYwRzCNJd0M7ZpmDzHaTrNjl85ThSXMoHSH7N+H07hgkxMC1B30BuOwJxbhVqM0O1bF1Lif3HQMwjuUEiZfWNAJCRVdGevNN+uFzb/N6ZI4U3aK1Pp5wbm6kdhIKTAGgK0GtE8hPWxh5G7DsFE2cHNdngQfX5OYn0LIFo9VE+RF0ce1Kk6/FBWv5zyZObFFLudpM+Dx4HKmNA0hqznPjcEK5HUx/CqIoy+T+89G2VSZc51jSHy763msXNjBNbnLPa0o0GL9GKYUn7eTjFkX0ic3rdZ/cy05luCYKmHyzGod00WDMbQb1yGwwwQ/ZnrEo9c2tl5FZE5pGnOxz7U0HTSL+F3xVmryBy3IzK7Wba5uEE/TNJcXcpdFNyidBoE5DdhffwjzH5ST+9v6dGBiQNWybRauhLu3glW4Y8SP5cbqGqNzDfH3p9Iuo1CS35rXY/zqTXCIYOMxMe/k4eBk5L31Xly023r2RGrhiNrbTK3oG3b458LgR4pkEiQOZI6XT5DbVqmDmF0vbLkOM3U8RLsM2vNiZaHGSgb9yLIl/jIiic1pGCTXXGy7dUH11hlrEfbCYR9WrhCbMQhdb09X6g0/OwAKyeKVpvZXKkoZj8g6iRj6JvK9gbgLl0Ot"
`endif