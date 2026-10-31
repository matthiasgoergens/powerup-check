#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Power-up determinism check for a synchronous design (Tiny Tapeout projects in particular).

Silicon flip-flops start with arbitrary values; `initial` blocks and `reg x = 0` do not exist on an
ASIC.  This tool asks: after reset, do the outputs depend only on the inputs?  Two copies of the
design start from independent, arbitrary register contents, get the same inputs, and have reset
held for RESET_CLOCKS clocks.  From then on their outputs must agree.

Steps (all files in the work directory):
  1. prep    yosys flattens the design, maps memories to flip-flops, drops init values (reporting
             any it found), turns black-box instances into ports and every register into an
             output (expose -evert, expose -dff).
  2. xsim    iverilog runs the reset with every register at X: registers still X afterwards are
             the candidates for "not cleared by reset".
  3. refine  bounded check that every other register agrees in both copies after reset; any that
             does not is added to the uncleared set, until the check passes.
  4. checks  pins:     outputs agree for --clocks clocks after reset, uncleared registers free
             held:     the same with the uncleared registers equal when reset ends
             noreset:  the control: no reset at all, must fail
             prove:    k-induction (unbounded) with every register equal as the helper and the
                       uncleared ones equal when reset ends
Black boxes are taken to be deterministic: both copies see the same black-box outputs, and their
inputs must agree from the last reset clock on.  Their own state is assumed equal at reset.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

OCS = os.environ.get("POWERUP_OCS", "/var/tmp/oss-cad-suite-20261014/oss-cad-suite")
IMAGE = os.environ.get("POWERUP_IMAGE", "debian:trixie")
FF_CELLS = {"$dff", "$dffe", "$adff", "$adffe", "$sdff", "$sdffe", "$sdffce", "$dffsr", "$dffsre",
            "$aldff", "$aldffe"}


def run(args, cmd, log, timeout=None):
    """Run a shell command in the work directory, in the offline container unless --runner host."""
    if args.runner == "host":
        full = ["sh", "-c", cmd]
    else:
        full = ["podman", "run", "--rm", "--network=none", "--userns=keep-id",
                f"--cpus={args.cpus}", "--volume", f"{OCS}:/ocs:ro",
                "--volume", f"{args.work}:/work", "--workdir", "/work",
                "--env", "PATH=/ocs/bin:/usr/bin:/bin", IMAGE, "sh", "-c", cmd]
    full = ["nice", "ionice", "--class", "idle"] + full
    with open(args.work / log, "w") as f:
        try:
            r = subprocess.run(full, cwd=args.work, stdout=f, stderr=subprocess.STDOUT,
                               timeout=timeout)
            return r.returncode
        except subprocess.TimeoutExpired:
            f.write(f"\npowerup-check: timed out after {timeout} s\n")
            return None


def tt_project(d):
    """top_module and source_files from a Tiny Tapeout info.yaml (no YAML library needed)."""
    text = (d / "info.yaml").read_text()
    top = re.search(r"^\s*top_module:\s*\"?(\w+)\"?", text, re.M).group(1)
    block = re.search(r"^\s*source_files:\s*\n((?:\s*(?:#.*)?\n|\s+-\s*.*\n)+)", text, re.M)
    files = [f.strip().strip("\"'") for f in re.findall(r"^\s+-\s*([^#\n]+)", block.group(1), re.M)]
    return top, [d / "src" / f for f in files]


def ports(js, top):
    return {n: (p["direction"], len(p["bits"])) for n, p in js["modules"][top]["ports"].items()}


def vid(name):
    """A Verilog identifier for a yosys port name."""
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else f"\\{name} "


def find_module(name, dirs):
    """A Verilog file under dirs that defines module name (to read as a black box)."""
    pat = re.compile(rb"^\s*module\s+" + re.escape(name.encode()) + rb"\b", re.M)
    for d in dirs:
        for f in sorted(Path(d).rglob("*.v")) + sorted(Path(d).rglob("*.sv")):
            if ".git" not in f.parts and f.is_file() and pat.search(f.read_bytes()):
                return f
    return None


