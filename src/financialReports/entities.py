"""What we know about each reporting entity, and how a filing is tied to one.

Everything keyed by an entity lives here - the profiles, the views derived
from them, and the detection that turns a folder into an entity name. Also
the two code-level fallback tables (SUMMARY_LABEL_FALLBACKS,
SUMMARY_CODE_DERIVATIONS), because they are declarations of the same kind:
what to do when a code is not where the scheme says it is.

Both extractors above sit on this and neither owns it - compute_ratios reads
SUMMARY_CODE_OVERRIDES and SUMMARY_LABEL_FALLBACKS, collect_summary_rows
reads all of it. That mutual dependency is why summary and ratios could not
simply be cut apart from each other; this module is the shared floor that
makes the split acyclic.

Deliberately knows NOTHING about summary layouts. The cross-check that every
entity defines the composites its layout needs lives in summary.py with the
layouts - see _validate_profiles.
"""
from pathlib import Path

from core.industry import detect_industry_category



# Fallback: when a SUMMARY_LAYOUT code isn't found under its own (possibly
# bank-overridden) number, some filings' conversions leave that ROW's own
# leading code cell blank (confirmed in a real 中信 individual filing - the
# subtotal/aggregate lines simply have no code at all), or the true code
# differs from what SUMMARY_CODE_OVERRIDES assumes (confirmed in a real 國泰
# individual filing - "本期淨利" sits at code 61000, not the FHC-consolidated-
# scope 63000/64000 the override was built around). Both cases are really
# the same underlying problem: the CODE is unreliable for these particular
# subtotal/aggregate lines specifically, but the LABEL text is not - these
# lines use one of a small, consistent set of phrasings across all 4 banks'
# filings observed so far. Keyed by the code's SUMMARY_LAYOUT/override slot
# (i.e. the value actually passed to find_code_value), not by whichever
# literal code happens to hold it in a given filing.
SUMMARY_LABEL_FALLBACKS = {
    "10000": ["資產總計", "資產合計"],
    # 淨收益合計 confirmed in a real 第一銀行 114Q4 individual filing - same
    # line, same position in the net-income walk, just worded with the
    # 合計 suffix. Exact whole-cell matching (see find_value_by_label) keeps
    # this from colliding with anything.
    "4xxxx": ["淨收益", "淨收益合計"],
    "49010": ["利息淨收益合計", "利息淨收益"],
    "58400": ["營業費用合計"],
    "61001": ["稅前淨利", "繼續營業單位稅前淨利"],
    "64000": ["本期稅後淨利", "本期淨利", "本年度淨利"],
    "63000": ["本期稅後淨利", "本期淨利", "本年度淨利"],
    "30000": ["權益總計", "權益合計"],
    # 20000 = 負債合計/負債總計 (total liabilities) - not read anywhere
    # currently (was only for the 資產=負債+權益 invariant-check row, since
    # removed from the summary output); kept here, unused, in case that
    # check is wanted again later.
    "20000": ["負債合計", "負債總計"],
}



# Last resort after both the code match and the label fallback fail: rebuild
# the line from its own components. Only for lines a filing can legitimately
# omit entirely - 兆豐 and 第一's real 114Q4 individual filings print 營業費用
# as a section HEADER with no amounts at all, then the three component rows,
# then go straight to 稅前淨利. There is no row to match, by code or by label.
#
# This is arithmetic, not a guess, and it was checked both ways before being
# added: on the four 114Q4 filings that DO print 58400 (台新/新光/永豐/華南)
# the sum of these three reproduces the printed total to the exact dollar,
# and on the two that don't it closes the filing's own
# 淨收益 - 呆帳 - 營業費用 = 稅前淨利 walk exactly. The derived row still says
# so in its note - a figure the filing itself never states must not be
# indistinguishable from one it does.
#
# ponytail: one entry, consulted only on a miss. If a filing ever turns up
# with a FOURTH opex component, this silently understates - which is why the
# note names the components it actually summed.
SUMMARY_CODE_DERIVATIONS = {
    "58400": ["58500", "59000", "59500"],
}



