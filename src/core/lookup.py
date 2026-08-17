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
    with any trailing footnote-reference suffix stripped, exactly equals
    one of `label_aliases`. Checks TWO shapes per row: cells[1] (the usual
    code+label+values shape most rows use - the label right after a
    code cell, blank or otherwise) and, if that doesn't match, cells[0]
    (a label+values shape with no code cell at all - confirmed in a real
    114Q4 filing's standalone 活期性存款比率 disclosure table, which isn't
    part of any coded statement and so was never given a leading code
    column by the conversion). nth_value's own cells[1:] scan already
    treats whichever cell holds the label as the thing to skip, so no
    other change is needed to support the second shape - only the label
    match itself needed to also look at cells[0].
    Used as a fallback for the handful of subtotal/aggregate SUMMARY_LAYOUT
    lines whose own code is unreliable across filings (see
    SUMMARY_LABEL_FALLBACKS), and as the sole lookup for "label"-kind
    SUMMARY_LAYOUT items that were never coded to begin with (e.g. 活存比).
    Returns (label_in_doc, value, source_file) for the first match found,
    or None. Requires an EXACT (stripped) match against the whole label
    cell, not a substring - safe here because these are short, standalone
    total-line labels (e.g. '淨收益'), not substrings of unrelated compound
    line items."""
    for doc_path in sorted(Path(folder).rglob("*.md")):
        # build_raw_lines (not a raw read+splitlines) so this benefits from
        # despacing AND the dual-column-table split the same way every
        # other consumer does - a blank/omitted code cell paired with a
        # label living in a table's right-hand section (see
        # _split_dual_column_tables) would otherwise never be found here.
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
    (label_in_doc, value, source_file) for the first match found, or None
    if the code doesn't appear anywhere (with a value) and no
    label_fallback matches either (see find_value_by_label). A code match
    whose row has no parseable value (e.g. a bare section-header row like
    "58400 | 營業費用 |  |  |") is skipped rather than treated as the
    final answer, so a real value elsewhere (another matching row, or the
    label fallback) still gets a chance."""
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
    """One-pass replacement for calling find_code_value() separately for
    every code in `codes`: scans every .md file in `folder` ONCE - not once
    per code - resolving each code as it's found and shrinking the set of
    still-needed codes, so a later file is only scanned for whatever's
    still missing rather than re-parsed from scratch per code. Safe to
    consolidate this way because exact-code matching is unambiguous (a code
    is a unique key, not a fuzzy text match like callfinder.py's term
    matching - which genuinely needs to compare candidates across the whole
    folder before choosing the best one, and is deliberately NOT
    consolidated the same way). Output is identical to calling
    find_code_value(folder, code, period, label_fallback=...) once per
    code - same first-file-with-a-value wins, same label-fallback behavior
    - just without the codes-times-files redundant file re-reads/re-parses.
    label_fallbacks: optional {code: [alias, ...]} for codes that should
    fall back to a label-text match (see find_value_by_label) if no code
    match with a value turns up in any file.
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
                # Same rule the code pass above already follows: a matched row
                # with no value for this period resolves nothing. Recording it
                # would hand callers a None where they expect a number (CIR's
                # abs(), a composite term's sum()) AND stop any later file
                # from supplying the real figure - contradicting this
                # function's own "first file WITH A VALUE wins" contract.
                continue
            for code in alias_to_codes[label]:
                if code in remaining:
                    if verbose:
                        print(f"[{doc_path.name}] label '{label}' (no reliable code) -> = {value!r}")
                    result[code] = (label, value, doc_path.name)
                    remaining.discard(code)
            pending_aliases.discard(label)

    return result
