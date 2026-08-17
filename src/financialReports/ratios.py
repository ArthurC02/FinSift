"""ROA/ROE: the disclosed figure, the manual formula, and their disagreement.

Two independent measurements of the same thing, and the rule that neither may
silently overwrite the other - see collect_roa_roe. The disclosed figure is
parsed by profitability.py; what happens here is the arithmetic, the
cross-check and the plausibility bounds.
"""
import csv
from pathlib import Path

from core.lookup import find_code_value
from core.numbers import nth_value, format_value, annualize, format_pct
from core.text import page_num
from financialReports.entities import (BANK_NAME_ALIASES, SUMMARY_CODE_OVERRIDES,
                                       SUMMARY_LABEL_FALLBACKS)
from financialReports.profitability import (derive_quarter_num, find_profitability_entries,
                                            quarter_num_from_period_label, parse_single_date)




def compute_ratios(folder, bank, coding_path=None, verbose=False):
    """Compute ROA(稅後年化) and ROE(稅後年化) from the same account codes
    and per-bank override/label-fallback logic SUMMARY_LAYOUT already uses
    (10000=資產, 30000=權益, 64000/63000=稅後淨利 per bank) via
    find_code_value. It used to go through a section-marker-restricted
    lookup (find_statement_rows, since deleted as dead code), which turned
    out to be unreliable in practice (a real 國泰 filing's income-statement
    page never repeats its own '...綜合損益表' section title, so the marker
    search silently found nothing even though the code rows were right
    there) and, separately, used codes (19999/39999/69000) left over from
    before this project's industry-coding-dictionary rewrite - confirmed
    absent from the current 金控業/金融業/保險業 workbooks entirely (that
    mapping was RATIO_CODES, also now deleted). Raises RuntimeError with a
    clear message if any required code/quarter can't be found.
    coding_path is unused now (kept as a parameter for backward
    compatibility with existing callers) since find_code_value doesn't
    need a coding dictionary - it matches raw document codes directly."""
    overrides = SUMMARY_CODE_OVERRIDES.get(bank, {})
    net_income_code = overrides.get("64000", "64000")

    def get(code, period, label, label_fallback=None):
        found = find_code_value(folder, code, period=period, verbose=verbose, label_fallback=label_fallback)
        if found is None:
            raise RuntimeError(f"Code {code} ({label}) not found in any file.")
        _label, value, source_file = found
        if value is None:
            raise RuntimeError(f"Code {code} ({label}) was found but had no period-{period} value.")
        return value, source_file

    net_income, inc_source = get(net_income_code, 1, "本期稅後淨利（淨損）",
                                  SUMMARY_LABEL_FALLBACKS.get("64000"))
    assets_cur, bs_source = get("10000", 1, "資產總計, current quarter", SUMMARY_LABEL_FALLBACKS.get("10000"))
    assets_prev, _ = get("10000", 2, "資產總計, last quarter", SUMMARY_LABEL_FALLBACKS.get("10000"))
    equity_cur, _ = get("30000", 1, "權益總計, current quarter", SUMMARY_LABEL_FALLBACKS.get("30000"))
    equity_prev, _ = get("30000", 2, "權益總計, last quarter", SUMMARY_LABEL_FALLBACKS.get("30000"))

    bs_path = next(Path(folder).rglob(bs_source), None)
    quarter_num = derive_quarter_num(bs_path) if bs_path else None
    if quarter_num is None:
        raise RuntimeError(f"Couldn't determine the current quarter number from {bs_source}.")

    # net_income is a YEAR-TO-DATE cumulative figure, so divide by quarter_num
    # BEFORE annualising - at quarter_num=4 that correctly degrades to a no-op.
    # Do not "simplify" the division away: assuming single-quarter and applying
    # a flat x4 made three banks' Q4 figures ~4x too high.
    # A zero average balance must raise RuntimeError like any other lookup
    # failure - ZeroDivisionError is NOT a subclass of it, so letting the
    # division raise took down the whole run instead of degrading.
    # 欄位標題證據 → docs/knowledge/financialReports.md#手算公式為什麼要先除以季數
    avg_assets = (assets_cur + assets_prev) / 2
    avg_equity = (equity_cur + equity_prev) / 2
    for label, denom in (("資產總計 (10000)", avg_assets), ("權益總計 (30000)", avg_equity)):
        if denom == 0:
            raise RuntimeError(f"Average {label} across the two periods is 0 - "
                               f"can't compute a ratio from it.")

    roa = net_income / avg_assets / quarter_num * 4
    roe = net_income / avg_equity / quarter_num * 4

    if verbose:
        print(f"Balance sheet: {bs_source} | Income statement: {inc_source} | quarter: Q{quarter_num}")
        print(f"  net_income ({net_income_code}) = {format_value(net_income)}")
        print(f"  total_assets (10000)  = {format_value(assets_cur)} (current), {format_value(assets_prev)} (last quarter)")
        print(f"  total_equity (30000)  = {format_value(equity_cur)} (current), {format_value(equity_prev)} (last quarter)")

    return {"roa": roa, "roe": roe, "quarter_num": quarter_num}




