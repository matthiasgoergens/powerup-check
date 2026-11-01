# powerup-check

Does your chip behave the same every time it is powered up and reset?

On an ASIC, flip-flops start with arbitrary values: `initial` blocks and `reg x = 0` do nothing in silicon. A register that reset does not clear can make the outputs depend on power-up state, which simulation never shows, because simulators start registers at 0 or X. This tool checks for it formally, on the design as written. It was written for Tiny Tapeout projects but works on any synchronous design with a clock and a reset.

## How it works

Two copies of the design start from independent, arbitrary register contents and get the same inputs. Reset is held for `--reset-clocks` clocks; after that it is an input like any other (shared, and free to be asserted again). From then on, the copies' outputs must agree; for a Tiny Tapeout top, `uio_out` is compared only where `uio_oe` enables it. The checks are run by SymbiYosys (sby), on a model in which each clock is two steps (rising and falling edge), so depths below are counted in half clocks.

1. **prep**: yosys flattens the design, maps memories to flip-flops, removes initial values (and lists them), turns black-box instances into ports and every register into an output.
2. **xsim**: iverilog runs the reset with every register at X; the registers still X afterwards are the first guess at "not cleared by reset".
3. **refine**: a bounded check (reset plus `--clocks` clocks) that every other register agrees between the copies after reset. A register that does not is added to the set of uncleared registers, and the check is repeated until it passes.
4. **checks**:
   - `pins`: the outputs agree for `--clocks` clocks after reset, with the uncleared registers free. A failure comes with a trace (VCD).
   - `held`: the same, with the uncleared registers assumed equal when reset ends.
   - `noreset`: the control: reset is never forced, only free like the other inputs. This must fail; if it passes, the outputs do not depend on any register and the other checks say nothing.
   - `prove`: an unbounded proof by k-induction. Induction needs a property strong enough to carry itself from one step to the next, and "the outputs agree" is not; "every register agrees" is, so that is what is proved, with the uncleared registers assumed equal when reset ends.

The report (`report.md` in the work directory) starts with a one-line verdict:

- **Not deterministic**: uncleared registers (or undriven signals) reach the outputs within the checked clocks. The tool then replays the failing trace in simulation with some registers starting equal in both copies, and names a set of registers whose power-up difference alone is enough to make the outputs differ: usually one register or memory word, which tells you where to look. Other sets may be enough too; this is the first one the search finds.
- **Deterministic for N clocks**: the bounded check passed. The line goes on to say what happened to the unbounded proof: not run, did not succeed, or proved on the condition that the uncleared registers agree when reset ends.
- **Deterministic**: no register is left uncleared and the induction proof passed, so it holds for all time after reset.
- **Inconclusive**: `pins` or `noreset` timed out or failed to run, or `noreset` passed. When `pins` times out, the verdict says for how many clocks after reset the outputs were checked to agree before it stopped.

It also lists the registers reset does not clear, initial values that were dropped, undriven signals, latches, and black boxes.

## Running it

It is one Python script (3.11 or later, standard library only); clone the repository and run it. It needs yosys, sby, yices and iverilog on `PATH`: the [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) has all of them (put its `bin` directory on `PATH`). On Linux it runs the tools under `nice` and `ionice` when they exist.

```
python3 powerup_check.py --tt-project path/to/your/tt-project --work /var/tmp/pc-work
python3 powerup_check.py rtl/*.v --top my_top --clock clk --reset rst_n --work /var/tmp/pc-work
python3 tests/run_tests.py /var/tmp/pc-tests     # one "ok" line per test design, a few minutes
```

`--tt-project` reads `top_module` and `source_files` (relative to `src/`) from the project's `info.yaml`. Options:

- `--top`, `--clock` (default `clk`), `--reset` (default `rst_n`), `--active-high` for an active-high reset.
- `--reset-clocks N` (default 10), `--clocks N` (default 20), `--k N` (induction depth in half clocks, default 8), `--timeout S` per formal run (default 3600), `--no-prove`.
- `--ihp PDK_ROOT` for IHP designs (e.g. `ihp-sg13cmos5l`): standard cells are modelled from their Liberty file, SRAM macros (`RM_IHPSG13_*`) become black boxes.
- `--liberty FILE`: cells modelled from the functions in a Liberty (`.lib`) cell library. `--lib FILE`: modules read as black boxes (declarations only). `--search DIR`: where to look for unknown modules; only names matching `--blackbox-names` (default: IHP SRAM macros and `sg13*` cells) are resolved this way, since an ordinary module read as a black box would hide its registers. `--define NAME`.
- With `--tt-project`, extra files on the command line are added to the sources, e.g. the gate-level netlist of a hardened macro.
- `--runner podman` runs the tools in an offline podman container (image `debian:trixie`, or `POWERUP_IMAGE`) with the OSS CAD Suite mounted read-only; set `POWERUP_OCS` to its directory. `--cpus N` (default 4) limits the container.

`tests/run_tests.py` runs the tool on the small designs in `tests/` and checks each verdict. Four are deterministic: `good`, `dead` (a register that is never reset but always loaded before it is shown), `bbram` (a black-box RAM macro) and `ram` (a RAM read only after it is written, so it is "Deterministic for N clocks" with its words uncleared). The other seven each leak power-up state in a different way: `leaky` (a mode bit), `initval` (an initial value), `async_leak` (a falling-edge register next to an asynchronously reset counter), `partial` (a partly reset register), `divclk` (a divided clock), `inreset` (a reset that depends on an input) and `late` (an undriven bit that reaches the output after 15 clocks).

## Assumptions and limits

- **Inputs are shared.** Both copies get the same inputs at all times, including during reset. A register that is only cleared when some input has a particular value during reset counts as not cleared.
- **Black boxes are assumed deterministic.** Both copies see the same black-box outputs; the tool asserts that their inputs agree from the last reset clock on, and assumes the black boxes' own state (e.g. SRAM contents) is equal at reset. SRAM contents are not cleared by anything, so a design that reads SRAM before writing it is not caught.
- **`x` constants are tied to 0.** Synthesis turns each `x` into one fixed value, the same in every chip; both copies are the same chip, so they share it. The tool uses 0; a design whose answer depends on which value synthesis picks (say, a reset that only happens when an `x` comes out as 1) is checked only for 0. **Undriven signals** are left free and independent in each copy, since on silicon they float.
- **Bounded by default.** `pins` covers `--clocks` clocks after reset. The induction proof needs every register in the helper, which is expensive on large designs and may time out.
- **Latches** are modelled by yosys (its `multiclock` mode); they are listed in the report and have had less testing than flip-flops, so check those by hand.
- **Single clock.** Other clock-like inputs are treated as data. A register clocked by a divided clock works, as the clock is generated inside the design.
- **Registers optimised away** by yosys, because nothing reads them (no output, no black box), are not checked. The report warns if the number of exposed register bits differs from the number of flip-flop bits.
- **The X simulation** drives the inputs to 0; it is only the first guess, and the formal refine step corrects it.

## Origin

Written for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/) on Tiny Tapeout's IHP shuttle, after an unbounded proof of the same property on our own design found a 37-bit configuration chain that reset never clears ([protocol-emulator-notes](https://github.com/matthiasgoergens/protocol-emulator-notes), chip-top README section 9). Run on that design with no design-specific setup, this tool finds the same 37 registers.

## Licence

Apache 2.0, see LICENSE.
