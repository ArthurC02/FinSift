"""L0 characterization tests - TEST_DESIGN §3.10, match_strength (6 cases).

The four strength levels are the input to the whole §5.1 decision table, so
they get pinned on their own rather than only through their callers.
"""
import json
from pathlib import Path

import pytest

from financialReports import acctfinder
from earningsCalls import callfinder
from earningsCalls.callfinder import Component, TermSpec, match_strength


def exact(aliases, negative=None):
    return TermSpec(name="t", aliases=aliases, negative_terms=negative or [])


MATCH_STRENGTH_CASES = [
    ("MS1", exact(["淨收益"]), "淨收益", 3),                 # whole cell equals an alias
    ("MS2", exact(["淨收益"]), "手續費淨收益合計", 2),        # substring only
    ("MS4", exact(["稅後淨利"], ["稅前"]), "稅前淨利", 0),    # negative vetoes even an exact hit
    ("MS5", exact(["淨收益"]), "營業費用", 0),
]


@pytest.mark.parametrize("spec,text,expected", [c[1:] for c in MATCH_STRENGTH_CASES],
                         ids=[c[0] for c in MATCH_STRENGTH_CASES])
def test_match_strength(spec, text, expected):
    assert match_strength(spec, text) == expected


def test_MS3_composite_clears_threshold():
    spec = TermSpec(
        name="t", type="composite", threshold=0.8,
        components=[Component(terms=["淨收益"], weight=0.5),
                    Component(terms=["合計"], weight=0.5)],
    )
    assert match_strength(spec, "淨收益合計") == 1
    assert match_strength(spec, "淨收益") == 0  # 0.5 < 0.8, one component isn't enough


def test_MS6_empty_alias_still_matches_everything_if_one_is_built_by_hand():
    """match_strength itself is unchanged: an empty alias is a substring of
    every string, so such a spec matches EVERY row at strength 2, outranking
    any composite. #23 was fixed at the trust boundary instead - load_terms now
    refuses to build one from a config file (see the tests at the bottom of
    this module). Pinned so the two halves stay distinguishable."""
    spec = exact([""])
    assert match_strength(spec, "營業費用") == 2
    assert match_strength(spec, "   ") == 3   # strips to "", so it reads as exact


# --------------------------------------------------------------------------
# TEST_DESIGN §7 #23 / §6.1 E7 - load_terms schema validation
# --------------------------------------------------------------------------

def write_config(tmp_path, spec):
    path = tmp_path / "terms.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_bundled_configs_still_load():
    # The validation is worthless if it rejects the real thing.
    #
    # Anchored on the repo root via this test file, not by walking up from a
    # module's __file__ - that walk is exactly what broke when the modules
    # moved down into packages, and it would break again on the next move
    # while still looking correct.
    bundled = Path(__file__).resolve().parent.parent / "data" / "con_call_terms.json"
    assert bundled.is_file(), bundled
    assert len(callfinder.load_terms(bundled)) > 1


def test_every_bundled_data_path_a_module_computes_actually_resolves():
    """The __file__-relative walks that survive in src/. Three of them were one
    level short after the package move, and only this one had a test. A path
    that resolves to nowhere fails at the first open, not in parsing, so it is
    worth pinning all of them in one place."""
    from earningsCalls import callfinder as cf
    from userInteractions import runfinder as rf
    assert Path(rf._DEFAULT_CONFIG).is_file(), rf._DEFAULT_CONFIG
    assert (cf._REPO_ROOT / "data" / "con_call_terms.json").is_file()
    for industry, path in acctfinder.INDUSTRY_CODING_FILES.items():
        assert Path(path).is_file(), f"{industry}: {path}"


def test_E7_a_malformed_component_names_the_file_and_the_term(tmp_path):
    """FIXED (was PINNED BUG #23). `Component(**c)` raised a bare TypeError
    naming neither the file, the term nor the missing field - nothing to search
    a 40-term config with."""
    path = write_config(tmp_path, {"壞設定": {"type": "composite",
                                               "components": [{"terms": ["x"]}]}})
    with pytest.raises(ValueError) as exc:
        callfinder.load_terms(path)
    message = str(exc.value)
    assert "壞設定" in message and str(path) in message and "weight" in message


def test_an_unexpected_component_field_is_reported_too(tmp_path):
    path = write_config(tmp_path, {"t": {"type": "composite",
                                          "components": [{"terms": ["x"], "weight": 1, "wieght": 2}]}})
    with pytest.raises(ValueError, match="wieght"):
        callfinder.load_terms(path)


def test_a_blank_alias_is_rejected_at_load_time(tmp_path):
    """The other half of #23. An empty alias is a substring of every string, so
    one blank entry made its term match EVERY row at strength 2 - outranking
    any composite - and nothing anywhere said so. MS6 above pins what
    match_strength still does with such a spec if one is built by hand; this
    stops one arriving from a config file."""
    path = write_config(tmp_path, {"t": {"aliases": ["淨收益", ""]}})
    with pytest.raises(ValueError, match="aliases"):
        callfinder.load_terms(path)


def test_a_blank_negative_term_is_rejected_too(tmp_path):
    # The mirror image: a blank negative term vetoes every row instead.
    path = write_config(tmp_path, {"t": {"aliases": ["淨收益"], "negative_terms": ["  "]}})
    with pytest.raises(ValueError, match="negative_terms"):
        callfinder.load_terms(path)
