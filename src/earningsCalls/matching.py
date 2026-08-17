"""Find a term's value in one table: the right row, column, entity and unit.

The widest decision surface in the project. Four independent filters compose
here - entity_tier, detect_unit_scale, share/growth columns, percent cells -
and each exists because of a real wrong number.
  → docs/knowledge/earnings-call-matching.md#四個獨立的過濾器

Sits on terms and periods; must not import summary - summary imports this.
"""
import re
from pathlib import Path

from core.numbers import parse_numeric
from core.tables import build_raw_lines, parse_pipe_tables, restrict_section
from core.text import _contains_any, page_num
from financialReports.entities import BANK_PROFILES
from earningsCalls.periods import (detect_orientation, parse_period_label,
                                   _rank_periods, _detect_period_column)
from earningsCalls.terms import TermSpec, match_strength




_SHARE_OR_GROWTH_RE = re.compile(
    r"占|佔|比重|比率|Ratio|Share|%|％|Chg|成長率|年增|季增|增率|YoY|QoQ|Growth",
    re.IGNORECASE,
)




def _is_share_or_growth_column(header_cell):
    return bool(_SHARE_OR_GROWTH_RE.search(header_cell))




_PERCENT_CELL_RE = re.compile(r"[%％]")




def _is_percent_cell(raw_cell):
    """True if a raw table cell is written as a percentage. Balance-type terms
    must NEVER take a value from one: parse_numeric strips the '%', so '0.08%'
    becomes 0.08 and is afterwards indistinguishable from a real balance.
      → docs/knowledge/earnings-call-matching.md#四個獨立的過濾器"""
    return bool(_PERCENT_CELL_RE.search(raw_cell))






# An ALLOWLIST of company-type markers, deliberately not a blocklist of
# generic axis labels (項目/期間/年度/...): a blocklist silently treats any
# unlisted generic label as a company name.
#   → docs/knowledge/entity-resolution.md#為什麼是白名單而不是黑名單
_ENTITY_NAME_RE = re.compile(
    r"銀行|金控|控股|人壽|產險|證券|投信|投顧|保險|公司|Bank|FHC|Holdings|Financial|Life|Securities|Insurance",
    re.IGNORECASE,
)



# Preferred entity when a figure exists identically in more than one entity's
# table: this term dictionary is bank-level throughout (存放比, NIM, ...), so a
# bank-named table beats one merely containing 金控 language.
_BANK_LABEL_HINT = "銀行"



# The PRIMARY bank subsidiary each deck is about. Picking the WRONG subsidiary
# is worse than reporting nothing - their row labels are identical.
# DERIVED, not held here, so adding an entity stays one edit in one place and
# summary's profile validation covers it.
#   → docs/knowledge/entity-resolution.md#primary_entities
PRIMARY_BANK_ENTITIES = {name: profile["primary_entities"]
                         for name, profile in BANK_PROFILES.items()}




def entity_tier(entity, primary_aliases):
    """Rank an entity name for bank-level term matching:
      2 = the deck's primary bank subsidiary (e.g. 台北富邦銀行)
      1 = no entity named - a generic/unscoped table, the common case
      0 = some OTHER named company - must NOT supply bank-level figures.
    primary_aliases may be None (bank unknown), in which case any bank-named
    entity is treated as primary rather than rejecting everything.
      → docs/knowledge/entity-resolution.md#法說會表格的機構分層"""
    if not entity:
        return 1
    if primary_aliases is None:
        return 2 if _BANK_LABEL_HINT in entity else (0 if "金控" in entity else 1)
    if any(alias in entity for alias in primary_aliases):
        return 2
    return 0 if _ENTITY_NAME_RE.search(entity) else 1



_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")




