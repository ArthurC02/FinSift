"""Folder-wide account-code / label lookup over converted .md filings.

Sits in core/ because BOTH extractors above it need it and neither owns it:
compute_ratios resolves single codes through find_code_value, and
collect_summary_rows resolves a whole layout's worth in one pass through
build_code_index. Depends only on core.tables/core.numbers/core.text - the
same one-way rule the rest of core follows, so nothing here may import an
extractor.
"""
from pathlib import Path

from core.numbers import nth_value
from core.tables import (build_raw_lines, group_rows_by_code, percent_stride_map,
                         _is_table_divider, _split_row)
from core.text import _strip_footnote_suffix


def find_value_by_label(folder, label_aliases, period=1, verbose=False):
    """Search every .md file in `folder` for a table row whose label cell,
    footnote suffix stripped, exactly equals one of `label_aliases`.

    Match is EXACT against the whole cell, never a substring - safe only
    because these are short standalone total-line labels ('淨收益'), not
    substrings of compound line items.
      → docs/knowledge/account-codes.md#一標籤比對summary_label_fallbacks

    Checks TWO row shapes: cells[1] (the usual code+label+values) and, failing
    that, cells[0] (label+values with no code cell at all - the 活期性存款比率
    disclosure table isn't part of any coded statement).

    Returns (label_in_doc, value, source_file) for the first match, or None."""
    for doc_path in sorted(Path(folder).rglob("*.md")):
        # build_raw_lines, NOT a raw read - this needs the despacing and the
        # dual-column split every other consumer gets, or a label living in a
        # table's right-hand section is never found here.
        doc_lines = build_raw_lines(doc_path)
        strides = percent_stride_map(doc_lines)
        for line_idx, (_pn, line_s) in enumerate(doc_lines):
            if not line_s.startswith("|") or _is_table_divider(line_s):
                continue
            cells = _split_row(line_s)
            if len(cells) < 2:
                continue
            label = _strip_footnote_suffix(cells[1].strip())
            if label not in label_aliases:
                label = _strip_footnote_suffix(cells[0].strip())
                if label not in label_aliases:
                    continue
            value = nth_value(cells, period, strides[line_idx])
            if value is None:
                continue  # keep looking - a later file may carry this period
            if verbose:
                print(f"[{doc_path.name}] label '{label}' (no reliable code) -> = {value!r}")
            return label, value, doc_path.name
    return None


def find_code_value(folder, code, period=1, verbose=False, label_fallback=None):
    """Search every .md file in `folder` (no statement-section restriction)
    for a row whose leading cell equals `code` exactly. Returns
    (label_in_doc, value, source_file) for the first match, or None.

    A code match whose row has NO parseable value (a bare section-header row
    like "58400 | 營業費用 |  |  |") is SKIPPED, never treated as the final
    answer - a real value elsewhere, or the label fallback, still gets a
    chance. → docs/knowledge/account-codes.md#代碼查不到時的三層退路"""
    for doc_path in sorted(Path(folder).rglob("*.md")):
        lines = build_raw_lines(doc_path)
        entries = group_rows_by_code(lines, {code})
        for found_code, _page_num, cells, stride in entries:
            value = nth_value(cells, period, stride)
            if value is None:
                continue
            label = cells[1].strip() if len(cells) > 1 else found_code
            if verbose:
                print(f"[{doc_path.name}] code {code} -> '{label}' = {value!r}")
            return label, value, doc_path.name
    if label_fallback:
        found = find_value_by_label(folder, label_fallback, period=period, verbose=verbose)
        if found and verbose:
            print(f"  (code {code} not found - matched by label instead)")
        return found
    return None


def build_code_index(folder, codes, label_fallbacks=None, period=1, verbose=False):
    """One-pass replacement for calling find_code_value() per code: scans
    every .md file ONCE, shrinking the still-needed set as codes resolve.

    Safe to consolidate ONLY because exact-code matching is unambiguous - a
    code is a unique key. earningsCalls' term matching genuinely has to
    compare candidates across the whole folder before choosing, and is
    deliberately NOT consolidated the same way.

    Output is identical to per-code find_code_value: same first-file-with-a-
    value wins, same label-fallback behaviour.
    label_fallbacks: optional {code: [alias, ...]}.
    Returns {code: (label, value, source_file) or None}."""
    label_fallbacks = label_fallbacks or {}
    remaining = set(codes)
    result = {code: None for code in codes}

    for doc_path in sorted(Path(folder).rglob("*.md")):
        if not remaining:
            break
        lines = build_raw_lines(doc_path)
        entries = group_rows_by_code(lines, remaining)
        for found_code, _page_num, cells, stride in entries:
            if found_code not in remaining:
                continue  # already resolved by an earlier file this pass
            value = nth_value(cells, period, stride)
            if value is None:
                continue
            label = cells[1].strip() if len(cells) > 1 else found_code
            if verbose:
                print(f"[{doc_path.name}] code {found_code} -> '{label}' = {value!r}")
            result[found_code] = (label, value, doc_path.name)
            remaining.discard(found_code)

    # Label-fallback pass, only for codes still unresolved after checking
    # every file by code - same one-file-one-pass consolidation, scanning
    # for every remaining code's label aliases together instead of running
    # a separate full-folder scan per code.
    alias_to_codes = {}
    for code in remaining:
        for alias in label_fallbacks.get(code, []):
            alias_to_codes.setdefault(alias, []).append(code)
    pending_aliases = set(alias_to_codes)

    for doc_path in sorted(Path(folder).rglob("*.md")):
        if not pending_aliases:
            break
        doc_lines = build_raw_lines(doc_path)
        strides = percent_stride_map(doc_lines)
        for line_idx, (_pn, line_s) in enumerate(doc_lines):
            if not pending_aliases:
                break
            if not line_s.startswith("|") or _is_table_divider(line_s):
                continue
            cells = _split_row(line_s)
            if len(cells) < 2:
                continue
            label = _strip_footnote_suffix(cells[1].strip())
            if label not in pending_aliases:
                continue
            value = nth_value(cells, period, strides[line_idx])
            if value is None:
                # Same rule as the code pass: a matched row with no value for
                # this period resolves NOTHING. Recording it hands callers a
                # None where they expect a number and blocks a later file from
                # supplying the real figure.
                continue
            for code in alias_to_codes[label]:
                if code in remaining:
                    if verbose:
                        print(f"[{doc_path.name}] label '{label}' (no reliable code) -> = {value!r}")
                    result[code] = (label, value, doc_path.name)
                    remaining.discard(code)
            pending_aliases.discard(label)

    return result
