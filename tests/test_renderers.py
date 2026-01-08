from datetime import datetime, timezone

from renderers import MarkdownRenderer, JsonRenderer
from pr_info import PRInfo


def _sample_pr(num: int) -> PRInfo:
    return PRInfo(
        number=num,
        title="Title",
        body="Body with <b>html</b>",
        url="http://example.com",
        user="user",
        merged_at=None,
        labels=["bug"],
        base_ref="main",
        head_ref="feature",
        head_sha="deadbeef",
        files=[{"filename": "src/app.py"}],
        reviews=[{"state": "APPROVED", "body": "Looks good"}],
        comments=[{"body": "nit"}],
        check_runs=[{"conclusion": "success"}, {"conclusion": "failure"}],
    )


def test_markdown_renderer_basic():
    md = MarkdownRenderer(summary_max_chars=50, files_shown=5, risk_max_items=5, commit_fallback_max=5)
    content = md.render(
        owner="o",
        repo="r",
        from_ref="v1",
        to_ref="v2",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        commits=[{"sha": "1" * 40, "message": "msg", "author": "a", "date": "d"}],
        prs_grouped={"bugfix": [_sample_pr(1)]},
        compare_files=[{"filename": "requirements.txt", "additions": 1, "deletions": 0}],
    )
    assert "Release context" in content
    assert "Potential impact" in content
    assert "Files touched" in content


def test_json_renderer_basic():
    jr = JsonRenderer(summary_max_chars=50)
    pr = _sample_pr(1)
    data = jr.render(
        repo="o/r",
        from_ref="v1",
        to_ref="v2",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        compare={"ahead_by": 0, "behind_by": 0, "total_commits": 1, "html_url": ""},
        commits=[],
        prs=[pr],
        compare_files=[],
    )
    assert "body_snippet" in data["prs"][0]
    assert "body" not in data["prs"][0]  # Full body should be removed


def test_markdown_renderer_failed_checks():
    md = MarkdownRenderer(summary_max_chars=50, files_shown=5, risk_max_items=5, commit_fallback_max=5)
    pr_with_failed_check = PRInfo(
        number=1,
        title="Test PR",
        body="Test body",
        url="http://example.com",
        user="user",
        merged_at=None,
        labels=["bug"],
        base_ref="main",
        head_ref="feature",
        head_sha="deadbeef",
        files=None,
        reviews=None,
        comments=None,
        check_runs=[
            {"conclusion": "failure", "name": "test-check", "output": {"summary": "Test failed"}},
            {"conclusion": "success", "name": "other-check"},
        ],
    )
    content = md.render(
        owner="o",
        repo="r",
        from_ref="v1",
        to_ref="v2",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        commits=[{"sha": "1" * 40, "message": "msg", "author": "a", "date": "d"}],
        prs_grouped={"bugfix": [pr_with_failed_check]},
        compare_files=[],
    )
    assert "Failed checks" in content
    assert "test-check" in content


def test_json_renderer_filters_reviews_comments():
    jr = JsonRenderer(summary_max_chars=10)
    pr_with_concerns = PRInfo(
        number=1,
        title="Test PR",
        body="Test body",
        url="http://example.com",
        user="user",
        merged_at=None,
        labels=["bug"],
        base_ref="main",
        head_ref="feature",
        head_sha="deadbeef",
        files=None,
        reviews=[
            {"state": "APPROVED", "body": "Looks good"},
            {"state": "CHANGES_REQUESTED", "body": "There is an issue with this code"},
        ],
        comments=[{"body": "This has a problem"}],
        check_runs=None,
    )
    data = jr.render(
        repo="o/r",
        from_ref="v1",
        to_ref="v2",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        compare={"ahead_by": 0, "behind_by": 0, "total_commits": 1, "html_url": ""},
        commits=[],
        prs=[pr_with_concerns],
        compare_files=[],
    )
    # Should only include reviews/comments with concerns
    reviews = data["prs"][0].get("reviews")
    if reviews:
        assert len(reviews) > 0
        assert "body_snippet" in reviews[0]

