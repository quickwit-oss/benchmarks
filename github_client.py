# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

import logging
import requests


class GithubClient:

    def __init__(self,
                 github_owner: str,
                 github_repo: str,
                 endpoint: str = "https://api.github.com"):
        self.owner = github_owner
        self.repo = github_repo
        self.endpoint = f"{endpoint}/repos/{github_owner}/{github_repo}"

    def get_commits(self, start_commit_sha: str) -> list[str] | None:
        """Return the last N GH commits before (and incl.) `start_commit_sha`."""
        try:
            response = requests.get(
                f"{self.endpoint}/commits?sha={start_commit_sha}&per_page=80",
                headers={"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"})
            response.raise_for_status()
        except requests.exceptions.RequestException as ex:
            logging.info("Could not get list of commits from github for owner %s repo %s: %s",
                         self.owner, self.repo, ex)
            return
        commit_shas = []
        # See: https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#list-commits
        # We assume they are in reverse chronological order.
        for commit_response in response.json():
            sha = commit_response.get("sha")
            if sha:
                commit_shas.append(sha)
        return commit_shas

    def get_pull_request_head(self, pull_request_id: str | int) -> str | None:
        """Return the head commit SHA of a pull request."""
        try:
            response = requests.get(
                f"{self.endpoint}/pulls/{pull_request_id}",
                headers={"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"})
            response.raise_for_status()
        except requests.exceptions.RequestException as ex:
            logging.info("Could not pull request info from github for owner %s repo %s: %s",
                         self.owner, self.repo, ex)
            return
        return response.json().get("head", {}).get("sha")

    def get_merge_base(self, commitish_a: str, commitish_b: str) -> str | None:
        """Return the merge-base of two commits.

        The merge-base is the 'best' common ancestor of two
        commits. See 'git help merge-base'

        One commit will typically be the HEAD of a pull request, and
        the other one the HEAD of the 'main' branch. Benc perf
        comparisons should be done between the HEAD of the PR and the
        ancestor commit returned by `get_merge_base`.
        
        Unfortunately, we cannot invoke 'git merge-base' directly on
        the repo, because the actions/checkout github action checks
        out a minimal git repo that does not allow running that
        command.
        """        
        try:
            response = requests.get(
                f"{self.endpoint}/compare/{commitish_a}...{commitish_b}",
                headers={"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"})
            response.raise_for_status()
        except requests.exceptions.RequestException as ex:
            logging.info("Could not compare two github commits for owner %s repo %s: %s",
                         self.owner, self.repo, ex)
            return
        return response.json().get("merge_base_commit", {}).get("sha")
