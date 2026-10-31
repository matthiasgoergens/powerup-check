// A small RAM (never cleared) read only after it is written: the pins check passes.
module ram (input clk, input rst_n, input [3:0] ui_in, output [3:0] uo_out);
  reg [3:0] mem [0:3]; reg [1:0] wa; reg [3:0] wr; reg [3:0] q;
  always @(posedge clk) begin
    if (!rst_n) begin wa <= 0; wr <= 0; q <= 0; end
    else begin
      mem[wa] <= ui_in; wa <= wa + 1; wr <= wr | (4'd1 << wa);
      q <= wr[ui_in[1:0]] ? mem[ui_in[1:0]] : 4'd0;
    end
  end
  assign uo_out = q;
endmodule