def prep(args, top, files, libs):
    src = args.work / "src"
    src.mkdir(exist_ok=True)
    libs, resolved = list(libs), []
    for attempt in range(40):
        reads = [f"read_liberty -ignore_miss_func src/lib{i:02d}_{Path(f).name}"
                 for i, f in enumerate(args.liberty)]
        for i, f in enumerate(args.liberty):
            shutil.copyfile(f, src / f"lib{i:02d}_{Path(f).name}")
        # `include files: everything next to a source, under its own name
        inc = src / "inc"
        inc.mkdir(exist_ok=True)
        for d in dict.fromkeys(Path(f).parent for f in files):
            for h in d.iterdir():
                if h.is_file() and h.suffix in (".vh", ".svh", ".h", ".v", ".sv", ".inc"):
                    shutil.copyfile(h, inc / h.name)
        for i, f in enumerate(files + libs):
            dst = src / f"{i:02d}_{Path(f).name}"
            shutil.copyfile(f, dst)
            defs = "".join(f" -D{d}" for d in args.define) + " -Isrc/inc"
            lib = " -lib" if i >= len(files) else ""
            reads.append(f"read_verilog -sv{lib}{defs} src/{dst.name}")
        (args.work / "prep.ys").write_text("\n".join(reads + [
            f"hierarchy -check -top {top}",
            "proc",
            "tee -q -o init.txt select -list a:init",
            "flatten",
            "memory -nomap",
            "memory_map",
            "opt_clean",
            "setattr -unset init w:*",
            "tee -q -o check.txt check",
            "tee -q -o latches.txt select -list t:$dlatch t:$adlatch t:$dlatchsr t:$sr t:$_DLATCH*",
            "write_json orig.json",
            "expose -evert c:* t:$* %d",  # black-box instances only, not every wire
            "write_json evert.json",
            "expose -dff",
            "opt_clean",
            "write_json dut.json",
            f"rename {top} pu_dut",
            "write_verilog -noattr pu_dut.v",
        ]) + "\n")
        if run(args, "yosys -q -l prep.log prep.ys", "prep.out") == 0:
            break
        # an unknown module: read its definition, from the project or the search path, as a black box
        m = re.search(r"Module `\\?(\S+?)' referenced in module .* is not part of the design",
                      (args.work / "prep.out").read_text())
        # only hard macros and cells: an RTL module read as a black box would hide its registers
        f = (m and re.fullmatch(args.blackbox_names, m.group(1))
             and find_module(m.group(1), args.search))
        if not f:
            sys.exit(f"yosys failed, see {args.work}/prep.log and prep.out")
        libs.append(f)
        resolved.append(f"{m.group(1)}: {f}")
    else:
        sys.exit("too many unknown modules")
    load = lambda n: json.loads((args.work / n).read_text())
    orig, evert, dut = load("orig.json"), load("evert.json"), load("dut.json")
    po, pe, pd = ports(orig, top), ports(evert, top), ports(dut, top)
    bb = {n: v for n, v in pe.items() if n not in po}
    regs = {n: v[1] for n, v in pd.items() if n not in pe}
    assert all(pd[n][0] == "output" for n in regs)
    # every flip-flop bit must have become a register port, or the check would silently skip it
    ff_bits = 0
    for c in evert["modules"][top]["cells"].values():
        if c["type"] in FF_CELLS:
            ff_bits += int(c["parameters"]["WIDTH"], 2)
        elif re.fullmatch(r"\$_(DFF|DFFE|SDFF|SDFFE|SDFFCE|DFFSR|DFFSRE|ALDFF|ALDFFE)_\w+_", c["type"]):
            ff_bits += 1
    reg_bits = sum(regs.values())
    init = [l.strip() for l in (args.work / "init.txt").read_text().splitlines() if l.strip()]
    undriven = sorted(set(re.findall(r"Wire [^.\s]+\.\\?(\S+) is used but has no driver",
                                     (args.work / "check.txt").read_text())))
    latches = [l.strip() for l in (args.work / "latches.txt").read_text().splitlines() if l.strip()]
    return dict(top=top, orig=po, bb=bb, regs=regs, ff_bits=ff_bits, reg_bits=reg_bits,
                init=init, latches=latches, undriven=undriven, resolved=resolved)


