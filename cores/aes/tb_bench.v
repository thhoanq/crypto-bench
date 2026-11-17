// tb_bench.v — testbench đo cho aes_core (giao tiếp init/next/ready).
// KAT: NIST SP 800-38A, ECB-AES128 (F.1.1/F.1.2) và ECB-AES256 (F.1.5/F.1.6); đã đối chiếu OpenSSL.
`timescale 1ns/1ps
module tb_bench;
    reg clk = 0, reset = 1;
    reg encdec = 0, init = 0, next = 0, keylen = 0;
    reg  [255:0] key = 0;
    reg  [127:0] block = 0;
    wire [127:0] result;
    wire ready, result_valid;

    always #5 clk = ~clk;

    aes_core dut (.clk(clk), .reset(reset), .encdec(encdec), .init(init), .next(next),
                  .ready(ready), .key(key), .keylen(keylen), .block(block),
                  .result(result), .result_valid(result_valid));

    // start = lõi nhận `next`; done = result_valid lên lại
    bench_probe probe (.clk(clk), .start(next), .blk(1'b0), .done(result_valid));

    reg [127:0] pt [0:3];
    reg [127:0] ct128 [0:3];
    reg [127:0] ct256 [0:3];
    integer errors = 0, i;

    task set_key(input [255:0] k, input kl);
        begin
            @(negedge clk); key = k; keylen = kl; init = 1;
            @(negedge clk); init = 0;
            while (!ready) @(negedge clk);
        end
    endtask

    task do_block(input enc, input [127:0] din, input [127:0] exp);
        begin
            encdec = enc; block = din; next = 1;
            @(negedge clk); next = 0;
            while (!ready) @(negedge clk);
            if (result !== exp) begin
                errors = errors + 1;
                $display("SAI: enc=%0d in=%032x ra=%032x mong=%032x", enc, din, result, exp);
            end
        end
    endtask

    // một phép toán: 1 khối đơn lẻ, rồi 8 khối liên tiếp
    task measure(input [127:0] op, input enc, input is256);
        begin
            probe.run_begin(op, "single");
            if (enc) do_block(1, pt[0], is256 ? ct256[0] : ct128[0]);
            else     do_block(0, is256 ? ct256[0] : ct128[0], pt[0]);
            probe.run_end;
            repeat (4) @(negedge clk);
            probe.run_begin(op, "stream");
            for (i = 0; i < 8; i = i + 1)
                if (enc) do_block(1, pt[i % 4], is256 ? ct256[i % 4] : ct128[i % 4]);
                else     do_block(0, is256 ? ct256[i % 4] : ct128[i % 4], pt[i % 4]);
            probe.run_end;
            repeat (4) @(negedge clk);
        end
    endtask

    initial begin
        pt[0] = 128'h6bc1bee22e409f96e93d7e117393172a;
        pt[1] = 128'hae2d8a571e03ac9c9eb76fac45af8e51;
        pt[2] = 128'h30c81c46a35ce411e5fbc1191a0a52ef;
        pt[3] = 128'hf69f2445df4f9b17ad2b417be66c3710;
        ct128[0] = 128'h3ad77bb40d7a3660a89ecaf32466ef97;
        ct128[1] = 128'hf5d3d58503b9699de785895a96fdbaaf;
        ct128[2] = 128'h43b1cd7f598ece23881b00e3ed030688;
        ct128[3] = 128'h7b0c785e27e8ad3f8223207104725dd4;
        ct256[0] = 128'hf3eed1bdb5d2a03c064b5a7e3db181f8;
        ct256[1] = 128'h591ccb10d410ed26dc5ba74a31362870;
        ct256[2] = 128'hb6ed21b99ca6f4f9f153e7b1beafed1d;
        ct256[3] = 128'h23304b7a39f9f3ff067d8d8f9e24ecc7;

        repeat (4) @(negedge clk); reset = 0;
        repeat (2) @(negedge clk);

        set_key({128'h2b7e151628aed2a6abf7158809cf4f3c, 128'h0}, 1'b0);
        measure("enc128", 1, 0);
        measure("dec128", 0, 0);
        set_key(256'h603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4, 1'b1);
        measure("enc256", 1, 1);
        measure("dec256", 0, 1);

        if (errors == 0) probe.pass; else probe.fail("sai KAT");
        $finish;
    end
endmodule
