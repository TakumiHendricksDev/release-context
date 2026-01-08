from __future__ import annotations

from typing import Any, Dict, List, Optional

from github_client import GitHubClient


class CompareFetcher:
    def __init__(self, client: GitHubClient):
        self.client = client

    def fetch(self, token: str) -> Dict[str, Any]:
        url = self.client.get_compare_url()
        return self.client.gh_get(url, token).json()


class PRFetcher:
    def __init__(self, client: GitHubClient):
        self.client = client

    def fetch_pr(self, pr_number: int, token: str) -> Dict[str, Any]:
        url = self.client.get_pr_url(pr_number)
        return self.client.gh_get(url, token).json()

    def fetch_files(self, pr_number: int, token: str, max_files: int, per_page: int = 100) -> List[Dict[str, Any]]:
        url = self.client.get_files_url(pr_number)
        pr_files: List[Dict[str, Any]] = []
        page = 1
        while len(pr_files) < max_files:
            batch = self.client.gh_get(url, token, params={"page": page, "per_page": per_page}).json()
            if not batch:
                break
            pr_files.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return pr_files[:max_files]

    def fetch_commit_pr_number(self, sha: str, token: str) -> Optional[int]:
        pulls = self.client.gh_get_commit_pulls(sha, token)
        if pulls:
            return pulls[0].get("number")
        return None

    def fetch_reviews(self, pr_number: int, token: str) -> List[Dict[str, Any]]:
        url = f"{self.client.get_pr_url(pr_number)}/reviews"
        return self.client.gh_get(url, token).json()

    def fetch_comments(self, pr_number: int, token: str, per_page: int = 100) -> List[Dict[str, Any]]:
        url = f"{self.client.get_pr_url(pr_number)}/comments"
        comments: List[Dict[str, Any]] = []
        page = 1
        while True:
            batch = self.client.gh_get(url, token, params={"page": page, "per_page": per_page}).json()
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return comments

    def fetch_check_runs(self, pr_number: int, head_sha: str, token: str) -> List[Dict[str, Any]]:
        # Checks are tied to the commit SHA; use the Checks API
        url = f"https://api.github.com/repos/{self.client.owner}/{self.client.repo}/commits/{head_sha}/check-runs"
        data = self.client.gh_get(url, token).json()
        return data.get("check_runs", []) if isinstance(data, dict) else []