def roles(args, d):
    o = d["orig"]
    if args.clock not in o or args.reset not in o:
        sys.exit(f"no port {args.clock!r} or {args.reset!r}; ports are {sorted(o)}")
    inputs = [n for n, (dr, _) in o.items() if dr == "input" and n not in (args.clock, args.reset)]
    outputs = [n for n, (dr, _) in o.items() if dr == "output"]
    bb_in = [n for n, (dr, _) in d["bb"].items() if dr == "input"]   # black-box outputs
    bb_out = [n for n, (dr, _) in d["bb"].items() if dr == "output"]  # black-box inputs
    return inputs, outputs, bb_in, bb_out


def xsim(args, d):
    """Registers that are still X after the reset, with every register starting at X."""
    inputs, outputs, bb_in, bb_out = roles(args, d)
    o, active = d["orig"], "1'b0" if args.active_low else "1'b1"
    regs = list(d["regs"])
    lines = ["`timescale 1ns/1ps", "module pu_xsim;", "  reg clk = 0;", f"  reg rst = {active};"]
    lines += [f"  reg [{o[n][1] - 1}:0] i_{k} = 0;" for k, n in enumerate(inputs)]
    lines += [f"  reg [{d['bb'][n][1] - 1}:0] b_{k} = 0;" for k, n in enumerate(bb_in)]
    lines += [f"  wire [{d['regs'][n] - 1}:0] r_{k};" for k, n in enumerate(regs)]
    conns = [f".{vid(args.clock)}(clk)", f".{vid(args.reset)}(rst)"]
    conns += [f".{vid(n)}(i_{k})" for k, n in enumerate(inputs)]
    conns += [f".{vid(n)}(b_{k})" for k, n in enumerate(bb_in)]
    conns += [f".{vid(n)}(r_{k})" for k, n in enumerate(regs)]
    lines += ["  pu_dut dut (" + ", ".join(conns) + ");", "  integer f;", "  initial begin",
              f"    repeat ({args.reset_clocks}) begin #5 clk = 1; #5 clk = 0; end",
              '    f = $fopen("xsim.txt");']
    lines += [f'    $fdisplay(f, "{k} %b", r_{k});' for k in range(len(regs))]
    lines += ["    $fclose(f);", "    $finish;", "  end", "endmodule"]
    (args.work / "pu_xsim.v").write_text("\n".join(lines) + "\n")
    rc = run(args, "iverilog -g2012 -o pu_xsim.vvp pu_xsim.v pu_dut.v && vvp -n pu_xsim.vvp",
             "xsim.log")
    if rc != 0:
        sys.exit(f"X simulation failed, see {args.work}/xsim.log")
    xs = dict(l.split() for l in (args.work / "xsim.txt").read_text().splitlines())
    return [regs[int(k)] for k, v in xs.items() if re.search(r"[xz]", v, re.I)]


