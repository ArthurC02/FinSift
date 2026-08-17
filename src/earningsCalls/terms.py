"""The term vocabulary, and how strongly a label matches one.

A deck carries no account codes, so a term IS the identifier here - which is
why this is the bottom of the package and depends on nothing else in it.
TermSpec is also the trust boundary: it rejects a config that would match
everything (see the blank-alias check) rather than letting it through to
produce confident wrong numbers.
"""
import json
from dataclasses import dataclass, field

from core.text import _contains_any


@dataclass
class Component:
    terms: list
    weight: float


@dataclass
class TermSpec:
    name: str
    type: str = "exact"  # "exact" or "composite"
    aliases: list = field(default_factory=list)
    components: list = field(default_factory=list)  # list[Component], "composite" only
    threshold: float = 0.8
    negative_terms: list = field(default_factory=list)
    search_start: list = None  # optional section-scoping markers (e.g. a specific speaker segment)
    search_end: list = None

    @classmethod
    def from_dict(cls, name, d):
        components = []
        for c in d.get("components", []):
            # Name the offending field: Component(**c) alone raises a bare
            # TypeError naming neither the file, the term, nor the field.
            if not isinstance(c, dict):
                raise ValueError(f"component must be an object, got {type(c).__name__}")
            missing = sorted({"terms", "weight"} - set(c))
            extra = sorted(set(c) - {"terms", "weight"})
            if missing or extra:
                raise ValueError(
                    f"component {c!r} is malformed"
                    + (f"; missing {missing}" if missing else "")
                    + (f"; unexpected {extra}" if extra else ""))
            components.append(Component(**c))

        # A blank alias is a substring of EVERY string: one of them makes its
        # term match every row in the folder at strength 2, outranking any
        # composite. A blank negative term vetoes everything instead.
        #   → docs/knowledge/earnings-call-matching.md#空白別名為什麼是信任邊界
        for field, values in (("aliases", d.get("aliases", [])),
                              ("negative_terms", d.get("negative_terms", []))):
            for v in values:
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(f"{field} contains a blank or non-string entry: {v!r}")

        return cls(
            name=name,
            type=d.get("type", "exact"),
            aliases=d.get("aliases", []),
            components=components,
            threshold=d.get("threshold", 0.8),
            negative_terms=d.get("negative_terms", []),
            search_start=d.get("search_start"),
            search_end=d.get("search_end"),
        )




def load_terms(config_path):
    """Load term definitions from a JSON config. See
    Bank_Term_Weighted_Decomposition.xlsx for the source dictionary this was
    generated from, and con_call_terms_example.json for a minimal
    hand-written example of the same shape."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    terms = {}
    for name, d in raw.items():
        if name.startswith("_"):
            continue
        try:
            terms[name] = TermSpec.from_dict(name, d)
        except (TypeError, ValueError) as e:
            # Say which file and which term - otherwise there is nothing to
            # search a 400-term config for.
            raise ValueError(f"{config_path}: term '{name}' is invalid - {e}") from e
    return terms




def match_strength(term_spec, text):
    """0 = no match (or vetoed by a negative term); 1 = composite; 2 =
    substring alias hit; 3 = exact alias hit (whole stripped text equals an
    alias).

    Exact beats substring beats composite, and callers must prefer the
    strongest match across the WHOLE folder rather than the first: 淨收益 is a
    literal substring of many unrelated compound line items.
      → docs/knowledge/earnings-call-matching.md#match_strength-的三層"""
    if term_spec.negative_terms and _contains_any(text, term_spec.negative_terms):
        return 0
    if term_spec.aliases:
        stripped_lower = text.strip().lower()
        if any(stripped_lower == a.lower() for a in term_spec.aliases):
            return 3
        if _contains_any(text, term_spec.aliases):
            return 2
    if term_spec.type == "composite" and term_spec.components:
        score = sum(c.weight for c in term_spec.components if _contains_any(text, c.terms))
        if score >= term_spec.threshold:
            return 1
    return 0
