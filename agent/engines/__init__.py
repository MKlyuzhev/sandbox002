"""Lien Ch. 8-16 entry engines (deterministic, research-only).

Each engine consumes already-computed ``regime.analyze_bars`` output and returns
a JSON-friendly signal dict. Engines never recompute indicators, never place
orders, and always run after the Ch. 7 regime filter.
"""
