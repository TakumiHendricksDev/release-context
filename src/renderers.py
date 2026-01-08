from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

from pr_info import PRInfo
from sanitize import summarize_text, extract_breaking_changes, extract_key_info


def _extract_failed_checks(check_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract only failed checks with error messages."""
    failed = []
    for cr in check_runs:
        conclusion = (cr.get("conclusion") or "").lower()
        status = (cr.get("status") or "").lower()
        output_obj = cr.get("output") or {}
        if conclusion in ("failure", "cancelled", "timed_out") or (
            conclusion == "" and status == "completed" and output_obj.get("title")
        ):
            output_text = output_obj.get("summary") or output_obj.get("title") or ""
            failed.append({
                "name": cr.get("name", "unknown"),
                "conclusion": conclusion or status,
                "output": output_text if isinstance(output_text, str) else "",
            })
    return failed


def _extract_review_concerns(reviews: List[Dict[str, Any]], comments: List[Dict[str, Any]]) -> List[str]:
    """Extract review comments that mention issues, concerns, or problems."""
    concerns: List[str] = []
    concern_keywords = [
        r"\b(?:issue|problem|bug|error|fail|broken|wrong|concern|risk|warning|deprecated|breaking)\b",
        r"\b(?:doesn't|does not|won't|will not|shouldn't|should not)\b",
        r"\b(?:TODO|FIXME|HACK|XXX)\b",
    ]
    concern_pattern = re.compile("|".join(concern_keywords), re.I)

    # Check review bodies
    for rv in reviews:
        body = (rv.get("body") or "").strip()
        state = (rv.get("state") or "").lower()
        if body and state in ("changes_requested", "commented") and concern_pattern.search(body):
            snippet = summarize_text(body, 200)
            if snippet:
                concerns.append(f"Review ({state}): {snippet}")

    # Check review comments
    for cm in comments:
        body = (cm.get("body") or "").strip()
        if body and concern_pattern.search(body):
            snippet = summarize_text(body, 200)
            if snippet:
                concerns.append(f"Comment: {snippet}")

    return concerns[:5]  # Limit to top 5 concerns


def _prioritize_files(files: List[Dict[str, Any]], max_files: int) -> List[Dict[str, Any]]:
    """Prioritize risky files and files with significant changes."""
    risky_patterns = [
        r"migrations?/",
        r"schema",
        r"migration",
        r"settings",
        r"config",
        r"requirements",
        r"package.*\.json",
        r".*lock",
        r"dockerfile",
        r"\.github/workflows",
    ]
    risky_re = re.compile("|".join(risky_patterns), re.I)

    # Separate into risky and non-risky
    risky_files: List[Dict[str, Any]] = []
    other_files: List[Dict[str, Any]] = []

    for f in files:
        filename = f.get("filename", "")
        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)
        total_changes = additions + deletions

        if risky_re.search(filename) or total_changes > 50:
            risky_files.append(f)
        else:
            other_files.append(f)

    # Sort risky files by change magnitude, then add others
    risky_files.sort(key=lambda x: x.get("additions", 0) + x.get("deletions", 0), reverse=True)
    result = risky_files[:max_files]
    remaining = max_files - len(result)
    if remaining > 0:
        other_files.sort(key=lambda x: x.get("additions", 0) + x.get("deletions", 0), reverse=True)
        result.extend(other_files[:remaining])

    return result


class MarkdownRenderer:
    def __init__(self, *, summary_max_chars: int, files_shown: int, risk_max_items: int, commit_fallback_max: int):
        self.summary_max_chars = summary_max_chars
        self.files_shown = files_shown
        self.risk_max_items = risk_max_items
        self.commit_fallback_max = commit_fallback_max

    def render(
        self,
        owner: str,
        repo: str,
        from_ref: str,
        to_ref: str,
        generated_at: datetime,
        commits: List[Dict[str, Any]],
        prs_grouped: Dict[str, List[PRInfo]],
        compare_files: List[Dict[str, Any]],
    ) -> str:
        now_str = generated_at.strftime("%Y-%m-%d %H:%M:%SZ")
        md_lines: List[str] = []
        md_lines.append(f"# Release context: {owner}/{repo}")
        md_lines.append("")
        md_lines.append(f"- Range: `{from_ref}` → `{to_ref}`")
        md_lines.append(f"- Generated: {now_str}")
        md_lines.append(f"- Commits in range: **{len(commits)}**")
        md_lines.append(f"- PRs detected: **{sum(len(v) for v in prs_grouped.values())}**")
        md_lines.append("")

        if compare_files:
            additions = sum(f.get("additions", 0) for f in compare_files)
            deletions = sum(f.get("deletions", 0) for f in compare_files)
            changed_files = len(compare_files)
            md_lines.append("## Diff stats (compare endpoint)")
            md_lines.append(f"- Files changed: **{changed_files}**")
            md_lines.append(f"- Additions: **{additions}** | Deletions: **{deletions}**")
            md_lines.append("")

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
        import re

        risky_re = re.compile("|".join(risky_paths), re.I)
        for f in compare_files:
            fn = f.get("filename", "")
            if risky_re.search(fn):
                risk_flags.append(fn)

        if risk_flags:
            md_lines.append("## Potential impact areas (heuristic)")
            md_lines.append("These files suggest higher-risk changes (migrations/config/CI/deps):")
            for fn in sorted(set(risk_flags))[: self.risk_max_items]:
                md_lines.append(f"- `{fn}`")
            if len(set(risk_flags)) > self.risk_max_items:
                md_lines.append(f"- …and {len(set(risk_flags)) - self.risk_max_items} more")
            md_lines.append("")

        section_order = ["breaking", "security", "feature", "bugfix", "performance", "deps", "docs", "chore", "test", "other"]
        for section in section_order:
            prs_in_section = sorted(prs_grouped.get(section, []), key=lambda p: p.number)
            if not prs_in_section:
                continue
            
            # Highlight security and breaking changes sections
            if section == "security":
                md_lines.append(f"## ⚠️ {section.capitalize()}")
            elif section == "breaking":
                md_lines.append(f"## 🔴 {section.capitalize()} Changes")
            else:
                md_lines.append(f"## {section.capitalize()}")
            
            for pr in prs_in_section:
                # Highlight security PRs
                is_security = section == "security" or any(
                    "security" in label.lower() or "cve" in label.lower() or "vulnerability" in label.lower()
                    for label in pr.labels
                )
                prefix = "🔒 " if is_security else ""
                md_lines.append(f"- {prefix}#{pr.number} — {pr.title} (@{pr.user})")
                if pr.labels:
                    md_lines.append(f"  - Labels: {', '.join(pr.labels)}")
                md_lines.append(f"  - URL: {pr.url}")
                
                # Extract and highlight breaking changes
                breaking = extract_breaking_changes(pr.body or "")
                if breaking:
                    md_lines.append("  - ⚠️ Breaking changes:")
                    for bc in breaking[:3]:  # Show top 3
                        for line in bc.split("\n"):
                            if line.strip():
                                md_lines.append(f"    - {line.strip()}")
                
                body_snip = summarize_text(pr.body, self.summary_max_chars)
                if body_snip:
                    md_lines.append("  - Notes:")
                    for line in body_snip.split("\n"):
                        md_lines.append(f"    - {line}")
                if pr.files:
                    prioritized = _prioritize_files(pr.files, self.files_shown)
                    if prioritized:
                        md_lines.append(f"  - Files touched ({len(prioritized)} shown, {len(pr.files)} total):")
                        for pf in prioritized:
                            filename = pf.get("filename", "")
                            additions = pf.get("additions", 0)
                            deletions = pf.get("deletions", 0)
                            if additions > 0 or deletions > 0:
                                md_lines.append(f"    - `{filename}` (+{additions}/-{deletions})")
                            else:
                                md_lines.append(f"    - `{filename}`")
                        if len(pr.files) > len(prioritized):
                            md_lines.append(f"    - …and {len(pr.files) - len(prioritized)} more")
                if pr.check_runs:
                    failed_checks = _extract_failed_checks(pr.check_runs)
                    if failed_checks:
                        md_lines.append(f"  - Failed checks ({len(failed_checks)}):")
                        for fc in failed_checks[:3]:  # Show top 3 failures
                            name = fc.get("name", "unknown")
                            output = fc.get("output") or ""
                            if isinstance(output, str) and output.strip():
                                output_snippet = summarize_text(output, 150)
                                md_lines.append(f"    - {name}: {output_snippet}")
                            else:
                                md_lines.append(f"    - {name} ({fc.get('conclusion', 'failed')})")
                        if len(failed_checks) > 3:
                            md_lines.append(f"    - …and {len(failed_checks) - 3} more failures")
                if pr.reviews or pr.comments:
                    concerns = _extract_review_concerns(pr.reviews or [], pr.comments or [])
                    if concerns:
                        md_lines.append(f"  - Review concerns ({len(concerns)}):")
                        for concern in concerns:
                            md_lines.append(f"    - {concern}")
            md_lines.append("")

        if not sum(len(v) for v in prs_grouped.values()):
            md_lines.append("## Commits (no PRs detected)")
            for ce in commits[: self.commit_fallback_max]:
                md_lines.append(f"- {ce['sha'][:8]} — {ce['message']}")
            if len(commits) > self.commit_fallback_max:
                md_lines.append(f"- …and {len(commits) - self.commit_fallback_max} more")
            md_lines.append("")

        return "\n".join(md_lines).rstrip() + "\n"


class JsonRenderer:
    def __init__(self, *, summary_max_chars: int, min_file_lines: int = 10):
        self.summary_max_chars = summary_max_chars
        self.min_file_lines = min_file_lines

    def _filter_compare_files(self, compare_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter compare_files to only risky files or files with significant changes."""
        risky_patterns = [
            r"migrations?/",
            r"schema",
            r"migration",
            r"settings",
            r"config",
            r"requirements",
            r"package.*\.json",
            r".*lock",
            r"dockerfile",
            r"\.github/workflows",
        ]
        risky_re = re.compile("|".join(risky_patterns), re.I)
        
        filtered = []
        for f in compare_files:
            filename = f.get("filename", "")
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            total_changes = additions + deletions
            
            # Include if risky or has significant changes
            if risky_re.search(filename) or total_changes >= self.min_file_lines:
                filtered.append(f)
        
        return filtered

    def render(
        self,
        repo: str,
        from_ref: str,
        to_ref: str,
        generated_at: datetime,
        compare: Dict[str, Any],
        commits: List[Dict[str, Any]],
        prs: List[PRInfo],
        compare_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        artifact_prs: List[Dict[str, Any]] = []
        for pr in sorted(prs, key=lambda p: p.number):
            pr_entry = asdict(pr)
            
            # Use single body_snippet instead of both body_sanitized and body_snippet
            pr_entry["body_snippet"] = summarize_text(pr.body or "", self.summary_max_chars)
            if "body" in pr_entry:
                del pr_entry["body"]  # Remove full body to save space
            
            # Extract breaking changes and key info
            breaking = extract_breaking_changes(pr.body or "")
            if breaking:
                pr_entry["breaking_changes"] = breaking
            
            key_info = extract_key_info(pr.body or "")
            if key_info:
                pr_entry["key_info"] = key_info
            
            # Filter reviews to only those with concerns
            if pr_entry.get("reviews"):
                filtered_reviews = []
                concern_pattern = re.compile(
                    r"\b(?:issue|problem|bug|error|fail|broken|wrong|concern|risk|warning|deprecated|breaking|changes?[_\s]?requested)\b",
                    re.I
                )
                for rv in pr_entry["reviews"]:
                    body = (rv.get("body") or "").strip()
                    state = (rv.get("state") or "").lower()
                    if body and (state in ("changes_requested", "commented") or concern_pattern.search(body)):
                        filtered_reviews.append({
                            "state": state,
                            "body_snippet": summarize_text(body, self.summary_max_chars),
                            "user": rv.get("user", {}).get("login", "") if isinstance(rv.get("user"), dict) else "",
                        })
                pr_entry["reviews"] = filtered_reviews if filtered_reviews else None
            
            # Filter comments to only those with concerns
            if pr_entry.get("comments"):
                filtered_comments = []
                concern_pattern = re.compile(
                    r"\b(?:issue|problem|bug|error|fail|broken|wrong|concern|risk|warning|deprecated|breaking)\b",
                    re.I
                )
                for cm in pr_entry["comments"]:
                    body = (cm.get("body") or "").strip()
                    if body and concern_pattern.search(body):
                        filtered_comments.append({
                            "body_snippet": summarize_text(body, self.summary_max_chars),
                            "user": cm.get("user", {}).get("login", "") if isinstance(cm.get("user"), dict) else "",
                        })
                pr_entry["comments"] = filtered_comments if filtered_comments else None
            
            # Only include failed checks with error messages
            if pr_entry.get("check_runs"):
                failed_checks = _extract_failed_checks(pr_entry["check_runs"])
                if failed_checks:
                    pr_entry["check_runs"] = failed_checks
                else:
                    pr_entry["check_runs"] = None
            
            artifact_prs.append(pr_entry)

        # Filter compare_files
        filtered_compare_files = self._filter_compare_files(compare_files)

        return {
            "repo": repo,
            "from": from_ref,
            "to": to_ref,
            "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%SZ"),
            "compare": {
                "ahead_by": compare.get("ahead_by"),
                "behind_by": compare.get("behind_by"),
                "total_commits": compare.get("total_commits"),
                "html_url": compare.get("html_url"),
            },
            "commits": commits,
            "prs": artifact_prs,
            "compare_files": filtered_compare_files,
        }