def _select_profitability_entry(entries, bank):
    """Pick the most-recent, best-scoped entry from find_profitability_entries'
    output. entity=None (layout 3 - a filing's single entity) always counts
    as in-scope, since there's nothing else it could be; otherwise prefer an
    entry whose entity text names `bank` (via BANK_NAME_ALIASES), falling
    back to any entity if none matches. Among the scoped candidates, prefers
    one that actually has a value, then the most recent period. None if
    `entries` is empty."""
    if not entries:
        return None
    aliases = BANK_NAME_ALIASES.get(bank, []) if bank else []
    scoped = [e for e in entries
              if e.get("entity") is None or (aliases and any(a in e["entity"] for a in aliases))]
    pool = scoped or entries
    with_value = [e for e in pool if e["roa_posttax"] is not None or e["roe_posttax"] is not None]
    pool = with_value or pool

    def period_key(e):
        m = parse_single_date(e["period_label"]) if e["period_label"] else None
        return m or (0, 0, 0)

    return max(pool, key=period_key)




# Two as-disclosed ROA/ROE readings for the SAME quarter shouldn't diverge
# by more than this factor if they're measuring the same thing the same
# way; a bigger gap flags a likely convention mismatch (e.g. one figure is
# a raw cumulative-YTD number, the other already annualized) worth a human
# double-check, rather than silently picking one.
_ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR = 2.0



# Wide, deliberately generous plausibility bounds - catch a grossly
# implausible value (e.g. a decimal/percent parsing bug producing ~92%
# instead of ~0.92%), not legitimate variation. Grounded in real observed
# data across all 4 currently-supported banks this session: ROA(稅後)
# ranged 0.24%-1.12%, ROE(稅後) ranged 3.33%-15.02%. Allows negative
# values too, since a quarterly loss is a real result, not a bug - this is
# Deliberately WIDE: a loss quarter is an ordinary input, so this should
# almost never fire on real data. If it does, suspect a parsing/scale/sign
# error rather than a real outlier bank.
# → docs/knowledge/financialReports.md#合理範圍為什麼開得那麼寬
_ROA_PLAUSIBLE_MIN, _ROA_PLAUSIBLE_MAX = -5.0, 5.0     # percent


_ROE_PLAUSIBLE_MIN, _ROE_PLAUSIBLE_MAX = -50.0, 50.0   # percent




