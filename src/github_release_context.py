#!/usr/bin/env python3
"""
GitHub Release Context Builder
- Builds AI-ready context for changes between two refs (tags/SHAs).
- Uses GitHub REST API v3.
- Outputs Markdown summary + JSON artifact.

Usage:
  # Put GITHUB_TOKEN in .env (recommended)
  python github_release_context.py --repo owner/name --from v1.2.3 --to v1.3.0 --out-dir ./out

Notes:
- Best results if your repo uses PR-based workflow.
- If commits aren’t linked to PRs, summary will be commit-based.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from github_service import GithubService
from github_client import GitHubClient
from pr_info import PRInfo
from fetchers import CompareFetcher, PRFetcher
from enrich import group_prs
from renderers import MarkdownRenderer, JsonRenderer
from parallel_fetcher import ParallelFetcher

# Optional fancy progress bar
try:
    from tqdm import tqdm  # type: ignore
except Exception:
    tqdm = None

PR_NUMBER_PATTERNS = [
    re.compile(r"\(#(\d+)\)\s*$"),  # "Fix thing (#123)"
    re.compile(r"Merge pull request #(\d+)\b"),  # "Merge pull request #123 from ..."
    re.compile(r"pull request #(\d+)\b", re.I),
]

@dataclasses.dataclass
class Config:
    repo: str
    from_ref: str
    to_ref: str
    out_dir: str
    include_pr_files: bool
    max_pr_files: int
    env_file: str
    summary_max_chars: int
    files_shown: int
    risk_max_items: int
    commit_fallback_max: int
    include_pr_reviews: bool
    include_pr_comments: bool
    include_pr_checks: bool
    max_concurrent_requests: int
    min_file_lines: int


def parse_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise ValueError("--repo must be like owner/name")
    owner, name = repo.split("/", 1)
    return owner, name


def extract_pr_number(commit_message: str) -> int | None:
    for pat in PR_NUMBER_PATTERNS:
        m = pat.search(commit_message or "")
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def progress_iter(iterable, total: int | None = None, desc: str | None = None):
    """Wrap an iterable with a progress indicator, if possible."""
    if tqdm:
        return tqdm(iterable, total=total, desc=desc)

    # Fallback simple progress printer
    class _SimpleProgress:
        def __init__(self, it, total, desc):
            self._it = iter(it)
            self.total = total
            self.desc = desc or ""
            self.count = 0

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self._it)
            self.count += 1
            if self.count % 10 == 0 or (self.total and self.count == self.total):
                if self.total:
                    print(f"{self.desc} {self.count}/{self.total}", end="\r", file=sys.stderr)
                else:
                    print(f"{self.desc} {self.count}", end="\r", file=sys.stderr)
            return item

        def close(self):
            print(file=sys.stderr)

    return _SimpleProgress(iterable, total, desc)


def load_env(env_file: str) -> None:
    """
    Load environment variables from a .env file.
    Real environment variables still override values in the .env file.
    """
    # Only load if file exists; otherwise do nothing.
    if os.path.exists(env_file):
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        # Not fatal; allows CI / exported vars
        print(f"[env] No env file found at: {env_file} (skipping)", file=sys.stderr)


def build_config() -> Config:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--from", dest="from_ref", required=True, help="from tag/sha")
    ap.add_argument("--to", dest="to_ref", required=True, help="to tag/sha")
    ap.add_argument("--out-dir", default="out", help="output directory")
    ap.add_argument("--include-pr-files", action="store_true", help="fetch PR file lists (slower)")
    ap.add_argument("--max-pr-files", type=int, default=200, help="max files per PR to fetch")
    ap.add_argument("--env-file", default=".env", help="path to .env file (default: ./.env)")
    ap.add_argument("--summary-max-chars", type=int, default=700, help="max characters for sanitized PR body snippets")
    ap.add_argument("--files-shown", type=int, default=25, help="max files listed per PR in Markdown")
    ap.add_argument("--risk-max-items", type=int, default=60, help="max risky file paths to show in heuristic section")
    ap.add_argument("--commit-fallback-max", type=int, default=200, help="max commits to list when no PRs are detected")
    ap.add_argument("--include-pr-reviews", action="store_true", help="include PR reviews (adds API calls)")
    ap.add_argument("--include-pr-comments", action="store_true", help="include PR review comments (adds API calls)")
    ap.add_argument("--include-pr-checks", action="store_true", help="include PR check runs via head SHA (adds API calls)")
    ap.add_argument("--max-concurrent-requests", type=int, default=10, help="max concurrent API requests (default: 10)")
    ap.add_argument("--min-file-lines", type=int, default=10, help="min lines changed to include file in JSON output (default: 10)")
    args = ap.parse_args()
    return Config(
        repo=args.repo,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
        out_dir=args.out_dir,
        include_pr_files=args.include_pr_files,
        max_pr_files=args.max_pr_files,
        env_file=args.env_file,
        summary_max_chars=args.summary_max_chars,
        files_shown=args.files_shown,
        risk_max_items=args.risk_max_items,
        commit_fallback_max=args.commit_fallback_max,
        include_pr_reviews=args.include_pr_reviews,
        include_pr_comments=args.include_pr_comments,
        include_pr_checks=args.include_pr_checks,
        max_concurrent_requests=args.max_concurrent_requests,
        min_file_lines=args.min_file_lines,
    )


def make_client(cfg: Config) -> GitHubClient:
    owner, name = parse_repo(cfg.repo)
    return GithubService(owner, name, cfg.from_ref, cfg.to_ref)


def main() -> int:
    cfg = build_config()
    load_env(cfg.env_file)

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("Missing GITHUB_TOKEN. Put it in .env or export it.", file=sys.stderr)
        return 2

    client = make_client(cfg)
    compare_fetcher = CompareFetcher(client)
    pr_fetcher = PRFetcher(client)
    parallel_fetcher = ParallelFetcher(max_concurrent=cfg.max_concurrent_requests)

    compare = compare_fetcher.fetch(token)
    commits = compare.get("commits", [])
    compare_files = compare.get("files", [])

    # First pass: Extract PR numbers from commit messages (no API calls)
    commit_entries: List[Dict[str, Any]] = []
    commits_needing_lookup: List[str] = []
    sha_to_pr: Dict[str, Optional[int]] = {}

    for c in commits:
        sha = c.get("sha")
        msg = (c.get("commit", {}) or {}).get("message", "")
        author = ((c.get("commit", {}) or {}).get("author", {}) or {}).get("name")
        date = ((c.get("commit", {}) or {}).get("author", {}) or {}).get("date")

        pr_number = extract_pr_number(msg)
        if not pr_number and sha:
            commits_needing_lookup.append(sha)
            sha_to_pr[sha] = None
        else:
            sha_to_pr[sha] = pr_number

        commit_entries.append(
            {"sha": sha, "message": msg.split("\n", 1)[0], "author": author, "date": date, "pr_number": pr_number}
        )

    # Batch fetch commit->pulls for commits missing PR numbers
    if commits_needing_lookup:
        print(f"[info] Looking up PR numbers for {len(commits_needing_lookup)} commits...", file=sys.stderr)
        lookup_results = parallel_fetcher.fetch_commit_pr_numbers(client, commits_needing_lookup, token, pr_fetcher)
        sha_to_pr.update(lookup_results)
        # Update commit_entries with found PR numbers
        for entry in commit_entries:
            if not entry["pr_number"] and entry["sha"] in sha_to_pr:
                entry["pr_number"] = sha_to_pr[entry["sha"]]

    # Collect unique PR numbers
    unique_pr_numbers = set()
    for entry in commit_entries:
        if entry["pr_number"]:
            unique_pr_numbers.add(entry["pr_number"])

    # Batch fetch PR details in parallel
    prs: Dict[int, PRInfo] = {}
    if unique_pr_numbers:
        print(f"[info] Fetching {len(unique_pr_numbers)} PR details...", file=sys.stderr)
        pr_data_map = parallel_fetcher.fetch_pr_details(client, list(unique_pr_numbers), token, pr_fetcher)

        # Build PRInfo objects and fetch extras in parallel
        pr_extras_tasks: List[Tuple[int, Optional[str]]] = []
        for pr_number, pr_data in pr_data_map.items():
            labels = [l["name"] for l in pr_data.get("labels", []) if "name" in l]
            head_sha = ((pr_data.get("head", {}) or {}).get("sha", "")) or None
            pr_info = PRInfo(
                number=pr_number,
                title=pr_data.get("title", ""),
                body=pr_data.get("body") or "",
                url=pr_data.get("html_url", ""),
                user=(pr_data.get("user", {}) or {}).get("login", ""),
                merged_at=pr_data.get("merged_at"),
                labels=labels,
                base_ref=((pr_data.get("base", {}) or {}).get("ref", "")),
                head_ref=((pr_data.get("head", {}) or {}).get("ref", "")),
                head_sha=head_sha,
            )
            prs[pr_number] = pr_info
            pr_extras_tasks.append((pr_number, head_sha))

        # Fetch extras (files, reviews, comments, checks) in parallel per PR
        if pr_extras_tasks and (cfg.include_pr_files or cfg.include_pr_reviews or cfg.include_pr_comments or cfg.include_pr_checks):
            print(f"[info] Fetching PR extras (files/reviews/comments/checks)...", file=sys.stderr)
            for pr_number, head_sha in progress_iter(pr_extras_tasks, total=len(pr_extras_tasks), desc="PR extras"):
                extras = parallel_fetcher.fetch_pr_extras(
                    pr_number,
                    head_sha,
                    token,
                    pr_fetcher,
                    cfg.include_pr_files,
                    cfg.include_pr_reviews,
                    cfg.include_pr_comments,
                    cfg.include_pr_checks,
                    cfg.max_pr_files,
                )
                if "files" in extras:
                    prs[pr_number].files = extras["files"]
                if "reviews" in extras:
                    prs[pr_number].reviews = extras["reviews"]
                if "comments" in extras:
                    prs[pr_number].comments = extras["comments"]
                if "check_runs" in extras:
                    prs[pr_number].check_runs = extras["check_runs"]

    grouped = group_prs(list(prs.values()))
    generated_at = datetime.now(timezone.utc)

    md_renderer = MarkdownRenderer(
        summary_max_chars=cfg.summary_max_chars,
        files_shown=cfg.files_shown,
        risk_max_items=cfg.risk_max_items,
        commit_fallback_max=cfg.commit_fallback_max,
    )
    json_renderer = JsonRenderer(summary_max_chars=cfg.summary_max_chars, min_file_lines=cfg.min_file_lines)

    owner, name = parse_repo(cfg.repo)
    md_content = md_renderer.render(owner, name, cfg.from_ref, cfg.to_ref, generated_at, commit_entries, grouped, compare_files)
    json_content = json_renderer.render(cfg.repo, cfg.from_ref, cfg.to_ref, generated_at, compare, commit_entries, list(prs.values()), compare_files)

    os.makedirs(cfg.out_dir, exist_ok=True)
    md_path = os.path.join(cfg.out_dir, f"release_context_{owner}_{name}_{cfg.from_ref}_to_{cfg.to_ref}.md")
    json_path = os.path.join(cfg.out_dir, f"release_context_{owner}_{name}_{cfg.from_ref}_to_{cfg.to_ref}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_content, f, indent=2, ensure_ascii=False)

    print(f"Wrote:\n- {md_path}\n- {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
