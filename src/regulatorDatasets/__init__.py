"""FSC 銀行局 public monthly datasets, fetched over HTTP.

The only package whose input is not a local file. Deliberately imports
nothing from the other three so it can be tested and scheduled alone;
earningsCalls calls INTO it for the 信用卡循環 fallback, never the reverse.
"""
