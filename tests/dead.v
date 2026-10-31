// data is never reset, but is always loaded before it is shown: deterministic in practice.
module dead (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] data; reg valid;
  always @(posedge clk) begin
    data <= ui_in;
    if (!rst_n) valid <= 0; else valid <= 1;
  end
  assign uo_out = valid ? data : 4'd0;
endmodule
