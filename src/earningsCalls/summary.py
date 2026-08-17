"""The curated earnings-call summary: a fixed set of rows, every deck.

Top of the package - the counterpart to financialReports.summary, and the only
module here that knows about business concepts (loan recomposition, the NPL
rows sourced from the regulator) rather than about reading tables.

Also holds the `call` subcommand's CLI. Imports matching, never the reverse.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

from core.numbers import format_pct, format_maybe_pct
from core.text import page_num
from financialReports.entities import detect_bank
from financialReports.statements import pick_folder
from financialReports.ratios import derive_quarter_num
from earningsCalls.matching import (PRIMARY_BANK_ENTITIES, find_term_value,
                                    extract_term, detect_unit_scale)
from earningsCalls.terms import TermSpec, load_terms



# Repo root is THREE levels up from src/<package>/, not two. This module
# moved down a directory; the same expression silently pointed at src/
# instead. See core/industry.py, where exactly this bit once.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent




# ---------------------------------------------------------------------------
# Curated summary: a fixed, business-relevant subset of the 32-term
# dictionary, rather than every term (several of which - 總資產, 淨收益,
# 稅前/稅後淨利, ROA/ROE, etc. - overlap conceptually with statements.py's
# financial-report extraction and aren't wanted in con-call output). This is
# the default mode when no term is given, matching statements.py's
# curated `summary` design: both ratio-type and balance-type terms get only
# the latest-quarter, as-reported value. An earlier version of this code
# additionally scaled ratio terms by x4/quarter_num to produce an
# "annualized" figure, on the assumption that a con-call table's ratios are
# cumulative year-to-date flows the same way financial-statement income
# figures are. Verification against real decks (中信金 2Q25/1Q26, 國泰金
# 2Q25) disproved that assumption for every ratio term here: NIM, 存放比,
# 放款均率, 存款均率, and the computed CIR are all already-annualized
# rates/point-in-time ratios (confirmed by comparing the same deck's 1Q/1H/
# 9M/FY columns, which stay in the same magnitude rather than scaling with
# period length) - so x4/quarter_num scaling produced impossible values
# (e.g. a >100% loan-to-deposit ratio, a >100% cost-income ratio) that don't
# correspond to anything in the source. Removed; only the as-reported value
# is shown, same as balance-type terms.
#
# Each term is looked up ONCE per folder (first match found, scanning files
# in sorted order) rather than accumulating every occurrence across every
# file, so the summary doesn't repeat the same term for every page it
# happens to appear on.
# ---------------------------------------------------------------------------

RATIO_TERMS = ["NIM", "放款均率", "存款均率", "存放利差"]


# CIR moved to the fin_report summary (statements.SUMMARY_LAYOUT), computed
# directly as abs(營業費用)/淨收益 from the SAME filing, no crosscheck - the
# con-call deck's own 營業費用/營業收入 table turned out to be a different
# scope (bank-level operating figures at deck-reported scale, e.g. 中信's
# own page showed 4Q25 revenue/opex an order of magnitude different from
# the individual-basis fin_report figures) that doesn't reconcile with
# fin_report's individual-entity CIR, so a con-call-only CIR here was
# comparing two genuinely different quantities under the same name.
# 其他放款 was dropped as an OUTPUT row: under LOAN_RECOMPOSITION below its
# value is always folded into 個人放款 (北富銀's 其他 column, 中信's
# 信用貸款與其他), so keeping it as its own row would double-count. It stays
# in HELPER_TERMS because 中信's 個人放款 formula still reads it as an input.
BALANCE_TERMS = ["企業放款", "房貸", "個人放款", "信用卡循環",
                 "法說會放款餘額合計", "法說會外幣放款"]


# Looked up like balance terms (same matching, same require_absolute), but
# never shown - they exist only to feed LOAN_RECOMPOSITION's formulas.
HELPER_TERMS = ["其他放款", "政府放款", "信貸", "其他個人授信其他",
                "海外子行", "海外分行", "OBU_DBU", "個人擔保貸款", "小額信貸"]



# 逾放比率/備抵呆帳/逾期放款 - bank-WIDE ratios (all loans, not credit-card
# specific - confirmed against a real download; an earlier version of this
# wrongly sourced two similarly-named but different metrics off the
# credit-card sheet instead), read from the SAME FSC "本國銀行資產品質評估
# 分析統計表" sheet as 逾期放款總額, no con-call source at all. Already
# percent-scale numbers there - see disclosures.NPL_RATIO_HEADER/
# NPL_COVERAGE_HEADER - passed straight through as "ratio" kind rows.
NPL_RATIO_TERM = "逾放比率"


NPL_COVERAGE_TERM = "備抵呆帳/逾期放款"



# This project's short bank names -> the exact legal names the FSC regulator
# spreadsheets use in their own bank-name column (see disclosures.TARGET_BANKS).
#
# DELIBERATELY NOT extended to the six entities added to BANK_PROFILES from
# their 114Q4 filings. The key here has to be the string the FSC's own
# spreadsheet prints, which is not necessarily a filing's registered name and
# cannot be established without reading a real regulator file - and this repo
# must not reach banking.gov.tw to find out (see AGENTS.md). Guessing
# "兆豐國際商業銀行" and being one character off looks exactly like a bank the
# regulator didn't publish that month. So an unmapped entity is refused rather
# than guessed, and now says so (see gov_name_note) instead of producing
# unexplained N/A rows.
_GOV_BANK_NAMES = {
    "北富銀": "台北富邦商業銀行",
    "國泰": "國泰世華商業銀行",
    "玉山": "玉山商業銀行",
    "中信": "中國信託商業銀行",
}




def gov_name_note(bank):
    """Why the regulator-sourced rows are N/A for `bank`, or "" if they
    shouldn't be. Same reasoning as the fin_report side: an N/A row and a
    correctly-extracted one are indistinguishable once exported, and "this
    entity was never mapped to a regulator name" is a different problem from
    "the regulator dataset was unreachable"."""
    if not bank:
        return "no entity resolved for this deck, so no regulator lookup was attempted"
    if bank not in _GOV_BANK_NAMES:
        return (f"'{bank}' has no FSC regulator name mapped (see _GOV_BANK_NAMES) - "
                f"the mapping needs the exact string the regulator's own spreadsheet "
                f"prints, which has not been confirmed for this entity")
    return ""




def _add(*values):
    """Sum, propagating None: if any input is missing the whole formula is
    unresolvable, which must surface as N/A rather than a partial total
    silently computed from whichever components happened to match."""
    if any(v is None for v in values):
        return None
    return sum(values)




def _sub(a, b):
    return None if a is None or b is None else a - b




# ---------------------------------------------------------------------------
# Per-bank loan recomposition.
#
# Every deck breaks its loan book down differently, and none of them match
# the shape this project wants (企業/房貸/個人/信用卡循環 as four disjoint
# buckets that sum to the total). The raw matched values therefore need
# per-bank arithmetic before they mean the same thing across banks:
#
#   北富銀: 個人放款 as printed BUNDLES 房貸 + 信貸 + 其他 + 信用卡循環, so
#           房貸/信用卡循環 must be pulled back out, leaving 信貸 + 其他.
#           企業授信 excludes 政府 lending, which belongs in the corporate
#           bucket here. Verified: 1172.2 + 154.0 + 1209.0 + 11.5 = 2546.7,
#           matching the deck's own 授信餘額合計 2546.6 (rounding).
#   中信:   figures are group-wide and include 海外子行 (overseas
#           subsidiaries), which this project scopes out; 企業放款 as printed
#           is TWD-only so FX lending has to be added back. 信用貸款與其他
#           still contains 信用卡循環, which the deck never breaks out (hence
#           the regulator fallback below). Verified: 1699 + 1393 + 299.1 +
#           17.9 = 3409.0 = the deck's 總放款 4246 - 海外子行 837.
#   玉山:   房貸 as printed covers only 房屋貸款; 個人擔保貸款 (fully
#           collateralised personal lending, per the deck's own footnote) is
#           the same economic bucket here. 個人放款 as printed bundles all
#           three personal categories, so the unsecured piece is 小額信貸
#           alone. Total is the sum of the four recomposed buckets, which
#           excludes 海外子行 - consistent with 中信's treatment.
#   國泰:   left alone - its deck already publishes the four buckets disjoint
#           and they sum exactly to its stated 放款總額 (942.6 + 1404.9 +
#           477.7 + 22.2 = 2847.4). Only 信用卡循環 is filled from the
#           regulator dataset, since the deck reports 信用卡放款 instead.
#
# Each formula reads from the RAW (pre-recomposition) values dict, so
# formulas never see each other's output and can be written in any order.
#
# Deliberately NOT folded into statements.BANK_PROFILES with the other
# per-entity tables. Two reasons. It is logic, not data - lambdas nested in a
# dict literal, the one construct static import checking can't see (which is
# why tools/undefined.py walks dicts and why the L2 suite executes all nine),
# so moving it carries risk the other tables don't. And it degrades safely on
# its own: an entity with no entry here simply gets no recomposition, which is
# the correct default (國泰 already has none), whereas a missing composites or
# aliases entry produces a wrong or absent number. The profile completeness
# check therefore has nothing to enforce about it.
# ---------------------------------------------------------------------------
LOAN_RECOMPOSITION = {
    "北富銀": {
        "企業放款": (lambda v: _add(v["企業放款"], v["政府放款"]),
                     "企業授信 + 政府"),
        "個人放款": (lambda v: _add(v["信貸"], v["其他個人授信其他"]),
                     "信貸 + 其他（其他個人授信餘額表）"),
    },
    "中信": {
        "企業放款": (lambda v: _sub(_add(v["企業放款"], v["法說會外幣放款"]), v["海外子行"]),
                     "台幣法人放款 + 外幣放款 − 海外子行"),
        "個人放款": (lambda v: _sub(v["其他放款"], v["信用卡循環"]),
                     "信用貸款與其他 − 信用卡循環"),
        "法說會放款餘額合計": (lambda v: _sub(v["法說會放款餘額合計"], v["海外子行"]),
                                "總放款 − 海外子行"),
        # Built additively from the deck's own 外幣放款成長率-依分子行
        # breakdown rather than as (總外幣放款 − 海外子行): the subtractive
        # form gave 670 vs this form's 682 for Q4 2025, because the
        # breakdown's three rows don't sum exactly to the headline total.
        # The additive form is the one that matches the intended scope
        # (onshore + offshore branches, excluding overseas subsidiaries).
        "法說會外幣放款": (lambda v: _add(v["海外分行"], v["OBU_DBU"]),
                            "海外分行 + OBU+DBU"),
    },
    "玉山": {
        "房貸": (lambda v: _add(v["房貸"], v["個人擔保貸款"]),
                 "房屋貸款 + 個人擔保貸款"),
        "個人放款": (lambda v: v["小額信貸"], "小額信貸"),
        "法說會放款餘額合計": (lambda v: _add(v["企業放款"],
                                                _add(v["房貸"], v["個人擔保貸款"]),
                                                v["小額信貸"], v["信用卡循環"]),
                                "企業放款 + 房貸 + 小額信貸 + 信用卡循環"),
    },
    "國泰": {},
}



# The four recomposed buckets should sum to the recomposed total. Verified
# against real Q4 2025 decks for all 4 banks: 中信 and 玉山 reconcile to the
# cent, 北富銀 is off 0.1 and 國泰 0.03 purely from the decks' own one-decimal
# rounding. Anything beyond this tolerance means a component matched the
# wrong row or a formula's assumption stopped holding for a new filing -
# surfaced as a note rather than silently accepted (same principle as
# statements's 資產=負債+權益 check).
# Sized for the decks' own rounding, not for arithmetic error: 中信's
# standalone loan table prints whole 拾億元 integers, so 4 components plus a
# total carry up to ~±2.5 of accumulated rounding - and that deck's OWN rows
# already don't tie (its components sum to 4,044 against a printed 總放款 of
# 4,043). A tolerance below this fires on the source's rounding rather than
# on anything the extractor did.
_LOAN_RECONCILE_TOLERANCE = 2.5


_LOAN_COMPONENTS = ["企業放款", "房貸", "個人放款", "信用卡循環"]




_CN_QUARTER_ORDINAL = {"一": 1, "二": 2, "三": 3, "四": 4,
                       "1": 1, "2": 2, "3": 3, "4": 4}


# '第四季' / '第 4 季' - the ordinal may be a Chinese numeral or an Arabic
# digit, with or without surrounding spaces (real decks use both: 國泰 and
# 中信 write 第四季, 玉山 writes '第 4 季').
_QUARTER_TITLE_RE = re.compile(r"第\s*([一二三四1-4])\s*季")


# '2025年全年法人說明會' - a full-year deck (confirmed in a real 富邦 deck)
# is the Q4 deck; it reports the same period, just named differently.
_FULL_YEAR_TITLE_RE = re.compile(r"全年|FY\d{2,4}|Full[- ]?Year", re.IGNORECASE)




def detect_con_call_quarter(folder):
    """Derive the current quarter number (1-4) from the first .md file in
    `folder` (sorted - typically the cover page). Con-call decks state the
    quarter directly in the title (e.g. '2025年第四季法人說明會' = Q4 2025)
    using the Western calendar year, not the ROC-calendar convention
    statements.py's derive_quarter_num assumes for financial statements -
    so this looks for that ordinal pattern instead of reusing it. A deck
    titled '全年'/full-year is treated as Q4, since that is the period it
    covers. Falls back to derive_quarter_num (ROC-date parsing) if no such
    title is found, in case a deck instead states a plain ROC-style date."""
    paths = sorted(Path(folder).rglob("*.md"))
    if not paths:
        return None
    text = paths[0].read_text(encoding="utf-8", errors="ignore")
    m = _QUARTER_TITLE_RE.search(text)
    if m:
        return _CN_QUARTER_ORDINAL[m.group(1)]
    if _FULL_YEAR_TITLE_RE.search(text):
        return 4
    return derive_quarter_num(paths[0])




# '2025年第四季法人說明會' - the Western calendar year in a deck's own title,
# needed to pick the right month of the FSC regulator datasets (which are
# indexed by ROC year). Bounded to 20xx so a stray 4-digit figure elsewhere
# on the cover page can't be mistaken for it.
_WESTERN_YEAR_RE = re.compile(r"(20\d{2})\s*年")




def detect_con_call_year(folder):
    """Western calendar year of the deck's reporting period, from the first
    .md file's title text. None if not stated - callers then fall back to
    the regulator datasets' latest published month instead of guessing."""
    paths = sorted(Path(folder).rglob("*.md"))
    if not paths:
        return None
    m = _WESTERN_YEAR_RE.search(paths[0].read_text(encoding="utf-8", errors="ignore"))
    return int(m.group(1)) if m else None




