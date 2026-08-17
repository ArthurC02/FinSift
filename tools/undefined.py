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

# ---------------------------------------------------------------------------
# Second pass: attributes read off an IMPORTED MODULE (`fin.page_num`).
#
# The pass above reads LOAD_GLOBAL, so it sees a lost import but not a lost
# re-export: in `fin.page_num` the name `fin` IS in globals and `page_num` is
# a LOAD_ATTR. That is the same failure - a name that does not resolve at
# runtime - and it is the one this repo is most exposed to, because cli
# reaches both extractors only through their package facades.
#
# It went unnoticed for several commits: `fin.page_num` raised on every real
# fin_report run while the L3 tests stubbed that function out and ab.py never
# called it. Static, per-module, no folder needed. See docs/VERIFICATION.md.
import ast

attr_problems = 0
for name in MODULES:
    path = SRC.joinpath(*name.split(".")).with_suffix(".py")
    if not path.exists():                       # package -> __init__.py
        path = SRC.joinpath(*name.split("."), "__init__.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # local alias -> the module object it is bound to, from this file's imports
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in loaded:
                    aliases[a.asname or a.name.split(".")[0]] = loaded[a.name]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                sub = f"{node.module}.{a.name}"
                if sub in loaded:
                    aliases[a.asname or a.name] = loaded[sub]

    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and not hasattr(aliases[node.value.id], node.attr)):
            print(f"{name}:{node.lineno}: '{node.value.id}.{node.attr}' "
                  f"is not provided by that module")
            attr_problems += 1

problems += attr_problems

print(f"\n{len(loaded)}/{len(MODULES)} modules checked, {problems} missing-import reference(s) "
      f"({attr_problems} of them module-attribute)")

# `len(loaded) != len(MODULES)` is only a RELATIVE floor - it asks whether
# everything discovered imported, not whether anything was discovered. MODULES
# comes from a glob, so a moved src/ makes it empty and "0/0 modules checked"
# exits 0. Assert the absolute denominator too. See docs/VERIFICATION.md §分母.
if not MODULES:
    sys.exit(f"undefined.py: discovered 0 modules under {SRC}. HARNESS FAILURE, "
             f"not a result - a check that examined nothing cannot report success.")
sys.exit(1 if problems or len(loaded) != len(MODULES) else 0)
