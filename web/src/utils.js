// Copyright 2024 The benchmarks Authors
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

// Only useful when the Benchmark Service REST API has not the same address as the web pages.
export const BENCHMARK_SERVICE_ADDRESS = "";

// Does not include the track on purpose, because there is a specific
// selector in the UI for that.
// run_info: schemas.RunInfo
export function getRunDisplayName(run_info, include_hash=true) {
  let display_name = run_info.engine + "." + run_info.storage + "." + run_info.instance;
  if (include_hash && run_info.commit_hash != null) {
    display_name += "." + run_info.commit_hash.substring(0, 8);
  }
  display_name += "." + run_info.tag;
  return display_name;
}

