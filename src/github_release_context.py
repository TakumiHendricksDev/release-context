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
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from github_service import GithubService
from pr_info import PRInfo

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

def parse_repo(repo: str) -> Tuple[str, str]:
    if "/" not in repo:
        raise ValueError("--repo must be like owner/name")
    owner, name = repo.split("/", 1)
    return owner, name

def extract_pr_number(commit_message: str) -> Optional[int]:
    for pat in PR_NUMBER_PATTERNS:
        m = pat.search(commit_message or "")
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def progress_iter(iterable, total: Optional[int] = None, desc: Optional[str] = None):
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


def group_for_labels(labels: List[str]) -> str:
    s = {l.lower() for l in labels}
    for group, labelset in DEFAULT_LABEL_GROUPS:
        if s.intersection(labelset):
            return group
    # fallback heuristic
    if any("break" in l for l in s):
        return "breaking"
    if any("fix" in l or "bug" in l for l in s):
        return "bugfix"
    if any("doc" in l for l in s):
        return "docs"
    if any("dep" in l for l in s):
        return "deps"
    return "other"


def _strip_html_preserve_links(text: str) -> str:
    """Basic HTML to plain text conversion.
    - Converts <a href="url">label</a> to 'label (url)'.
    - Replaces <br> and block tags with newlines.
    - Removes other tags.
    """
    t = text or ""
    # Normalize line breaks from <br> and <p>/<div>/li
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", t, flags=re.I)
    t = re.sub(r"</\s*(p|div|li|h\d)\s*>", "\n", t, flags=re.I)
    # Anchor tags -> label (url)
    def _a_sub(m: re.Match[str]) -> str:
        url = m.group(1).strip()
        label = (m.group(2) or url).strip()
        return f"{label} ({url})"
    t = re.sub(r"<\s*a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</\s*a\s*>", _a_sub, t, flags=re.I | re.S)
    # Remove all remaining tags
    t = re.sub(r"<[^>]+>", "", t)
    # Decode common HTML entities (minimal set)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    # Collapse excessive blank lines
    t = re.sub(r"\r\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _safe_truncate(text: str, max_chars: int) -> str:
    """Truncate at a sensible boundary without breaking words or code fences.
    - Prefers cutting at sentence boundary or newline.
    - Ensures backtick fences are balanced in the snippet.
    """
    t = text.strip()
    if len(t) <= max_chars:
        return t
    cutoff = max_chars
    # Prefer last newline before cutoff
    nl = t.rfind("\n", 0, cutoff)
    dot = t.rfind(". ", 0, cutoff)
    space = t.rfind(" ", 0, cutoff)
    candidate = max(nl, dot, space)
    if candidate > max_chars // 2:
        cutoff = candidate
    snip = t[:cutoff].rstrip()
    # Balance triple backticks if present
    fences = snip.count("```")
    if fences % 2 == 1:
        snip += "\n```"
    # Balance single backticks (best-effort)
    if snip.count("`") % 2 == 1:
        snip += "`"
    return snip + "\n…(truncated)…"


def summarize_text(text: str, max_chars: int = 600) -> str:
    # Convert HTML-heavy PR bodies to readable plain text first
    cleaned = _strip_html_preserve_links(text or "")
    # Normalize whitespace
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return _safe_truncate(cleaned, max_chars)


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--from", dest="from_ref", required=True, help="from tag/sha")
    ap.add_argument("--to", dest="to_ref", required=True, help="to tag/sha")
    ap.add_argument("--out-dir", default="out", help="output directory")
    ap.add_argument("--include-pr-files", action="store_true", help="fetch PR file lists (slower)")
    ap.add_argument("--max-pr-files", type=int, default=200, help="max files per PR to fetch")
    ap.add_argument("--env-file", default=".env", help="path to .env file (default: ./.env)")
    # New tunables
    ap.add_argument("--summary-max-chars", type=int, default=700, help="max characters for sanitized PR body snippets")
    ap.add_argument("--files-shown", type=int, default=25, help="max files listed per PR in Markdown")
    ap.add_argument("--risk-max-items", type=int, default=60, help="max risky file paths to show in heuristic section")
    ap.add_argument("--commit-fallback-max", type=int, default=200, help="max commits to list when no PRs are detected")
    args = ap.parse_args()

    load_env(args.env_file)

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print(
            "Missing GITHUB_TOKEN. Put it in .env or export it.\n"
            "Example .env:\n"
            "  GITHUB_TOKEN=ghp_...\n",
            file=sys.stderr,
        )
        return 2

    # Get repo owner/name from args
    owner, name = parse_repo(args.repo)

    # Prepare output dir (Defaults to "out")
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    # Setup gh service class
    gs = GithubService(owner, name, args.from_ref, args.to_ref)

    compare_url = gs.get_compare_url()
    compare = gs.gh_get(compare_url, token).json()

    commits = compare.get("commits", [])
    files = compare.get("files", [])  # compare-level file list (may be truncated for large diffs)

    prs: Dict[int, PRInfo] = {}
    commit_entries: List[Dict[str, Any]] = []

    for c in progress_iter(commits, total=len(commits), desc="Commits"):
        sha = c.get("sha")
        msg = (c.get("commit", {}) or {}).get("message", "")
        author = ((c.get("commit", {}) or {}).get("author", {}) or {}).get("name")
        date = ((c.get("commit", {}) or {}).get("author", {}) or {}).get("date")

        pr_number = extract_pr_number(msg)
        # If commit message didn't include a PR number, try the commit->pulls API
        if not pr_number and sha:
            try:
                pulls = gs.gh_get_commit_pulls(sha, token)
                if pulls:
                    pr_number = pulls[0].get("number")
            except Exception as e:
                print(f"[warn] commit->pulls lookup failed for {sha}: {e}", file=sys.stderr)

        commit_entries.append(
            {"sha": sha, "message": msg.split("\n", 1)[0], "author": author, "date": date, "pr_number": pr_number}
        )

        if pr_number and pr_number not in prs:
            pr_url = gs.get_pr_url(pr_number)
            print(f"[fetch] PR #{pr_number} details...", file=sys.stderr)
            pr = gs.gh_get(pr_url, token).json()
            labels = [l["name"] for l in pr.get("labels", []) if "name" in l]
            pr_info = PRInfo(
                number=pr_number,
                title=pr.get("title", ""),
                body=pr.get("body") or "",
                url=pr.get("html_url", ""),
                user=(pr.get("user", {}) or {}).get("login", ""),
                merged_at=pr.get("merged_at"),
                labels=labels,
                base_ref=((pr.get("base", {}) or {}).get("ref", "")),
                head_ref=((pr.get("head", {}) or {}).get("ref", "")),
            )

            if args.include_pr_files:
                pr_files_url = gs.get_files_url(pr_number)
                print(f"[fetch] PR #{pr_number} files...", file=sys.stderr)
                pr_files: List[Dict[str, Any]] = []
                page = 1
                per_page = 100
                while len(pr_files) < args.max_pr_files:
                    batch = gs.gh_get(pr_files_url, token, params={"page": page, "per_page": per_page}).json()
                    if not batch:
                        break
                    pr_files.extend(batch)
                    if len(batch) < per_page:
                        break
                    page += 1
                pr_info.files = pr_files[: args.max_pr_files]

            prs[pr_number] = pr_info

    # Group PRs
    grouped: Dict[str, List[PRInfo]] = defaultdict(list)
    for pr in prs.values():
        grouped[group_for_labels(pr.labels)].append(pr)

    # Make Markdown
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    md_lines: List[str] = []
    md_lines.append(f"# Release context: {owner}/{name}")
    md_lines.append("")
    md_lines.append(f"- Range: `{args.from_ref}` → `{args.to_ref}`")
    md_lines.append(f"- Generated: {now}")
    md_lines.append(f"- Commits in range: **{len(commits)}**")
    md_lines.append(f"- PRs detected: **{len(prs)}**")
    md_lines.append("")

    # High-level compare file stats
    if files:
        additions = sum(f.get("additions", 0) for f in files)
        deletions = sum(f.get("deletions", 0) for f in files)
        changed_files = len(files)
        md_lines.append("## Diff stats (compare endpoint)")
        md_lines.append(f"- Files changed: **{changed_files}**")
        md_lines.append(f"- Additions: **{additions}** | Deletions: **{deletions}**")
        md_lines.append("")

    # Risks / flags (simple heuristics)
    risk_flags: List[str] = []
    risky_paths = [
        r"migrations/",
        r"schema",
        r"docker",
        r"helm",
        r"k8s",
        r"terraform",
        r"\.github/workflows",
        r"settings",
        r"config",
        r"requirements",
        r"package-lock\.json",
        r"pnpm-lock\.yaml",
        r"yarn\.lock",
        r"poetry\.lock",
    ]
    risky_re = re.compile("|".join(risky_paths), re.I)
    for f in files:
        fn = f.get("filename", "")
        if risky_re.search(fn):
            risk_flags.append(fn)

    if risk_flags:
        md_lines.append("## Potential impact areas (heuristic)")
        md_lines.append("These files suggest higher-risk changes (migrations/config/CI/deps):")
        for fn in sorted(set(risk_flags))[: args.risk_max_items]:
            md_lines.append(f"- `{fn}`")
        if len(set(risk_flags)) > args.risk_max_items:
            md_lines.append(f"- …and {len(set(risk_flags)) - args.risk_max_items} more")
        md_lines.append("")

    # PR sections (ordered)
    section_order = ["breaking", "security", "feature", "bugfix", "performance", "deps", "docs", "chore", "test", "other"]
    pr_json_list: List[Dict[str, Any]] = []
    for section in section_order:
        prs_in_section = sorted(grouped.get(section, []), key=lambda p: p.number)
        if not prs_in_section:
            continue
        md_lines.append(f"## {section.capitalize()}")
        for pr in prs_in_section:
            md_lines.append(f"- #{pr.number} — {pr.title} (@{pr.user})")
            if pr.labels:
                md_lines.append(f"  - Labels: {', '.join(pr.labels)}")
            md_lines.append(f"  - URL: {pr.url}")
            body_snip = summarize_text(pr.body, args.summary_max_chars)
            if body_snip:
                md_lines.append("  - Notes:")
                for line in body_snip.split("\n"):
                    md_lines.append(f"    - {line}")
            if pr.files:
                touched = [pf.get("filename") for pf in pr.files if pf.get("filename")]
                if touched:
                    md_lines.append(f"  - Files touched ({min(len(touched), args.files_shown)} shown):")
                    for fn in touched[: args.files_shown]:
                        md_lines.append(f"    - `{fn}`")
                    if len(touched) > args.files_shown:
                        md_lines.append(f"    - …and {len(touched) - args.files_shown} more")
            # Build JSON entry with sanitized snippet
            pr_entry = dataclasses.asdict(pr)
            pr_entry["body_sanitized"] = _strip_html_preserve_links(pr.body or "")
            pr_entry["body_snippet"] = summarize_text(pr.body or "", args.summary_max_chars)
            pr_json_list.append(pr_entry)
        md_lines.append("")

    # If no PRs found, fall back to commits
    if not prs:
        md_lines.append("## Commits (no PRs detected)")
        for ce in commit_entries[: args.commit_fallback_max]:
            md_lines.append(f"- {ce['sha'][:8]} — {ce['message']}")
        if len(commit_entries) > args.commit_fallback_max:
            md_lines.append(f"- …and {len(commit_entries) - args.commit_fallback_max} more")
        md_lines.append("")

    # JSON artifact
    artifact = {
        "repo": args.repo,
        "from": args.from_ref,
        "to": args.to_ref,
        "generated_at": now,
        "compare": {
            "ahead_by": compare.get("ahead_by"),
            "behind_by": compare.get("behind_by"),
            "total_commits": compare.get("total_commits"),
            "html_url": compare.get("html_url"),
        },
        "commits": commit_entries,
        "prs": sorted(pr_json_list, key=lambda x: x["number"]),
        "compare_files": files,
    }

    md_path = os.path.join(out_dir, f"release_context_{owner}_{name}_{args.from_ref}_to_{args.to_ref}.md")
    json_path = os.path.join(out_dir, f"release_context_{owner}_{name}_{args.from_ref}_to_{args.to_ref}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines).rstrip() + "\n")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    print(f"Wrote:\n- {md_path}\n- {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