# ---------------------------------------------------------------------------
# Per-entity profiles.
#
# Everything keyed by a reporting entity lives here. It used to live in five
# separate tables (BANKS, BANK_NAME_ALIASES, SUMMARY_CODE_OVERRIDES,
# SUMMARY_CODE_OVERRIDES_FINSUM, COMPOSITE_TERMS) plus
# decks.PRIMARY_BANK_ENTITIES - six edits to add one entity, with nothing
# checking they all happened. Every one of those names still exists, derived
# below; only their source moved. _validate_profiles() then makes an
# incomplete entity an import-time error instead of a silent N/A at run time.
#
# FIELDS
#
# industries - which coding schemes this entity's filings are written under.
#   This is what makes the entity axis safe to open up. Aliases have to be
#   short forms (an earnings-call deck's cover says "玉山金控", never a
#   registered name), and short forms collide inside a group: "國泰人壽保險股
#   份有限公司" contains "國泰", so an insurer resolved to its sibling BANK
#   and inherited that bank's overrides and composites. Scoping detection to
#   the filing's own industry removes that by construction instead of hoping
#   the aliases stay distinct. Both bank and FHC schemes are listed for every
#   entity here because both document types are in scope for these groups -
#   the override tables were built around FHC-consolidated scope in the first
#   place (see code_overrides_finsum).
#
# aliases - alternate/full names, used both to normalize a --bank value typed
#   as the full name (e.g. "台北富邦銀行" for 北富銀) and to auto-detect the
#   entity from a filing's own text when --bank isn't given. "臺"/"台" are
#   both included since either can appear in a filing.
#
# primary_entities - the PRIMARY bank subsidiary each earnings-call deck is
#   about. A financial-holding deck also reports OTHER bank subsidiaries
#   (富邦華一銀行 in RMB, 富邦銀行(香港) in HKD, ...) whose tables use
#   identical row labels (存放比, 總放款, 營業費用) - decks's
#   _BANK_LABEL_HINT alone can't tell them apart, since every one of them
#   contains "銀行". Matching a figure from the wrong subsidiary is worse than
#   reporting nothing: in a real 富邦 deck it produced 存放比 72.17% and
#   放款餘額 81,769 from the mainland-China subsidiary's RMB-denominated table
#   instead of 台北富邦銀行's own figures. English/romanized forms are
#   included because these decks label appendix tables in English ("E.SUN
#   Bank's Income Statement"); without them such a heading matches
#   _ENTITY_NAME_RE ("Bank") but not the primary alias, and the bank's own
#   appendix would be rejected as if it were another company's.
#
# code_overrides - per-entity overrides for individual SUMMARY_LAYOUT code
#   entries (code -> replacement).
#
# code_overrides_finsum - same idea for the quarterly SUMMARIZED fin-report
#   disclosure (see collect_summary_rows_finsum), a DIFFERENT and shorter
#   document than the full individual filing, confirmed to use a different
#   code scheme for 稅後淨利 in real 114Q4 filings: 中信 prints no code at all
#   for that row (label-only - already covered by
#   SUMMARY_LABEL_FALLBACKS["64000"], no override needed), 北富銀 prints 64000
#   directly (no override needed either), but 國泰 prints 61000 - NOT 63000
#   like the full individual filing needs. Kept as its own field rather than
#   reusing code_overrides since the two document types' schemes genuinely
#   differ for this entity.
#
# composites - this entity's code list for each composite SUMMARY_LAYOUT item.
#   其他非利息收益 was dropped from the default summary output for a while
#   (per an earlier standardized-term-list request) and has been re-added;
#   手續費淨收益 (code 49100, added alongside it) is a plain single-code
#   SUMMARY_LAYOUT item, not a composite, so it has no entry here.
# ---------------------------------------------------------------------------

