// cleared only if ui_in[0] is high during reset; free inputs during reset: Not deterministic
module inreset (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] r;
  always @(posedge clk) if (!rst_n && ui_in[0]) r <= 0; else if (rst_n) r <= r + 1;
  assign uo_out = r;
endmodule
