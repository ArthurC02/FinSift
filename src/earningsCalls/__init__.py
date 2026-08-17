"""Earnings-call decks (法說會簡報), matched by TERM TEXT.

Decks carry no account codes, so everything here is text matching against
data/con_call_terms.json - which is why this package has machinery the
code-matched side never needs: entity tiering, unit normalisation, and
period-axis detection.
"""
