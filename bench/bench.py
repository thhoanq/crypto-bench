#!/usr/bin/env python3
"""bench.py — framework đo chung cho các lõi mật mã (Python 3.11+, chỉ stdlib).

Mỗi lõi = một thư mục cores/<id>/ gồm core.toml (mô tả) + testbench đo (tb_bench.v).
Phần dùng chung: bench_probe.v (đếm chu kỳ), impl_ooc.tcl (Vivado OOC), file này.

  bench.py list                          liệt kê lõi
  bench.py sim  <id>                     mô phỏng: kiểm KAT + đếm chu kỳ
  bench.py impl <id> [--period NS] [--iters N]
                                         synth + P&R OOC, dò Fmax, LUT/FF/BRAM/DSP/công suất
  bench.py all  <id> [...]               sim rồi impl
  bench.py report [<id> ...] [--locale vi|en] [--by TÊN]
                                         in bảng + ghi results/group_{A,B}.tsv
                                         (đúng thứ tự cột của sheet "Số đo nhóm A/B")

Kết quả thô: results/<id>/{sim,impl}.json; thư mục chạy: build/<id>/.
Xem bench/README.md.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
CORES = os.path.join(ROOT, "cores")
BUILD = os.path.join(ROOT, "build")
RESULTS = os.path.join(ROOT, "results")

DEF_PART = "xc7a100tcsg324-1"
DEF_BOARD = "Arty A7-100T"
DEF_PERIOD = 10.0
DEF_IO_DELAY = 0.5
DEF_ITERS = 3
SOURCE = "Framework bench.py (P&R OOC)"

COLS_A = ["Tên lõi", "Cấu hình", "Phép toán", "Board", "Công cụ và phiên bản", "block_bits",
          "LUT", "FF", "BRAM", "DSP", "Công suất động (mW)", "Fmax (MHz)", "Latency (chu kỳ)",
          "II (chu kỳ)", "Chu kỳ/khối (streaming)", "Số khối N (streaming)",
          "Throughput lõi (Mbps)", "Throughput streaming (Mbps)", "Throughput lõi / kLUT (Mbps)",
          "Latency (µs)", "Nguồn số liệu", "Người đo", "Ngày đo", "Ghi chú"]
COLS_B = ["Tên lõi", "Cấu hình", "Phép toán", "Board", "Công cụ và phiên bản",
          "LUT", "FF", "BRAM", "DSP", "Công suất động (mW)", "Fmax (MHz)",
          "Latency trung bình (chu kỳ)", "Latency tối thiểu (chu kỳ)", "Latency tối đa (chu kỳ)",
          "Số lần chạy", "Thời gian thực hiện (µs)", "Số phép toán / giây",
          "Biến thiên latency (%)", "Năng lượng / phép toán (µJ)",
          "Nguồn số liệu", "Người đo", "Ngày đo", "Ghi chú"]


def die(msg):
    sys.exit("bench: " + msg)


def find_tool(name, extra=()):
    p = shutil.which(name)
    if p:
        return p
    for pat in extra:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    die("không tìm thấy '%s' (PATH hoặc %s)" % (name, ", ".join(extra)))


# ------------------------------------------------------------ cấu hình lõi
def core_ids():
    if not os.path.isdir(CORES):
        return []
    return sorted(d for d in os.listdir(CORES)
                  if os.path.isfile(os.path.join(CORES, d, "core.toml")))


def expand(base, patterns, exclude=()):
    """Mở glob tương đối `base`, giữ thứ tự khai báo, bỏ trùng."""
    out = []
    skip = set()
    for pat in exclude:
        skip.update(os.path.abspath(p) for p in glob.glob(os.path.join(base, pat)))
    for pat in patterns:
        hits = sorted(glob.glob(os.path.join(base, pat)))
        if not hits:
            die("không có file nào khớp '%s' (gốc %s)" % (pat, base))
        for h in hits:
            h = os.path.abspath(h)
            if h not in skip and h not in out:
                out.append(h)
    return out


def load_core(cid):
    cdir = os.path.join(CORES, cid)
    path = os.path.join(cdir, "core.toml")
    if not os.path.isfile(path):
        die("không có lõi '%s' (có: %s)" % (cid, ", ".join(core_ids()) or "—"))
    with open(path, "rb") as f:
        c = tomllib.load(f)
    for k in ("name", "group", "rtl", "op"):
        if k not in c:
            die("%s: thiếu khoá '%s'" % (path, k))
    if c["group"] not in ("A", "B"):
        die("%s: group phải là \"A\" (throughput) hoặc \"B\" (latency)" % path)
    rtl = c["rtl"]
    for k in ("files", "top"):
        if k not in rtl:
            die("%s: thiếu rtl.%s" % (path, k))
    rroot = os.path.normpath(os.path.join(cdir, rtl.get("root", ".")))
    c["_id"] = cid
    c["_dir"] = cdir
    c["_rtl_files"] = expand(rroot, rtl["files"], rtl.get("exclude", ()))
    c["_incdirs"] = [os.path.normpath(os.path.join(rroot, d)) for d in rtl.get("include_dirs", ())]
    c["_data"] = expand(rroot, rtl.get("data", ()))
    sim = c.setdefault("sim", {})
    c["_tb_files"] = expand(cdir, sim.get("files", ["tb_bench.v"]))
    c["_data"] += [p for p in expand(cdir, sim.get("data", ())) if p not in c["_data"]]
    keys = [op.get("key") for op in c["op"]]
    if None in keys or len(set(keys)) != len(keys):
        die("%s: mỗi [[op]] cần một 'key' riêng" % path)
    if c["group"] == "A":
        for op in c["op"]:
            if "block_bits" not in op:
                die("%s: op '%s' (nhóm A) thiếu block_bits" % (path, op["key"]))
    return c


def link_data(c, rundir):
    for src in c["_data"]:
        dst = os.path.join(rundir, os.path.basename(src))
        if os.path.lexists(dst):
            os.remove(dst)
        os.symlink(src, dst)


def save_json(cid, name, obj):
    d = os.path.join(RESULTS, cid)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    return p


def load_json(cid, name):
    p = os.path.join(RESULTS, cid, name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ mô phỏng
def parse_sim_log(text):
    """Đọc các dòng BENCH_* của bench_probe. Trả (runs, lats, n_pass, fails)."""
    runs, lats, fails, n_pass = [], {}, [], 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("BENCH_RUN "):
            kv = dict(t.split("=", 1) for t in line.split()[1:])
            r = {"op": kv.pop("op"), "kind": kv.pop("kind")}
            r.update((k, int(float(v))) for k, v in kv.items())
            runs.append(r)
        elif line.startswith("BENCH_LAT "):
            kv = dict(t.split("=", 1) for t in line.split()[1:])
            lats.setdefault(kv["op"], []).append(int(kv["cycles"]))
        elif line.startswith("BENCH_PASS"):
            n_pass += 1
        elif line.startswith("BENCH_FAIL"):
            fails.append(line)
    return runs, lats, n_pass, fails


def summarize_op(runs, lats, key):
    """Gộp các lượt đo của một phép toán thành số cho bảng."""
    mine = [r for r in runs if r["op"] == key]
    samples = lats.get(key, [])
    if not mine or not samples:
        return None
    s = {"runs": len(samples), "lat_mean": sum(samples) / len(samples),
         "lat_min": min(samples), "lat_max": max(samples)}
    single = [r for r in mine if r["kind"] == "single" and r["dones"] > 0]
    stream = [r for r in mine if r["kind"] == "stream" and r["blocks"] > 0]
    s["latency"] = single[0]["lat_min"] if single else s["lat_min"]
    if stream:
        r = max(stream, key=lambda r: r["blocks"])
        s["stream_n"] = r["blocks"]
        s["stream_cpb"] = r["total"] / r["blocks"]
        # II = khoảng cách nhỏ nhất giữa hai khối liên tiếp; 1 khối thì = chu kỳ/khối
        s["ii"] = r["ii_min"] if r["blocks"] > 1 and r["ii_min"] > 0 else s["stream_cpb"]
        s["stream_lat"] = [r["lat_min"], r["lat_max"]]
    else:
        # lõi không pipeline, không đo streaming: II = chu kỳ/khối = latency
        s["stream_n"] = 1
        s["stream_cpb"] = s["latency"]
        s["ii"] = s["latency"]
    return s


def cmd_sim(c):
    iverilog = find_tool("iverilog", ["~/oss-cad-suite/bin/iverilog"])
    vvp = find_tool("vvp", ["~/oss-cad-suite/bin/vvp"])
    rundir = os.path.join(BUILD, c["_id"], "sim")
    os.makedirs(rundir, exist_ok=True)
    link_data(c, rundir)
    sim = c["sim"]
    cmd = [iverilog, "-g2012", "-o", "sim.vvp", "-s", sim.get("top", "tb_bench")]
    for d in c["_incdirs"] + [c["_dir"]]:
        cmd += ["-I", d]
    for d in list(c["rtl"].get("defines", ())) + list(sim.get("defines", ())):
        cmd += ["-D", d]
    cmd += [os.path.join(BENCH, "bench_probe.v")] + c["_rtl_files"] + c["_tb_files"]
    r = subprocess.run(cmd, cwd=rundir, capture_output=True, text=True)
    if r.returncode != 0:
        lines = (r.stdout + r.stderr).splitlines()
        sys.stderr.write("\n".join(lines[:30]) + "\n")
        if len(lines) > 30:
            sys.stderr.write("... (%d dòng nữa)\n" % (len(lines) - 30))
        die("%s: iverilog biên dịch lỗi" % c["_id"])
    try:
        r = subprocess.run([vvp, "-n", "sim.vvp"], cwd=rundir, capture_output=True, text=True,
                           timeout=sim.get("timeout_s", 1800))
    except subprocess.TimeoutExpired:
        die("%s: mô phỏng quá %d s" % (c["_id"], sim.get("timeout_s", 1800)))
    log = r.stdout + r.stderr
    with open(os.path.join(rundir, "sim.log"), "w", encoding="utf-8") as f:
        f.write(log)
    runs, lats, n_pass, fails = parse_sim_log(log)
    ok = r.returncode == 0 and n_pass > 0 and not fails
    ops = {}
    for op in c["op"]:
        s = summarize_op(runs, lats, op["key"])
        if s is None:
            ok = False
            fails.append("không có lượt đo nào cho op '%s'" % op["key"])
        else:
            ops[op["key"]] = s
    res = {"core": c["_id"], "pass": ok, "fails": fails, "simulator": "iverilog",
           "date": datetime.date.today().isoformat(), "ops": ops, "runs": runs}
    p = save_json(c["_id"], "sim.json", res)
    print("[sim] %s: %s  (log: %s)" % (c["_id"], "PASS" if ok else "FAIL",
                                       os.path.relpath(os.path.join(rundir, "sim.log"), ROOT)))
    for m in fails:
        print("   ", m)
    for k, s in ops.items():
        print("    %-10s latency=%s  II=%s  chu kỳ/khối=%s (N=%d)  [mean/min/max %s/%d/%d qua %d lần]"
              % (k, num(s["latency"]), num(s["ii"]), num(s["stream_cpb"]), s["stream_n"],
                 num(s["lat_mean"]), s["lat_min"], s["lat_max"], s["runs"]))
    print("    ->", os.path.relpath(p, ROOT))
    return ok


# ------------------------------------------------------------ Vivado OOC
def tcl_list(items):
    return "[list %s]" % " ".join("{%s}" % i for i in items)


def parse_util(text):
    def grab(pat):
        m = re.search(r"^\|\s*(?:%s)\*?\s*\|\s*([\d.]+)" % pat, text, re.M)
        return float(m.group(1)) if m else None
    u = {"lut": grab("Slice LUTs|CLB LUTs"), "ff": grab("Slice Registers|CLB Registers"),
         "bram": grab("Block RAM Tile"), "dsp": grab("DSPs")}
    if u["lut"] is None or u["ff"] is None:
        die("không đọc được LUT/FF từ util.rpt")
    return {k: (int(v) if v is not None and v == int(v) else v) for k, v in u.items()}


def parse_power(text):
    out = {}
    for key, label in (("dynamic_mw", "Dynamic"), ("static_mw", "Device Static"),
                       ("total_mw", "Total On-Chip Power")):
        m = re.search(r"^\|\s*%s \((m?W)\)\s*\|\s*([\d.]+)" % re.escape(label), text, re.M)
        if m:
            out[key] = float(m.group(2)) * (1.0 if m.group(1) == "mW" else 1000.0)
    return out


def run_vivado(c, period):
    vivado = find_tool("vivado", ["/opt/Xilinx/Vivado/*/bin/vivado", "/tools/Xilinx/Vivado/*/bin/vivado"])
    impl = c.get("impl", {})
    rundir = os.path.join(BUILD, c["_id"], "impl", "p%.3f" % period)
    if os.path.isdir(rundir):
        shutil.rmtree(rundir)
    os.makedirs(rundir)
    link_data(c, rundir)
    rtl = c["rtl"]
    with open(os.path.join(rundir, "run.tcl"), "w", encoding="utf-8") as f:
        f.write("set B_TOP {%s}\n" % rtl["top"])
        f.write("set B_PART {%s}\n" % impl.get("part", DEF_PART))
        f.write("set B_PERIOD %.3f\n" % period)
        f.write("set B_CLOCK {%s}\n" % rtl.get("clock", "clk"))
        f.write("set B_IO_DELAY %s\n" % impl.get("io_delay", DEF_IO_DELAY))
        f.write("set B_FILES %s\n" % tcl_list(c["_rtl_files"]))
        f.write("set B_INCDIRS %s\n" % tcl_list(c["_incdirs"]))
        f.write("set B_DEFINES %s\n" % tcl_list(rtl.get("defines", ())))
        f.write("source {%s}\n" % os.path.join(BENCH, "impl_ooc.tcl"))
    env = dict(os.environ, RDI_VERBOSE="False")
    r = subprocess.run([vivado, "-mode", "batch", "-nojournal", "-log", "vivado.log", "-source", "run.tcl"],
                       cwd=rundir, capture_output=True, text=True, env=env)
    res_path = os.path.join(rundir, "result.txt")
    if r.returncode != 0 or not os.path.isfile(res_path):
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
        die("%s: Vivado lỗi ở period %.3f ns — xem %s/vivado.log\n%s" % (c["_id"], period, rundir, tail))
    kv = dict(l.strip().split("=", 1) for l in open(res_path) if "=" in l)
    wns = float(kv["wns"])
    run = {"period": period, "wns": wns, "fmax": 1000.0 / (period - wns),
           "vivado": kv["vivado"], "part": kv["part"], "dir": os.path.relpath(rundir, ROOT)}
    run.update(parse_util(open(os.path.join(rundir, "util.rpt")).read()))
    run.update(parse_power(open(os.path.join(rundir, "power.rpt")).read()))
    log = open(os.path.join(rundir, "vivado.log"), errors="replace").read()
    run["critical_warnings"] = len(re.findall(r"^CRITICAL WARNING:", log, re.M))
    return run


def cmd_impl(c, period, iters):
    """Dò Fmax: mỗi vòng chạy đủ P&R ở một chu kỳ, vòng sau siết về chu kỳ đạt được.

    Chu kỳ đạt được của một lượt = period − WNS (kể cả lượt trượt timing: đường
    găng dài đúng bằng đó). Kết quả = lượt có Fmax cao nhất; LUT/FF/công suất lấy
    từ chính lượt đó.
    """
    period = period or c.get("impl", {}).get("period", DEF_PERIOD)
    runs = []
    for i in range(iters):
        print("[impl] %s: vòng %d/%d, period %.3f ns ..." % (c["_id"], i + 1, iters, period), flush=True)
        run = run_vivado(c, period)
        runs.append(run)
        print("       WNS %+.3f ns -> Fmax %.1f MHz | LUT %s FF %s BRAM %s DSP %s | dyn %.1f mW%s"
              % (run["wns"], run["fmax"], run["lut"], run["ff"], run["bram"], run["dsp"],
                 run.get("dynamic_mw", float("nan")),
                 " | %d CRITICAL WARNING" % run["critical_warnings"] if run["critical_warnings"] else ""),
              flush=True)
        achieved = period - run["wns"]
        # đạt timing: siết thêm 5 % dưới mức đạt được; trượt: nới về sát mức đạt được
        nxt = round(achieved * (0.95 if run["wns"] >= 0 else 1.01), 3)
        if abs(nxt - period) / period < 0.01 or any(abs(nxt - r["period"]) < 0.002 for r in runs):
            break
        period = nxt
    best = max(runs, key=lambda r: r["fmax"])
    impl = c.get("impl", {})
    res = {"core": c["_id"], "top": c["rtl"]["top"], "board": impl.get("board", DEF_BOARD),
           "tool": "Vivado " + best["vivado"], "date": datetime.date.today().isoformat(),
           "io_delay": impl.get("io_delay", DEF_IO_DELAY), "power_mode": "vectorless",
           "best": best, "runs": runs}
    p = save_json(c["_id"], "impl.json", res)
    print("[impl] %s: Fmax %.1f MHz (period %.3f, WNS %+.3f) -> %s"
          % (c["_id"], best["fmax"], best["period"], best["wns"], os.path.relpath(p, ROOT)))
    return True


# ------------------------------------------------------------ bảng kết quả
def num(v, nd=1):
    """Số gọn: nguyên thì in nguyên, không thì nd số lẻ."""
    if v is None:
        return ""
    if isinstance(v, int) or float(v) == int(v):
        return str(int(v))
    return "%.*f" % (nd, v)


def build_rows(c, by):
    sim, impl = load_json(c["_id"], "sim.json"), load_json(c["_id"], "impl.json")
    notes_core = []
    if sim is None:
        notes_core.append("chưa chạy sim")
    elif not sim["pass"]:
        notes_core.append("SIM CHƯA PASS — không ghi số chu kỳ")
        sim = None
    if impl is None:
        notes_core.append("chưa chạy impl")
    b = impl["best"] if impl else {}
    fmax, lut, pdyn = b.get("fmax"), b.get("lut"), b.get("dynamic_mw")
    if impl:
        notes_core.append("OOC, io_delay %s ns; công suất vectorless @ %.1f MHz"
                          % (num(impl["io_delay"]), 1000.0 / b["period"]))
        if b.get("critical_warnings"):
            notes_core.append("%d CRITICAL WARNING Vivado" % b["critical_warnings"])
    date = datetime.date.today().strftime("%d/%m/%Y")
    who = by or c.get("owner", "")
    rows = []
    for op in c["op"]:
        s = sim["ops"].get(op["key"]) if sim else None
        notes = [n for n in (c.get("notes", ""), op.get("notes", "")) if n] + notes_core
        head = [c["name"], op.get("config", c.get("config", "")), op.get("name", op["key"]),
                impl["board"] if impl else c.get("impl", {}).get("board", DEF_BOARD),
                impl["tool"] if impl else ""]
        area = [num(lut), num(b.get("ff")), num(b.get("bram")), num(b.get("dsp")), num(pdyn), num(fmax)]
        tail = [SOURCE, who, date, "; ".join(notes)]
        if c["group"] == "A":
            bits = op["block_bits"]
            lat = ii = cpb = n = tp = tps = tpk = lat_us = None
            if s:
                lat, ii, cpb, n = s["latency"], s["ii"], s["stream_cpb"], s["stream_n"]
                lo, hi = s.get("stream_lat", (lat, lat))
                if lo != hi:       # cùng một lượt streaming mà mỗi khối một khác
                    notes.insert(0, "latency mỗi khối không đều: %d..%d" % (lo, hi))
                    tail[3] = "; ".join(notes)
                if fmax:
                    tp, tps = bits * fmax / ii, bits * fmax / cpb
                    tpk = tp / (lut / 1000.0)
                    lat_us = lat / fmax
            rows.append(head + [num(bits)] + area
                        + [num(lat), num(ii), num(cpb), num(n), num(tp), num(tps), num(tpk), num(lat_us, 2)]
                        + tail)
        else:
            mean = lo = hi = runs = t_us = ops_s = var = e_uj = None
            if s:
                mean, lo, hi, runs = s["lat_mean"], s["lat_min"], s["lat_max"], s["runs"]
                var = (hi - lo) * 100.0 / mean
                if fmax:
                    t_us = mean / fmax
                    ops_s = fmax * 1e6 / mean
                    if pdyn is not None:
                        e_uj = pdyn * t_us / 1000.0
            rows.append(head + area
                        + [num(mean), num(lo), num(hi), num(runs), num(t_us), num(ops_s), num(var, 2), num(e_uj, 2)]
                        + tail)
    return rows


def localize(cell, locale):
    if locale == "vi" and re.fullmatch(r"-?\d+\.\d+", cell):
        return cell.replace(".", ",")
    return cell


def cmd_report(ids, locale, by):
    groups = {"A": (COLS_A, []), "B": (COLS_B, [])}
    for cid in ids:
        c = load_core(cid)
        groups[c["group"]][1].extend(build_rows(c, by))
    os.makedirs(RESULTS, exist_ok=True)
    for g, (cols, rows) in groups.items():
        if not rows:
            continue
        rows = [[localize(x, locale) for x in r] for r in rows]
        path = os.path.join(RESULTS, "group_%s.tsv" % g)
        with open(path, "w", encoding="utf-8") as f:
            for r in [cols] + rows:
                f.write("\t".join(r) + "\n")
        print("\n== Nhóm %s (%s) -> %s" % (g, "throughput" if g == "A" else "latency",
                                           os.path.relpath(path, ROOT)))
        for r in rows:
            print("-- %s | %s | %s" % (r[0], r[1], r[2]))
            for name, val in zip(cols[3:], r[3:]):
                if val:
                    print("   %-30s %s" % (name, val))


# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("sim", "impl", "all"):
        p = sub.add_parser(name)
        p.add_argument("cores", nargs="+")
        if name != "sim":
            p.add_argument("--period", type=float, help="chu kỳ khởi đầu (ns); mặc định impl.period trong core.toml")
            p.add_argument("--iters", type=int, default=DEF_ITERS, help="số vòng dò Fmax (mặc định %d)" % DEF_ITERS)
    p = sub.add_parser("report")
    p.add_argument("cores", nargs="*")
    p.add_argument("--locale", choices=("vi", "en"), default="vi",
                   help="vi = dấu phẩy thập phân (như sheet), en = dấu chấm")
    p.add_argument("--by", help="Người đo (mặc định: owner trong core.toml)")
    a = ap.parse_args()

    if a.cmd == "list":
        for cid in core_ids():
            c = load_core(cid)
            print("%-14s nhóm %s  %-22s top=%-18s op: %s  [sim %s, impl %s]"
                  % (cid, c["group"], c["name"], c["rtl"]["top"], ", ".join(o["key"] for o in c["op"]),
                     "có" if load_json(cid, "sim.json") else "—", "có" if load_json(cid, "impl.json") else "—"))
        return 0
    if a.cmd == "report":
        cmd_report(a.cores or core_ids(), a.locale, a.by)
        return 0
    ok = True
    for cid in a.cores:
        c = load_core(cid)
        if a.cmd in ("sim", "all"):
            ok = cmd_sim(c) and ok
        if a.cmd in ("impl", "all"):
            ok = cmd_impl(c, a.period, a.iters) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
