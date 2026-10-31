// Reads a RAM macro only at addresses it has written since reset: deterministic, given the
// macro's own state is equal at reset (it is outside the design).
module bbram (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [1:0] a; reg we; wire [3:0] q;
  always @(posedge clk)
    if (!rst_n) begin a <= 0; we <= 0; end else begin a <= ui_in[1:0]; we <= ui_in[2]; end
  macro4x4 m (.CLK(clk), .WE(we), .A(a), .D(ui_in), .Q(q));
  assign uo_out = q;
endmodule
