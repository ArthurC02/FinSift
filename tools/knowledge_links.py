"""Verify every knowledge-doc anchor cited from the code still exists (V5).

The reason this tool exists. Moving the long explanations out of the source and
into `docs/knowledge/` buys shorter files and pays for it with distance: the
prose no longer sits where someone edits, and nothing makes it wrong when the
code moves on. `docs/HANDOFF.md` is this repo's own evidence - written as the
complete maintainer's manual, and its first section is now a table of which
chapters went stale.

That risk cannot be removed, but half of it can. A pointer to a section that no
longer exists is mechanically detectable, so it should never survive a commit.
This checks the link, NOT the accuracy of what is behind it - a section can
still rot while its anchor stays valid. Treat a green run as "the map still has
this address", never as "the description is still true".

    python tools/knowledge_links.py

Exit 1 on either direction being broken:
  - a citation whose target section is missing (usually a rename that updated
    one side only), and
  - a module carrying substantial prose that cites nothing at all - knowledge
    with no pointer from the code it governs, which is how a knowledge base
    stops being read.
Sections nothing cites are reported but do not fail: a heading can legitimately
exist as context for its neighbours.
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "docs" / "knowledge"

# A module carrying this much prose and citing nothing is the drift this
# repo actually suffers from: knowledge written in a place nobody is sent to,
# and no pointer from the code it governs. Generous on purpose - a facade or
# a short pure-transform module clears it without a citation.
_PROSE_LINES_NEEDING_A_CITATION = 20

# ...but only for src/. A tool's docstring IS its manual - you read it when you
# run the tool - and what it explains is the VERIFICATION protocol, which lives
# in docs/VERIFICATION.md, not docs/knowledge/. Requiring a citation there
# flagged all four tools on this check's first run. Citations in tools/ are
# still followed and must resolve; they just aren't required.
_MUST_CITE = "src"

# A citation looks like `→ docs/knowledge/<doc>.md#<anchor>` in a comment.
# Written without a literal example on purpose: this file is scanned like any
# other source, so an illustrative citation here would be checked as a real one
# (it was, and it failed - which is at least the tool working).
# The stem class must include '-': the knowledge docs are named by TOPIC
# (account-codes, entity-resolution), and a stem class of [\w.] silently
# matched nothing at all for those - every citation to them counted as
# uncited rather than as broken, so the tool reported 0 broken while checking
# none of them.
CITATION_RE = re.compile(r"docs/knowledge/([\w.-]+)\.md#([\w-]+)")
# A markdown heading, reduced to the anchor GitHub would generate for it.
HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.M)


def anchor_of(heading):
    """GitHub's slug rules, restricted to what these docs actually use."""
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    return re.sub(r"\s+", "-", slug)


def prose_lines(source):
    """How many lines of `source` are comment or docstring."""
    lines = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            lines.add(token.start[0])
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                lines.update(range(body.lineno, body.end_lineno + 1))
    return len(lines)


def main():
    if not KNOWLEDGE.is_dir():
        sys.exit(f"no knowledge directory at {KNOWLEDGE}")

    available = {}
    for doc in sorted(KNOWLEDGE.glob("*.md")):
        available[doc.stem] = {anchor_of(h) for h in HEADING_RE.findall(doc.read_text(encoding="utf-8"))}

    cited, broken, unpointed = set(), [], []
    # The per-package AGENTS.md files cite knowledge sections too, as ordinary
    # markdown links. They rot exactly like a comment's citation does, so they
    # are scanned on the same terms - only the prose check below is Python-only.
    sources = (sorted(ROOT.joinpath("src").rglob("*.py"))
               + sorted(ROOT.joinpath("tools").glob("*.py"))
               + [ROOT / "AGENTS.md"] + sorted(ROOT.joinpath("src").rglob("AGENTS.md")))
    for path in sources:
        source = path.read_text(encoding="utf-8")
        found_here = 0
        for lineno, line in enumerate(source.splitlines(), 1):
            for stem, anchor in CITATION_RE.findall(line):
                found_here += 1
                cited.add((stem, anchor))
                if anchor not in available.get(stem, set()):
                    where = f"{path.relative_to(ROOT)}:{lineno}"
                    reason = ("no such document" if stem not in available
                              else "no such section")
                    broken.append(f"  {where}: docs/knowledge/{stem}.md#{anchor} - {reason}")
        # The reverse direction: prose that sends the reader nowhere.
        if (not found_here and path.suffix == ".py"
                and path.relative_to(ROOT).parts[0] == _MUST_CITE):
            n = prose_lines(source)
            if n >= _PROSE_LINES_NEEDING_A_CITATION:
                unpointed.append(f"  {path.relative_to(ROOT)}: {n} lines of prose, 0 citations")

    uncited = sorted(f"  docs/knowledge/{stem}.md#{a}"
                     for stem, anchors in available.items() for a in anchors
                     if (stem, a) not in cited)

    if broken:
        print(f"{len(broken)} broken citation(s):")
        print("\n".join(broken))
    if uncited:
        print(f"{len(uncited)} knowledge section(s) nothing points at:")
        print("\n".join(uncited))
        print("  (a section no code cites is one nobody will be sent to read)")
    if unpointed:
        print(f"{len(unpointed)} module(s) with substantial prose and no citation:")
        print("\n".join(unpointed))
        print("  (either the long-form belongs in docs/knowledge/ with a `→` line\n"
              "   pointing at it, or it is short enough to stay and this is noise -\n"
              "   see AGENTS.md's criterion, and say which one in the commit)")

    total = sum(len(a) for a in available.values())
    print(f"{len(cited)} citation(s) -> {total} section(s) across {len(available)} document(s), "
          f"{len(broken)} broken, {len(unpointed)} unpointed module(s)")

    # Assert the DENOMINATOR, not just the failure count. "0 broken out of 0
    # examined" and "0 broken out of 73" are the same exit code, and this tool
    # has silently scanned nothing twice: once when CITATION_RE's stem class
    # excluded '-' so every topic-doc citation matched nothing, and once when
    # the source list was .py-only and the new citations lived in .md. Both
    # times it reported success. See docs/VERIFICATION.md §分母.
    if not sources or not cited:
        sys.exit(f"knowledge_links: scanned {len(sources)} source(s) and found "
                 f"{len(cited)} citation(s). HARNESS FAILURE, not a result - "
                 f"a check that examined nothing cannot report success.")
    return 1 if (broken or unpointed) else 0


if __name__ == "__main__":
    sys.exit(main())
