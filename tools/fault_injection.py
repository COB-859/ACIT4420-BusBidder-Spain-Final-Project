"""Fault injection: would the tests notice if the program were wrong?

    python3 tools/fault_injection.py

The script copies the project to a temporary folder. In the copy it makes
one small change to the program at a time, such as "<" into "<=" or "and"
into "or", runs the unit tests, and notes whether a test failed. A change
that no test notices is a survivor: a rule that could be broken without
the tests saying so. The project itself is not changed.

Not every survivor is a gap. Some changes do not alter what the program
does (an equivalent change), for example in a log message.

The changes are chosen by a fixed rule (every k-th candidate), so the
result is the same on every run. It takes a few minutes.
"""

from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Every module of the program except the two that hold no rule (exceptions, logsetup).
MODULES = ["booking", "cli", "constraints", "criteria", "display", "explain", "loader", "models", "ranking",
           "validation"]
TARGET = 150
# (text to find, replacement). Spaces keep "->" and "<=" apart from "<" and ">".
CHANGES = [
    (" <= ", " < "), (" < ", " <= "), (" >= ", " > "), (" > ", " >= "),
    (" == ", " != "), (" != ", " == "), (" and ", " or "), (" or ", " and "),
    (" + ", " - "), (" - ", " + "), (" is None", " is not None"), (" is not None", " is None"),
    ("if not ", "if "), ("True", "False"), ("False", "True"),
]


def outside_quotes(line: str, position: int) -> bool:
    """Return True when the position is not inside a string on this line."""
    before = line[:position]
    return before.count('"') % 2 == 0 and before.count("'") % 2 == 0


def candidates() -> list[tuple[str, int, str, str]]:
    """Return every possible change as (module, line number, old line, new line)."""
    found = []
    for module in MODULES:
        in_docstring = False
        lines = (ROOT / "busbidder" / f"{module}.py").read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            quotes = stripped.count('"""')
            if in_docstring or quotes:
                # A docstring is text, not a rule. One line can open and close it.
                if quotes % 2 == 1:
                    in_docstring = not in_docstring
                continue
            if not stripped or stripped.startswith(("#", "import ", "from ", "@", "logger.")):
                continue
            code = line.split("  # ")[0]
            for old, new in CHANGES:
                for match in re.finditer(re.escape(old), code):
                    if outside_quotes(code, match.start()):
                        changed = code[:match.start()] + new + code[match.end():] + line[len(code):]
                        found.append((module, number, line, changed))
    return found


def tests_fail(folder: Path) -> bool:
    """Run the unit tests in the copy and return True when a test fails or the run hangs."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-f", "-q"],
            cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return True
    return result.returncode != 0


def main() -> int:
    """Inject the faults and print how many the tests caught."""
    every = candidates()
    step = max(1, -(-len(every) // TARGET))
    chosen = every[::step]
    with tempfile.TemporaryDirectory() as temp:
        copy = Path(temp) / "project"
        for name in ("busbidder", "tests", "tools", "data"):
            shutil.copytree(ROOT / name, copy / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "main.py", copy / "main.py")
        if tests_fail(copy):
            print("The tests do not pass on the unchanged copy, so nothing can be measured.")
            return 1
        caught: dict[str, int] = {}
        injected: dict[str, int] = {}
        survivors = []
        for module, number, old_line, new_line in chosen:
            path = copy / "busbidder" / f"{module}.py"
            original = path.read_text(encoding="utf-8")
            lines = original.splitlines(keepends=True)
            ending = lines[number - 1][len(lines[number - 1].rstrip("\r\n")):]
            lines[number - 1] = new_line + ending
            path.write_text("".join(lines), encoding="utf-8")
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError:
                path.write_text(original, encoding="utf-8")
                continue        # not valid Python, so not a fault a test could miss
            injected[module] = injected.get(module, 0) + 1
            if tests_fail(copy):
                caught[module] = caught.get(module, 0) + 1
            else:
                survivors.append((module, number, old_line.strip(), new_line.strip()))
            path.write_text(original, encoding="utf-8")

    total, total_caught = sum(injected.values()), sum(caught.values())
    print(f"Faults injected: one change at a time, every {step}th of {len(every)} possible changes "
          f"in {len(MODULES)} of the 12 modules.")
    print(f"\n{'Module':<14}{'Injected':>9}{'Caught':>8}{'Survived':>10}")
    for module in MODULES:
        made = injected.get(module, 0)
        print(f"{module:<14}{made:>9}{caught.get(module, 0):>8}{made - caught.get(module, 0):>10}")
    print(f"{'Total':<14}{total:>9}{total_caught:>8}{total - total_caught:>10}")
    print(f"\nThe tests caught {total_caught} of {total} injected faults "
          f"({100 * total_caught / total:.0f} percent).")
    if survivors:
        print("\nSurvivors (a change no test noticed):")
        for module, number, old, new in survivors:
            print(f"  busbidder/{module}.py:{number}")
            print(f"      was:  {old[:110]}")
            print(f"      made: {new[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
