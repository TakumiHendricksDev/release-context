from __future__ import annotations

import re
from typing import Any, Dict, List


def _strip_html_preserve_links(text: str) -> str:
    t = text or ""
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", t, flags=re.I)
    t = re.sub(r"</\s*(p|div|li|h\d)\s*>", "\n", t, flags=re.I)

    def _a_sub(m: re.Match[str]) -> str:
        url = m.group(1).strip()
        label = (m.group(2) or url).strip()
        return f"{label} ({url})"

    t = re.sub(r"<\s*a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</\s*a\s*>", _a_sub, t, flags=re.I | re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = re.sub(r"\r\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _safe_truncate(text: str, max_chars: int) -> str:
    t = text.strip()
    if len(t) <= max_chars:
        return t
    cutoff = max_chars
    nl = t.rfind("\n", 0, cutoff)
    dot = t.rfind(". ", 0, cutoff)
    space = t.rfind(" ", 0, cutoff)
    candidate = max(nl, dot, space)
    if candidate > max_chars // 2:
        cutoff = candidate
    snip = t[:cutoff].rstrip()
    fences = snip.count("```")
    if fences % 2 == 1:
        snip += "\n```"
    if snip.count("`") % 2 == 1:
        snip += "`"
    return snip + "\n…(truncated)…"


def summarize_text(text: str, max_chars: int = 600) -> str:
    cleaned = _strip_html_preserve_links(text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return _safe_truncate(cleaned, max_chars)


def extract_breaking_changes(text: str) -> List[str]:
    """Extract breaking changes from PR body text."""
    if not text:
        return []
    
    breaking: List[str] = []
    # Look for common breaking change patterns
    patterns = [
        r"(?:breaking|breaking change|breaking changes?)[\s:]+(.*?)(?:\n\n|\n#|$)",
        r"###\s*breaking[^#]*(.*?)(?=###|$)",
        r"##\s*breaking[^#]*(.*?)(?=##|$)",
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.I | re.S)
        for match in matches:
            content = match.group(1) if match.lastindex else match.group(0)
            if content:
                cleaned = _strip_html_preserve_links(content)
                cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
                if cleaned and len(cleaned) > 20:  # Ignore very short matches
                    breaking.append(_safe_truncate(cleaned, 300))
    
    return breaking[:5]  # Limit to 5 breaking changes


def extract_key_info(text: str) -> Dict[str, Any]:
    """Extract key information from PR body (tickets, related PRs, deprecations)."""
    info: Dict[str, Any] = {}
    
    if not text:
        return info
    
    # Extract ticket numbers (common patterns)
    ticket_patterns = [
        r"(?:ticket|issue|fixes?|closes?|resolves?)[\s:]+#?(\d+)",
        r"\[?ticket[-\s]?(\d+)\]?",
    ]
    tickets = set()
    for pattern in ticket_patterns:
        matches = re.finditer(pattern, text, re.I)
        for match in matches:
            tickets.add(match.group(1))
    if tickets:
        info["tickets"] = sorted(list(tickets))
    
    # Extract related PRs
    pr_patterns = [
        r"(?:related|see|refs?)[\s:]+(?:pr|pull request)[\s:]+#?(\d+)",
        r"#(\d+)",
    ]
    related_prs = set()
    for pattern in pr_patterns:
        matches = re.finditer(pattern, text, re.I)
        for match in matches:
            try:
                pr_num = int(match.group(1))
                if pr_num < 100000:  # Reasonable PR number limit
                    related_prs.add(pr_num)
            except ValueError:
                pass
    if related_prs:
        info["related_prs"] = sorted(list(related_prs))
    
    # Check for deprecation mentions
    if re.search(r"\b(?:deprecat|deprecated|obsolete|removed?|removal)\b", text, re.I):
        info["has_deprecation"] = True
    
    return info

