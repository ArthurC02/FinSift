"""Move symbols verbatim between modules by exact line span.

Usage: python move.py <spec.json>

spec = {
  "source": "src/acctfinder.py",
  "target": "src/core/text.py",
  "docstring": "...",
  "target_imports": ["import re", "from pathlib import Path"],
  "spans": [[293, 297], ...],          # 1-indexed inclusive, in file order
  "import_after": "from pathlib import Path",   # anchor line in source
  "import_module": "core.text",
  "also_update": ["src/callfinder.py"]  # rewrite `from acctfinder import X` -> core
}

Lines are copied byte-for-byte. Nothing is reformatted, renamed or
re-indented - that is the whole point, since the line-multiset check in
REFACTOR_PLAN §9 (V2) only works if the moved lines are identical.
"""
import ast
import json
import re
import sys
from pathlib import Path


def names_in_spans(src_lines, spans):
    """Top-level names defined inside the given spans."""
    tree = ast.parse("".join(src_lines))
    names = []
    for node in tree.body:
        if not any(s <= node.lineno <= e for s, e in spans):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
    return names


def collapse_blanks(text):
    return re.sub(r"\n{4,}", "\n\n\n", text)


def main(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    source = Path(spec["source"])
    src_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    spans = [tuple(s) for s in spec["spans"]]

    moved_names = names_in_spans(src_lines, spans)
    chunks = ["".join(src_lines[s - 1:e]) for s, e in spans]

    target = Path(spec["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    header = f'"""{spec["docstring"]}"""\n' + "\n".join(spec["target_imports"]) + "\n\n\n"
    target.write_text(collapse_blanks(header + "\n\n".join(c.rstrip("\n") + "\n" for c in chunks)),
                      encoding="utf-8")

    # delete back-to-front so earlier spans keep their indices
    kept = list(src_lines)
    for s, e in sorted(spans, reverse=True):
        del kept[s - 1:e]
    rest = collapse_blanks("".join(kept))

    # only import back what the source still references
    still_used = [n for n in moved_names
                  if re.search(rf"(?<![\w.]){re.escape(n)}\b", rest)]
    if still_used:
        anchor = spec["import_after"]
        line = f"from {spec['import_module']} import " + ", ".join(still_used) + "\n"
        rest = rest.replace(anchor + "\n", anchor + "\n" + line, 1)
    source.write_text(rest, encoding="utf-8")

    print(f"moved {len(moved_names)} symbol(s) -> {target}")
    print(f"  {', '.join(moved_names)}")
    print(f"  re-imported into {source.name}: {', '.join(still_used) or '(none)'}")

    # rewrite other modules that imported these from the source module
    src_mod = source.stem
    for other in spec.get("also_update", []):
        p = Path(other)
        text = p.read_text(encoding="utf-8")
        m = re.search(rf"from {src_mod} import \(\n((?:.*\n)*?)\)\n", text)
        if not m:
            continue
        entries = [ln for ln in m.group(1).splitlines()]
        take = [ln for ln in entries if ln.strip().rstrip(",") in moved_names]
        if not take:
            continue
        keep = [ln for ln in entries if ln not in take]
        taken_names = [ln.strip().rstrip(",") for ln in take]
        new_block = (f"from {spec['import_module']} import " + ", ".join(taken_names) + "\n"
                     + f"from {src_mod} import (\n" + "\n".join(keep) + "\n)\n")
        p.write_text(text[:m.start()] + new_block + text[m.end():], encoding="utf-8")
        print(f"  {p.name}: moved {len(taken_names)} import(s) to {spec['import_module']}")


if __name__ == "__main__":
    main(sys.argv[1])
