from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from pr_info import PRInfo

DEFAULT_LABEL_GROUPS = [
    ("breaking", {"breaking", "breaking-change", "semver-major"}),
    ("security", {"security"}),
    ("bugfix", {"bug", "bugfix", "fix"}),
    ("feature", {"feature", "enhancement"}),
    ("performance", {"performance", "perf"}),
    ("docs", {"documentation", "docs"}),
    ("deps", {"dependencies", "deps"}),
    ("chore", {"chore", "maintenance", "refactor"}),
    ("test", {"test", "testing"}),
]


def group_for_labels(labels: List[str]) -> str:
    s = {l.lower() for l in labels}
    for group, labelset in DEFAULT_LABEL_GROUPS:
        if s.intersection(labelset):
            return group
    if any("break" in l for l in s):
        return "breaking"
    if any("fix" in l or "bug" in l for l in s):
        return "bugfix"
    if any("doc" in l for l in s):
        return "docs"
    if any("dep" in l for l in s):
        return "deps"
    return "other"


def group_prs(prs: List[PRInfo]) -> Dict[str, List[PRInfo]]:
    grouped: Dict[str, List[PRInfo]] = defaultdict(list)
    for pr in prs:
        grouped[group_for_labels(pr.labels)].append(pr)
    return grouped