def _heading_topic(heading_text):
    """Strip a leading '公司名－' prefix from a slide title (e.g.
    '國泰世華銀行－外幣放款' -> '外幣放款'), so heading matching isn't
    thrown off by the company name always being present in the title."""
    parts = re.split(r"[－\-–—]", heading_text, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else heading_text.strip()




def collect_headings(lines):
    """Return list of (line_idx, raw_text, topic_text) for every markdown
    heading line in `lines`. raw_text is as written ('富邦金控－財務摘要');
    topic_text has the '公司名－' prefix stripped. Term matching wants
    topic_text; ENTITY detection needs raw_text - the prefix is exactly what
    it looks for."""
    headings = []
    for idx, (_pn, line) in enumerate(lines):
        m = _HEADING_LINE_RE.match(line)
        if m:
            raw = m.group(1).strip()
            headings.append((idx, raw, _heading_topic(raw)))
    return headings




def nearest_heading(headings, table_line_idx):
    """The (raw_text, topic_text) of the last heading before `table_line_idx`,
    or None - used when a table's own labels are too generic to identify a
    term but the slide's section title names it.
      → docs/knowledge/earnings-call-matching.md#用標題當退路時的唯一欄規則"""
    preceding = [h for h in headings if h[0] < table_line_idx]
    if not preceding:
        return None
    _idx, raw, topic = preceding[-1]
    return raw, topic




def _row_sections(rows):
    """For a col_period table, map each row index to the nearest preceding
    'bare' row's text (non-blank first cell, every other cell blank) - e.g. a
    '台幣'/'外幣'/'整體' row acting as an inline sub-section header.

    Lets negative_terms veto a candidate by its ENCLOSING SECTION even when
    the row's own label has nothing to veto on - decks repeat 放款利率/存款利率
    verbatim once per currency section with no currency wording in the label.
      → docs/knowledge/earnings-call-matching.md#標題也要套-negative_terms"""
    sections = {}
    current = None
    for idx, row in enumerate(rows):
        if row and row[0].strip() and all(not c.strip() for c in row[1:]):
            current = row[0].strip()
            sections[idx] = None
            continue
        sections[idx] = current
    return sections




# ---------------------------------------------------------------------------
# Table unit detection.
#
# Decks do NOT use one unit throughout (百萬元 and 拾億元 in one file, a 1000x
# gap), so any cross-table arithmetic - see LOAN_RECOMPOSITION - is wrong
# unless both sides are normalised to 十億元 first.
#
# Longer tokens MUST stay listed before their own substrings ('拾億'/'十億'
# before '億') so the more specific unit wins.
#   → docs/knowledge/reading-tables.md#單位不是全篇一致
# ---------------------------------------------------------------------------
_UNIT_TO_BILLIONS = [
    ("兆", 1e3),
    ("拾億", 1.0), ("十億", 1.0),
    ("百萬", 1e-3),
    ("仟元", 1e-6), ("千元", 1e-6),
    ("億", 1e-1),
    ("billion", 1.0), ("bn", 1.0),
    ("million", 1e-3), ("mn", 1e-3),
    ("thousand", 1e-6),
]



# A unit is only read off text that actually looks like a unit DECLARATION -
# without this guard a data row like '淨值(億元)' or a prose mention would be
# mistaken for one, silently rescaling a whole table by 10x.
_UNIT_DECL_RE = re.compile(r"單位|新台幣|新臺幣|NT\$|NTD", re.IGNORECASE)




def _unit_scale_from_text(text):
    if not text or not _UNIT_DECL_RE.search(text):
        return None
    low = text.lower()
    for token, scale in _UNIT_TO_BILLIONS:
        if token in text or token in low:
            return scale
    return None




def detect_unit_scale(lines, table, lookback=8):
    """Multiplier converting `table`'s figures to 十億元. Checks the table's
    own leading header cell first, then walks backwards for a '單位：...'
    declaration. Defaults to 1.0 when nothing is declared - the conservative
    choice, leaving already-correct output unchanged rather than rescaling on
    a guess."""
    texts = []
    header = table.get("header") or []
    if header:
        texts.append(header[0])
    idx = table["line_idx"]
    for i in range(idx - 1, max(-1, idx - 1 - lookback), -1):
        texts.append(lines[i][1])
    for text in texts:
        scale = _unit_scale_from_text(text)
        if scale is not None:
            return scale
    return 1.0




def find_value_in_table(table, term_spec, desired_period_label=None, prefer_absolute=True,
                         heading=None, prefer_quarterly=False, require_absolute=False):
    """Locate term_spec's value within one parsed table, using whichever
    orientation is detected. desired_period_label: an exact period-label
    string (e.g. 'FY25') to require, or None for the latest period present.
    prefer_absolute: when multiple header columns textually match the term
    (e.g. '企業放款' matching both '企業放款' and '企業放款占比'), prefer
    the one that doesn't look like a percentage/share/growth column, then
    break remaining ties by shortest header text (most exact match).
    prefer_quarterly: pass True for ratio terms whose curated output is meant
    to be a single quarter's figure.
    heading: the nearest preceding section title, used as a fallback when no
    row/column label identifies the term but the slide's own heading does.

    Returns (value, matched_label, period_label, strength, entity, is_percent)
    or None. NOTE find_term_value, which wraps this, returns the first two the
    OTHER way round - see its docstring before touching either.
      - strength lets callers prefer the best match across a whole folder.
      - entity is header[0] when it names a company, else None.
      - is_percent flags a '%' in the raw cell: parse_numeric strips the sign,
        so an ad-hoc lookup otherwise cannot tell '1.27%' from a plain 1.27."""
    detected = detect_orientation(table)
    if detected is None:
        return None
    orientation, period_col = detected
    header, rows = table["header"], table["rows"]

    heading_raw, heading = (heading if isinstance(heading, tuple) else (heading, heading))

    # negative_terms veto the row/column label AND the slide heading - the
    # heading is the label's context, and a column plainly headed '存放比' can
    # sit under an FX-only ratio the label alone cannot distinguish.
    #   → docs/knowledge/earnings-call-matching.md#標題也要套-negative_terms
    if term_spec.negative_terms and heading_raw and _contains_any(heading_raw, term_spec.negative_terms):
        return None

    entity = None
    if header and header[0].strip() and not parse_period_label(header[0]):
        h0 = header[0].strip()
        if _ENTITY_NAME_RE.search(h0):
            entity = h0
    if entity is None and heading_raw and _ENTITY_NAME_RE.search(heading_raw):
        # header[0] is a generic axis label, so fall back to the RAW section
        # heading when IT names a company. Must be heading_raw, never the
        # topic-stripped `heading` - stripping removes exactly the company
        # prefix this check needs.
        entity = heading_raw

    if orientation == "row_period":
        candidates = [(idx, h, match_strength(term_spec, h)) for idx, h in enumerate(header) if idx != period_col]
        candidates = [(idx, h, s) for idx, h, s in candidates if s > 0]
        if require_absolute:
            candidates = [(idx, h, s) for idx, h, s in candidates if not _is_share_or_growth_column(h)]
        if candidates:
            if prefer_absolute and len(candidates) > 1:
                non_share = [(idx, h, s) for idx, h, s in candidates if not _is_share_or_growth_column(h)]
                if non_share:
                    candidates = non_share
            best_strength = max(s for _, _, s in candidates)
            candidates = [(idx, h, s) for idx, h, s in candidates if s == best_strength]
            col_idx, col_header, strength = min(candidates, key=lambda t: len(t[1]))
        else:
            # No column header names the term - fall back to the slide's own
            # section heading. Requires EXACTLY ONE candidate value column:
            # with two or more the heading gives the topic but not the column,
            # and guessing yields half a currency-split figure.
            # Ambiguous -> no match, never a guess.
            #   → docs/knowledge/na-and-refusal.md#判不出來就拒絕在這個-codebase-的實作
            if not heading:
                return None
            strength = match_strength(term_spec, heading)
            if strength == 0:
                return None
            value_cols = [(idx, h) for idx, h in enumerate(header)
                          if idx != period_col and h.strip() and not _is_share_or_growth_column(h)]
            if len(value_cols) != 1:
                return None
            col_idx, _only_col = value_cols[0]
            col_header = heading  # report the heading, as the col_period branch does

        period_rows = [(parse_period_label(r[period_col]), r) for r in rows
                        if len(r) > period_col and parse_period_label(r[period_col])]
        if not period_rows:
            return None
        if desired_period_label is not None:
            period_rows = [(k, r) for k, r in period_rows if r[period_col].strip() == desired_period_label]
            if not period_rows:
                return None
        for key, row in _rank_periods(period_rows, prefer_quarterly):
            if col_idx >= len(row):
                continue
            if require_absolute and _is_percent_cell(row[col_idx]):
                continue
            value = parse_numeric(row[col_idx])
            if value is not None:
                return (value, col_header.strip(), row[period_col].strip(), strength, entity,
                        _is_percent_cell(row[col_idx]))
        return None

    candidates = [(idx, r, match_strength(term_spec, r[0])) for idx, r in enumerate(rows) if r]
    candidates = [(idx, r, s) for idx, r, s in candidates if s > 0]
    if term_spec.negative_terms:
        sections = _row_sections(rows)
        candidates = [(idx, r, s) for idx, r, s in candidates
                      if not (sections.get(idx) and _contains_any(sections[idx], term_spec.negative_terms))]
    if require_absolute:
        # Mirrors the row_period branch's header filtering. Without it a
        # share-labelled row wins the tie-break over the absolute-value row it
        # shares a strength with, then every period in THAT row is filtered
        # out below - yielding None without ever trying the correct row.
        absolute_only = [(idx, r, s) for idx, r, s in candidates if not _is_share_or_growth_column(r[0])]
        if absolute_only:
            candidates = absolute_only

    matched_label = None
    if candidates:
        best_strength = max(s for _, _, s in candidates)
        candidates = [(idx, r, s) for idx, r, s in candidates if s == best_strength]
        row_idx, row, strength = min(candidates, key=lambda t: len(t[1][0]))
        matched_label = row[0].strip()
    elif heading:
        strength = match_strength(term_spec, heading)
        if strength == 0:
            return None
        # Mirrors the row_period branch's "exactly one value row" rule: safe
        # only when the table is genuinely about ONE thing the heading names.
        # With several unrelated line items left, picking the first is a
        # guess - it once grabbed a corporate-loan row for 信用卡循環.
        #   → docs/knowledge/na-and-refusal.md#判不出來就拒絕在這個-codebase-的實作
        non_share = [r for r in rows if r and r[0].strip()
                     and not all(not c.strip() for c in r[1:])
                     and not _is_share_or_growth_column(r[0])]
        if len(non_share) != 1:
            return None
        row = non_share[0]
        matched_label = heading.strip()
    else:
        return None

    period_cols = [(idx, parse_period_label(h)) for idx, h in enumerate(header) if idx > 0]
    period_cols = [(k, idx) for idx, k in period_cols if k is not None]
    if not period_cols:
        return None
    if desired_period_label is not None:
        period_cols = [(k, idx) for k, idx in period_cols if header[idx].strip() == desired_period_label]
        if not period_cols:
            return None
    for key, col_idx in _rank_periods(period_cols, prefer_quarterly):
        if col_idx >= len(row):
            continue
        if require_absolute and _is_percent_cell(row[col_idx]):
            continue
        value = parse_numeric(row[col_idx])
        if value is not None:
            return (value, matched_label, header[col_idx].strip(), strength, entity,
                    _is_percent_cell(row[col_idx]))
    return None




def _rank_key(strength, entity, period_label=None, prefer_quarterly=False, primary_aliases=None):
    """Sort key preferring, in order: higher match strength; then the deck's
    primary bank subsidiary's table over an unscoped one (tier 0 is rejected
    outright by callers, not merely deprioritised); then, with
    prefer_quarterly, a sub-annual period over an annual one - two equally
    good alias matches must not resolve by which file sorts first when the
    point is comparing quarter to quarter."""
    tier = entity_tier(entity, primary_aliases)
    quarterly_bonus = 0
    if prefer_quarterly and period_label:
        key = parse_period_label(period_label)
        if key is not None and key[1] < 5:
            quarterly_bonus = 1
    return (strength, tier, quarterly_bonus)




def _best_match_in_file(doc_path, term_spec, prefer_quarterly=False, primary_aliases=None,
                         require_absolute=False):
    """Search every table in a single file for term_spec and return the best
    local match: (rank_key, value, matched_label, period_label, entity,
    is_percent, unit_scale), or None.

    unit_scale is CARRIED, not applied here, so unitless callers can ignore it
    while balance terms normalise by it.

    Tables at entity_tier 0 are SKIPPED OUTRIGHT, never ranked low: their row
    labels are identical to the primary bank's but the figures are another
    company's, often in another currency.
      → docs/knowledge/entity-resolution.md#法說會表格的機構分層"""
    lines = build_raw_lines(doc_path)
    if term_spec.search_start:
        lines = restrict_section(lines, term_spec.search_start, term_spec.search_end)
        if lines is None:
            return None
    headings = collect_headings(lines)
    best = None
    for table in parse_pipe_tables(lines):
        heading = nearest_heading(headings, table["line_idx"])
        result = find_value_in_table(table, term_spec, heading=heading, prefer_quarterly=prefer_quarterly,
                                      require_absolute=require_absolute)
        if result is None:
            continue
        value, matched_label, period_label, strength, entity, is_percent = result
        key = _rank_key(strength, entity, period_label, prefer_quarterly, primary_aliases)
        if key[1] == 0:
            continue
        if best is None or key > best[0]:
            best = (key, value, matched_label, period_label, entity, is_percent,
                    detect_unit_scale(lines, table))
    return best




def find_term_value(folder, term_spec, verbose=False, prefer_quarterly=False, primary_aliases=None,
                     require_absolute=False):
    """Search every .md file/table in `folder` and return the BEST match
    across the WHOLE folder, not the first one found - a generic term can
    match identically in more than one entity's table, and first-found is not
    necessarily right. Stops early only on rank (3, 2) or (3, 2, 1), which
    nothing can outrank.

    Returns (matched_label, value, source_file, period_label, is_percent,
    unit_scale), or None.

    NOTE the first two are the OPPOSITE way round from find_value_in_table,
    which this wraps: that one returns (value, matched_label, ...). Both are
    6-tuples, both are consumed positionally, and both orders are pinned side
    by side in test_l2_lookup.py - do NOT "fix" one to match the other.
      → docs/knowledge/earnings-call-matching.md#find_term_value-的回傳順序陷阱"""
    best = None  # (rank_key, value, matched_label, source_file, period_label, is_percent, unit_scale)
    best_possible = (3, 2, 1) if prefer_quarterly else (3, 2, 0)
    for doc_path in sorted(Path(folder).rglob("*.md")):
        local = _best_match_in_file(doc_path, term_spec, prefer_quarterly=prefer_quarterly,
                                     primary_aliases=primary_aliases, require_absolute=require_absolute)
        if local is None:
            continue
        key, value, matched_label, period_label, _entity, is_percent, unit_scale = local
        if best is None or key > best[0]:
            best = (key, value, matched_label, doc_path.name, period_label, is_percent, unit_scale)
        if best is not None and best[0] == best_possible:
            break
    if best is None:
        return None
    _key, value, matched_label, source_file, period_label, is_percent, unit_scale = best
    if verbose:
        unit_note = "" if unit_scale == 1.0 else f" [x{unit_scale:g} -> 十億元]"
        print(f"[{source_file}] '{term_spec.name}' -> '{matched_label}' @ {period_label} = {value!r}{unit_note}")
    return matched_label, value, source_file, period_label, is_percent, unit_scale




def extract_term(folder, term_spec, verbose=False):
    """Scan every .md file/table in `folder` and return EVERY match found,
    unlike find_term_value which returns only the strongest. Returns a list of
    {term, label_in_doc, value, source_file, period_label, is_percent} -
    is_percent matters here because this ad-hoc search, unlike the curated
    summary, has no RATIO_TERMS/BALANCE_TERMS list to tell it in advance."""
    results = []
    for doc_path in sorted(Path(folder).rglob("*.md")):
        lines = build_raw_lines(doc_path)
        if term_spec.search_start:
            restricted = restrict_section(lines, term_spec.search_start, term_spec.search_end)
            if restricted is None:
                continue
            lines = restricted
        headings = collect_headings(lines)
        for table in parse_pipe_tables(lines):
            heading = nearest_heading(headings, table["line_idx"])
            result = find_value_in_table(table, term_spec, heading=heading)
            if result is None:
                continue
            value, matched_label, period_label, _strength, _entity, is_percent = result
            results.append({
                "term": term_spec.name, "label_in_doc": matched_label, "value": value,
                "source_file": doc_path.name, "period_label": period_label, "is_percent": is_percent,
            })
            if verbose:
                print(f"[{doc_path.name}] '{term_spec.name}' matched '{matched_label}' "
                      f"@ {period_label} -> {value!r}{'%' if is_percent else ''}")
    return results
