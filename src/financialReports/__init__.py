"""Statutory financial filings, matched by ACCOUNT CODE.

The one axis where a row is identified by an exact code (10000 = 資產總計)
rather than by its wording. Layered statements -> summary -> ratios ->
entities; see AGENTS.md for why that order is one-way.
"""

# The package IS the facade. These four modules are one subject split for
# readability, not four independent APIs, so callers reach them through
# `financialReports.X` and never need to know which file a name lives in.
# That also means statements.py is only statements - the re-export block used
# to sit there, back when this file was called acctfinder.py and was both the
# entry point and half the implementation.
#
# Import order matters and is not alphabetical: entities has no intra-package
# dependencies, ratios needs entities, summary needs both. See AGENTS.md.
from financialReports.entities import (BANK_PROFILES, BANKS, BANK_NAME_ALIASES, COMPOSITE_TERMS,
                      SUMMARY_CODE_OVERRIDES, SUMMARY_CODE_OVERRIDES_FINSUM,
                      SUMMARY_LABEL_FALLBACKS, SUMMARY_CODE_DERIVATIONS,
                      _PROFILE_FIELDS, _invert_composites, resolve_bank_name,
                      bank_candidates, detect_bank, bank_detection_message)
from financialReports.profitability import (find_profitability_entries, find_profitability_files,
                    extract_metrics, extract_single_entity_profitability_tables,
                    extract_transposed_entity_tables, group_rows_by_entity,
                    classify_metric_row, is_metric_column_layout, parse_single_date,
                    parse_period_header_date, quarter_num_from_period_label,
                    derive_quarter_num)
from financialReports.ratios import (collect_roa_roe, collect_ratio_rows, compute_ratios,
                    _select_profitability_entry,
                    print_ratio_rows, write_ratio_csv, RATIO_COLUMNS,
                    _ROA_PLAUSIBLE_MIN, _ROA_PLAUSIBLE_MAX,
                    _ROE_PLAUSIBLE_MIN, _ROE_PLAUSIBLE_MAX,
                    _ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR)
from core.industry import INDUSTRY_CODING_FILES, detect_industry_category
from financialReports.statements import (STATEMENTS, UNSUPPORTED_STATEMENT_MSG,
                     load_code_dictionary, resolve_coding_path, extract_statement,
                     collect_statement_rows, print_statement_rows, write_statement_csv,
                     write_combined_csv, STATEMENT_COLUMNS, pick_folder, main)
from financialReports.summary import (SUMMARY_LAYOUT, INDUSTRY_SUMMARY_LAYOUTS, summary_layout_error,
                     apply_cost_sign, _validate_profiles, collect_summary_rows,
                     collect_summary_rows_finsum, summary_coverage_warning,
                     print_summary_rows, write_summary_csv, _SUMMARY_NA_WARN_RATIO)
