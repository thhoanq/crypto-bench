// tb_bench.v — testbench đo cho sign_core của Ed25519 (core_ena / core_comp_done).
// Vector: vectors.hex (gen_vectors.py, RFC 8032, đã đối chiếu OpenSSL): sk, pk, M, R, S.
`timescale 1ns/1ps
module tb_bench;
    localparam NVEC = 32;
    localparam TIMEOUT = 200000;     // chu kỳ tối đa cho một lần ký

    reg clk = 0, rst = 1, core_ena = 0;
    reg  [255:0] core_sk = 0, core_pk = 0, core_M = 0;
    wire [255:0] core_S, core_R;
    wire core_ready, core_comp_done;

    always #5 clk = ~clk;

    sign_core dut (.clk(clk), .rst(rst), .core_ena(core_ena), .core_ready(core_ready),
                   .core_comp_done(core_comp_done), .core_sk(core_sk), .core_pk(core_pk),
                   .core_M(core_M), .core_S(core_S), .core_R(core_R));

    bench_probe probe (.clk(clk), .start(core_ena), .blk(1'b0), .done(core_comp_done));

    reg [255:0] vec [0:5*NVEC-1];
    integer errors = 0, i, wd;

    initial begin
        $readmemh("vectors.hex", vec);
        repeat (4) @(negedge clk); rst = 0;
        repeat (2) @(negedge clk);

        probe.run_begin("sign", "kat");
        for (i = 0; i < NVEC; i = i + 1) begin
            while (!core_ready) @(negedge clk);
            core_sk = vec[5*i]; core_pk = vec[5*i+1]; core_M = vec[5*i+2];
            core_ena = 1;
            @(negedge clk); core_ena = 0;
            wd = 0;
            while (!core_comp_done && wd < TIMEOUT) begin @(negedge clk); wd = wd + 1; end
            if (!core_comp_done) begin                    // lõi treo: dừng ngay
                probe.fail("timeout cho core_comp_done");
                $finish;
            end
            // R, S chỉ hợp lệ trong chu kỳ core_comp_done = 1
            if (core_R !== vec[5*i+3] || core_S !== vec[5*i+4]) begin
                errors = errors + 1;
                $display("SAI sign v%0d: R=%064x S=%064x", i, core_R, core_S);
            end
            $display("sign v%0d xong sau %0d chu ky", i, wd);
        end
        probe.run_end;

        if (errors == 0) probe.pass; else probe.fail("sai KAT");
        $finish;
    end
endmodule
