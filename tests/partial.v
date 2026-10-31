// only r[1:0] reset; r[3:2] leak through a slice: must be Not deterministic
module partial (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] r;
  always @(posedge clk) begin
    if (!rst_n) r[1:0] <= 0; else r[1:0] <= ui_in[1:0];
    if (ui_in[3]) r[3:2] <= ui_in[3:2];
  end
  assign uo_out = {r[3], r[1:0], 1'b0};
endmodule
