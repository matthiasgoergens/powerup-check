// Every register is cleared: deterministic.
module good (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] cnt; reg [3:0] acc;
  always @(posedge clk)
    if (!rst_n) begin cnt <= 0; acc <= 0; end
    else begin cnt <= cnt + 1; acc <= acc ^ ui_in ^ cnt; end
  assign uo_out = acc;
endmodule
