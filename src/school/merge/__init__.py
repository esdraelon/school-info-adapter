"""Cross-source dedup and merge of Driver/Resource records.

Patterns: Strategy (per-source-kind precedence policy), pure functions.
Drivers never talk to each other — all merging happens here.
"""

from school.merge.engine import DEFAULT_PRECEDENCE, merge_driver_records

__all__ = ["DEFAULT_PRECEDENCE", "merge_driver_records"]
