"""Text normalisation shared by the fin-report and con-call extractors.

No dependency on anything above core/ - these are generic string helpers that
happen to know about CJK filings' typography, not about financial statements.
"""
import re
from pathlib import Path


_CJK_GAP_RE = re.compile(r"(?<=[一-鿿])\s+(?=[一-鿿])")


def despace_cjk(text):
    return _CJK_GAP_RE.sub("", text)


def _contains_any(text, terms):
    text_lower = text.lower()
    return any(t.lower() in text_lower for t in terms)


_TOC_LINE_RE = re.compile(r"^[一二三四五六七八九十百]+[、.].{0,30}\d+\s*[-–—]?\s*$")


def _is_toc_like(segment):
    return bool(_TOC_LINE_RE.match(segment.strip()))


_PAGE_NUM_RE = re.compile(r"^(\d+)")


def page_num(source_file):
    """'013_xinp7x.md' -> '013' (just the leading page number, dropping the
    random per-file suffix and the .md extension) - used wherever a source
    file is shown to the user, so output shows the page to open rather than
    a meaningless random string. Falls back to the full filename stem if it
    doesn't start with digits, and to '' for None (no source file - the
    term wasn't found)."""
    if not source_file:
        return ""
    stem = Path(source_file).stem
    m = _PAGE_NUM_RE.match(stem)
    return m.group(1) if m else stem


_FOOTNOTE_RE = re.compile(r"[（(]\s*註\s*\d+\s*[）)]")


def strip_footnote(label):
    return _FOOTNOTE_RE.sub("", label).strip()


_FOOTNOTE_SUFFIX_RE = re.compile(r"[（(]附註.*$")


def _strip_footnote_suffix(label):
    """'利息淨收益(附註六(卅三))' -> '利息淨收益'. Filings routinely append a
    footnote-reference suffix directly onto an otherwise-exact label
    (nested parens and all), which would otherwise defeat the EXACT-match
    requirement in find_value_by_label for a label that is, semantically,
    identical to one of its aliases."""
    return _FOOTNOTE_SUFFIX_RE.sub("", label).strip()
