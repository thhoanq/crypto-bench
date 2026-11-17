// mult_macro_sim.v — mô hình hành vi CHỈ ĐỂ MÔ PHỎNG của Xilinx MULT_MACRO
// (unimacro, bọc DSP48E1): P = A × B có dấu, trễ LATENCY chu kỳ, CE/RST đồng bộ.
// Vivado dùng macro thật khi tổng hợp; file này chỉ nằm trong sim.files.
`timescale 1ns/1ps
module MULT_MACRO #(
    parameter DEVICE  = "7SERIES",
    parameter LATENCY = 3,
    parameter WIDTH_A = 18,
    parameter WIDTH_B = 18
) (
    output wire [WIDTH_A+WIDTH_B-1:0] P,
    input  wire [WIDTH_A-1:0]         A,
    input  wire [WIDTH_B-1:0]         B,
    input  wire                       CE,
    input  wire                       CLK,
    input  wire                       RST
);
    wire signed [WIDTH_A+WIDTH_B-1:0] prod = $signed(A) * $signed(B);
    generate
        if (LATENCY == 0) begin : g_comb
            assign P = prod;
        end else begin : g_pipe
            reg [WIDTH_A+WIDTH_B-1:0] pipe [0:LATENCY-1];
            integer k;
            initial for (k = 0; k < LATENCY; k = k + 1) pipe[k] = 0;
            always @(posedge CLK) begin
                if (RST)
                    for (k = 0; k < LATENCY; k = k + 1) pipe[k] <= 0;
                else if (CE) begin
                    pipe[0] <= prod;
                    for (k = 1; k < LATENCY; k = k + 1) pipe[k] <= pipe[k-1];
                end
            end
            assign P = pipe[LATENCY-1];
        end
    endgenerate
endmodule
