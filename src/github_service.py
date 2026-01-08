import sys
from typing import Optional, Dict, Any

import requests
import time

API_BASE = "https://api.github.com"
UA = "release-context-builder/1.0"

class GithubService:
    def __init__(self, owner: str, repo: str, from_ref: str, to_ref: str):
        self.owner = owner
        self.repo = repo
        self.from_ref = from_ref
        self.to_ref = to_ref

    def gh_headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        }

    def gh_get(self, url: str, token: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        resp = requests.get(url, headers=self.gh_headers(token), params=params, timeout=60)
        # basic rate limit handling
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
            sleep_for = max(0, reset - int(time.time()) + 2)
            print(f"[rate-limit] sleeping {sleep_for}s until reset...", file=sys.stderr)
            time.sleep(sleep_for)
            resp = requests.get(url, headers=self.gh_headers(token), params=params, timeout=60)
        resp.raise_for_status()
        return resp

    def gh_get_commit_pulls(self, sha: str, token: str) -> list[dict]:
        url = f"{API_BASE}/repos/{self.owner}/{self.repo}/commits/{sha}/pulls"
        headers = self.gh_headers(token)
        headers["Accept"] = "application/vnd.github+json"
        # Some GitHub installs historically required a preview header; if you hit 415/406,
        # switch Accept to: "application/vnd.github.groot-preview+json"
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def get_compare_url(self) -> str:
        compare_url = f"{API_BASE}/repos/{self.owner}/{self.repo}/compare/{self.from_ref}...{self.to_ref}"
        return compare_url

    def get_pr_url(self, pr_number: int) -> str:
        pr_url = f"{API_BASE}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        return pr_url

    def get_files_url(self, pr_number: int) -> str:
        pr_files_url = f"{API_BASE}/repos/{self.owner}/{self.repo}/pulls/{pr_number}/files"
        return pr_files_url