def collect_roa_roe(folder, bank, coding=None, concall_roa=None, concall_roe=None, verbose=False):
    """ROA/ROE for the curated fin_report summary.

    Priority: the filing's own 獲利能力 disclosure table, then the con-call
    figure the caller supplies, then this filing's manual formula (clearly
    labelled an approximation). The disclosed figure is used AS DISCLOSED -
    never re-annualised, because the convention is NOT consistent across banks.

    Whichever wins, the manual formula is ALSO computed and returned as a
    cross-check. It must never silently override a disclosed figure; a
    divergence is reported in `note` because the gap itself is signal.

    Returns {"roa": row_or_None, "roe": row_or_None}, each a dict with term,
    value, matched_label, source_file, crosscheck_value, note.

    三個來源的證據與為什麼不能盲目年化
      → docs/knowledge/financialReports.md#roaroe-的三個來源
    """
    entries = find_profitability_entries(folder, verbose=verbose)
    entry = _select_profitability_entry(entries, bank)

    manual = None
    try:
        manual = compute_ratios(folder, bank, coding, verbose=verbose)
    except RuntimeError as e:
        if verbose:
            print(f"ROA/ROE manual-formula cross-check unavailable: {e}")

    def build(term, metric_key, concall_value, manual_key):
        crosscheck = manual[manual_key] * 100 if manual else None  # manual formula is a plain ratio, not %
        if entry is not None and entry[metric_key] is not None:
            value, source, label, source_file = (
                entry[metric_key], "fin_report disclosure",
                f"{term}(年) 稅後 @ {entry['period_label']}", entry["source_file"],
            )
        elif concall_value is not None:
            value, source, label, source_file = concall_value, "concall", term, None
        elif crosscheck is not None:
            value, source, label, source_file = crosscheck, "fin_report manual formula (approximated)", term, None
            crosscheck = None  # it IS the value here, not a separate cross-check of itself
        else:
            return None

        notes = []
        if crosscheck is not None and value:
            # Compare MAGNITUDES, and treat opposite signs as divergent
            # outright. The numerator used to be max(value, crosscheck) with
            # no abs(), so a loss quarter - a perfectly ordinary input, the
            # plausible range runs down to -5%/-50% - could produce a ratio of
            # 1.0 or even a negative one and silently pass: max(-3.0, 1.0) is
            # 1.0, max(-3.0, -1.0) is -1.0. Opposite signs mean one source
            # says profit and the other says loss, which is the largest
            # disagreement there is and was likewise never reported.
            magnitude_ratio = max(abs(value), abs(crosscheck)) / min(abs(value), abs(crosscheck) or 1e-9)
            if (value > 0) != (crosscheck > 0) or magnitude_ratio > _ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR:
                # Don't assert WHY they diverge - the manual formula's own
                # single-quarter-net-income x4 assumption is itself unverified
                # for a Q4/annual filing (where "this period's" net income
                # code may cover the full year, not just Q4 alone), so a gap
                # here isn't reliable evidence the disclosed figure is wrong.
                notes.append(f"cross-check diverges: manual formula gives {crosscheck:.2f}% vs {value:.2f}% "
                             f"as-disclosed - could be a real discrepancy, or just this manual formula's own "
                             f"assumptions not holding for this filing; treat as a prompt to look closer, not "
                             f"as evidence either number is wrong")

        # Plausibility bound - a SEPARATE signal from the cross-check
        # divergence above (that flags disagreement BETWEEN two
        # measurements of the same thing; this flags a value that's
        # implausible in absolute terms regardless of source or agreement).
        # Deliberately wide (see the bounds' own comment), so this should
        # almost never fire on real data - both notes are kept if both fire,
        # rather than one overwriting the other.
        lo, hi = (_ROA_PLAUSIBLE_MIN, _ROA_PLAUSIBLE_MAX) if term == "ROA" else (_ROE_PLAUSIBLE_MIN, _ROE_PLAUSIBLE_MAX)
        if value is not None and not (lo <= value <= hi):
            notes.append(f"implausible value: {value:.2f}% is outside the expected [{lo}%, {hi}%] range for "
                         f"{term} - likely a parsing/scale/sign error, worth a manual check")

        note = "; ".join(notes)
        return {"term": term, "value": value, "matched_label": label, "source_file": source_file,
                "crosscheck_value": crosscheck, "note": note}

    return {
        "roa": build("ROA", "roa_posttax", concall_roa, "roa"),
        "roe": build("ROE", "roe_posttax", concall_roe, "roe"),
    }