def harness(args, d, held):
    inputs, outputs, bb_in, bb_out = roles(args, d)
    o, regs = d["orig"], list(d["regs"])
    active = "1'b0" if args.active_low else "1'b1"
    tt = "uio_out" in outputs and "uio_oe" in outputs
    decl = [f"input [{o[n][1] - 1}:0] i_{k}" for k, n in enumerate(inputs)]
    decl += ["input rst_free"]
    decl += [f"input [{d['bb'][n][1] - 1}:0] b_{k}" for k, n in enumerate(bb_in)]
    L = [f"module pu_harness #(parameter RESET_CLOCKS = {args.reset_clocks}) (",
         "  " + ",\n  ".join(decl), ");",
         "  reg clk = 1'b0;", "  always @($global_clock) clk <= !clk;",
         "  reg [15:0] half = 16'd0;",
         "  always @($global_clock) if (half != 16'hffff) half <= half + 16'd1;",
         "  wire in_reset = half < 2 * RESET_CLOCKS;",
         f"  wire rst = in_reset ? {active} : rst_free;"]
    for c in "ab":
        L += [f"  wire [{o[n][1] - 1}:0] {c}_o{k};" for k, n in enumerate(outputs)]
        L += [f"  wire [{d['bb'][n][1] - 1}:0] {c}_bo{k};" for k, n in enumerate(bb_out)]
        L += [f"  wire [{d['regs'][n] - 1}:0] {c}_r{k};" for k, n in enumerate(regs)]
        conns = [f".{vid(args.clock)}(clk)", f".{vid(args.reset)}(rst)"]
        conns += [f".{vid(n)}(i_{k})" for k, n in enumerate(inputs)]
        conns += [f".{vid(n)}(b_{k})" for k, n in enumerate(bb_in)]
        conns += [f".{vid(n)}({c}_o{k})" for k, n in enumerate(outputs)]
        conns += [f".{vid(n)}({c}_bo{k})" for k, n in enumerate(bb_out)]
        conns += [f".{vid(n)}({c}_r{k})" for k, n in enumerate(regs)]
        L += [f"  pu_dut copy_{c} (" + ",\n    ".join(conns) + ");"]
    L += ["  always @(*) if (!in_reset) begin"]
    for k, n in enumerate(outputs):
        if tt and n == "uio_out":
            oe = outputs.index("uio_oe")
            L += [f"    o_{k}: assert ((a_o{k} & a_o{oe}) == (b_o{k} & b_o{oe}));  // {n}, where enabled"]
        else:
            L += [f"    o_{k}: assert (a_o{k} == b_o{k});  // {n}"]
    L += ["  end"]
    if bb_out:
        L += ["  // black-box inputs agree from the last reset clock on",
              "  always @(*) if (half + 2 >= 2 * RESET_CLOCKS) begin"]
        L += [f"    bb_{k}: assert (a_bo{k} == b_bo{k});  // {n}" for k, n in enumerate(bb_out)]
        L += ["  end"]
    L += ["`ifdef HELPER"]
    L += [f"  always @(*) if (!in_reset) r_{k}: assert (a_r{k} == b_r{k});  // {n}"
          for k, n in enumerate(regs) if n not in held]
    L += ["`endif", "`ifdef HELD_EQ"]
    for k, n in enumerate(regs):
        if n in held:
            L += [f"  always @(*) if (half == 2 * RESET_CLOCKS) assume (a_r{k} == b_r{k});  // {n}",
                  f"  always @(*) if (!in_reset) h_{k}: assert (a_r{k} == b_r{k});"]
    L += ["`endif", "endmodule"]
    (args.work / "pu_harness.sv").write_text("\n".join(L) + "\n")


