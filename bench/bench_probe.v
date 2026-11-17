// ============================================================
// bench_probe.v — đầu dò đếm chu kỳ DÙNG CHUNG cho mọi lõi.
//
// Testbench của từng lõi chỉ cần nối 3 sự kiện vào đây và gọi 2 task:
//
//   bench_probe probe (.clk(clk), .start(<lõi nhận lệnh>),
//                      .blk(<xong 1 khối, hoặc 1'b0>), .done(<xong phép toán>));
//   ...
//   probe.run_begin("enc", "single");   // (phép toán, kiểu lượt đo)
//   ... chạy lõi ...
//   probe.run_end;
//   probe.pass;        // hoặc probe.fail("ly do") khi sai KAT
//
// Kiểu lượt đo (kind):
//   "single"  1 khối / 1 phép toán đơn lẻ        -> cột Latency (nhóm A)
//   "stream"  N khối liên tiếp nhanh nhất có thể -> cột II, Chu kỳ/khối, N
//   khác      (vd. "kat") mọi lần chạy gộp lại   -> trung bình/min/max (nhóm B)
//
// Quy ước đếm: mọi tín hiệu được lấy mẫu ở sườn lên clk.
//   latency = (sườn lấy mẫu thấy done) − (sườn lấy mẫu thấy start)
// => testbench phải đổi kích thích ở SƯỜN XUỐNG (hoặc trễ #1 sau sườn lên) để
//    lõi và đầu dò cùng thấy start ở một sườn.
//
// EDGE = 1 (mặc định): sự kiện = sườn lên của tín hiệu (tín hiệu giữ mức nhiều
//                      chu kỳ, vd. done giữ tới khi restart, vẫn chỉ tính 1 lần).
// EDGE = 0           : mỗi chu kỳ tín hiệu = 1 là một sự kiện (lõi pipeline,
//                      valid liên tục, II = 1).
// Nhiều start trước khi có done (pipeline) được ghép theo thứ tự FIFO.
//
// blk: xung "xong một khối" bên trong MỘT phép toán nhiều khối (AEAD/hash
// streaming). Không dùng thì nối 1'b0 — khi đó mỗi done tính là một khối.
// ============================================================
`timescale 1ns/1ps
module bench_probe #(
    parameter EDGE   = 1,
    parameter QDEPTH = 4096
) (
    input wire clk,
    input wire start,
    input wire blk,
    input wire done
);
    integer cyc;
    reg p_start, p_blk, p_done;
    reg active;
    reg [127:0] r_op, r_kind;

    integer q [0:QDEPTH-1];
    integer q_wr, q_rd;

    integer n_start, n_blk, n_done;
    integer t_first, t_last, t_prev_blk, t_prev_done;
    integer lat, lat_min, lat_max;
    real    lat_sum;
    integer bi_min, bi_max, di_min, di_max, iv;
    integer n_pass, n_fail;

    wire e_start = EDGE ? (start & ~p_start) : start;
    wire e_blk   = EDGE ? (blk   & ~p_blk)   : blk;
    wire e_done  = EDGE ? (done  & ~p_done)  : done;

    initial begin
        cyc = 0; active = 0; n_pass = 0; n_fail = 0;
        p_start = 0; p_blk = 0; p_done = 0;
        q_wr = 0; q_rd = 0;
    end

    always @(posedge clk) begin
        cyc = cyc + 1;
        if (active) begin
            // done trước, start sau: phép toán k xong và k+1 bắt đầu cùng một sườn
            if (e_done === 1'b1) begin
                if (q_rd == q_wr) begin
                    $display("BENCH_FAIL probe: done khong co start tuong ung (op=%0s, chu ky %0d)", r_op, cyc);
                    n_fail = n_fail + 1;
                end else begin
                    lat = cyc - q[q_rd % QDEPTH];
                    q_rd = q_rd + 1;
                    if (n_done == 0 || lat < lat_min) lat_min = lat;
                    if (n_done == 0 || lat > lat_max) lat_max = lat;
                    lat_sum = lat_sum + lat;
                    if (n_done > 0) begin
                        iv = cyc - t_prev_done;
                        if (n_done == 1 || iv < di_min) di_min = iv;
                        if (n_done == 1 || iv > di_max) di_max = iv;
                    end
                    n_done = n_done + 1;
                    t_prev_done = cyc;
                    t_last = cyc;
                    $display("BENCH_LAT op=%0s kind=%0s cycles=%0d", r_op, r_kind, lat);
                end
            end
            if (e_blk === 1'b1) begin
                if (n_blk > 0) begin
                    iv = cyc - t_prev_blk;
                    if (n_blk == 1 || iv < bi_min) bi_min = iv;
                    if (n_blk == 1 || iv > bi_max) bi_max = iv;
                end
                n_blk = n_blk + 1;
                t_prev_blk = cyc;
                t_last = cyc;
            end
            if (e_start === 1'b1) begin
                if (q_wr - q_rd >= QDEPTH) begin
                    $display("BENCH_FAIL probe: hang doi start day (QDEPTH=%0d)", QDEPTH);
                    n_fail = n_fail + 1;
                end else begin
                    q[q_wr % QDEPTH] = cyc;
                    q_wr = q_wr + 1;
                end
                if (n_start == 0) t_first = cyc;
                n_start = n_start + 1;
            end
        end
        p_start <= start; p_blk <= blk; p_done <= done;
    end

    // Bắt đầu một lượt đo. op/kind: chuỗi tối đa 16 ký tự, không dấu cách.
    task run_begin(input [127:0] op, input [127:0] kind);
        begin
            if (active) begin
                $display("BENCH_FAIL probe: run_begin khi luot truoc chua run_end");
                n_fail = n_fail + 1;
            end
            r_op = op; r_kind = kind;
            n_start = 0; n_blk = 0; n_done = 0;
            t_first = 0; t_last = 0; t_prev_blk = 0; t_prev_done = 0;
            lat_min = 0; lat_max = 0; lat_sum = 0;
            bi_min = 0; bi_max = 0; di_min = 0; di_max = 0;
            q_wr = 0; q_rd = 0;
            active = 1;
        end
    endtask

    // Kết thúc lượt đo, in một dòng tóm tắt cho bench.py (tốn 1 chu kỳ, gọi sau
    // khi đã thấy done cuối cùng).
    //   blocks = số khối (số xung blk nếu có, không thì số done)
    //   total  = sự kiện cuối − start đầu tiên
    //   ii_*   = khoảng cách giữa hai khối liên tiếp (0 nếu chỉ có 1 khối)
    task run_end;
        integer blocks, ii_lo, ii_hi;
        begin
            // testbench thường thấy done ở sườn xuống, TRƯỚC khi đầu dò lấy mẫu
            // nó ở sườn lên kế tiếp => chờ qua một sườn lên rồi mới chốt
            @(negedge clk);
            if (q_rd != q_wr) begin
                $display("BENCH_FAIL probe: %0d start chua co done (op=%0s kind=%0s)", q_wr - q_rd, r_op, r_kind);
                n_fail = n_fail + 1;
            end
            if (n_blk > 0) begin blocks = n_blk;  ii_lo = bi_min; ii_hi = bi_max; end
            else           begin blocks = n_done; ii_lo = di_min; ii_hi = di_max; end
            $display("BENCH_RUN op=%0s kind=%0s starts=%0d dones=%0d blocks=%0d total=%0d lat_min=%0d lat_max=%0d lat_sum=%0.0f ii_min=%0d ii_max=%0d",
                     r_op, r_kind, n_start, n_done, blocks, t_last - t_first,
                     lat_min, lat_max, lat_sum, ii_lo, ii_hi);
            active = 0;
        end
    endtask

    // Kết quả kiểm chứng (KAT). bench.py chỉ nhận số đo khi có BENCH_PASS và
    // không có BENCH_FAIL nào.
    task pass;
        begin n_pass = n_pass + 1; $display("BENCH_PASS"); end
    endtask

    task fail(input [8*64-1:0] msg);
        begin n_fail = n_fail + 1; $display("BENCH_FAIL %0s", msg); end
    endtask
endmodule
