"""Earnings-call decks (法說會簡報), matched by TERM TEXT.

Decks carry no account codes, so everything here is text matching against
data/con_call_terms.json - which is why this package has machinery the
code-matched side never needs: entity tiering, unit normalisation, and
period-axis detection.

Layered bottom-up: terms (the vocabulary) and periods (which axis is which
period) depend on nothing else here; matching sits on both; summary sits on
matching. The package itself is the facade - callers use
`import earningsCalls as ec` and need not know which file a name lives in.
"""
from earningsCalls.terms import Component, TermSpec, load_terms, match_strength
from earningsCalls.periods import (parse_period_label, detect_orientation,
                                   _normalize_year, _rank_periods)
from earningsCalls.matching import (PRIMARY_BANK_ENTITIES, entity_tier, collect_headings,
                                    nearest_heading, detect_unit_scale, find_value_in_table,
                                    find_term_value, extract_term, _row_sections)
from earningsCalls.summary import (RATIO_TERMS, BALANCE_TERMS, HELPER_TERMS,
                                   NPL_RATIO_TERM, NPL_COVERAGE_TERM, LOAN_RECOMPOSITION,
                                   _GOV_BANK_NAMES, gov_name_note, _add, _sub,
                                   detect_con_call_quarter, detect_con_call_year,
                                   collect_con_call_summary, print_summary_rows,
                                   write_summary_csv, main)
