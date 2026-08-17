"""The curated cross-entity summary: one fixed set of lines, every filing.

The layouts live here, and so does the check that every entity profile
defines the composites the layout it is read under actually needs
(_validate_profiles, run at import - an incomplete entity is an import-time
error, never a silent N/A at run time).

Top of the stack: reads entities for the profiles and ratios for the trailing
ROA/ROE rows. Nothing imports this except acctfinder's CLI and runfinder.
"""
import csv
from pathlib import Path

from core.industry import INDUSTRY_CODING_FILES, detect_industry_category
from core.lookup import build_code_index, find_value_by_label
from core.numbers import format_pct, format_maybe_pct
from core.text import page_num
from entities import (BANK_PROFILES, COMPOSITE_TERMS, _PROFILE_FIELDS, SUMMARY_CODE_OVERRIDES,
                      SUMMARY_CODE_OVERRIDES_FINSUM, SUMMARY_CODE_DERIVATIONS,
                      SUMMARY_LABEL_FALLBACKS)
from ratios import collect_roa_roe




# ---------------------------------------------------------------------------
# Curated per-bank summary export ("summary" mode): a fixed set of specific
# codes plus two composite/derived terms, with bank-specific variations.
#
# Unlike the whole-statement dumps above, these codes are matched directly
# against a document's rows regardless of whether they're present in a
# coding dictionary - most of them aren't. This is expected - the list spans
# concepts that not every bank reports under the same code, which is also
# why some entries need a bank-specific override (e.g. 國泰 uses 63000
# instead of 64000) or an entirely different composite formula. No
# statement-section restriction is applied, since a given code isn't
# guaranteed to live in the same statement across all 4 banks - the whole
# document is searched.
#
# A code not found in a given filing - or an entire composite term, if any
# one of its component codes is missing - shows as N/A rather than the row
# being omitted, so every expected line always appears.
# ---------------------------------------------------------------------------

# Ordered summary layout: the fixed sequence of lines "summary" mode always
# outputs, each with a CANONICAL display term (not the document's own
# wording, which varies across banks - e.g. 資產合計/資產總計/資產 all become
# 總資產) - a deliberate departure from this mode's earlier "always use the
# document's own label" rule, per explicit instruction to standardize
# output terms for cross-bank comparability. The document's own label is
# still kept (see collect_summary_rows' "matched_label" field) so nothing
# is lost, just no longer what's shown as "term".
# is_cost=True marks a line that should DISPLAY AS POSITIVE for a genuine
# cost (see apply_cost_sign) - a real filing stores an expense as a
# NEGATIVE number in this net-income-walk table style, so a positive
# is_cost value after conversion means "this much cost", and a NEGATIVE
# is_cost value means "this was actually a benefit/reversal, not a cost".
SUMMARY_LAYOUT = [
    {"kind": "code", "code": "10000", "term": "總資產", "is_cost": False},
    {"kind": "code", "code": "4xxxx", "term": "淨收益", "is_cost": False},
    {"kind": "code", "code": "49010", "term": "利息淨收益", "is_cost": False},
    {"kind": "code", "code": "49100", "term": "手續費淨收益", "is_cost": False},
    {"kind": "composite", "name": "評價及已實現", "term": "評價及已實現", "is_cost": False},
    {"kind": "composite", "name": "其他非利息收益", "term": "其他非利息收益", "is_cost": False},
    {"kind": "code", "code": "58400", "term": "營業費用", "is_cost": True},
    {"kind": "code", "code": "58500", "term": "員工福利費用", "is_cost": True},
    {"kind": "code", "code": "59000", "term": "折舊及攤銷費用", "is_cost": True},
    {"kind": "code", "code": "59500", "term": "其他費用", "is_cost": True},
    {"kind": "code", "code": "58200", "term": "呆帳提存(收回)", "is_cost": True},
    {"kind": "code", "code": "61001", "term": "稅前淨利", "is_cost": False},
    {"kind": "code", "code": "61003", "term": "所得稅費用", "is_cost": True},
    {"kind": "code", "code": "64000", "term": "稅後淨利", "is_cost": False},
    # 活存比 has no account code (it's a disclosed ratio, not a coded
    # balance-sheet/income-statement line) - matched purely by label text,
    # like SUMMARY_LABEL_FALLBACKS' subtotal rows (see find_value_by_label),
    # not through build_code_index's code-keyed batch pass. None of the 4
    # banks' filings checked so far disclose it, so this exports N/A for
    # now - kept as a row per explicit instruction, rather than only adding
    # it once a filing with the label actually turns up.
    # "活期性存款比率" is the real wording confirmed in the quarterly
    # summarized fin-report disclosures (see collect_summary_rows_finsum) -
    # kept alongside the earlier guessed wordings in case a full annual/
    # quarterly filing ever uses different phrasing for the same ratio.
    {"kind": "label", "label_aliases": ["活期性存款比率", "活存性存款比率", "活存比"],
     "term": "活存比", "is_percent": True},
]




