# powerup-check

Does your chip behave the same every time it is powered up and reset?

On an ASIC, flip-flops start with arbitrary values: `initial` blocks and `reg x = 0` do nothing in silicon. A register that reset does not clear can make the outputs depend on power-up state, which simulation never shows, because simulators start registers at 0 or X. This tool checks for it formally, on the design as written. It was written for Tiny Tapeout projects but works on any synchronous design with a clock and a reset.

## How it works

Two copies of the design start from independent, arbitrary register contents and get the same inputs. Reset is held for `--reset-clocks` clocks. From then on, their outputs must agree.

1. **prep**: yosys flattens the design, maps memories to flip-flops, removes initial values (and lists them), turns black-box instances into ports and every register into an output.
2. **xsim**: iverilog runs the reset with every register at X; the registers still X afterwards are the first guess at "not cleared by reset".
3. **refine**: a bounded check that every other register agrees between the copies after reset. A register that does not is added to the uncleared set, until the check passes.
4. **checks**:
   - `pins`: the outputs agree for `--clocks` clocks after reset, with the uncleared registers free. A failure comes with a trace (VCD).
   - `held`: the same, with the uncleared registers assumed equal when reset ends.
   - `noreset`: the control: no reset at all. This must fail; if it passes, the outputs do not depend on any register and the other checks say nothing.
   - `prove`: k-induction (unbounded), with every register equal as the inductive helper and the uncleared registers assumed equal when reset ends.

The report (`report.md` in the work directory) starts with a one-line verdict:

- **Not deterministic**: uncleared registers (or undriven signals) reach the outputs within the checked clocks.
- **Deterministic for N clocks**: the bounded check passed, and some registers are not cleared by reset.
- **Deterministic**: no register is left uncleared and the induction proof passed, so it holds for all time after reset.
- **Inconclusive**: a check timed out or failed to run.

It also lists the registers reset does not clear, initial values that were dropped, undriven signals, latches, and black boxes.

## Running it

Needs Python 3.11 and the [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) (yosys, sby, yices, iverilog) on `PATH`.

```
./powerup_check.py --tt-project path/to/your/tt-project --work /var/tmp/pc-work
./powerup_check.py rtl/*.v --top my_top --clock clk --reset rst_n --work /var/tmp/pc-work
```

`--tt-project` reads `top_module` and `source_files` from `info.yaml`. Useful options:

- `--reset-clocks N` (default 10), `--clocks N` (default 20), `--k N` (induction depth in half clocks, default 8), `--timeout S` per formal run, `--no-prove`.
- `--active-high` for an active-high reset.
- `--ihp PDK_ROOT` for IHP designs (e.g. `ihp-sg13cmos5l`): standard cells are modelled from their Liberty file, SRAM macros (`RM_IHPSG13_*`) become black boxes.
- `--liberty FILE`, `--lib FILE` (black-box declarations), `--search DIR`, `--define NAME`.
- With `--tt-project`, extra files on the command line are added to the sources, e.g. the gate-level netlist of a hardened macro.
- `--runner podman` runs the tools in an offline container with the OSS CAD Suite mounted read-only; set `POWERUP_OCS` to its directory.

`tests/run_tests.py WORKDIR` runs the tool on the small designs in `tests/` and checks each verdict. Some are deterministic, the others each leak power-up state in a different way (asynchronous reset, a falling-edge register, a partly reset register, a divided clock, a reset that depends on an input, a leak after 15 clocks, an initial value).

## Assumptions and limits

- **Inputs are shared.** Both copies get the same inputs at all times, including during reset. A register that is only cleared when some input has a particular value during reset counts as not cleared.
- **Black boxes are assumed deterministic.** Both copies see the same black-box outputs; the tool asserts that their inputs agree from the last reset clock on, and assumes the black boxes' own state (e.g. SRAM contents) is equal at reset. SRAM contents are not cleared by anything, so a design that reads SRAM before writing it is not caught.
- **`x` constants are tied to 0.** Synthesis picks one fixed value, the same in every chip, so for comparing two copies of one chip the choice does not matter. **Undriven signals** are left free and independent in each copy, since on silicon they float.
- **Bounded by default.** `pins` covers `--clocks` clocks after reset. The induction proof needs every register in the helper, which is expensive on large designs and may time out.
- **Latches** are modelled by yosys under `multiclock on`; they are listed in the report.
- **Single clock.** Other clock-like inputs are treated as data. A register clocked by a divided clock works, as the clock is generated inside the design.
- **Registers optimised away** by yosys (outputs never used) are not checked; they cannot affect the outputs. The report warns if the number of exposed register bits differs from the number of flip-flop bits.

## Origin

Written for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/) on Tiny Tapeout's IHP shuttle, after an unbounded proof of the same property on our own design found a 37-bit configuration chain that reset never clears ([protocol-emulator-notes](https://github.com/matthiasgoergens/protocol-emulator-notes), chip-top README section 9). Run on that design with no design-specific setup, this tool finds the same 37 registers.

## Licence

Apache 2.0, see LICENSE.
