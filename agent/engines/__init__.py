"""Lien Ch. 8–16 entry engines (deterministic, research-only).

Each engine consumes already-computed ``regime.analyze_bars`` output and returns
a JSON-friendly signal dict. Engines never recompute indicators, never place
orders, and always run after the Ch. 7 regime filter.

Encoded this iteration: Ch. 8 MTF, Ch. 9 DBB, Ch. 13 Fader, Ch. 14 20-day
breakout, Ch. 16 perfect order, plus Ch. 7 geometry fallback.
"""
