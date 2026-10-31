// mode is never reset and steers the output at once: not deterministic.
module leaky (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] cnt; reg mode;
  always @(posedge clk) begin
    if (!rst_n) cnt <= 0; else cnt <= cnt + 1;
    if (ui_in[3]) mode <= ui_in[0];
  end
  assign uo_out = mode ? cnt : ~cnt;
endmodule
