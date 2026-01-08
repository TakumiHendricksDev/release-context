from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from typing import Any, Callable, Dict, List, Optional, Tuple

from github_client import GitHubClient


class ParallelFetcher:
    """
    Helper class for parallel API fetching with rate limit awareness.
    Uses a semaphore to limit concurrent requests.
    """

    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self.semaphore = Semaphore(max_concurrent)

    def _fetch_with_semaphore(
        self, func: Callable[[], Any], desc: str = ""
    ) -> Tuple[Any, Optional[Exception]]:
        """Execute a fetch function with semaphore control."""
        self.semaphore.acquire()
        try:
            result = func()
            return result, None
        except Exception as e:
            return None, e
        finally:
            self.semaphore.release()

    def fetch_commit_pr_numbers(
        self,
        client: GitHubClient,
        shas: List[str],
        token: str,
        pr_fetcher: Any,
    ) -> Dict[str, Optional[int]]:
        """
        Batch fetch PR numbers for commits in parallel.
        Returns dict mapping SHA -> PR number (or None).
        """
        results: Dict[str, Optional[int]] = {}

        def fetch_one(sha: str) -> Tuple[str, Optional[int]]:
            def _fetch():
                return pr_fetcher.fetch_commit_pr_number(sha, token)

            result, error = self._fetch_with_semaphore(_fetch, f"commit {sha[:8]}")
            if error:
                print(f"[warn] commit->pulls lookup failed for {sha[:8]}: {error}", file=sys.stderr)
                return sha, None
            return sha, result

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {executor.submit(fetch_one, sha): sha for sha in shas}
            for future in as_completed(futures):
                sha, pr_number = future.result()
                results[sha] = pr_number

        return results

    def fetch_pr_details(
        self,
        client: GitHubClient,
        pr_numbers: List[int],
        token: str,
        pr_fetcher: Any,
    ) -> Dict[int, Dict[str, Any]]:
        """
        Batch fetch PR details in parallel.
        Returns dict mapping PR number -> PR data.
        """
        results: Dict[int, Dict[str, Any]] = {}

        def fetch_one(pr_number: int) -> Tuple[int, Optional[Dict[str, Any]]]:
            def _fetch():
                return pr_fetcher.fetch_pr(pr_number, token)

            result, error = self._fetch_with_semaphore(_fetch, f"PR #{pr_number}")
            if error:
                print(f"[warn] failed to fetch PR #{pr_number}: {error}", file=sys.stderr)
                return pr_number, None
            return pr_number, result

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {executor.submit(fetch_one, pr_num): pr_num for pr_num in pr_numbers}
            for future in as_completed(futures):
                pr_number, pr_data = future.result()
                if pr_data:
                    results[pr_number] = pr_data

        return results

    def fetch_pr_extras(
        self,
        pr_number: int,
        head_sha: Optional[str],
        token: str,
        pr_fetcher: Any,
        include_files: bool,
        include_reviews: bool,
        include_comments: bool,
        include_checks: bool,
        max_files: int,
    ) -> Dict[str, Any]:
        """
        Fetch additional PR data (files, reviews, comments, checks) in parallel.
        Returns dict with keys: files, reviews, comments, check_runs.
        """
        extras: Dict[str, Any] = {}

        def fetch_files() -> Tuple[str, Optional[List[Dict[str, Any]]]]:
            def _fetch():
                return pr_fetcher.fetch_files(pr_number, token, max_files)

            result, error = self._fetch_with_semaphore(_fetch, f"PR #{pr_number} files")
            if error:
                print(f"[warn] failed to fetch files for PR #{pr_number}: {error}", file=sys.stderr)
                return "files", None
            return "files", result

        def fetch_reviews() -> Tuple[str, Optional[List[Dict[str, Any]]]]:
            def _fetch():
                return pr_fetcher.fetch_reviews(pr_number, token)

            result, error = self._fetch_with_semaphore(_fetch, f"PR #{pr_number} reviews")
            if error:
                print(f"[warn] failed to fetch reviews for PR #{pr_number}: {error}", file=sys.stderr)
                return "reviews", None
            return "reviews", result

        def fetch_comments() -> Tuple[str, Optional[List[Dict[str, Any]]]]:
            def _fetch():
                return pr_fetcher.fetch_comments(pr_number, token)

            result, error = self._fetch_with_semaphore(_fetch, f"PR #{pr_number} comments")
            if error:
                print(f"[warn] failed to fetch comments for PR #{pr_number}: {error}", file=sys.stderr)
                return "comments", None
            return "comments", result

        def fetch_checks() -> Tuple[str, Optional[List[Dict[str, Any]]]]:
            if not head_sha:
                return "check_runs", None

            def _fetch():
                return pr_fetcher.fetch_check_runs(pr_number, head_sha, token)

            result, error = self._fetch_with_semaphore(_fetch, f"PR #{pr_number} checks")
            if error:
                print(f"[warn] failed to fetch checks for PR #{pr_number}: {error}", file=sys.stderr)
                return "check_runs", None
            return "check_runs", result

        tasks = []
        if include_files:
            tasks.append(fetch_files)
        if include_reviews:
            tasks.append(fetch_reviews)
        if include_comments:
            tasks.append(fetch_comments)
        if include_checks:
            tasks.append(fetch_checks)

        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {executor.submit(task): task for task in tasks}
            for future in as_completed(futures):
                key, value = future.result()
                if value is not None:
                    extras[key] = value

        return extras