def collect_ratio_rows(folder, bank, coding, verbose=False):
    """Run the ROA/ROE extraction (reported 獲利能力 table, falling back to
    the manual formula). Returns (rows, used_fallback) where rows is a list
    of dicts with period/entity/quarter/roa_posttax/roa_posttax_annualized/
    roe_posttax/roe_posttax_annualized/profit_margin/source_file."""
    entries = find_profitability_entries(folder, verbose=verbose)
    if entries:
        rows = []
        for e in entries:
            rows.append({
                "period": e["period_label"], "entity": e["entity"] or "(this filing)",
                "quarter": e["quarter_num"],
                "roa_posttax": e["roa_posttax"], "roa_posttax_annualized": annualize(e["roa_posttax"], e["quarter_num"]),
                "roe_posttax": e["roe_posttax"], "roe_posttax_annualized": annualize(e["roe_posttax"], e["quarter_num"]),
                "profit_margin": e["profit_margin"], "source_file": e["source_file"],
            })
        return rows, False

    if verbose:
        print("No 獲利能力 disclosure table found; falling back to manual formula.")
    try:
        result = compute_ratios(folder, bank, coding, verbose=verbose)
    except RuntimeError as e:
        print(f"NOTE: manual ROA/ROE fallback also failed - {e}")
        return [], True
    row = {
        "period": None, "entity": "(approximated)", "quarter": result["quarter_num"],
        "roa_posttax": None, "roa_posttax_annualized": result["roa"],
        "roe_posttax": None, "roe_posttax_annualized": result["roe"],
        "profit_margin": None, "source_file": None,
    }
    return [row], True




RATIO_COLUMNS = ["roa_posttax", "roa_posttax_annualized", "roe_posttax", "roe_posttax_annualized", "profit_margin"]




def print_ratio_rows(rows, used_fallback):
    print("\n=== ratios ===")
    if used_fallback and not rows:
        return  # collect_ratio_rows already printed why both the reported table and the fallback failed
    if used_fallback:
        print("NOTE: no reported 獲利能力 table found in this folder - the figures below are "
              "an approximation computed from balance sheet / income statement codes, not a "
              "company-reported number.")
        r = rows[0]
        print(f"ROA(稅後年化, approximated)\t{r['roa_posttax_annualized']:.2%}")
        print(f"ROE(稅後年化, approximated)\t{r['roe_posttax_annualized']:.2%}")
        return
    print("Reported profitability (獲利能力), directly from filing - 稅後 (posttax) only:")
    for r in rows:
        metrics_str = " ".join(f"{c}={format_pct(r[c])}" for c in RATIO_COLUMNS)
        print(f"{r['period']}\t{r['entity']}\t{metrics_str}\t({page_num(r['source_file'])})")




def write_ratio_csv(folder, rows, used_fallback):
    out_path = Path(folder) / "profitability_export.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if used_fallback and not rows:
            writer.writerow(["error", "no reported 獲利能力 table and manual fallback also failed - see console output"])
        elif used_fallback:
            writer.writerow(["metric", "value_annualized_approximated"])
            r = rows[0]
            writer.writerow(["ROA(稅後年化)", f"{r['roa_posttax_annualized']:.2%}"])
            writer.writerow(["ROE(稅後年化)", f"{r['roe_posttax_annualized']:.2%}"])
        else:
            writer.writerow(["period", "entity", "quarter"] + RATIO_COLUMNS + ["page"])
            for r in rows:
                writer.writerow([r["period"], r["entity"], r["quarter"]] +
                                 [format_pct(r[c]) for c in RATIO_COLUMNS] + [page_num(r["source_file"])])
    return out_path