BANK_PROFILES = {
    "國泰": {
        "industries": ["金融業", "金控業"],
        "aliases": ["國泰"],
        "primary_entities": ["國泰世華", "Cathay United"],
        "code_overrides": {"64000": "63000"},
        "code_overrides_finsum": {"64000": "61000"},
        "composites": {
            "評價及已實現": ["49200", "49310", "49450", "49600"],
            "其他非利息收益": ["49700", "49750", "49800"],
        },
    },
    "中信": {
        "industries": ["金融業", "金控業"],
        "aliases": ["中信", "中國信託"],
        "primary_entities": ["中信銀", "中國信託商業銀行", "中國信託銀行", "CTBC Bank"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "49310", "49450", "49600"],
            "其他非利息收益": ["49700", "49750", "49800", "49815", "49899"],
        },
    },
    "北富銀": {
        "industries": ["金融業", "金控業"],
        # "富邦" alone is included so a deck/filing that only ever names the
        # FHC parent ("富邦金控", as the 4Q25 analyst-meeting cover page does)
        # still resolves to this entity; no other profile contains "富邦", so
        # it stays unambiguous.
        "aliases": ["北富銀", "台北富邦銀行", "臺北富邦銀行", "台北富邦", "臺北富邦", "富邦"],
        "primary_entities": ["台北富邦", "臺北富邦", "Taipei Fubon"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "49310", "49450", "49600"],
            "其他非利息收益": ["49700", "49750", "49800"],
        },
    },
    "玉山": {
        "industries": ["金融業", "金控業"],
        "aliases": ["玉山"],
        "primary_entities": ["玉山銀", "E.SUN", "ESUN"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "49310", "49600"],
            "其他非利息收益": ["49700", "49750", "49899"],
        },
    },
    # The six below were added from real 114Q4 individual filings. Their
    # composites are read off each filing's own 個體綜合損益表 rather than
    # copied from a sibling - the component codes genuinely differ (兆豐 and
    # 第一 print 43100 where everyone else prints 49310; 第一 alone uses 43600
    # and 45000; 華南 uses 47003 for the equity-method line; 新光 has no
    # 除列按攤銷後成本 line at all).
    # primary_entities here are NOT verified against a real earnings-call
    # deck - no deck for these six has been through this tool yet. They only
    # affect con-call extraction; check them against a real deck before
    # trusting con-call output for these entities.
    "兆豐": {
        "industries": ["金融業", "金控業"],
        "aliases": ["兆豐"],
        "primary_entities": ["兆豐銀", "Mega International", "Mega Bank"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "43100", "49450", "49600"],
            "其他非利息收益": ["49700", "49750", "49800"],
        },
    },
    "台新": {
        "industries": ["金融業", "金控業"],
        "aliases": ["台新", "臺新"],
        "primary_entities": ["台新銀", "臺新銀", "Taishin"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "49310", "49450", "49600"],
            "其他非利息收益": ["49700", "49750", "49800"],
        },
    },
    "新光": {
        "industries": ["金融業", "金控業"],
        "aliases": ["新光"],
        "primary_entities": ["新光銀", "Shin Kong Bank"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            # No 除列按攤銷後成本衡量之金融資產損益 line in this filing at all.
            "評價及已實現": ["49200", "49310", "49600"],
            "其他非利息收益": ["49700", "49815", "49899"],
        },
    },
    "永豐": {
        "industries": ["金融業", "金控業"],
        "aliases": ["永豐"],
        "primary_entities": ["永豐銀", "Bank SinoPac"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "49310", "49450", "49600"],
            "其他非利息收益": ["49700", "49750", "49800"],
        },
    },
    # "第一" alone would be a substring of ordinary text (第一階段, 第一季) in
    # every other bank's filing, making this entity a candidate everywhere and
    # turning detect_bank ambiguous for all of them - hence only the longer
    # forms.
    "第一": {
        "industries": ["金融業", "金控業"],
        "aliases": ["第一商業銀行", "第一銀行", "第一金"],
        "primary_entities": ["第一銀", "First Commercial Bank"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "43100", "43600", "49600"],
            "其他非利息收益": ["45000", "49750", "49800"],
        },
    },
    "華南": {
        "industries": ["金融業", "金控業"],
        "aliases": ["華南"],
        "primary_entities": ["華南銀", "Hua Nan"],
        "code_overrides": {},
        "code_overrides_finsum": {},
        "composites": {
            "評價及已實現": ["49200", "49310", "49450", "49600"],
            "其他非利息收益": ["49700", "47003", "49899"],
        },
    },
}



_PROFILE_FIELDS = {"industries", "aliases", "primary_entities",
                   "code_overrides", "code_overrides_finsum", "composites"}



# Derived views. These are the names the rest of the codebase (and the tests)
# already use; BANK_PROFILES is simply where they now come from.
BANKS = list(BANK_PROFILES)


BANK_NAME_ALIASES = {name: p["aliases"] for name, p in BANK_PROFILES.items()}


SUMMARY_CODE_OVERRIDES = {name: p["code_overrides"] for name, p in BANK_PROFILES.items()}


SUMMARY_CODE_OVERRIDES_FINSUM = {name: p["code_overrides_finsum"] for name, p in BANK_PROFILES.items()}




