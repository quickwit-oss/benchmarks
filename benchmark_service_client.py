# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

import json
import logging
import pprint
import requests
from service import schemas


class BearerAuthentication(requests.auth.AuthBase):
    """Helper to pass a bearer token as a header with requests."""

    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers["authorization"] = f"Bearer {self.token.strip()}"
        return r


class BenchmarkServiceClient:

    def __init__(self, service_endpoint: str, verify_https: bool = True):
        self.endpoint = service_endpoint
        self._verify_https = verify_https

    def build_url_for_run_ids(self, run_ids: list[int]) -> str:
        """Build an URL for the comparison page for the given `run_ids`."""
        run_ids_str = ",".join([str(id) for id in run_ids])
        return f"{self.endpoint}/?run_ids={run_ids_str}"

    def get_run(self, run_id: int) -> schemas.SearchRun | None:
        uri = f'{self.endpoint}/api/v1/all_runs/get/'
        try:
            response = requests.post(
                uri,
                json={
                    "run_ids": [run_id],
                },
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
                verify=self._verify_https)
            response.raise_for_status()
        except requests.exceptions.RequestException as ex:
            logging.error("Failed to get run %d with %s: %s, %s", run_id, uri, ex, response.content)
            return

        runs = response.json().get("runs")
        if not runs:
            return
        return schemas.SearchRun.model_validate(runs[0])

    def list_runs(self, req: schemas.ListRunsRequest) -> list[schemas.RunInfo] | None:
        uri = f'{self.endpoint}/api/v1/all_runs/list/'
        try:
            response = requests.post(
                uri,
                json=req,
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
                verify=self._verify_https)
            response.raise_for_status()
        except requests.exceptions.RequestException as ex:
            logging.error("Failed to list runs with %s: %s,  %s", uri, ex, response.content)
            return
        return [schemas.RunInfo.model_validate(run_info)
                for run_info in response.json().get("run_infos", [])]

    def export_run(self, run: dict, run_type: str, exporter_token: str) -> schemas.RunInfo | None:
        """Exports a run to the service.

        Args:
          run: expected to follow schemas.CreateIndexingRunRequest or CreateSearchRunRequest.
        """
        uri = f'{self.endpoint}/api/v1/{run_type}_runs/'
        try:
            response = requests.post(
                uri, json=run,
                verify=self._verify_https,
                auth=BearerAuthentication(exporter_token) if exporter_token else None)
        except requests.exceptions.ConnectionError as ex:
            logging.error("Failed to export results to %s: %s", uri, ex)
            return

        if response.status_code != 200:
            resp_content = response.content
            try:
                # Just trying to get the best error message
                resp_content = json.loads(resp_content)
            except json.JSONDecodeError:
                pass
            logging.error(f'Failed exporting results to {uri}: {response} {pprint.pformat(resp_content)}')
            return

        return schemas.RunInfo.model_validate(response.json()["run_info"])

    def check_exporter_token(self, token: str | None) -> bool:
        """Returns true if the token is valid according to the service."""
        endpoint = f'{self.endpoint}/api/v1/check_jwt_token'
        try:
            print(f"Checking JWT token using {endpoint}")
            response = requests.get(endpoint,
                                    verify=self._verify_https,
                                    auth=BearerAuthentication(token) if token else None)
        except requests.exceptions.ConnectionError as ex:
            logging.error("Failed to connect to %s: %s", endpoint, ex)
            return False
        if response.status_code == 404:
            logging.error("Endpoint '%s' not found (404)", endpoint)
            return False
        if response.status_code == 401:
            return False
        if response.status_code != 200:
            logging.error("Unexpected error from endpoint %s: %s", endpoint, response)
            return False
        return True