# SUMMARY_LAYOUT is bank-shaped, and its codes are matched RAW against the
# document - summary mode never loads an industry coding dictionary (see
# collect_summary_rows), so nothing about a code carries its own industry
# with it. The same number means a different account under a different
# scheme: 58200 is 呆帳提存 under 金融業 but an insurance cost line under
# 保險業 (see INDUSTRY_CODING_FILES, which documents that difference).
#
# Applying this layout to a filing it wasn't built for therefore does NOT
# fail - it relabels a real, correctly-parsed number. Confirmed on a 國泰
# 人壽 filing: industry detected as 保險業 correctly, then '保險成本' was
# emitted under the canonical term '呆帳提存(收回)' with its sign flipped by
# apply_cost_sign. matched_label still held '保險成本', but term is the field
# the CSV/Excel exports and _MERGED_TERM_ORDER key on, and standardizing
# term for cross-entity comparison is this layout's whole purpose.
#
# So an industry with no layout of its own is REFUSED, not defaulted.
INDUSTRY_SUMMARY_LAYOUTS = {
    "金融業": SUMMARY_LAYOUT,
    # 金控業 shares the bank layout: the per-bank override tables were built
    # around FHC-consolidated scope in the first place (see
    # SUMMARY_CODE_OVERRIDES_FINSUM's note on 63000/64000), so these codes
    # are already exercised against FHC-scope rows. Not independently
    # re-verified against a 金控's own filing - if one turns up whose rows
    # don't line up, give it its own layout rather than widening this list.
    "金控業": SUMMARY_LAYOUT,
    # 保險業 deliberately absent. A layout for it needs the 保險業 scheme's
    # own codes for 保費收入/保險給付/etc., which nothing in this repo
    # establishes - guessing them would produce exactly the mislabelling
    # above, only harder to spot.
}




def summary_layout_error(industry):
    """Message for a filing whose industry has no summary layout."""
    return (f"summary mode has no layout for industry {industry!r} - it currently covers "
            f"{', '.join(INDUSTRY_SUMMARY_LAYOUTS)}. Its account codes are matched raw "
            f"against the document, so running it on another industry's scheme relabels "
            f"real numbers instead of failing (58200 is 呆帳提存 for a bank but an "
            f"insurance cost line under 保險業). Use a per-statement mode "
            f"(balance_sheet/income_statement/cash_flow), which resolves codes through "
            f"that industry's own coding dictionary.")




def apply_cost_sign(value, matched_label, is_cost):
    """Flip sign so a cost line displays positive (see SUMMARY_LAYOUT's
    is_cost docs). Skipped when the document's OWN label already carries a
    '減' ('less:'/deduct) prefix (confirmed in a real 中信 filing:
    '減：所得稅費用' printed as a POSITIVE number) - that convention already
    stores the value in "amount to subtract" form, i.e. already
    cost-positive, so flipping it again would wrongly turn a normal expense
    into an apparent benefit."""
    if value is None or not is_cost:
        return value
    # A '減：' PREFIX, not a bare '減' anywhere in the label: 減損 (impairment)
    # and 減資 are ordinary line items that merely start with the character,
    # and treating them as already-deducted left a normal expense negative,
    # displaying as if it were a reversal.
    if matched_label and matched_label.strip().startswith(("減：", "減:")):
        return value
    return -value




