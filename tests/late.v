// leaks only after 15 clocks (a shift chain of an unreset bit): bounded 20 catches it, 10 would not
module late (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg seed; reg [14:0] sh;
  always @(posedge clk) if (!rst_n) sh <= 0; else sh <= {sh[13:0], seed};
  assign uo_out = {3'b0, sh[14]};
endmodule
