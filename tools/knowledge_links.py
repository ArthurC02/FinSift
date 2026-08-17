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

Exit 1 lists every citation whose target is missing, and every knowledge
section nothing cites (usually a rename that updated one side only).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "docs" / "knowledge"

# A citation looks like `→ docs/knowledge/<doc>.md#<anchor>` in a comment.
# Written without a literal example on purpose: this file is scanned like any
# other source, so an illustrative citation here would be checked as a real one
# (it was, and it failed - which is at least the tool working).
CITATION_RE = re.compile(r"docs/knowledge/([\w.]+)\.md#([\w-]+)")
# A markdown heading, reduced to the anchor GitHub would generate for it.
HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.M)


def anchor_of(heading):
    """GitHub's slug rules, restricted to what these docs actually use."""
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    return re.sub(r"\s+", "-", slug)


def main():
    if not KNOWLEDGE.is_dir():
        sys.exit(f"no knowledge directory at {KNOWLEDGE}")

    available = {}
    for doc in sorted(KNOWLEDGE.glob("*.md")):
        available[doc.stem] = {anchor_of(h) for h in HEADING_RE.findall(doc.read_text(encoding="utf-8"))}

    cited, broken = set(), []
    sources = sorted(ROOT.joinpath("src").rglob("*.py")) + sorted(ROOT.joinpath("tools").glob("*.py"))
    for path in sources:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for stem, anchor in CITATION_RE.findall(line):
                cited.add((stem, anchor))
                if anchor not in available.get(stem, set()):
                    where = f"{path.relative_to(ROOT)}:{lineno}"
                    reason = ("no such document" if stem not in available
                              else "no such section")
                    broken.append(f"  {where}: docs/knowledge/{stem}.md#{anchor} - {reason}")

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

    total = sum(len(a) for a in available.values())
    print(f"{len(cited)} citation(s) -> {total} section(s) across {len(available)} document(s), "
          f"{len(broken)} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
