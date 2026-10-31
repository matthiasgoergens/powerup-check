// a divided clock that is never reset clocks a reset register; the phase of div leaks: Not deterministic
module divclk (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg div; reg [3:0] c;
  always @(posedge clk) div <= ~div;
  always @(posedge div) if (!rst_n) c <= 0; else c <= c + 1;
  assign uo_out = c;
endmodule
