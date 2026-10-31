// A variable part-select that can read out of range: deterministic, and the bounded checks run
// on the AIGER backend (an x from $shiftx used to stop it).
module shiftx (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [7:0] v; reg [3:0] q;
  always @(posedge clk)
    if (!rst_n) begin v <= 0; q <= 0; end
    else begin v <= {v[3:0], ui_in}; q <= v[ui_in[2:0] +: 4]; end
  assign uo_out = q;
endmodule