def _invert_composites(profiles):
    """term -> {entity: codes}, the shape collect_summary_rows reads."""
    inverted = {}
    for name, profile in profiles.items():
        for term, codes in profile["composites"].items():
            inverted.setdefault(term, {})[name] = codes
    return inverted




COMPOSITE_TERMS = _invert_composites(BANK_PROFILES)




def resolve_bank_name(name):
    """Normalize a --bank value to its canonical BANKS entry, accepting
    either the short form or any alias in BANK_NAME_ALIASES. Raises
    ValueError with the accepted names if nothing matches."""
    for canonical, aliases in BANK_NAME_ALIASES.items():
        if name == canonical or name in aliases:
            return canonical
    accepted = ", ".join(f"{b} ({'/'.join(BANK_NAME_ALIASES[b])})" for b in BANKS)
    raise ValueError(f"Unrecognized bank '{name}'. Accepted: {accepted}")




def bank_candidates(folder, industry=None):
    """Every entity in BANK_PROFILES whose alias appears in the first few .md
    files of `folder` (sorted by name - typically the cover/first pages), in
    BANKS order. Empty list if no file exists or nothing matches.

    Restricted to entities whose `industries` include the filing's own, so a
    group's insurer can't be resolved as its sibling bank purely because the
    short alias is a substring of both. `industry` may be passed by a caller
    that already resolved it; None means detect it. When the industry can't
    be established at all, every entity stays a candidate - an earnings-call
    deck carries no registered name and so never resolves an industry, and
    narrowing there would break con-call detection outright.

    Reads the same first-5 window detect_industry_category does. Reading only
    paths[0] meant a filing whose cover page didn't carry the bank's name -
    the exact case detect_industry_category was widened to 5 files for -
    detected its industry fine but failed on the bank, and cli then
    skipped the whole folder."""
    paths = sorted(Path(folder).rglob("*.md"))[:5]
    if not paths:
        return []
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in paths)
    if industry is None:
        industry = detect_industry_category(folder)
    return [name for name, profile in BANK_PROFILES.items()
            if (industry is None or industry in profile["industries"])
            and any(alias in text for alias in profile["aliases"])]




def detect_bank(folder):
    """The single bank `folder` belongs to, or None when that can't be
    established - either because nothing matched, or because MORE THAN ONE
    bank did.

    This used to return the first match in BANK_NAME_ALIASES order, which is
    only safe while no filing can name two of them. That assumption does not
    survive the full set of Taiwanese banks: the aliases are short forms
    (they have to be - an earnings-call deck's cover says '玉山金控', never
    the legal name, so detect_industry_category's full-legal-name approach
    can't be borrowed here), many are substrings of each other, and a filing
    naming a peer in a related-party or interbank note is ordinary rather
    than exceptional. Under those conditions first-match-wins doesn't
    degrade to N/A - it silently picks the wrong bank, applies that bank's
    COMPOSITE_TERMS and SUMMARY_CODE_OVERRIDES, and produces a full set of
    plausible-looking wrong numbers.

    Ambiguity is therefore refused rather than guessed. Callers already have
    a path for "couldn't detect" (statements asks for --bank, cli skips
    the folder and says so), so this needs no new control flow - only a
    message that distinguishes the two cases, which is what bank_candidates
    is exposed for."""
    matched = bank_candidates(folder)
    return matched[0] if len(matched) == 1 else None




def bank_detection_message(folder):
    """The --bank guidance to show when detect_bank returned None. The two
    reasons need different actions from whoever reads it: nothing matched
    means this entity may not be supported at all, while several matched
    means it IS supported and only needs disambiguating - so they must not
    collapse into one message."""
    found = bank_candidates(folder)
    if found:
        return ("Several banks are named in this filing's first pages (" + ", ".join(found)
                + ") - pass --bank explicitly to say which one is the reporting entity")
    # A filing whose industry IS resolvable but which no profile covers is a
    # third case: --bank can't help, because every accepted value would then
    # be refused again by the layout guard. Saying "couldn't auto-detect"
    # here sends the reader down a dead end.
    industry = detect_industry_category(folder)
    supported = {i for p in BANK_PROFILES.values() for i in p["industries"]}
    if industry and industry not in supported:
        return (f"This filing reads as {industry}, and no supported entity files under that scheme "
                f"yet - the entities in BANK_PROFILES cover {', '.join(sorted(supported))}")
    return ("Couldn't auto-detect the bank from the filing's text - pass --bank "
            "explicitly (choices: " + ", ".join(BANKS) + ")")