def _validate_profiles(profiles):
    """Reject an incomplete or inconsistent entity profile at import time.

    The whole point of consolidating six tables into one is that adding an
    entity can't half-happen any more. A missing composites entry used to
    surface as a single N/A row in one bank's summary - indistinguishable
    from a genuinely undisclosed line, and only in that bank's output."""
    for name, profile in profiles.items():
        missing = _PROFILE_FIELDS - set(profile)
        extra = set(profile) - _PROFILE_FIELDS
        if missing or extra:
            raise ValueError(f"entity profile {name!r} is malformed"
                             + (f"; missing {sorted(missing)}" if missing else "")
                             + (f"; unexpected {sorted(extra)}" if extra else ""))
        if not profile["aliases"]:
            raise ValueError(f"entity profile {name!r} has no aliases, so it can never be detected")
        for industry in profile["industries"]:
            if industry not in INDUSTRY_CODING_FILES:
                raise ValueError(f"entity profile {name!r} names industry {industry!r}, which is not "
                                 f"one of {list(INDUSTRY_CODING_FILES)}")
        # Every composite item in a layout this entity's filings are read
        # under needs a code list here - that is the check the six separate
        # tables could not perform.
        for industry in profile["industries"]:
            for item in INDUSTRY_SUMMARY_LAYOUTS.get(industry) or []:
                if item["kind"] == "composite" and item["name"] not in profile["composites"]:
                    raise ValueError(f"entity profile {name!r} is read under {industry!r}, whose summary "
                                     f"layout has composite item {item['name']!r}, but defines no codes "
                                     f"for it")




_validate_profiles(BANK_PROFILES)




# The three folder-wide lookups these extractors sit on moved to
# core/lookup.py - both compute_ratios and collect_summary_rows need
# them and neither owns them. Re-exported above, so `acctfinder.X`
# still resolves for callers and for tests that monkeypatch it.


