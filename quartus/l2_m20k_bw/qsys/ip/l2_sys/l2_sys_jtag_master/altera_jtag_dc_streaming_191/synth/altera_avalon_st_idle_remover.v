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
//| Avalon ST Idle Remover 
// --------------------------------------------------------------------------------

`timescale 1ns / 100ps
module altera_avalon_st_idle_remover (

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
         if (in_valid & in_ready) begin
            if (escape_char & ~received_esc) begin
                 received_esc <= 1;
            end else if (out_valid) begin
                 received_esc <= 0;
            end
         end
      end
   end

   always @* begin
      in_ready = out_ready;
      //out valid when in_valid.  Except when we get idle or escape
      //however, if we have received an escape character, then we are valid
      out_valid = in_valid & ~idle_char & (received_esc | ~escape_char);
      out_data = received_esc ? (in_data ^ 8'h20) : in_data;
   end
endmodule
`ifdef QUESTA_INTEL_OEM
`pragma questa_oem_00 "YTu29eqC0KhfAXeRYUICwrAy8f9dNBV8Yu+dcGvIzwUfS9I5ptCmbZm2uCuZOPVBwtVKcWxCI/+NeoaHntIaySHYiXfBS0rjxyGQ1PvQXHCWij1r9uR37Xhl/QPx0bCXnHcqTISdKzkt8Ln8KkNYH7nrnMsVsPv0qdFfytVvHaZA2ymERVJWzYTBStzr1wFRdvdc4EFdV8tOn6e9D/1x/55lNPxUJFwXEnW21qrq43m+IIK1ixAbxPYTwnlCpKSDGmf9frfeCOy1EuDdRMaMJDEncztGlmHshJHRvfDGi/EEg4qjealxu0yL1imOpUKwuC6jLHx+dcdMKTloQvSNAU4Ymo3vSmsF7htT5b7B+pQMk3eJ63Nlxzofxy0nRoc2DSa78oKwkrWsu24+0oPKMoSmiMZAgW09tP9QVzEx7lZXQxm+3D73Xk7A5PXrOZPeu7qTH+pV8ogw4chnFtGP8J3P7FSAaef2V+PkFrJZed+TxKjcfG1zWgD52v1YNr4EwgTXtsfI2IkNCcmUeiJzw0MmKBg0cFqgYY/KXk+ILqaO39elRp0WwiXa0ZgcZ01GZeKMErd/mirZg1w1BoqX+E7VvvuuBkzGRjDflArMJwOmqHDZXKvCmRaegV32DmVtkNv5em3f/A4hvUp6y11KPZoYd/lg3Dv7S2OZT8UtmT62CJPubbNCZr8O+odRCIgO+Msq0LW6sevM+v8eEJzcn8OCYmDyUHyW1XO9jmtBrFFtAYl97lb7cgg6nkgaN3+CafnTFJb+x8piB+Xk/S7gS9ZnKC8xMd1469RwAP9ohUVJHNA60UUFfc6uixYibBz9o86aCTZ0+ICG91msNAwFSGv7UmYnYK8TxTPc7V3eA8puX8bO4h6tt0EAs8Zb794I5mcDCYafM79/RkgRkWtxlMkqSYyASjn/anZHE8GW1Re7jrUJgxWBPIBOMY61EztDDdY/TKPhbH1O4tF7cI4lSVM5OQwfY5RgFqX52cCydrhFFqEIX1NmMX/N/4bLUcyX"
`endif