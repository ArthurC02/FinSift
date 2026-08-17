"""Find global names a function body references that its own module doesn't
define or import - i.e. imports lost during a symbol move. Walks nested code
objects too (comprehensions, closures, inner defs) and functions hidden inside
module-level data structures.

Reports a name only if some OTHER project module defines it, which filters out
the attribute names that also land in co_names.

Usage: python tools/undefined.py     (exit 1 if anything unresolved)
"""
import builtins
import dis
import importlib
import sys
import types
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# Every module under src/, as a dotted name. Picks up future packages
# (core/, cli/, ...) without needing this list maintained by hand.
MODULES = sorted(
    ".".join(p.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
    for p in SRC.rglob("*.py")
    if "__pycache__" not in p.parts
)

loaded = {}
for name in MODULES:
    try:
        loaded[name] = importlib.import_module(name)
    except Exception as e:
        print(f"!! {name}: import failed: {type(e).__name__}: {e}")

# every name any project module defines
provided = {}
for name, mod in loaded.items():
    for attr in vars(mod):
        provided.setdefault(attr, []).append(name)

BUILTIN = set(dir(builtins))


def codes(obj):
    yield obj
    for const in obj.co_consts:
        if isinstance(const, types.CodeType):
            yield from codes(const)


def global_loads(code):
    """Names actually read as globals. co_names also carries attribute names,
    so `af.collect_summary_rows` would otherwise look like a missing import.
    A lost import is always a bare call -> LOAD_GLOBAL, so nothing real is lost.
    """
    return {i.argval for i in dis.get_instructions(code)
            if i.opname in ("LOAD_GLOBAL", "STORE_GLOBAL", "DELETE_GLOBAL")}


def walk_functions(obj, depth=0):
    """Yield every function reachable inside nested dicts/lists/tuples."""
    if depth > 6:
        return
    if isinstance(obj, types.FunctionType):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_functions(v, depth + 1)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from walk_functions(v, depth + 1)


problems = 0
for name, mod in sorted(loaded.items()):
    for attr, val in sorted(vars(mod).items()):
        if isinstance(val, type):         # class: check its methods
            members = list(vars(val).values())
        elif isinstance(val, types.FunctionType):
            members = [val]
        else:
            # functions also hide INSIDE module-level data - e.g. the lambdas
            # in LOAN_RECOMPOSITION's nested dicts. Missing those is how a
            # real missing import survived the first pass of this check.
            members = list(walk_functions(val))
        # only what this module actually DEFINES - an imported function
        # resolves its globals in its own module, not here
        members = [m for m in members
                   if isinstance(m, types.FunctionType) and m.__globals__.get("__name__") == name]
        if not members:
            continue
        for m in members:
            g = m.__globals__
            for co in codes(m.__code__):
                for used in global_loads(co):
                    if used in g or used in BUILTIN:
                        continue
                    if used in provided:
                        print(f"{name}.{attr}: '{used}' unresolved "
                              f"(defined in {provided[used]})")
                        problems += 1

print(f"\n{len(loaded)}/{len(MODULES)} modules checked, {problems} missing-import reference(s)")

# `len(loaded) != len(MODULES)` is only a RELATIVE floor - it asks whether
# everything discovered imported, not whether anything was discovered. MODULES
# comes from a glob, so a moved src/ makes it empty and "0/0 modules checked"
# exits 0. Assert the absolute denominator too. See docs/VERIFICATION.md §分母.
if not MODULES:
    sys.exit(f"undefined.py: discovered 0 modules under {SRC}. HARNESS FAILURE, "
             f"not a result - a check that examined nothing cannot report success.")
sys.exit(1 if problems or len(loaded) != len(MODULES) else 0)