def collect_summary_rows(folder, bank, period=1, verbose=False, coding=None,
                          concall_roa=None, concall_roe=None, overrides_table=None,
                          industry=None):
    """Build the curated summary, in SUMMARY_LAYOUT's fixed order, plus a
    trailing ROA and ROE row (see collect_roa_roe). Returns a list of
    {term, value, matched_label, source_file, is_percent, crosscheck_value,
    note}:
      - term: the CANONICAL display name from SUMMARY_LAYOUT (not the
        document's own wording - see SUMMARY_LAYOUT's docstring).
      - matched_label: the document's own label for that row (or the term
        itself for a composite, which has no single source row) - kept so
        the original wording isn't lost even though it's no longer "term".
      - value: sign-adjusted per is_cost (see apply_cost_sign); None
        (displayed as N/A) rather than the row being omitted when a code
        genuinely can't be found, so every expected line always appears.
      - is_percent: True only for ROA/ROE (already a % rate, not a NT$
        amount) - display code uses this to choose format_pct vs
        format_value rather than assuming based on term name.
      - crosscheck_value: ROA/ROE only, None for every other row (see
        collect_roa_roe - the manual-formula cross-check).
      - note: "" for a row that resolved cleanly. Set on ROA/ROE when the
        cross-check diverges or the value is implausible (collect_roa_roe),
        AND on every N/A row, saying WHY it's N/A - missing code, no
        matching label, no composite formula for this bank. That reason is
        also printed under -v, but the note is the only copy that reaches
        the csv/excel exports, which is where the distinction between "not
        disclosed" and "we failed to read this filing" actually matters.
    concall_roa/concall_roe: an earnings-call deck's own reported ROA/ROE
    (looked up by the caller via callfinder.py, since this module can't
    import it - see collect_roa_roe), used as a fallback when this fin
    folder has no reported 獲利能力 disclosure table of its own.
    overrides_table: which per-bank code-override table to apply (defaults
    to SUMMARY_CODE_OVERRIDES, for full individual filings) - passed as
    SUMMARY_CODE_OVERRIDES_FINSUM by collect_summary_rows_finsum(), since
    the quarterly summarized disclosure uses a different code scheme for
    at least one bank (see that table's docs). Every other piece of logic
    here (SUMMARY_LAYOUT, COMPOSITE_TERMS, label fallbacks, ROA/ROE, CIR)
    is identical between the two document types - only the code-resolution
    step differs, which is why this is a shared function with a swappable
    table rather than a separate parallel implementation.
    """
    # Resolved here rather than at the call sites: this is the only place
    # that knows a layout is about to be applied, so a future caller can't
    # forget the check. industry may be passed in when the caller already
    # resolved it; None means detect it from the filing.
    if industry is None:
        industry = detect_industry_category(folder)
    layout = INDUSTRY_SUMMARY_LAYOUTS.get(industry)
    if layout is None:
        raise ValueError(summary_layout_error(industry))

    overrides = (overrides_table if overrides_table is not None else SUMMARY_CODE_OVERRIDES).get(bank, {})

    # Gather every code this run will need up front, so build_code_index can
    # resolve all of them in one shared pass over the folder's files instead
    # of one pass per code (see build_code_index) - both the "code"-kind
    # items (after per-bank override resolution) and every composite item's
    # component codes.
    needed_codes = set()
    label_fallbacks = {}
    for item in layout:
        if item["kind"] == "code":
            code = overrides.get(item["code"], item["code"])
            needed_codes.add(code)
            # Fetched whether or not they're needed - they're layout items in
            # their own right today, but this must not depend on that.
            needed_codes.update(SUMMARY_CODE_DERIVATIONS.get(code, []))
            fallback = SUMMARY_LABEL_FALLBACKS.get(item["code"])
            if fallback:
                label_fallbacks[code] = fallback
        elif item["kind"] == "composite":
            needed_codes.update(COMPOSITE_TERMS[item["name"]].get(bank) or [])
        # "label" kind (e.g. 活存比) has no account code at all - resolved
        # separately via find_value_by_label in the row-building loop below,
        # not part of this code-keyed batch pass.

    index = build_code_index(folder, needed_codes, label_fallbacks=label_fallbacks,
                              period=period, verbose=verbose)

    rows = []
    for item in layout:
        if item["kind"] == "code":
            code = overrides.get(item["code"], item["code"])
            found = index.get(code)
            note = ""
            if found is None and code in SUMMARY_CODE_DERIVATIONS:
                parts = [index.get(c) for c in SUMMARY_CODE_DERIVATIONS[code]]
                if all(p is not None for p in parts):
                    labels = [p[0] for p in parts]
                    found = ("+".join(labels), sum(p[1] for p in parts), parts[0][2])
                    note = (f"derived: this filing states no {item['term']} total, "
                            f"so this is {' + '.join(labels)}")
                    if verbose:
                        print(f"[derived] code {code} ({item['term']}) = {' + '.join(labels)}")
            if found is None:
                # Same string to the console and to the row's note: the note is
                # the only place this reason survives into the exported csv/
                # excel, where an unreadable filing and an undisclosed line
                # otherwise look identical (see summary_coverage_warning).
                reason = f"code {code} ({item['term']}) not found in any file"
                if verbose:
                    print(f"[N/A] {reason}")
                rows.append({"term": item["term"], "value": None, "matched_label": None, "source_file": None,
                             "is_percent": False, "crosscheck_value": None, "note": reason})
                continue
            matched_label, value, source_file = found
            value = apply_cost_sign(value, matched_label, item["is_cost"])
            rows.append({"term": item["term"], "value": value,
                         "matched_label": matched_label, "source_file": source_file,
                         "is_percent": False, "crosscheck_value": None, "note": note})
            continue

        if item["kind"] == "label":
            term_name = item["term"]
            found = find_value_by_label(folder, item["label_aliases"], period=period, verbose=verbose)
            if found is None:
                reason = f"'{term_name}' - none of {item['label_aliases']} found in any file"
                if verbose:
                    print(f"[N/A] {reason}")
                rows.append({"term": term_name, "value": None, "matched_label": None, "source_file": None,
                             "is_percent": item.get("is_percent", False), "crosscheck_value": None,
                             "note": reason})
                continue
            matched_label, value, source_file = found
            rows.append({"term": term_name, "value": value,
                         "matched_label": matched_label, "source_file": source_file,
                         "is_percent": item.get("is_percent", False), "crosscheck_value": None, "note": ""})
            continue

        # composite
        term_name = item["term"]
        codes = COMPOSITE_TERMS[item["name"]].get(bank)
        if codes is None:
            reason = f"'{term_name}' has no formula defined for bank '{bank}'"
            if verbose:
                print(f"[N/A] {reason}")
            rows.append({"term": term_name, "value": None, "matched_label": None, "source_file": None,
                         "is_percent": False, "crosscheck_value": None, "note": reason})
            continue
        component_values, component_files, missing = [], [], None
        for code in codes:
            found = index.get(code)
            if found is None:
                missing = f"'{term_name}': component code {code} not found"
                if verbose:
                    print(f"[N/A] {missing}")
                break
            _label, value, source_file = found
            component_values.append(value)
            component_files.append(source_file)
        if missing:
            rows.append({"term": term_name, "value": None, "matched_label": None, "source_file": None,
                         "is_percent": False, "crosscheck_value": None, "note": missing})
            continue
        total = sum(component_values)
        total = apply_cost_sign(total, None, item["is_cost"])
        rows.append({"term": term_name, "value": total,
                     "matched_label": term_name, "source_file": component_files[0],
                     "is_percent": False, "crosscheck_value": None, "note": ""})

    # --- CIR: abs(營業費用) / 淨收益, direct from THIS filing, no crosscheck.
    # Previously computed on the con-call side from that deck's own
    # 營業費用/營業收入 table, but that figure turned out to be a different
    # scope from this fin_report's individual-entity 58400/4xxxx codes (e.g.
    # a real 中信 4Q25 con-call page's revenue/opex were roughly an order of
    # magnitude off fin_report's, not a rounding-level gap) - moved here per
    # explicit instruction, no extra folder scan needed.
    #
    # Read off the ROWS just built, not the raw code index: those two rows
    # have already been through the label fallback and SUMMARY_CODE_DERIVATIONS,
    # so a filing that states no 營業費用 total still gets a CIR instead of
    # this silently disagreeing with the 營業費用 line printed right above it.
    # abs() makes the is_cost sign flip on the 營業費用 row irrelevant here.
    by_term = {r["term"]: r["value"] for r in rows}
    netrev_value, opex_value = by_term.get("淨收益"), by_term.get("營業費用")
    cir_value, cir_note = None, ""
    if netrev_value is not None and opex_value is not None:
        if netrev_value:
            cir_value = abs(opex_value) / netrev_value * 100
        else:
            cir_note = "CIR undefined: 淨收益 is zero"
    else:
        absent = [t for t, v in (("淨收益", netrev_value), ("營業費用", opex_value)) if v is None]
        cir_note = f"CIR needs {' and '.join(absent)}, which came back N/A above"
    rows.append({"term": "CIR", "value": cir_value, "matched_label": "abs(營業費用) / 淨收益",
                 "source_file": None, "is_percent": True, "crosscheck_value": None, "note": cir_note})

    roa_roe = collect_roa_roe(folder, bank, coding=coding, concall_roa=concall_roa,
                               concall_roe=concall_roe, verbose=verbose)
    for key, term in (("roa", "ROA"), ("roe", "ROE")):
        r = roa_roe[key]
        if r is None:
            # None means all three sources in collect_roa_roe's priority order
            # came up empty - naming them is what tells a reader whether to go
            # looking for a 獲利能力 table or for the missing balance-sheet
            # codes the manual formula needs.
            rows.append({"term": term, "value": None, "matched_label": None, "source_file": None,
                         "is_percent": True, "crosscheck_value": None,
                         "note": f"no {term}: filing discloses no 獲利能力 table, no con-call figure "
                                 f"was supplied, and the manual formula wasn't derivable"})
        else:
            rows.append({"term": r["term"], "value": r["value"], "matched_label": r["matched_label"],
                         "source_file": r["source_file"], "is_percent": True,
                         "crosscheck_value": r["crosscheck_value"], "note": r["note"]})

    return rows




