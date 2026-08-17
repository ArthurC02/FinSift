"""CLI-boundary tests (TEST_DESIGN §6.1).

argparse errors exit 2, so SystemExit's code is the assertion.
"""
import sys

import pytest

from financialReports import acctfinder as af


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["acctfinder.py", *argv])
    with pytest.raises(SystemExit) as exc:
        af.main()
    return exc.value.code


@pytest.mark.parametrize("period", ["0", "-1"])
def test_E3_period_below_1_is_rejected(monkeypatch, capsys, tmp_path, period):
    """FIXED (was PINNED BUG #2, case E3): `--period 0` used to exit 0 and
    print the OLDEST period's numbers as if they were what was asked for.
    Guarding nth_value alone would only have turned that into a silent page of
    N/A, so the CLI rejects it here instead of letting it through."""
    assert run_cli(monkeypatch, str(tmp_path), "balance_sheet", "--period", period) == 2
    assert "--period must be 1 or greater" in capsys.readouterr().err


def test_E1_equity_statement_gets_the_explanation_not_a_traceback(monkeypatch, capsys, tmp_path):
    """FIXED (was PINNED BUG #17). 'equity_statement' reached
    load_code_dictionary and surfaced as a bare ValueError traceback, despite
    the docstring promising a clear error. It stays in argparse's `choices` so
    --help still lists it and the user gets this explanation rather than
    'invalid choice'."""
    assert run_cli(monkeypatch, str(tmp_path), "equity_statement") == 2
    err = capsys.readouterr().err
    assert "權益變動表" in err and "not supported" in err
    assert "Traceback" not in err


def test_supported_statements_are_unaffected(monkeypatch, tmp_path):
    # The guard must reject only equity_statement. A supported statement with
    # an explicit coding file runs to completion on an empty folder.
    monkeypatch.setattr(sys, "argv", ["acctfinder.py", str(tmp_path), "balance_sheet",
                                      "--coding", af.INDUSTRY_CODING_FILES["金融業"]])
    af.main()
