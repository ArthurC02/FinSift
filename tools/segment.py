"""Segment a module into top-level chunks (name -> exact line span),
extending each span upward over any contiguous preceding comment block so
comments travel with the code they document. Used to move symbols between
files verbatim - no reformatting, no rewriting.

Usage: python tools/segment.py src/acctfinder.py
"""
import ast
import sys
from pathlib import Path


def segments(path):
    src = Path(path).read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(src))
    nodes = tree.body
    out = []
    for i, node in enumerate(nodes):
        start = node.lineno
        # decorators sit above node.lineno
        for d in getattr(node, "decorator_list", []):
            start = min(start, d.lineno)
        # walk upward over contiguous comment / blank lines, but stop before
        # the previous node's last line
        prev_end = (nodes[i - 1].end_lineno or 0) if i else 0
        # Walk up over comments AND blank lines; `first_comment` only moves on
        # a real comment line, so a banner block separated from its def by a
        # blank line still travels with the def, while trailing blank lines
        # stay with the previous segment.
        j = start - 1  # 1-indexed line above this node
        first_comment = start
        while j > prev_end:
            line = src[j - 1].strip()
            if line.startswith("#"):
                first_comment = j
                j -= 1
            elif line == "":
                j -= 1
            else:
                break
        start = first_comment
        out.append({
            "name": names_of(node),
            "kind": type(node).__name__,
            "start": start,
            "end": node.end_lineno,
        })
    return out, src


def names_of(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        out = []
        for t in node.targets:
            # `A, B = 1, 2` is one statement defining two names
            elts = t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]
            out += [e.id for e in elts if isinstance(e, ast.Name)]
        return out
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return ["<import>"]
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return ["<docstring>"]
    if isinstance(node, ast.If):
        return ["<if>"]
    if isinstance(node, ast.Try):
        return ["<try>"]
    return ["<" + type(node).__name__ + ">"]


if __name__ == "__main__":
    segs, src = segments(sys.argv[1])
    for s in segs:
        print(f"{s['start']:5d}-{s['end']:5d}  {s['kind']:12s}  {','.join(s['name'])}")
    # coverage check: every line must belong to exactly one segment or be a gap
    covered = set()
    for s in segs:
        for ln in range(s["start"], s["end"] + 1):
            covered.add(ln)
    gaps = [ln for ln in range(1, len(src) + 1) if ln not in covered and src[ln - 1].strip()]
    print(f"\nTOTAL {len(segs)} segments, {len(src)} lines")
    print(f"UNCOVERED non-blank lines: {gaps}")