def collect_con_call_summary(folder, terms, verbose=False, bank=None):
    """Build the curated con-call summary. Returns a list of row dicts with
    TWO different shapes - write_summary_csv branches on `kind`, so anything
    else reading these rows has to as well:
      - ratio terms: {term, kind: "ratio", individual, period_label,
                      matched_label, source_file, note}
        (the value lives in `individual`, NOT in `value` - there is no
        `value` key on a ratio row at all)
      - balance terms: {term, kind: "balance", value, period_label,
                        matched_label, source_file, is_percent, note}
    `note` is on BOTH shapes (ratio rows carry it too) and reaches the
    exported CSV, so it is part of this contract rather than an internal
    detail. It carries the loan-reconciliation warning (see
    _LOAN_RECONCILE_TOLERANCE) on the loan total, and on the
    regulator-sourced rows (逾放比率, 備抵呆帳/逾期放款, and 信用卡循環 when
    the deck itself disclosed nothing) the reason that lookup produced
    nothing - see gov_name_note.
    CIR is no longer produced here - see statements.SUMMARY_LAYOUT (moved to
    the fin_report summary, computed directly from the same filing's
    營業費用/淨收益 with no crosscheck against this deck's figures - see
    RATIO_TERMS' comment for why). `bank` scopes matching to that bank's own
    subsidiary table
    (see PRIMARY_BANK_ENTITIES); auto-detected from the deck when omitted.
    period_label and matched_label are carried through to the output so the
    period a figure came from, and the row/column label it was read off, are
    both checkable without re-running in verbose mode."""
    quarter_num = detect_con_call_quarter(folder)
    if bank is None:
        bank = detect_bank(folder)
    primary_aliases = PRIMARY_BANK_ENTITIES.get(bank) if bank else None
    if verbose:
        print(f"Detected quarter: {quarter_num}; bank: {bank} "
              f"(primary entity: {primary_aliases})")

    rows = []
    for name in RATIO_TERMS:
        found = find_term_value(folder, terms[name], verbose=verbose,
                                 prefer_quarterly=True, primary_aliases=primary_aliases)
        rows.append({
            "term": name, "kind": "ratio", "note": "",
            "individual": found[1] if found else None,
            "matched_label": found[0] if found else None,
            "period_label": found[3] if found else None,
            "source_file": found[2] if found else None,
        })

    # Look up the output balance terms AND the recomposition helper terms in
    # one pass, keeping each one's full match record - the per-bank formulas
    # below read raw values, but the surviving rows still need their original
    # matched_label/period/source for auditability.
    raw = {}
    for name in BALANCE_TERMS + HELPER_TERMS:
        found = find_term_value(folder, terms[name], verbose=verbose,
                                 primary_aliases=primary_aliases, require_absolute=True)
        # Normalise to 十億元 before anything downstream touches the number:
        # LOAN_RECOMPOSITION combines figures ACROSS tables, and decks do not
        # use one unit throughout (see detect_unit_scale) - a 百萬元 loan
        # table minus a 拾億元 subsidiary table is off by 1000x otherwise.
        scaled = None
        if found and found[1] is not None:
            scaled = found[1] * found[5]
        raw[name] = {
            "value": scaled,
            "matched_label": found[0] if found else None,
            "period_label": found[3] if found else None,
            "source_file": found[2] if found else None,
            # require_absolute=True already excludes percent cells for
            # balance terms, so this should always be False in practice -
            # kept as a safety net rather than assuming it can never happen.
            "is_percent": found[4] if found else False,
        }

    # 信用卡循環 fallback: several decks never publish it standalone (中信
    # folds it into 信用貸款與其他; 國泰 reports only an aggregate 信用卡放款),
    # yet 中信's 個人放款 formula subtracts it. The FSC regulator dataset
    # publishes it for every bank monthly, so fill from there when - and only
    # when - the deck itself didn't provide it, converting 千元 to the 十億元
    # the loan tables use. 逾放比率/備抵呆帳/逾期放款 come from a DIFFERENT
    # FSC sheet (fetch_overdue_loans - the bank-wide NPL disclosure, not the
    # credit-card one) and have no con-call source at all. Both fetched once
    # per run, whenever the bank is known (not only when 信用卡循環 is missing).
    cc = npl = None
    western_year = detect_con_call_year(folder)
    need_gov = raw["信用卡循環"]["value"] is None
    if bank:
        try:
            from regulatorDatasets import disclosures
            legal = _GOV_BANK_NAMES.get(bank)
            if legal:
                if western_year and quarter_num:
                    y, m = disclosures.roc_year(western_year), disclosures.quarter_end_month(quarter_num)
                else:
                    y = m = None
                cc = disclosures.fetch_credit_card_revolving(y, m, banks=[legal], verbose=verbose)
                npl = disclosures.fetch_overdue_loans(y, m, banks=[legal], verbose=verbose)
                if cc:
                    cc["values_billions"] = {b: disclosures.thousands_to_billions(v)
                                              for b, v in cc["values"].items()}
        except Exception as e:  # network/site/parse - never sink the whole run
            if verbose:
                print(f"Regulator dataset unavailable ({e}) - 信用卡循環 fallback and "
                      f"逾放比率/備抵呆帳/逾期放款 will be N/A.")
            cc = npl = None

    npl_ratio_value = npl_ratio_period = None
    coverage_value = coverage_period = None
    if cc:
        legal = _GOV_BANK_NAMES.get(bank)
        if need_gov and cc["values_billions"].get(legal) is not None:
            raw["信用卡循環"] = {
                "value": cc["values_billions"][legal],
                "matched_label": f"循環信用餘額（金管會{cc['period']}）",
                "period_label": cc["period"], "source_file": None, "is_percent": False,
            }
            if verbose:
                print(f"信用卡循環 not in deck - using regulator figure "
                      f"{raw['信用卡循環']['value']:.2f} 十億元 ({cc['period']})")
    if npl:
        legal = _GOV_BANK_NAMES.get(bank)
        # Already percent-scale numbers in the source (see
        # disclosures.NPL_RATIO_HEADER/NPL_COVERAGE_HEADER) - passed through
        # as-is, never rescaled, so they display via format_pct exactly as
        # the regulator published them.
        if npl.get("npl_ratios", {}).get(legal) is not None:
            npl_ratio_value = npl["npl_ratios"][legal]
            npl_ratio_period = npl["period"]
        if npl.get("coverage_ratios", {}).get(legal) is not None:
            coverage_value = npl["coverage_ratios"][legal]
            coverage_period = npl["period"]

    # Per-bank recomposition (see LOAN_RECOMPOSITION). Formulas read the RAW
    # values captured above, so none of them can see another's output.
    raw_values = {k: v["value"] for k, v in raw.items()}
    recomposed = {}
    for term, (formula, description) in LOAN_RECOMPOSITION.get(bank, {}).items():
        recomposed[term] = (formula(raw_values), description)

    cc_note = gov_name_note(bank) if raw["信用卡循環"]["value"] is None else ""
    for name in BALANCE_TERMS:
        row = dict(raw[name], term=name, kind="balance",
                   note=cc_note if name == "信用卡循環" else "")
        if name in recomposed:
            value, description = recomposed[name]
            row["value"] = value
            # Once recomposed, the document's own row label no longer
            # describes the reported figure - show the formula instead, so
            # the number stays auditable back to its inputs.
            row["matched_label"] = f"重組：{description}"
        rows.append(row)

    # Reconciliation check on the recomposed loan book (see
    # _LOAN_RECONCILE_TOLERANCE).
    by_term = {r["term"]: r for r in rows if r["kind"] == "balance"}
    total_row = by_term.get("法說會放款餘額合計")
    parts = [by_term[t]["value"] for t in _LOAN_COMPONENTS if t in by_term]
    if total_row and total_row["value"] is not None and all(p is not None for p in parts):
        diff = sum(parts) - total_row["value"]
        if abs(diff) > _LOAN_RECONCILE_TOLERANCE:
            total_row["note"] = (
                f"components sum to {sum(parts):,.1f} vs total {total_row['value']:,.1f} "
                f"(off by {diff:,.1f}) - a component may have matched the wrong row, or a "
                f"recomposition rule may not hold for this filing")

    # These two exist only if the regulator lookup ran. When it didn't, say
    # which reason - an unmapped entity is a different problem from a
    # dataset that was unreachable, and they need different fixes.
    gov_note = gov_name_note(bank)
    for term, value, period in ((NPL_RATIO_TERM, npl_ratio_value, npl_ratio_period),
                                 (NPL_COVERAGE_TERM, coverage_value, coverage_period)):
        rows.append({
            "term": term, "kind": "ratio", "individual": value,
            "matched_label": f"金管會公布（{period}）" if value is not None else None,
            "period_label": period, "source_file": None,
            "note": gov_note if value is None else "",
        })
    return rows