def collect_summary_rows_finsum(folder, bank, period=1, verbose=False, coding=None,
                                 concall_roa=None, concall_roe=None, industry=None):
    """Curated summary for a bank's quarterly SUMMARIZED fin-report
    disclosure (依「公開發行銀行財務報告編製準則」第三十二條規定網站揭露財務
    業務資訊) - a much shorter document (~10-15 pages) than the full
    individual filing (~150-280), but confirmed (114Q4 CTBC/北富銀/國泰) to
    use the exact same table shapes, code conventions, and section content
    for every SUMMARY_LAYOUT item - including CTBC's dual-column balance
    sheet (already handled by build_raw_lines' _split_dual_column_tables)
    and a real 活期性存款比率 disclosure (see SUMMARY_LAYOUT's 活存比 entry).
    The only genuine difference found is 稅後淨利's code varying by bank in
    THIS document type specifically (see SUMMARY_CODE_OVERRIDES_FINSUM) -
    everything else is identical, so this is a thin wrapper around
    collect_summary_rows rather than a separate parsing implementation, per
    explicit instruction to reuse the same logic as the full filing.
    Deliberately NOT wired into any automatic/default code path - only
    called once a caller (runfinder.py's folder classifier) has positively
    identified a folder as this document type, never run on a folder just
    because it happens to be a fin_report."""
    return collect_summary_rows(folder, bank, period=period, verbose=verbose, coding=coding,
                                 concall_roa=concall_roa, concall_roe=concall_roe,
                                 overrides_table=SUMMARY_CODE_OVERRIDES_FINSUM,
                                 industry=industry)




