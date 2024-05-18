// Copyright 2024 The benchmarks Authors
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.
//
// Generates a page that shows the list of benchmark runs and allows
// selecting the ones to compare.

import React, { useMemo, useState } from 'react';
import ReactDOM from 'react-dom';
import 'react-data-grid/lib/styles.css';
import DataGrid, {SelectColumn} from 'react-data-grid';

import {BENCHMARK_SERVICE_ADDRESS, getRunDisplayName} from "./utils.js";

const GITHUB_REPO = "quickwit";
const GITHUB_OWNER = "quickwit-oss";

function rowKeyGetter(row) {
  return row.id;
}

// Expected props:
// `rows`
function RunList(props) {
  let [selected_rows, set_selected_rows] = useState(new Set());
  const [filters, setFilters] = useState({
    id: null,
    verified_email: null,
    timestamp: null,
    run_type: null,
    tag: null,
    track: null,
    engine: null,
    storage: null,
    instance: null,
    short_commit_hash: null,
    source: null,
  });
  const [showFilters, setShowFilters] = useState(false);

  // Fields that accept filtering by regexp. Others will require an
  // exact match.
  const fields_accept_filter_regex = useMemo(() => [
    "verified_email",
    "timestamp",
    "run_type",
    "tag",
    "track",
    "engine",
    "storage",
    "instance",
    "short_commit_hash",
    "source"
  ], []);

  // Just to create the <input>'s for the filters.
  const filter_specs = [
    {name: "ID", placeholder: "Type to filter rows by ID (exact match)", field: "id"},
    {name: "User", placeholder: "Type to filter rows by user (regex)", field: "verified_email"},
    {name: "Timestamp", placeholder: "Type to filter rows by timestamp (regex)", field: "timestamp"},
    {name: "RunType", placeholder: "Type to filter rows by run type (regex)", field: "run_type"},
    {name: "Tags", placeholder: "Type to filter rows by tags (regex)", field: "tag"},
    {name: "Track", placeholder: "Type to filter rows by track (regex)", field: "track"},
    {name: "Engine", placeholder: "Type to filter rows by engine (regex)", field: "engine"},
    {name: "Storage", placeholder: "Type to filter rows by storage (regex)", field: "storage"},
    {name: "Instance", placeholder: "Type to filter rows by instance (regex)", field: "instance"},
    {name: "Commit", placeholder: "Type to filter rows by commit (regex)", field: "short_commit_hash"},
    {name: "Source", placeholder: "Type to filter rows by source (regex)", field: "source"},
  ];
  
  const columns = useMemo(() => {
    return [
      SelectColumn,
      {
	key: 'id',
	name: 'ID',
	renderCell: (props) => {
	  return <a href={props.row.raw_link}>{props.row.id}</a>;
	}
      },
      { key: 'verified_email', name: 'User' },
      { key: 'timestamp', name: 'Timestamp' },
      { key: 'run_type', name: 'Run Type' },
      { key: 'tag', name: 'Tags' },
      { key: 'track', name: 'Track' },
      { key: 'engine', name: 'Engine' },
      { key: 'storage', name: 'Storage' },
      { key: 'instance', name: 'Instance' },
      {
	key: 'short_commit_hash',
	name: 'Commit Hash',
	renderCell: (props) => {
	  if (props.row.engine === "quickwit") {
	    return (
	      <a href={`https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/commit/${props.row.commit_hash}`}>
		{props.row.short_commit_hash}
	      </a>
	    );
	  } else {
	    return props.row.short_commit_hash;
	  }
	}
      },
      {
	key: 'source',
	name: 'Source',
	renderCell: (props) => {
	  if (props.row.github_workflow_run_id != null) {
	    return (
	      <a href={`https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${props.row.github_workflow_run_id}`}>
		{props.row.source}
	      </a>
	    );
	  } else {
	    return props.row.source;
	  }
	}
      },
      {
	key: 'github_pr',
	name: 'GH PR',
	renderCell: (props) => {
	  if (props.row.github_pr != null) {
	    return (
	      <a href={`https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/pull/${props.row.github_pr}`}>
		{props.row.github_pr}
	      </a>
	    );
	  } else {
	    return "";
	  }
	}
      },
//      { key: 'index_uid', name: 'Index UID' },
    ];
  }, []);

  let rows = props.rows;
  const filteredRows = useMemo(() => {
    let filter_regexps = {};
    for (let field of fields_accept_filter_regex) {
      filter_regexps[field] = filters[field] != null ? new RegExp(filters[field]) : null;
    }
    console.log("filtering rows with:", filters, filter_regexps);
    return rows.filter((row) => {
      let ok = true;
      for (let field of Object.keys(filters)) {
	if (filter_regexps.hasOwnProperty(field)) {
	  ok = filter_regexps[field] == null || filter_regexps[field].test(row[field]);
	} else {
	  // We use == on purpose (the filter might be a string and
	  // the row might contain an integer).
	  ok = filters[field] == null || filters[field] == "" || filters[field] == row[field];
	}
	if (!ok) {
	  break;
	}
      }
      return ok;
    });
  }, [rows, filters, fields_accept_filter_regex]);

  let comparison_url = "?run_ids=" + Array.from(selected_rows).join(",");
  return (
    <div>
      <button onClick={() => setShowFilters(!showFilters)}>Toggle filters</button>
      <br/>
      {
	filter_specs.map((filter_spec) => {
	  if (!showFilters) return;
	  return (
            <div class="row">
	      <div class="runlist-column-filter-name">
		{filter_spec.name}:  
	      </div>
	      <div class="runlist-filter">
		<input onChange={e => {
			 let copy = {...filters};
			 copy[filter_spec.field] = e.target.value;
			 setFilters(copy);
		       }}
		       placeholder={filter_spec.placeholder} />
	      </div>
            </div>
          );
        })
      }
      <a class="button" href={comparison_url}>Compare {selected_rows.size} selected runs (indexing runs are automatically selected)</a>
      <DataGrid columns={columns} rows={filteredRows} rowKeyGetter={rowKeyGetter}
		onSelectedRowsChange={set_selected_rows}
		selectedRows={selected_rows}
		className="react-data-grid"
      />
    </div>
  );
  // Breaks rendering:
  // defaultColumnOptions={{resizable: true }}
}

export function showRunList() {
  // By default only show the runs of the last 6 months.
  // TODO: consider adding date pickers.
  const start_ts_s = Date.now() / 1000 - 6 * 30 * 24 * 3600;
  fetch(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/list/?start_timestamp=${start_ts_s}`)
    .then((res) => { return res.json(); })
    .then((resp) => {
      let rows = [];
      for (let run of resp.run_infos) {
	run.raw_link = "?page=raw&run_ids=" + run.id.toString()
	run.short_commit_hash = run.commit_hash?.slice(0, 9);
	rows.push(run);
      }
      let el = document.getElementById("app-container");
      ReactDOM.render(<React.StrictMode>
			<RunList rows={rows}
			/>
		      </React.StrictMode>, el);
    });
}
