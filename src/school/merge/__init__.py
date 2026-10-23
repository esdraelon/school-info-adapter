"""Cross-source dedup and merge of Driver/Resource records.

Patterns: Strategy (per-source-kind precedence policy), pure functions.
Drivers never talk to each other — all merging happens here.
"""