# Fraction of N/A rows above which a summary is more likely a failed read
# than a genuinely absent disclosure. Deliberately generous: a handful of
# N/A rows is ordinary (活存比 is N/A for every bank checked so far, and a
# filing that truly doesn't disclose a line is a real result), so this has
# to clear normal variation and only fire on wholesale failure.
_SUMMARY_NA_WARN_RATIO = 0.5




def summary_coverage_warning(rows, folder=None):
    """A one-line warning when most of a summary came back N/A, else None.

    An N/A row still occupies a line like any other (every SUMMARY_LAYOUT
    line always appears, see collect_summary_rows), so a filing whose layout
    this extractor simply failed to read produces output shaped exactly like
    a successful run. Each N/A row now carries its own reason in `note`, but
    that is per-row: it says why THIS line is missing, not that the run as a
    whole went wrong. At four banks the wholesale case was catchable by eye;
    across the whole sector it is not, and the csv/excel export paths don't
    even print the rows for anyone to look at.

    ponytail: one flat ratio over all rows. If it turns out to be noisy, the
    fix is a per-row 'expected N/A' flag in SUMMARY_LAYOUT (活存比 is the
    known case), not a tuned constant - the constant can't tell the two
    kinds of N/A apart no matter what it's set to."""
    if not rows:
        return None
    missing = [r["term"] for r in rows if r["value"] is None]
    if len(missing) <= _SUMMARY_NA_WARN_RATIO * len(rows):
        return None
    where = f" for {folder}" if folder else ""
    return (f"WARNING: {len(missing)} of {len(rows)} summary rows are N/A{where} - "
            f"check this filing's layout is being read correctly ({', '.join(missing)})")




def print_summary_rows(rows):
    print("\n=== summary ===")
    if not rows:
        print("No matching codes found in any file.")
        return
    for r in rows:
        found = r.get("matched_label") or "-"
        value_str = format_maybe_pct(r["value"], r.get("is_percent", False))
        line = f"{r['term']}\t{value_str}\t{found}\t({page_num(r['source_file'])})"
        # Only surface the cross-check value when it's BOTH available AND
        # something was actually flagged about this row (crosscheck_value is
        # populated whenever the manual formula is derivable, whether or not
        # it agrees - showing it unconditionally would defeat the earlier
        # "don't show a cross-check that just confirms the primary value"
        # design; requiring a note too keeps it hidden when everything
        # agrees, and correctly hides it for the invariant-check row below,
        # which always has a note but never a crosscheck_value at all).
        if r.get("crosscheck_value") is not None and r.get("note"):
            line += f"\tcross-check: {format_pct(r['crosscheck_value'])}"
        print(line)
        if r.get("note"):
            print(f"  NOTE: {r['note']}")




def write_summary_csv(folder, rows):
    out_path = Path(folder) / "summary_export.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["term", "value", "term_found", "page", "crosscheck_value", "note"])
        for r in rows:
            value_str = format_maybe_pct(r["value"], r.get("is_percent", False))
            # Only surface the cross-check value when it's both available and
            # something was flagged - see the matching comment in print_summary_rows.
            crosscheck = (format_pct(r["crosscheck_value"])
                          if r.get("crosscheck_value") is not None and r.get("note") else "")
            writer.writerow([r["term"], value_str, r.get("matched_label") or "",
                              page_num(r["source_file"]), crosscheck, r.get("note") or ""])
    return out_path
