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



# Keyed by the SUMMARY_LAYOUT/override code slot (what actually reaches
# find_code_value), NOT by whichever literal code a given filing uses. Match is
# whole-cell exact, never substring.
# 為什麼代碼對小計列不可靠 → docs/knowledge/account-codes.md#一標籤比對summary_label_fallbacks
SUMMARY_LABEL_FALLBACKS = {
    "10000": ["資產總計", "資產合計"],
    "4xxxx": ["淨收益", "淨收益合計"],
    "49010": ["利息淨收益合計", "利息淨收益"],
    "58400": ["營業費用合計"],
    "61001": ["稅前淨利", "繼續營業單位稅前淨利"],
    "64000": ["本期稅後淨利", "本期淨利", "本年度淨利"],
    "63000": ["本期稅後淨利", "本期淨利", "本年度淨利"],
    "30000": ["權益總計", "權益合計"],
    "20000": ["負債合計", "負債總計"],   # unused; kept for the 資產=負債+權益 check
}



# Last resort after code AND label both fail. Arithmetic, not a guess -
# verified both ways before being added. The derived row MUST say so in its
# note: a figure the filing never states must not look like one it does.
# ponytail: a filing with a FOURTH opex component would silently understate.
# 驗證方式與實測數字 → docs/knowledge/account-codes.md#二由組成項重建summary_code_derivations
SUMMARY_CODE_DERIVATIONS = {
    "58400": ["58500", "59000", "59500"],
}



# ---------------------------------------------------------------------------
# Per-entity profiles. Every older table (BANKS, BANK_NAME_ALIASES, the two
# override tables, COMPOSITE_TERMS, earningsCalls' PRIMARY_BANK_ENTITIES) is
# derived from this one; _validate_profiles makes an incomplete entity an
# import-time error.
#
# 不要從別家複製 composites - 組成代碼真的每家不同。抄來的是看起來正常的錯數字。
# 別名不能是日常用語（「第一」會命中每份財報裡的「第一季」，使 detect_bank
# 對所有資料夾都變成歧義而整批靜默跳過）。
#
# 六個欄位各自怎麼填、每機構的組成代碼對照表
#   → docs/knowledge/entity-resolution.md#profile-的六個欄位
#   → docs/knowledge/account-codes.md#每機構的-composite-組成
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
        # 單獨的 "富邦" 是逐案確認過不與其他 profile 相撞才加的，不是通則。
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
    # 以下六家的 primary_entities 沒有對過任何一份真實法說會簡報。它們只影響
    # 法說會擷取 - 信任這六家的法說會輸出前，先拿真實簡報驗一次。
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
            # 這份財報整份沒有「除列按攤銷後成本」這一列。
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
    # 只收長名。單獨的「第一」是每份財報都有的日常用語（第一季、第一階段）。
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
    """Normalize a --bank value to its canonical BANKS entry, accepting either
    the short form or any alias in BANK_NAME_ALIASES. Raises ValueError with
    the accepted names if nothing matches.
      → docs/knowledge/entity-resolution.md#aliases"""
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
    short alias is a substring of both.
      → docs/knowledge/entity-resolution.md#industries
    `industry` may be passed by a caller
    that already resolved it; None means detect it. When the industry can't
    be established at all, EVERY entity stays a candidate - a deck carries no
    registered name, and narrowing there breaks con-call detection outright.

    Reads the same first-5 window detect_industry_category does; the cover
    page does not always carry the bank's name.
      → docs/knowledge/industry-and-layout.md#產業怎麼判定"""
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

    **Ambiguity is refused, never resolved by order.** Do not restore a
    first-match-wins rule: the aliases are short forms that are substrings of
    each other, and a filing naming a peer in a related-party note is
    ordinary - so picking the first would silently apply the WRONG bank's
    COMPOSITE_TERMS and SUMMARY_CODE_OVERRIDES and emit a full set of
    plausible wrong numbers.
      → docs/knowledge/entity-resolution.md#為什麼歧義要拒絕而不是取第一個"""
    matched = bank_candidates(folder)
    return matched[0] if len(matched) == 1 else None




def bank_detection_message(folder):
    """The --bank guidance to show when detect_bank returned None.

    THREE cases, deliberately not collapsed: nothing matched, several matched
    (fixable with --bank), and an industry no profile covers at all (--bank
    cannot help - every accepted value is refused again by the layout guard,
    so "couldn't auto-detect" would send the reader down a dead end)."""
    found = bank_candidates(folder)
    if found:
        return ("Several banks are named in this filing's first pages (" + ", ".join(found)
                + ") - pass --bank explicitly to say which one is the reporting entity")
    industry = detect_industry_category(folder)
    supported = {i for p in BANK_PROFILES.values() for i in p["industries"]}
    if industry and industry not in supported:
        return (f"This filing reads as {industry}, and no supported entity files under that scheme "
                f"yet - the entities in BANK_PROFILES cover {', '.join(sorted(supported))}")
    return ("Couldn't auto-detect the bank from the filing's text - pass --bank "
            "explicitly (choices: " + ", ".join(BANKS) + ")")
