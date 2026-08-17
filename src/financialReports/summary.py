"""The curated cross-entity summary: one fixed set of lines, every filing.

The layouts live here, and so does the check that every entity profile
defines the composites the layout it is read under actually needs
(_validate_profiles, run at import - an incomplete entity is an import-time
error, never a silent N/A at run time).

Top of the stack: reads entities for the profiles and ratios for the trailing
ROA/ROE rows. Nothing imports this except statements's CLI and cli.
"""
import csv
from pathlib import Path

from core.industry import INDUSTRY_CODING_FILES, detect_industry_category
from core.lookup import build_code_index, find_value_by_label
from core.numbers import format_pct, format_maybe_pct
from core.text import page_num
from financialReports.entities import (BANK_PROFILES, COMPOSITE_TERMS, _PROFILE_FIELDS, SUMMARY_CODE_OVERRIDES,
                      SUMMARY_CODE_OVERRIDES_FINSUM, SUMMARY_CODE_DERIVATIONS,
                      SUMMARY_LABEL_FALLBACKS)
from financialReports.ratios import collect_roa_roe




# ---------------------------------------------------------------------------
# The fixed sequence of lines "summary" mode always outputs.
#
# Codes are matched RAW against the document (no coding dictionary, no
# statement-section restriction) - a code is not guaranteed to live in the
# same statement across banks. A code (or a whole composite, if one component
# is missing) that isn't found shows as N/A rather than the row being
# omitted, so every expected line always appears.
#
# `term` is a CANONICAL display name, not the document's own wording; the
# document's wording is kept as matched_label. is_cost=True marks a line that
# must DISPLAY AS POSITIVE for a genuine cost - filings store an expense as a
# NEGATIVE number in this net-income-walk style, so a negative is_cost value
# after conversion means "a reversal, not a cost".
#   → docs/knowledge/account-codes.md#標準化-term-與文件原本的措辭
# ---------------------------------------------------------------------------

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
    # 活存比 has no account code at all - label text only. N/A for every bank
    # so far, and that is the CORRECT result: the filings carry only 活期存款
    # balances, no disclosed ratio. Do not compute one.
    #   → docs/knowledge/na-and-refusal.md#活存比為什麼永遠是-na
    {"kind": "label", "label_aliases": ["活期性存款比率", "活存性存款比率", "活存比"],
     "term": "活存比", "is_percent": True},
]




# These codes are matched RAW against the document - summary mode never loads
# an industry coding dictionary, so a code carries no industry with it, and
# 58200 is 呆帳提存 under 金融業 but an insurance cost line under 保險業.
# Applying this layout to the wrong industry does NOT fail; it relabels a real,
# correctly-parsed number. So an industry with no layout is REFUSED, not
# defaulted. 實際案例（國泰人壽）
#   → docs/knowledge/industry-and-layout.md#summary_layout-為什麼綁死產業
INDUSTRY_SUMMARY_LAYOUTS = {
    "金融業": SUMMARY_LAYOUT,
    # 金控業 shares the bank layout, but was never independently verified
    # against a 金控's own filing. If one turns up whose rows don't line up,
    # give it its OWN layout rather than widening this list.
    "金控業": SUMMARY_LAYOUT,
    # 保險業 deliberately absent - its codes are not established anywhere in
    # this repo, and guessing produces the mislabelling above, harder to spot.
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
    is_cost docs). Skipped when the document's OWN label carries a '減：'
    prefix - that convention already stores the value cost-positive, so
    flipping again turns a normal expense into an apparent benefit."""
    if value is None or not is_cost:
        return value
    # A '減：' PREFIX, never a bare '減' anywhere in the label: 減損 and 減資
    # are ordinary line items that merely start with the character.
    #   → docs/knowledge/account-codes.md#標準化-term-與文件原本的措辭
    if matched_label and matched_label.strip().startswith(("減：", "減:")):
        return value
    return -value




def _validate_profiles(profiles):
    """Reject an incomplete or inconsistent entity profile at IMPORT time.

    The point of consolidating six tables into one is that adding an entity
    can no longer half-happen: a missing composites entry surfaces as a single
    N/A row in one bank's output, indistinguishable from a genuinely
    undisclosed line. → docs/knowledge/entity-resolution.md#為什麼機構資料集中在一張表"""
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
# them and neither owns them. Re-exported above, so `statements.X`
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
        cross-check diverges or the value is implausible, AND on every N/A
        row, saying WHICH KIND of N/A it is. The note is the only copy that
        reaches the csv/excel exports - that is where "not disclosed" and
        "we failed to read this filing" otherwise look identical.
          → docs/knowledge/na-and-refusal.md#na-的六種成因
    concall_roa/concall_roe: a deck's own reported ROA/ROE, supplied by the
    caller (this module cannot import earningsCalls - it would be a cycle).
    overrides_table: which per-bank code-override table to apply; defaults to
    SUMMARY_CODE_OVERRIDES, swapped for SUMMARY_CODE_OVERRIDES_FINSUM by
    collect_summary_rows_finsum. Only the code-resolution step differs
    between the two document types, hence one function with a swappable
    table rather than a parallel implementation.
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
    # Do not source it from a deck instead - that table is a different scope
    # (~an order of magnitude off), so the same name would cover two
    # different quantities. → docs/knowledge/ratios.md#cir-為什麼從法說會搬到財報
    #
    # Read off the ROWS just built, NOT the raw code index: those two rows
    # have already been through the label fallback and SUMMARY_CODE_DERIVATIONS,
    # so a filing that states no 營業費用 total still gets a CIR instead of
    # silently disagreeing with the 營業費用 line printed right above it.
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
    disclosure (依「公開發行銀行財務報告編製準則」第三十二條).

    A thin wrapper: the only confirmed difference from a full filing is
    稅後淨利's per-bank code, hence one swapped override table rather than a
    parallel parser. → docs/knowledge/account-codes.md#每機構的代碼覆寫

    Deliberately NOT wired into any automatic path - only called once
    cli.py's classifier has positively identified this document type, never
    just because a folder happens to be a fin_report."""
    return collect_summary_rows(folder, bank, period=period, verbose=verbose, coding=coding,
                                 concall_roa=concall_roa, concall_roe=concall_roe,
                                 overrides_table=SUMMARY_CODE_OVERRIDES_FINSUM,
                                 industry=industry)




# Fraction of N/A rows above which a summary is more likely a failed read
# than a genuinely absent disclosure. Deliberately generous - a handful of
# N/A rows is ordinary, so this must clear normal variation and only fire on
# wholesale failure.
_SUMMARY_NA_WARN_RATIO = 0.5




def summary_coverage_warning(rows, folder=None):
    """A one-line warning when most of a summary came back N/A, else None.

    Per-row notes say why THIS line is missing; this says the run as a whole
    went wrong. A misread layout produces output shaped exactly like a
    successful one, and the csv/excel paths never print the rows.
      → docs/knowledge/na-and-refusal.md#整份讀錯的警告

    ponytail: one flat ratio over all rows. If it turns out noisy, the fix is
    a per-row 'expected N/A' flag in SUMMARY_LAYOUT (活存比 is the known
    case), not a tuned constant - no value of the constant can tell the two
    kinds of N/A apart."""
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
        # Show the cross-check only when it's available AND something was
        # flagged: crosscheck_value is populated whenever the manual formula
        # is derivable, agreeing or not, so requiring a note too is what keeps
        # a merely-confirming cross-check off the output.
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