def sby(args, name, mode, depth, defines=(), chparam=None, engine=None):
    engine = engine or ("abc bmc3" if mode == "bmc" else "smtbmc yices")
    defs = "".join(f" -D {x}" for x in defines)
    text = f"""[options]
mode {mode}
depth {depth}
multiclock on

[engines]
{engine}

[script]
read -formal pu_dut.v
read -formal{defs} pu_harness.sv
{f"chparam -set RESET_CLOCKS {chparam} pu_harness" if chparam is not None else ""}
prep -top pu_harness

[files]
pu_dut.v
pu_harness.sv
"""
    (args.work / f"{name}.sby").write_text(text)
    rc = run(args, f"timeout {args.timeout} sby -f {name}.sby", f"{name}.out")
    out = (args.work / f"{name}.out").read_text()
    m = re.search(r"DONE \((\w+), rc=", out)
    status = m.group(1) if m else ("TIMEOUT" if "Keyboard interrupt" in out or rc == 124 else "ERROR")
    failed = sorted(set(re.findall(r"Assert failed in pu_harness: \S+ \((\w+)\)", out)) |
                    set(re.findall(r"Assert failed in pu_harness: (\w+)\b", out)))
    trace = next(iter(sorted((args.work / name).glob("engine_0/trace*.vcd"))), None)
    return status, failed, trace


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", type=Path, help="Verilog sources")
    ap.add_argument("--tt-project", type=Path, help="a Tiny Tapeout project directory (info.yaml)")
    ap.add_argument("--top", help="top module")
    ap.add_argument("--lib", type=Path, action="append", default=[],
                    help="black-box declarations (read with -lib); instances become ports")
    ap.add_argument("--liberty", type=Path, action="append", default=[],
                    help="Liberty file whose cells are modelled from their functions (standard cells)")
    ap.add_argument("--search", type=Path, action="append", default=[],
                    help="directory searched for unknown modules, read as black boxes")
    ap.add_argument("--blackbox-names", default=r"RM_IHPSG13_\w+|sg13\w+",
                    help="regex: unknown modules that may be read as black boxes (hard macros, cells)")
    ap.add_argument("--ihp", type=Path, help="an IHP PDK root (ihp-sg13cmos5l): standard cells from "
                    "Liberty, SRAM macros as black boxes")
    ap.add_argument("--define", action="append", default=[], help="Verilog define, NAME or NAME=V")
    ap.add_argument("--clock", default="clk")
    ap.add_argument("--reset", default="rst_n")
    ap.add_argument("--active-high", dest="active_low", action="store_false",
                    help="reset is active high (default: active low)")
    ap.add_argument("--reset-clocks", type=int, default=10, help="clocks reset is held (10)")
    ap.add_argument("--clocks", type=int, default=20, help="clocks checked after reset (20)")
    ap.add_argument("--k", type=int, default=8, help="induction depth in half clocks (8)")
    ap.add_argument("--work", type=Path, required=True, help="work directory (created)")
    ap.add_argument("--runner", choices=("podman", "host"), default="podman")
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per formal run (3600)")
    ap.add_argument("--no-prove", action="store_true", help="skip the k-induction")
    args = ap.parse_args()
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.tt_project:
        top, files = tt_project(args.tt_project)
        files += args.files  # extra sources, e.g. the netlist of a hardened macro
        top = args.top or top
    else:
        top, files = args.top, args.files
    if args.tt_project:
        root = args.tt_project.resolve()
        while not (root / ".git").exists() and root.parent != root:
            root = root.parent
        args.search.insert(0, root if (root / ".git").exists() else args.tt_project)
    if args.ihp:
        ref = args.ihp / "libs.ref"
        args.liberty += sorted(ref.glob("*_stdcell/lib/*_stdcell_typ_1p20V_25C.lib"))
        args.search += [p for p in sorted(ref.glob("*_sram/verilog"))]
    if not top or not files:
        ap.error("give --tt-project, or --top and source files")

    d = prep(args, top, files, args.lib)
    report = [f"# Power-up determinism: {top}", "",
              f"{len(d['regs'])} registers, {d['reg_bits']} bits; "
              f"{len(d['bb'])} black-box ports; reset held {args.reset_clocks} clocks."]
    if d["ff_bits"] != d["reg_bits"]:
        report += [f"", f"**WARNING**: {d['ff_bits']} flip-flop bits but {d['reg_bits']} register "
                   "bits exposed; the helper does not cover every flip-flop."]
    if d["resolved"]:
        report += ["", "Black boxes read from (unknown modules):"] + [f"- {x}" for x in d["resolved"]]
    if d["latches"]:
        report += ["", "**Latches** (not modelled faithfully; results are unreliable):"] + \
                  [f"- `{x}`" for x in d["latches"]]
    if d["init"]:
        report += ["", "**Initial values** (an ASIC ignores these; they were dropped):"] + \
                  [f"- `{x}`" for x in d["init"]]

    held = xsim(args, d)
    from_xsim = list(held)
    harness(args, d, set(held))
    rounds = 0
    while True:
        status, failed, _ = sby(args, "refine", "bmc", 2 * (args.reset_clocks + args.clocks),
                                ("HELPER", "HELD_EQ"))
        new = [list(d["regs"])[int(f[2:])] for f in failed if f.startswith("r_")]
        if status == "PASS" or not new:
            break
        held += new
        rounds += 1
        harness(args, d, set(held))
    refine_status = status
    report += ["", f"## Registers reset does not clear ({len(held)})", ""]
    report += [f"- `{n}` ({d['regs'][n]} bits){'' if n in from_xsim else ' (found by the formal check, not by X simulation)'}"
               for n in held] or ["none"]
    if refine_status != "PASS":
        report += ["", f"Refinement ended {refine_status} after {rounds} rounds (failed: {failed}); see refine.out."]

    rows = []
    depth = 2 * (args.reset_clocks + args.clocks)
    for name, mode, dp, defs, ch, expect in (
            ("pins", "bmc", depth, (), None, "PASS"),
            ("held", "bmc", depth, ("HELD_EQ",), None, "PASS"),
            ("noreset", "bmc", 2 * args.clocks, (), 0, "FAIL"),
            ("prove", "prove", args.k, ("HELPER", "HELD_EQ"), None, "PASS")):
        if name == "prove" and args.no_prove:
            continue
        st, fl, tr = sby(args, name, mode, dp, defs, ch)
        _, outputs, _, bb_out = roles(args, d)
        named = {**{f"o_{k}": n for k, n in enumerate(outputs)},
                 **{f"bb_{k}": f"black-box input {n}" for k, n in enumerate(bb_out)}}
        rows.append((name, st, expect, ", ".join(named.get(f, f) for f in fl), tr))
    # undriven bits become free inputs, independent in each copy: on silicon they float
    undriven = d["undriven"]
    if undriven:
        report += ["", f"## Undriven signals ({len(undriven)}; free and independent in each copy)", ""]
        report += [f"- `{u}`" for u in undriven]
    status = {n: s for n, s, *_ in rows}
    if status["pins"] == "FAIL":
        cause = "registers that reset does not clear" + (" or undriven signals" if undriven else "")
        verdict = (f"**Not deterministic**: {cause} reach the outputs within {args.clocks} clocks "
                   "of reset (pins FAILs; trace in the table).")
    elif status["pins"] != "PASS" or status["noreset"] != "FAIL":
        verdict = "**Inconclusive**: see the checks below."
    elif status.get("prove") == "PASS" and not held:
        verdict = "**Deterministic**: proved for all time after reset."
    else:
        verdict = (f"**Deterministic for {args.clocks} clocks** after reset (bounded); "
                   + ("proved for all time if the uncleared registers agree at reset."
                      if status.get("prove") == "PASS" else "no unbounded proof."))
    report[2:2] = [verdict, ""]
    report += ["", "## Checks", "", "| check | result | expected | failed | trace |", "|---|---|---|---|---|"]
    report += [f"| {n} | {s} | {e} | {f} | {t.relative_to(args.work) if t else ''} |"
               for n, s, e, f, t in rows]
    report += ["", "pins: outputs agree for the checked clocks after reset, uncleared registers free.",
               "held: the same, with the uncleared registers equal when reset ends.",
               "noreset: no reset; must fail, or the outputs do not depend on the registers at all.",
               "prove: k-induction, every register equal as the helper, uncleared ones equal at reset."]
    (args.work / "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
