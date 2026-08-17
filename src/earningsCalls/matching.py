"""Find a term's value in one table: the right row, column, entity and unit.

The widest decision surface in the project. Four independent filters compose
here and each exists because of a real wrong number:
  - entity_tier      a figure from the FHC parent or a sibling subsidiary is
                     not this bank's figure (a real 富邦 deck produced 存放比
                     72.17% from the mainland-China arm's RMB table)
  - detect_unit_scale decks mix 百萬元 and 拾億元 across tables in one file
  - share/growth      a "占比" column is not a balance
  - percent cell      parse_numeric strips the '%', so 0.08% reads as 0.08

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
    """True if a raw table cell is written as a percentage. Balance-type
    terms (loan balances - currency amounts) must never take a value from
    one: parse_numeric strips the '%' sign, so '0.08%' becomes 0.08 and is
    afterwards indistinguishable from a real balance. In a real 富邦 deck
    that let 房貸 match the 房貸 column of a 業務別逾放比 (NPL-ratio-by-
    business) table and report 0.08, and let 法說會放款餘額合計 match the
    ratio row '逾期放款／總放款' and report 0.12."""
    return bool(_PERCENT_CELL_RE.search(raw_cell))






# A header[0] cell (or slide heading) counts as naming an ENTITY only if it
# contains one of these company-type markers. This is deliberately an
# allowlist rather than a blocklist of generic axis labels (項目/期間/年度/
# ...): a blocklist silently treats any unlisted generic label as a company
# name, which is exactly how '| Quarterly | 4Q22 | ... |' in a real 國泰 deck
# came to be read as an entity named "Quarterly" and outranked by a
# genuinely bank-named table, making NIM resolve to the annual FY25 figure
# instead of the 4Q25 one sitting in that very table.
_ENTITY_NAME_RE = re.compile(
    r"銀行|金控|控股|人壽|產險|證券|投信|投顧|保險|公司|Bank|FHC|Holdings|Financial|Life|Securities|Insurance",
    re.IGNORECASE,
)



# Preferred entity when a figure exists identically in more than one
# entity's table (e.g. a bank subsidiary's own 營業費用 vs its FHC parent's
# consolidated 營業費用) - this term dictionary is built around bank-level
# concepts throughout (存放比, 放款結構, NIM, ...), so the bank-named table
# is preferred over one merely containing "金控"/holding-company language.
_BANK_LABEL_HINT = "銀行"



# The PRIMARY bank subsidiary each deck is about - see BANK_PROFILES'
# primary_entities field for why picking the wrong subsidiary is worse than
# reporting nothing. Derived rather than held here so that adding an entity
# is one edit in one place, and so statements's profile validation covers it.
PRIMARY_BANK_ENTITIES = {name: profile["primary_entities"]
                         for name, profile in BANK_PROFILES.items()}




def entity_tier(entity, primary_aliases):
    """Rank an entity name for bank-level term matching:
      2 = the deck's primary bank subsidiary (e.g. 台北富邦銀行)
      1 = no entity named - a generic/unscoped table, the common case
      0 = some OTHER named company (another bank subsidiary, the FHC
          parent, an insurance/securities arm) - must not supply
          bank-level figures.
    primary_aliases may be None (bank unknown), in which case any
    bank-named entity is treated as primary, preserving the older
    _BANK_LABEL_HINT-only behaviour rather than rejecting everything."""
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
    heading line ('#'..'######') in `lines`. raw_text is the heading as
    written (e.g. '富邦金控－財務摘要'); topic_text has the leading
    '公司名－' prefix stripped (see _heading_topic), for term-matching use
    where the always-present company name would throw off matching."""
    headings = []
    for idx, (_pn, line) in enumerate(lines):
        m = _HEADING_LINE_RE.match(line)
        if m:
            raw = m.group(1).strip()
            headings.append((idx, raw, _heading_topic(raw)))
    return headings




def nearest_heading(headings, table_line_idx):
    """The (raw_text, topic_text) of the last heading appearing before
    `table_line_idx`, or None if there isn't one - used when a table's own
    row/column labels are too generic to identify a term (e.g. '放款餘額'/
    '占全行放款'), but the term is actually the slide's section title above
    the table (e.g. '## 外幣放款'), a pattern confirmed in a real
    earnings-call deck. raw_text is also used for entity detection (e.g.
    spotting a '金控'/FHC-parent-labeled slide) since topic_text has the
    company name stripped out."""
    preceding = [h for h in headings if h[0] < table_line_idx]
    if not preceding:
        return None
    _idx, raw, topic = preceding[-1]
    return raw, topic




