// async-reset counter is fine; b (negedge, unreset) leaks to the output: must be Not deterministic
module async_leak (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] c; reg b;
  always @(posedge clk or negedge rst_n) if (!rst_n) c <= 0; else c <= c + 1;
  always @(negedge clk) if (ui_in[3]) b <= ui_in[0];
  assign uo_out = c ^ {4{b}};
endmodule