def print_summary_rows(rows):
    for r in rows:
        where = f"{r.get('matched_label') or '-'} @ {r.get('period_label') or '-'} ({page_num(r['source_file'])})"
        if r["kind"] == "ratio":
            print(f"{r['term']}\t{format_pct(r['individual'])}\t{where}")
        else:
            print(f"{r['term']}\t{format_maybe_pct(r['value'], r.get('is_percent', False))}\t{where}")
        # Notes carry the loan-book reconciliation warning and the regulator
        # dataset's "requested month not published, used an earlier one"
        # flag - both are the kind of thing that must not stay buried.
        if r.get("note"):
            print(f"  NOTE: {r['note']}")




def write_summary_csv(folder, rows):
    out_path = Path(folder) / "con_call_summary_export.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["term", "value", "label_in_doc", "period", "page", "note"])
        for r in rows:
            value = format_pct(r["individual"]) if r["kind"] == "ratio" else format_maybe_pct(r["value"], r.get("is_percent", False))
            writer.writerow([r["term"], value,
                              r.get("matched_label") or "", r.get("period_label") or "",
                              page_num(r["source_file"]), r.get("note") or ""])
    return out_path




def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # --folder (not a positional) since `term` is also optional - two optional
    # positionals in a row would be ambiguous (a single token would always be
    # consumed by the first one, folder, rather than falling through to term).
    ap.add_argument("--folder", default=None,
                     help="Folder containing earnings-call .md files. If omitted, a "
                          "folder-picker dialog opens.")
    ap.add_argument(
        "term", nargs="?", default=None,
        help="Term name(s) from the config, comma/space separated. 'summary' (or omitting this "
             "argument) runs the curated business-relevant subset instead of every dictionary term.",
    )
    ap.add_argument("--config", default=str(_REPO_ROOT / "data" / "con_call_terms.json"),
                     help="Path to the term config JSON (default: the bundled data/con_call_terms.json)")
    ap.add_argument("--export", choices=["csv"], help="Write results to a CSV file instead of stdout")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print per-file/per-term detail")
    args = ap.parse_args()

    if args.folder is None:
        args.folder = pick_folder()
        if args.folder is None:
            ap.error("No folder selected.")

    terms = load_terms(args.config)

    if args.term is None or args.term.strip() == "summary":
        rows = collect_con_call_summary(args.folder, terms, verbose=args.verbose)
        if args.export == "csv":
            out_path = write_summary_csv(args.folder, rows)
            print(f"Wrote {len(rows)} row(s) to {out_path}")
            return
        print_summary_rows(rows)
        return

    names = [t for t in re.split(r"[,\s]+", args.term.strip()) if t]
    all_results = []
    for name in names:
        if name not in terms:
            raise KeyError(f"Unknown term '{name}'. Known: {list(terms)}")
        all_results.extend(extract_term(args.folder, terms[name], verbose=args.verbose))

    if not all_results:
        print("No matching terms found in any file.")
        return

    if args.export == "csv":
        out_path = Path(args.folder) / "con_call_export.csv"
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["term", "label_in_doc", "value", "period_label", "page"])
            for r in all_results:
                writer.writerow([r["term"], r["label_in_doc"], format_maybe_pct(r["value"], r["is_percent"]),
                                  r["period_label"], page_num(r["source_file"])])
        print(f"Wrote {len(all_results)} row(s) to {out_path}")
        return

    for r in all_results:
        print(f"{r['term']}\t{r['label_in_doc']}\t{format_maybe_pct(r['value'], r['is_percent'])}\t@{r['period_label']}\t({page_num(r['source_file'])})")
