# ============================================================
# impl_ooc.tcl — flow Vivado DÙNG CHUNG: synth + place + route out-of-context
# cho MỘT lõi, lấy LUT / FF / BRAM / DSP / WNS / công suất.
# Không gọi trực tiếp: bench.py sinh run.tcl đặt các biến dưới rồi source file này.
#
#   B_TOP       module top của lõi
#   B_PART      part FPGA (vd. xc7a100tcsg324-1)
#   B_PERIOD    chu kỳ clock ràng buộc (ns)
#   B_CLOCK     tên cổng clock
#   B_IO_DELAY  input/output delay (ns) đặt lên mọi cổng còn lại
#   B_FILES     danh sách file RTL (đường dẫn tuyệt đối)
#   B_INCDIRS   danh sách thư mục include
#   B_DEFINES   danh sách define Verilog (NAME hoặc NAME=VAL)
#
# Kết quả trong thư mục chạy: util.rpt, util_hier.rpt, timing_summary.rpt,
# timing.rpt, power.rpt, result.txt (key=value).
# Fmax = 1000 / (period − WNS).
# Công suất: report_power VECTORLESS (toggle mặc định của Vivado) tại đúng clock
# đã ràng buộc — chỉ để so tương đối giữa các lõi đo cùng flow.
# ============================================================

foreach f $B_FILES {
    switch -- [string tolower [file extension $f]] {
        .sv            { read_verilog -sv $f }
        .vhd - .vhdl   { read_vhdl $f }
        default        { read_verilog $f }
    }
}

set fh [open ooc.xdc w]
puts $fh "create_clock -name clk -period $B_PERIOD \[get_ports $B_CLOCK\]"
puts $fh "set_input_delay  -clock clk $B_IO_DELAY \[get_ports -filter {DIRECTION == IN && NAME != $B_CLOCK}\]"
puts $fh "set_output_delay -clock clk $B_IO_DELAY \[get_ports -filter {DIRECTION == OUT}\]"
close $fh
read_xdc -mode out_of_context ooc.xdc

set synth_args [list -part $B_PART -top $B_TOP -mode out_of_context]
if {[llength $B_INCDIRS] > 0} { lappend synth_args -include_dirs $B_INCDIRS }
foreach d $B_DEFINES { lappend synth_args -verilog_define $d }
synth_design {*}$synth_args

opt_design
place_design
phys_opt_design
route_design

report_utilization               -file util.rpt
report_utilization -hierarchical -file util_hier.rpt
report_timing_summary            -file timing_summary.rpt
report_timing -max_paths 5       -file timing.rpt
set_units -power mW
report_power                     -file power.rpt

set paths [get_timing_paths -delay_type max]
if {[llength $paths] == 0} { error "impl_ooc: không có đường timing nào (sai tên clock '$B_CLOCK'?)" }
set wns [get_property SLACK $paths]

set fh [open result.txt w]
puts $fh "period=$B_PERIOD"
puts $fh "wns=$wns"
puts $fh "vivado=[version -short]"
puts $fh "part=$B_PART"
close $fh
puts "==== OOC top=$B_TOP period=$B_PERIOD ns WNS=$wns ns Fmax=[expr {1000.0/($B_PERIOD - $wns)}] MHz ===="