def _row_sections(rows):
    """For a col_period table, map each row index to the nearest preceding
    'bare' row's text (non-blank first cell, every other cell blank) - e.g.
    a '台幣'/'外幣'/'整體' row acting as an inline sub-section header for
    the metric rows beneath it (confirmed in a real 中信金 deck, where
    '放款利率'/'存款利率' repeat verbatim once per currency section with no
    currency wording in the metric row's own label). None for rows with no
    such preceding header, or for a header row itself. Lets negative_terms
    veto a candidate by its enclosing section even when the row's own label
    text has nothing to veto on."""
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
# Decks do NOT use one unit throughout - confirmed in a real 中信金 deck
# where the loan-balance table on one page is 新台幣-百萬元 while the
# 外幣放款成長率 table it gets combined with is 新台幣拾億元, a 1000x gap.
# Any cross-table arithmetic (see LOAN_RECOMPOSITION) is therefore wrong
# unless both sides are converted to a common unit first. Everything is
# normalised to 十億元 (NT$ billions), the unit every bank's headline loan
# table already uses (confirmed for all 4 banks' Q4 2025 decks: NT$BN,
# 新台幣拾億元, NT$十億元, 新臺幣拾億元 - so normalisation is a no-op there
# and only kicks in on the mixed-unit decks it exists to fix).
#
# Longer tokens are listed BEFORE their own substrings ('拾億'/'十億' before
# '億') so the more specific unit wins the match.
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
    own leading header cell first (some decks put the unit there, e.g.
    '| 新台幣-百萬元,% | 2022 | ...'), then walks backwards through the lines
    just above the table for a '單位：...' style declaration. Defaults to 1.0
    (assume 十億元) when nothing is declared - the conservative choice, since
    it leaves already-correct output unchanged rather than rescaling on a
    guess."""
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
    prefer_quarterly: see _pick_latest_period - pass True for ratio terms
    whose curated output is meant to be a single quarter's figure.
    heading: the nearest preceding section title (see nearest_heading) -
    used as a fallback (col_period orientation only) when no row label
    identifies the term but the table's own section heading does (e.g. a
    '## 外幣放款' slide whose table rows are just generically labeled
    '放款餘額'/'占全行放款') - confirmed against a real earnings-call deck.
    Returns (value, matched_label, period_label, strength, entity, is_percent)
    or None - strength lets callers prefer the best match found across an
    entire folder rather than just the first one encountered (see
    find_term_value); entity is the table's header[0] text when it names a
    specific company (e.g. '國泰世華銀行' vs '國泰金控' - the same figure,
    like 營業費用, can legitimately appear once per entity in a multi-entity
    appendix table), or None when header[0] is just a generic column label.
    is_percent is True when the matched cell's raw text carries a '%'/'％'
    sign - parse_numeric() strips that sign off, so without this flag a
    generic ad-hoc term lookup (unlike the curated summary, which already
    knows which of its terms are ratios) has no way to tell whether a
    printed '1.27' means '1.27%' or a plain count/balance of 1.27 - see
    format_maybe_pct(), used by callers that don't already know the term's
    kind. heading may be the (raw_text, topic_text) tuple from
    nearest_heading(), or a plain string/None for callers that don't need
    the entity-detection fallback."""
    detected = detect_orientation(table)
    if detected is None:
        return None
    orientation, period_col = detected
    header, rows = table["header"], table["rows"]

    heading_raw, heading = (heading if isinstance(heading, tuple) else (heading, heading))

    # A term's negative_terms veto its own row/column label (see
    # match_strength); apply them to the slide heading too, since the
    # heading is the label's context. In a real 富邦 deck a column headed
    # plainly '存放比' sits under '外幣放款及債券投資佔外幣存款比例' - an
    # FX-only ratio that the label alone gives no way to tell apart from
    # the overall loan-to-deposit ratio the term means.
    if term_spec.negative_terms and heading_raw and _contains_any(heading_raw, term_spec.negative_terms):
        return None

    entity = None
    if header and header[0].strip() and not parse_period_label(header[0]):
        h0 = header[0].strip()
        if _ENTITY_NAME_RE.search(h0):
            entity = h0
    if entity is None and heading_raw and _ENTITY_NAME_RE.search(heading_raw):
        # The table's own header[0] doesn't name a company (it's a generic
        # axis label like "項目"/"Quarterly"), so fall back to the RAW
        # section heading when IT names one (e.g. "富邦華一銀行－財務摘要"),
        # so another subsidiary's table can still be told apart from the
        # primary bank's downstream (see entity_tier / _best_match_in_file).
        # Deliberately uses heading_raw, not the topic-stripped `heading`,
        # since topic-stripping removes exactly the company-name prefix
        # this check needs.
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
            # section heading, mirroring the col_period branch (e.g. a
            # '## 外幣放款' slide whose only value column is generically
            # labelled '餘額'). Deliberately requires EXACTLY ONE candidate
            # value column (after dropping the period column and any
            # share/growth column): with two or more the heading tells us
            # the topic but not which column it refers to, and guessing
            # there is how a currency-split table like 富邦's
            # '企業授信餘額（依幣別）' (台幣授信 | 外幣授信) would silently
            # yield half the figure. Ambiguous -> no match, not a guess.
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
        # Mirrors the row_period branch's header filtering: without this, a
        # share/percentage-labeled row (e.g. '外幣放款佔全行放款') can win
        # the tie-break over the real absolute-value row it shares a match
        # strength with, and then every period in THAT row gets filtered
        # out by the require_absolute check below, yielding None instead of
        # ever trying the correct row (confirmed in a real 國泰金 deck).
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
        # Mirrors the row_period branch's "exactly one value column" rule:
        # safe only when the table is genuinely about ONE thing the heading
        # names (e.g. a '## 外幣放款' slide whose rows are just absolute vs.
        # share views of the same figure). If several distinct, unrelated
        # line items remain (e.g. a general '各類放款佔比' breakdown where
        # the heading only mentions the term in a passing footnote), picking
        # "the first one" is a guess, not a match - confirmed in a real
        # 中信金 deck where this previously grabbed an unrelated corporate-
        # loan row for a term (信用卡循環) that had no row of its own.
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
    primary bank subsidiary's own table over an unscoped one (see
    entity_tier - a table belonging to some OTHER named company scores 0
    and is rejected outright by callers, not merely deprioritised); then,
    if prefer_quarterly, a result whose period is sub-annual (quarter/half/
    9-month) over one that's only annual - two equally-good alias matches
    for the same term (e.g. '營收' with only annual data vs '營業收入' with
    quarterly data) shouldn't silently resolve to whichever file sorts
    first when the whole point is to compare quarter-to-quarter figures
    (e.g. CIR's 營業費用 and 淨收益 inputs must come from the same
    granularity, not a quarterly expense against an annual revenue)."""
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
    is_percent, unit_scale), or None if term_spec doesn't appear in this
    file at all. unit_scale is the multiplier converting that table's
    figures to 十億元 (see detect_unit_scale) - carried alongside rather
    than applied here, so ratio terms (which are unitless) and the CIR
    inputs (a ratio of two figures, where a shared unit cancels) can ignore
    it while balance terms normalise by it.
    Tables belonging to a named entity that ISN'T the deck's primary bank
    (entity_tier 0 - another bank subsidiary, the FHC parent, an insurance/
    securities arm) are skipped outright rather than ranked low: their row
    labels are identical to the primary bank's (存放比, 總放款, 營業費用) but
    the figures are a different company's, often in a different currency."""
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
    """Search every .md file/table in `folder` for term_spec and return the
    BEST match found across the whole folder (see _rank_key: highest
    match_strength first, then a bank-named entity over a non-bank one),
    not just the first one encountered - a generic term can be an exact or
    substring match in more than one entity's table (e.g. 營業費用 appears,
    identically named, in both a bank subsidiary's own figures and its FHC
    parent's consolidated figures), and the first-found one isn't
    necessarily the right one. Stops early only once a same-entity-type
    exact match is found (rank (3, 1) - nothing can outrank it).
    prefer_quarterly: see find_value_in_table/_pick_latest_period. Returns
    (matched_label, value, source_file, period_label, is_percent,
    unit_scale), or None if term_spec doesn't appear in any table.
    NOTE the first two are the OPPOSITE way round from find_value_in_table,
    which this wraps: that one returns (value, matched_label, ...). Both are
    6-tuples, both are consumed positionally, and the two orders are pinned
    side by side in test_l2_lookup.py - do not "fix" one to match the other
    without changing every caller.
    unit_scale is the multiplier to 十億元 for the table the value came off
    (see detect_unit_scale); the raw as-printed value is returned unscaled
    so unitless callers can ignore it."""
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
    """Scan every .md file/table in `folder` for term_spec and return every
    match found (unlike find_term_value, which returns only the single
    strongest). Returns a list of {term, label_in_doc, value, source_file,
    period_label, is_percent} - is_percent (see find_value_in_table) lets
    the caller print '%' on values it wouldn't otherwise know are ratios
    (unlike the curated summary, this ad-hoc search has no RATIO_TERMS/
    BALANCE_TERMS list to tell it in advance)."""
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
