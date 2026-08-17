"""The user-facing surface: one CLI over all four axes.

cli classifies a folder, runs the right extractor and merges the
output - and is also the single entry point, dispatching to the other
packages' CLIs as subcommands (acct / call / npl).
"""
