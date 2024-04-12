# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

from pydantic import BaseModel, Field
import datetime
import enum


class RunSource(enum.StrEnum):
    # Run manually launched by a real person.
    MANUAL = "manual"
    # Automated run launched by continuous benchmarking.
    CONTINUOUS_BENCHMARKING = "continuous_benchmarking"


class RunInfo(BaseModel):
    # Don't specify this when inserting benchmark runs into the
    # service. The service will set it.
    id: int | None = None
    # Don't specify this when inserting benchmark runs into the
    # service. The service will set it.
    run_type: str | None = None  # 'indexing' or 'search'
    track: str
    engine: str
    storage: str
    instance: str
    tag: str
    # Don't specify this when inserting benchmark runs into the
    # service. The service will set it.
    timestamp: datetime.datetime | None = None
    # Purely informative, not for authentication purposes.
    unsafe_user: str | None = None
    # Email of the user who submitted those results. Verified through
    # oauth2 Google API.
    # Don't specify this when inserting benchmark runs into the
    # service. The service will set it.
    verified_email: str | None = None
    source: RunSource = RunSource.MANUAL


class BuildInfo(BaseModel):
    build_target: str
    commit_date: str
    commit_hash: str
    version: str


class IndexingRunResults(BaseModel):
    index: str | None = None
    build_info: BuildInfo | None = None
    doc_per_second: float | None = None
    indexing_duration_secs: float | None = None
    mb_bytes_per_second: float | None = None
    num_indexed_bytes: int | None = None
    num_indexed_docs: int | None = None
    num_ingested_bytes: int | None = None
    num_splits: int | None = None
    qbench_returncode: int | None = None
    qbench_command_line: str | None = None
    platform_uname: str | None = None
    # Each engine has its own info format.
    engine_info: dict | None = None
    docker_info: dict | None = None
    command_line: str | None = None


class IndexingRun(BaseModel):
    run_info: RunInfo
    run_results: IndexingRunResults


class CreateIndexingRunRequest(BaseModel):
    run: IndexingRun


class ListRunsResponse(BaseModel):
    # For convenient runs are returned from most recent to oldest.
    run_infos: list[RunInfo]


class QueryMeasurements(BaseModel):
    values: list[float]
    min: float
    max: float
    mean: float
    median: float
    stddev: float
    p90: float


class QueryResult(BaseModel):
    name: str
    query: dict  # We don't want to validate that as it can be arbitrary.
    count: int | None = None
    duration: QueryMeasurements
    engine_duration: QueryMeasurements
    total_cpu_time_s: QueryMeasurements | None = None
    object_storage_download_megabytes: QueryMeasurements | None = None
    object_storage_fetch_requests: QueryMeasurements | None = None


class SearchRunResults(BaseModel):
    index: str
    queries: list[QueryResult]
    platform_uname: str | None = None
    # Each engine has its own info format.
    engine_info: dict | None = None
    docker_info: dict | None = None
    command_line: str | None = None


class SearchRun(BaseModel):
    run_info: RunInfo
    run_results: SearchRunResults

class CreateSearchRunRequest(BaseModel):
    run: SearchRun

class GetRunsRequest(BaseModel):
    run_ids: list[int]

class GetRunsResponse(BaseModel):
    runs: list[IndexingRun | SearchRun]


class Timeseries(BaseModel):
    name: str  # Will typically be a query name or "indexing".
    metric_name: str  # E.g. "engine_duration", "total_cpu_time_s", etc..
    # The 3 fields below will have the same number of elements.
    # In service responses, we guarantee that the timestamps are in increasing order.
    timestamps_s: list[int]
    data_points: list[float]
    tags: list[str]


class GetRunsAsTimeseriesResponse(BaseModel):
    timeseries: list[Timeseries]
    