from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

import requests


class GitHubClient(Protocol):
    owner: str
    repo: str

    def gh_get(self, url: str, token: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        ...

    def gh_get_commit_pulls(self, sha: str, token: str) -> list[dict]:
        ...

    def get_compare_url(self) -> str:
        ...

    def get_pr_url(self, pr_number: int) -> str:
        ...

    def get_files_url(self, pr_number: int) -> str:
        ...

