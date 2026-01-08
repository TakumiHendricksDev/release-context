from enrich import group_for_labels, group_prs
from pr_info import PRInfo


def test_group_for_labels_defaults_to_other():
    assert group_for_labels(["random"]) == "other"


def test_group_for_labels_matches_labelset():
    assert group_for_labels(["bug"]) == "bugfix"
    assert group_for_labels(["breaking-change"]) == "breaking"
    assert group_for_labels(["docs"]) == "docs"


def test_group_prs_groups_by_label():
    prs = [
        PRInfo(number=1, title="", body="", url="", user="u", merged_at=None, labels=["bug"], base_ref="b", head_ref="h"),
        PRInfo(number=2, title="", body="", url="", user="u", merged_at=None, labels=["feature"], base_ref="b", head_ref="h"),
    ]
    grouped = group_prs(prs)
    assert len(grouped["bugfix"]) == 1
    assert len(grouped["feature"]) == 1

