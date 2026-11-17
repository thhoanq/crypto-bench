# Framework đo lõi mật mã

Đo các cột của sheet **Crypto Collection** ("Số đo nhóm A (throughput)" và
"Số đo nhóm B (latency)") bằng một flow chung, trên Arty A7-100T / Vivado OOC.

## Cái gì chung, cái gì riêng

| Số đo | Lấy từ đâu | Phần riêng của từng lõi |
|---|---|---|
| LUT, FF, BRAM, DSP, Fmax, công suất động | `impl_ooc.tcl` — synth + P&R out-of-context, lặp vài vòng để dò Fmax | không có (chỉ khai file, top, tên clock) |
| Latency, II, chu kỳ/khối, N (nhóm A); trung bình/min/max, số lần chạy (nhóm B) | `bench_probe.v` đếm chu kỳ trong mô phỏng | **testbench `tb_bench.v`**: lái giao tiếp của lõi, so KAT, nối `start`/`done` vào đầu dò |
| Throughput, throughput/kLUT, µs, phép toán/giây, biến thiên, năng lượng | `bench.py report` tính theo công thức của sheet | `block_bits` của từng phép toán |

Tức là mỗi lõi chỉ phải viết **một file mô tả + một testbench**; cách đo, cách
đếm và công thức giống nhau cho mọi lõi nên số so được với nhau.

## Dùng

```bash
python3 bench/bench.py list
python3 bench/bench.py sim  aes            # kiểm KAT + đếm chu kỳ (iverilog)
python3 bench/bench.py impl aes            # Vivado OOC, dò Fmax (mặc định 3 vòng)
python3 bench/bench.py all  aes xoodyak    # sim rồi impl
python3 bench/bench.py report              # in bảng + ghi results/group_{A,B}.tsv
```

`results/group_A.tsv` và `group_B.tsv` có đúng thứ tự cột của sheet (dấu phẩy
thập phân; `--locale en` để dùng dấu chấm) — mở file, copy dòng, dán vào sheet.
Các cột xám của sheet cũng được tính sẵn; dán đè lên thì công thức ô đó bị thay
bằng giá trị.

Thư mục: `cores/<id>/` (mô tả lõi), `build/<id>/` (thư mục chạy, log, report
Vivado), `results/<id>/{sim,impl}.json` (số thô).

## Thêm một lõi

1. `cores/<id>/core.toml` — chép từ `cores/aes/core.toml`:
   - `group = "A"` (mã khối, mã dòng, AEAD, hash, MAC) hoặc `"B"` (chữ ký, KEM, trao đổi khóa);
   - `[rtl]`: `root`, `files` (glob được), `top`, `clock`; tùy chọn `defines`,
     `include_dirs`, `exclude`, `data` (file `$readmemh`, được link vào thư mục chạy);
   - `[impl]`: `period` khởi đầu (ns); tùy chọn `part`, `board`, `io_delay`;
   - mỗi `[[op]]` là **một dòng trên sheet**: `key`, `name` (cột Phép toán),
     `block_bits` (nhóm A), tùy chọn `config`, `notes`.
2. `cores/<id>/tb_bench.v`:

   ```verilog
   bench_probe probe (.clk(clk), .start(<lõi nhận lệnh>), .blk(1'b0), .done(<xong>));
   ...
   probe.run_begin("enc", "single");  // 1 khối đơn lẻ        -> Latency
   ...chạy 1 khối, so KAT...
   probe.run_end;
   probe.run_begin("enc", "stream");  // N khối liên tiếp     -> II, chu kỳ/khối, N
   ...
   probe.run_end;
   if (errors == 0) probe.pass; else probe.fail("sai KAT");
   ```

   - Tên op trong `run_begin` phải trùng `key` trong `core.toml`.
   - Nhóm B: dùng một kiểu bất kỳ (vd. `"kat"`); mọi lần chạy của op được gộp
     thành trung bình / min / max / số lần chạy.
   - Lõi xử lý nhiều khối trong MỘT lệnh (AEAD/hash streaming): nối xung "xong
     một khối" vào `blk`; khi đó II = khoảng cách giữa hai xung `blk`,
     chu kỳ/khối = (sự kiện cuối − start) / số khối.
   - Đổi kích thích ở **sườn xuống** clk (đầu dò lấy mẫu ở sườn lên).
   - Lõi pipeline (valid liên tục): `bench_probe #(.EDGE(0))`.
3. `bench.py all <id>` rồi `bench.py report`.

Không có `BENCH_PASS`, hoặc có bất kỳ `BENCH_FAIL` nào ⇒ `report` không ghi số
chu kỳ của lõi đó (verify trước, đo sau).

## Quy ước đo (ghi kèm khi so với số nguồn khác)

- **Latency** = số sườn clk từ sườn lõi lấy mẫu `start` đến sườn đầu tiên có thể
  lấy mẫu `done` = 1. Đo tay kiểu khác có thể lệch ±1–2 chu kỳ.
- **Fmax** = 1000 / (period − WNS) của lượt P&R tốt nhất trong các vòng dò; LUT,
  FF, công suất lấy từ chính lượt đó. P&R có nhiễu cỡ vài %.
- **OOC**: không có I/O, BUFG; mọi cổng có input/output delay `io_delay`
  (mặc định 0,5 ns) nên đường tổ hợp từ cổng vào/ra cũng được tính.
- **Công suất động**: `report_power` vectorless (toggle mặc định của Vivado) tại
  clock đã ràng buộc — chỉ để so tương đối giữa các lõi đo cùng flow, không phải
  công suất tuyệt đối. (Flow SAIF post-route chính xác hơn có ở
  `ntt_dilithium/scripts/run_power.sh`, chưa tổng quát hoá vào đây.)
- Đo ở biên **lõi** (không kèm wrapper MMIO/APB/TileLink) để so được giữa các
  lõi có bus khác nhau.

## Chưa có

- Nhóm C (TRNG: min-entropy) — không phải bài toán hiệu năng.
- Mức hệ thống (SoC + firmware) của `crypto_benchmark_methodology.md`.
- Công suất theo SAIF, mô phỏng netlist post-route.
