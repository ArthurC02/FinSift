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



# Repo root is THREE levels up from src/<package>/, NOT two - two levels
# silently points at src/. This has bitten four modules in this repo.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent




# ---------------------------------------------------------------------------
# Curated summary: a fixed, business-relevant subset of the term dictionary.
#
# Ratio terms are shown AS REPORTED, never scaled by x4/quarter_num. They are
# already-annualised rates or point-in-time ratios - scaling them produced
# impossible values (>100% loan-to-deposit, >100% cost-income).
#   → docs/knowledge/ratios.md#法說會的比率為什麼不年化
#
# Each term is looked up ONCE per folder (best match, not every occurrence).
# ---------------------------------------------------------------------------

RATIO_TERMS = ["NIM", "放款均率", "存款均率", "存放利差"]


# CIR is NOT here - it lives in the fin_report summary, computed from the same
# filing. → docs/knowledge/ratios.md#cir-為什麼從法說會搬到財報
#
# 其他放款 is deliberately not an OUTPUT row: LOAN_RECOMPOSITION always folds
# it into 個人放款, so its own row would double-count. It stays in
# HELPER_TERMS because 中信's 個人放款 formula reads it as an input.
BALANCE_TERMS = ["企業放款", "房貸", "個人放款", "信用卡循環",
                 "法說會放款餘額合計", "法說會外幣放款"]


# Looked up like balance terms (same matching, same require_absolute), but
# never shown - they exist only to feed LOAN_RECOMPOSITION's formulas.
HELPER_TERMS = ["其他放款", "政府放款", "信貸", "其他個人授信其他",
                "海外子行", "海外分行", "OBU_DBU", "個人擔保貸款", "小額信貸"]



# bank-WIDE ratios off the FSC 資產品質 sheet - NOT the credit-card sheet,
# which carries similarly-named but different metrics. Already percent-scale
# there, so they pass straight through, never rescaled.
#   → docs/knowledge/regulator-datasets.md#逾放比率曾經取錯表
NPL_RATIO_TERM = "逾放比率"


NPL_COVERAGE_TERM = "備抵呆帳/逾期放款"



# This project's short bank names -> the exact string the FSC's own
# spreadsheet prints in its bank-name column.
#
# DO NOT add an entry by guessing it from a filing's registered name. Being
# one character off looks exactly like a bank the regulator didn't publish
# that month, and confirming it means reading a real regulator file - which
# this repo must not fetch (AGENTS.md red line 2). Unmapped is REFUSED and
# says so via gov_name_note.
#   → docs/knowledge/entity-resolution.md#金管會名稱對照為什麼只有四家
_GOV_BANK_NAMES = {
    "北富銀": "台北富邦商業銀行",
    "國泰": "國泰世華商業銀行",
    "玉山": "玉山商業銀行",
    "中信": "中國信託商業銀行",
}




def gov_name_note(bank):
    """Why the regulator-sourced rows are N/A for `bank`, or "" if they
    shouldn't be. "Never mapped to a regulator name" is a different problem
    from "the dataset was unreachable", and they need different fixes.
      → docs/knowledge/na-and-refusal.md#na-的六種成因"""
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
# No deck publishes the shape this project wants (企業/房貸/個人/信用卡循環 as
# four disjoint buckets summing to the total), so raw matched values need
# per-bank arithmetic before they mean the same thing across banks. Every
# formula was reconciled against that deck's own stated total.
#   → docs/knowledge/earnings-call-matching.md#放款重組每家的公式
#
# Each formula reads the RAW (pre-recomposition) values dict, so formulas
# never see each other's output and may be written in any order.
#
# Deliberately NOT folded into BANK_PROFILES: it is LOGIC, not data (lambdas
# in a dict literal - the one construct static import checking can't see), and
# it degrades safely, unlike a missing composites or aliases entry.
#   → docs/knowledge/earnings-call-matching.md#為什麼不併進-bank_profiles
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
        # ADDITIVE, not (總外幣放款 − 海外子行): the two disagree (682 vs 670
        # for Q4 2025) because the breakdown's rows don't sum to the headline
        # total, and only the additive form matches the intended scope.
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



# Sized for the DECKS' OWN ROUNDING, not for arithmetic error: a deck printing
# whole 拾億元 integers carries up to ~±2.5 of accumulated rounding across 4
# components plus a total, and one deck's own rows already don't tie. Anything
# tighter fires on the source's rounding rather than on the extractor.
#   → docs/knowledge/earnings-call-matching.md#放款重組的容忍度是怎麼來的
_LOAN_RECONCILE_TOLERANCE = 2.5


_LOAN_COMPONENTS = ["企業放款", "房貸", "個人放款", "信用卡循環"]




_CN_QUARTER_ORDINAL = {"一": 1, "二": 2, "三": 3, "四": 4,
                       "1": 1, "2": 2, "3": 3, "4": 4}


# '第四季' / '第 4 季' - real decks use Chinese numerals AND Arabic digits,
# with or without spaces.
_QUARTER_TITLE_RE = re.compile(r"第\s*([一二三四1-4])\s*季")


# A full-year deck IS the Q4 deck - same period, different name.
_FULL_YEAR_TITLE_RE = re.compile(r"全年|FY\d{2,4}|Full[- ]?Year", re.IGNORECASE)




def detect_con_call_quarter(folder):
    """Derive the current quarter (1-4) from the first .md file in `folder`.

    Decks state the quarter in their title using the WESTERN calendar year,
    not the ROC convention derive_quarter_num assumes for filings - hence a
    separate pattern rather than reuse. Falls back to derive_quarter_num when
    no such title is found.
      → docs/knowledge/reading-tables.md#民國年西元年季度"""
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




# Bounded to 20xx so a stray 4-digit figure on the cover page can't be
# mistaken for the deck's reporting year.
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
    `note` is on BOTH shapes and reaches the exported CSV, so it is part of
    this contract, not an internal detail: it carries the loan-reconciliation
    warning and, on the regulator-sourced rows, the reason that lookup
    produced nothing (see gov_name_note).

    CIR is NOT produced here - it lives in the fin_report summary.
    `bank` scopes matching to that bank's own subsidiary table; auto-detected
    from the deck when omitted. period_label and matched_label reach the
    output so a figure stays checkable without re-running in verbose mode."""
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
        # Normalise to 十億元 BEFORE anything downstream touches the number -
        # LOAN_RECOMPOSITION combines figures across tables, and decks mix
        # units. → docs/knowledge/reading-tables.md#單位不是全篇一致
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

    # 信用卡循環 is filled from the regulator dataset when - and ONLY when -
    # the deck itself didn't provide it, converting 千元 to 十億元.
    # 逾放比率/備抵呆帳 come from a DIFFERENT FSC sheet and have no deck source
    # at all. → docs/knowledge/earnings-call-matching.md#信用卡循環的金管會退路
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
        # Already percent-scale in the source - passed through as-is, NEVER
        # rescaled, so they display exactly as the regulator published them.
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
    # --folder is a flag, not a positional: `term` is also optional, and two
    # optional positionals in a row are ambiguous.
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
