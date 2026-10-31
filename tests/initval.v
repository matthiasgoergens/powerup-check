// state relies on an initial value, which an ASIC does not have.
module initval (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [1:0] state = 2'd0; reg [3:0] q;
  always @(posedge clk) begin
    state <= state + 1;
    if (!rst_n) q <= 0; else if (state == 2'd3) q <= ui_in;
  end
  assign uo_out = q;
endmodule
