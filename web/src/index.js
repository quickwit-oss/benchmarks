import React from 'react';
import Select from 'react-select';
import $ from 'jquery';
import { JsonView, defaultStyles } from 'react-json-view-lite';
import 'react-json-view-lite/dist/index.css';
import ReactDOM from 'react-dom';
import './style.css'
import * as serviceWorker from './serviceWorker';
import {showContinuousGraphs} from "./graphs.js";
import {BENCHMARK_SERVICE_ADDRESS, getRunDisplayName} from "./utils.js";
import {showRunList} from "./runlist.js";

function formatPercentVariation(p) {
  if (p !== undefined) {
    return "+" + (p * 100).toFixed(1) + " %";
  } else {
    return "";
  }
}

function numberWithCommas(x, max_fraction_digits=2) {
  return x.toLocaleString("en-US", {maximumFractionDigits: max_fraction_digits});
}

function stats(timings) {
  let median = timings[(timings.length / 2) | 0];
  let mean = timings.reduce(((pv, cv) => pv + cv), 0) / timings.length;
  let p90 = timings[(timings.length * 0.9) | 0];
  return {
    "median": median,
    "mean": mean,
    "min": timings[0],
    "max": timings[timings.length - 1],
    "p90": p90,
  };
}

function aggregate(query, metric="engine_duration", multiplier=1.) {
  if (!query.hasOwnProperty(metric) ||
      query[metric] == null ||
      query[metric].values == null ||
      query[metric].values.length === 0) {
    return {
      name: query.name,
      query: query.query,
      className: "unsupported",
      unsupported: true
    }
  }
  let res = stats(query[metric].values.map((x) => x * multiplier));
  res.count = query.count;
  res.query = query.query;
  res.name = query.name;
  return res;
}


class Benchmark extends React.Component {

  // Expects in props:
  // `initial_search_metric_field`
  // `ids_to_display`, a list of {indexing: run_id or null, search: run_id or null} to display.
  constructor(props) {
    super(props);
    this.search_metrics = [
      {
	label: "Search Latency",
	value: {
	  field: "engine_duration",
	  // Microseconds -> milliseconds.
	  field_multiplier: 1. / 1000.,
	  unit: "ms",
	  // For the ratio computations, ignore diffs (after the
	  // field_multiplier has been applied) less than this number.
	  ratio_ignore_diff_lt: 3,
	  // Add this value to the numerator and denominator of the
	  // ratio computation (after the field_multiplier has been
	  // applied), not to have huge ratios for small values.
	  ratio_num_denum_constant: 10
	}
      },
      {
	label: "Search CPU time",
	value: {
	  field: "total_cpu_time_s",
	  // Seconds -> milliseconds.
	  field_multiplier: 1000.,
	  unit: "ms",
	  ratio_ignore_diff_lt: 3,
	  ratio_num_denum_constant: 10
	},
      },
      {
	label: "Search object storage GETs",
	value: {
	  field: "object_storage_fetch_requests",
	  field_multiplier: 1,
	  unit: "",
	  ratio_ignore_diff_lt: 0,
	  ratio_num_denum_constant: 5
	}
      },
      {
	label: "Search object storage download MBs",
	value: {
	  field: "object_storage_download_megabytes",
	  field_multiplier: 1,
	  unit: "MB",
	  ratio_ignore_diff_lt: 10,
	  ratio_num_denum_constant: 10
	}
      },
      {
	label: "Peak resident memory usage (caveats)",
	value: {
	  field: "peak_memory_megabytes",
	  field_multiplier: 1.,
	  unit: "MB",
	  ratio_ignore_diff_lt: 3,
	  ratio_num_denum_constant: 32
	},
      }
    ];
    this.initial_search_metric = this.search_metrics[0];
    for (let metric of this.search_metrics) {
      if (metric.value.field === this.props.initial_search_metric_field) {
	this.initial_search_metric = metric;
	break;
      }
    }
    this.state = {
      // Maps opaque unique IDs to run pairs to display: {indexing:
      // run, search: run}.
      runs: {},
      // This search metric to display.
      search_metric: this.initial_search_metric,
      // The set of queries for which the checkbox is checked.
      checked_queries: null,
    };
    this.fetchRuns(this.props.ids_to_display);
  }

  handleChangeSearchMetric(evt) {
    this.setState({ "search_metric": evt });
  }

  // get_runs_response: schemas.GetRunsResponse.
  // run_ids: Array of {indexing: numerical run ID, search: numerical run ID}.
  // Sets the `runs` state to a map from opaque unique IDs to a run
  // pairs to display: {indexing: run, search: run}.
  handleGetRunsResponse(get_runs_response, run_ids) {
    let id_to_run = {};
    for (const run of get_runs_response.runs) {
      id_to_run[run.run_info.id] = run;
    }
    let runs = {};
    for (let id_pair of run_ids) {
      let run_pair = {indexing: null, search: null};
      let id = null;
      if (id_pair.search != null) {
	id = id_pair.search;
	run_pair.search = id_to_run[id_pair.search];
      }
      if (id_pair.indexing != null) {
	if (id === null) {
	  id = id_pair.indexing;
	}
	run_pair.indexing = id_to_run[id_pair.indexing];
      }
      runs[id] = run_pair;
    }
    let all_query_names = new Set();
    for (let run_id in runs) {
      let queries = runs[run_id].search?.run_results?.queries;
      if (!queries) {
	continue;
      }
      for (let q of Array.from(queries)) {
	all_query_names.add(q.name);
      }
    }
    this.setState({"runs": runs,
		   "checked_queries": all_query_names});
  }
  
  // run_ids: Array of {indexing: numerical run ID, search: numerical run ID}.
  fetchRuns(run_ids) {
    let all_run_ids = [];
    for (const {indexing, search} of run_ids) {
      all_run_ids.push(indexing);
      all_run_ids.push(search);
    }
    try {
      fetch(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/get/`,
	    {
	      method: "POST",
	      headers: {
		"Content-Type": "application/json",
	      },
	      body: JSON.stringify({run_ids: all_run_ids.filter(x => x != null)})
	    })
	.then((res) => { return res.json(); })
	.then((resp) => { this.handleGetRunsResponse(resp, run_ids); });
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }

  generateDataView() {
    // Maps opaque run IDs to indexing run results, together with extra
    // info computed in this function.
    let engines = {}
    // query_name -> (run ID -> query stats).
    let queries = {}
    let reference_engine = null;
    if (Object.keys(this.state.runs).length > 0) {
      reference_engine = Object.keys(this.state.runs)[0];
    }
    for (let run_id in this.state.runs) {
      // {search: run, indexing: run}.
      let run_pair = this.state.runs[run_id];
      let engine_results = run_pair.indexing?.run_results;
      if (engine_results == null) {
	engine_results = {};
      }
      let engine_queries = run_pair.search?.run_results.queries
      if (engine_queries == null) {
	engine_queries = {};
      }
      
      engine_queries = Array.from(engine_queries);
      engine_queries = engine_queries.map(
	(query_results) => aggregate(query_results,
				     this.state.search_metric.value.field,
				     this.state.search_metric.value.field_multiplier));
      let metric_average_over_queries = 0
      let unsupported = false
      let num_selected_queries = 0;
      for (let query of engine_queries) {
	if (query.unsupported) {
	  unsupported = true;
        } else if (this.state.checked_queries.has(query.name)) {
          metric_average_over_queries += query.median;
	  num_selected_queries += 1;
        }
      }
      if (unsupported) {
        metric_average_over_queries = undefined;
      } else {
        metric_average_over_queries = (metric_average_over_queries / num_selected_queries) | 0;
      }
      engines[run_id] = engine_results;
      engines[run_id].indexing_run_info = run_pair.indexing?.run_info;
      engines[run_id].search_run_info = run_pair.search?.run_info;
      engines[run_id].metric_average_over_queries = metric_average_over_queries;
      // Ratios with a similar formula as in the clickhouse benchmark.
      // https://github.com/ClickHouse/ClickBench/?tab=readme-ov-file#results-usage-and-scoreboards
      // Sum(log(ratio)) for each query comparing the current engine to the first engine.
      // If this computation (or its parameters) change,
      // `compare_runs()` in run.py should be updated.
      let sum_log_ratios = 0;
      let ratio_has_unsupported = false;
      for (let query of engine_queries) {
	if (!(query.name in queries)) {
	  queries[query.name] = {};
	}
	let query_data = queries[query.name];
        query_data[run_id] = query
	if (query.unsupported ||
	    !(reference_engine in query_data) ||
	    query_data[reference_engine].unsupported) {
	  ratio_has_unsupported = true;
	  continue;
	}
	if (!this.state.checked_queries.has(query.name)) {
	  continue;
	}
	let reference_metric = query_data[reference_engine].median;
        let ratio = 1.0;
        if (Math.abs(reference_metric - query.median) >=
	    this.state.search_metric.value.ratio_ignore_diff_lt) {
          ratio = (query.median + this.state.search_metric.value.ratio_num_denum_constant) /
	    (reference_metric + this.state.search_metric.value.ratio_num_denum_constant);
        }
        sum_log_ratios += Math.log(ratio);
      }
      if (ratio_has_unsupported || num_selected_queries === 0) {
	engines[run_id].avg_ratios = null;
      } else {
	engines[run_id].avg_ratios = Math.exp(sum_log_ratios / num_selected_queries);
      }
    }

    for (let query in queries) {
      let query_data = queries[query];

      // Filter out unsupported engines
      let supportedEngines = Object.keys(query_data).filter(engine => !query_data[engine].unsupported);

      // Determine minimum and maximum times
      let min_microsecs = Math.min(...supportedEngines.map(engine => query_data[engine].median));
      let max_microsecs = Math.max(...supportedEngines.map(engine => query_data[engine].median));

      // Mark engines as fastest and slowest, and calculate variation
      supportedEngines.forEach(engine => {
        let engine_data = query_data[engine];
        if (engine_data.median === min_microsecs) {
          engine_data.className = "fastest";
        } else if (engine_data.median === max_microsecs) {
          engine_data.className = "slowest";
        } 

        if (engine_data.median !== min_microsecs) {
          // Calculate variation for engines that are not fastest
          engine_data.variation = (engine_data.median - min_microsecs) / min_microsecs;
        }
      });
    }
    return { engines, queries };
  }

  // Updates the URL so that it becomes a permalink.
  setPermalink() {
    let all_run_ids = [];
    // Apparently buggy
    if (this.state.runs != null) {
      for (let id in this.state.runs) {
	all_run_ids.push(this.state.runs[id].indexing?.run_info.id);
	all_run_ids.push(this.state.runs[id].search?.run_info.id);
      }
    }
    window.history.pushState(
      "", "",
      "?run_ids=" + all_run_ids.filter(x => x != null).map(x => x.toString()).join(",") +
	"&search_metric=" + this.state.search_metric.value.field);
  }

  handleQueryCheckboxChange(evt) {
    const query_name = evt.target.id;
    if (this.state.checked_queries.has(query_name)) {
      this.state.checked_queries.delete(query_name);
    } else {
      this.state.checked_queries.add(query_name);
    }
    // Yes, this is needed to trigger rendering.
    this.setState({"checked_queries": this.state.checked_queries});
  }
  
  render() {
    this.setPermalink();
    let data_view = this.generateDataView();
    return <div>
	     <form>
               <fieldset>
		 <label>Search metrics to compare</label>
		 <Select options={this.search_metrics}
			 defaultValue={this.initial_search_metric}
			 onChange={(evt) => this.handleChangeSearchMetric(evt)}/>
               </fieldset>
	     </form>
	     <hr />
	     <table>
               <thead>
		 <tr>
		   <th></th>
		   {
		     Object.entries(data_view.engines).map((kv) => {
		       let engine = kv[0];
		       let display_name = "Unknown";
		       if (kv[1].indexing_run_info) {
			 display_name = getRunDisplayName(kv[1].indexing_run_info);
		       } else if (kv[1].search_run_info) {
			 display_name = getRunDisplayName(kv[1].search_run_info);
		       }
		       let params = new URLSearchParams({
			 page: "raw",
			 run_ids: [kv[1].indexing_run_info?.id, kv[1].search_run_info?.id].filter(x => x != null)
		       });
		       return (<th key={"col-" + engine}><a href={"?" + params}>{display_name}</a></th>);
		     })
		   }
		 </tr>
               </thead>
               <tbody>
		 <tr>
		   <td>Dataset</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let track = kv[1].indexing_run_info?.track;
		       if (!track) {
			 track = kv[1].search_run_info?.track;
		       }
                       if (track !== undefined) {
			 return <td key={"result-" + engine}>
				  {track}
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Indexing run timestamp</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_run_ts = kv[1].indexing_run_info?.timestamp;
                       if (engine_run_ts !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_run_ts}
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Indexing time</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].indexing_duration_secs?.toFixed(2);
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_stats} s
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Indexing CPU time</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].total_cpu_time_s?.toFixed(2);
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_stats} s
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Indexing peak resident memory usage</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].peak_memory_megabytes?.toFixed(0);
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_stats} MB
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Indexing throughput</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].megabytes_per_second?.toFixed(2);
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_stats} MB/s
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Index size</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
		       if ("num_indexed_bytes" in kv[1]) {
			 let engine_stats = (kv[1].num_indexed_bytes / 1000000000).toFixed(2);
			 return <td key={"result-" + engine}>
				  {engine_stats} GB
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Number of documents</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
		       if ("num_indexed_docs" in kv[1]) {
			 let engine_stats = (kv[1].num_indexed_docs / 1000000).toFixed(2);
			 return <td key={"result-" + engine}>
				  {engine_stats} M
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Number of splits/segments</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].num_splits;
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_stats}
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Docs per sec</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
		       if ("num_indexed_docs" in kv[1] && "indexing_duration_secs" in kv[1]) {
			 let engine_stats = (kv[1].num_indexed_docs / 1000 / kv[1].indexing_duration_secs).toFixed(2);
			 return <td key={"result-" + engine}>
				  {engine_stats} K docs/s
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr className="average-row">
		   <td>{this.state.search_metric.label} AVERAGE</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].metric_average_over_queries;
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {numberWithCommas(engine_stats)} {this.state.search_metric.value.unit}
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Some Unsupported Queries
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr className="average-row">
		   <td>Geometric average of {this.state.search_metric.label} ratios</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_stats = kv[1].avg_ratios?.toFixed(2);
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_stats}x
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Some Unsupported Queries
				</td>;
                       }
		     })
		   }
		 </tr>
		 <tr>
		   <td>Search run timestamp</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       let engine = kv[0];
                       let engine_run_ts = kv[1].search_run_info?.timestamp;
                       if (engine_run_ts !== undefined) {
			 return <td key={"result-" + engine}>
				  {engine_run_ts}
				</td>;
                       } else {
			 return <td key={"result-" + engine}>
				  Unknown
				</td>;
                       }
		     })
		   }
		 </tr>
		 {
		   Object.entries(data_view.queries).map(kv => {
		     let query_name = kv[0];
		     let engine_queries = kv[1];
		     let ref_engine = Object.keys(engine_queries)[0];
		     return (
		       <tr key={query_name}>
			 <td>
			   <Display name={query_name} query={engine_queries[ref_engine].query}></Display>
			   <input type="checkbox" id={query_name}
				  checked={this.state.checked_queries.has(query_name)}
				  onChange={this.handleQueryCheckboxChange.bind(this)}
				  title="Include query in aggregated stats?"
			   />
			 </td>
			 {
			   Object.keys(data_view.engines).map(engine => {
			     let cell_data = engine_queries[engine];
			     if (cell_data == null || cell_data.unsupported) {
			       return <td key={query_name + engine} className={"data"}></td>;
			     } else {
			       return <td key={query_name + engine} className={"data " + cell_data.className}>
					<div className="timing">{numberWithCommas(cell_data.median)} {this.state.search_metric.value.unit}</div>
					<div className="timing-variation">{formatPercentVariation(cell_data.variation)}</div>
					<div className="count">{numberWithCommas(cell_data.count)} docs</div>
				      </td>;
			     }
			   })
			 }
		       </tr>
		     );
		   })
		 }
               </tbody>
	     </table>
	   </div>;
  }
}

class Display extends React.Component {
  constructor(props) {
    super(props);
  }

  render() {
    let query = this.props.query;
    let name = this.props.name;
    if (query.start_timestamp !== undefined) {

    }
    // if (query.aggs !== undefined) {
    //   let agg_name = Object.keys(query.aggs)[0];
    //   return <div>Aggregation "{agg_name}" - Search "{query.query}" <DisplayTimestampFilter query={query}/></div>
    // }
    return <div className="note">
             {name}
             <div className="tooltip tooltip-query"> {JSON.stringify(query, null, 2)} </div>
             <DisplayTimestampFilter query={query}/>
           </div>
  }
}

class DisplayTimestampFilter extends React.Component {
  constructor(props) {
    super(props);
  }
  render() {
    let query = this.props.query;
    if (query.start_timestamp !== undefined) {
      let start_date = new Date(query.start_timestamp * 1000).toISOString().substring(0, 10);
      let end_date = new Date(query.end_timestamp * 1000).toISOString().substring(0, 10);
      return <><br/><small>from {start_date} to {end_date}</small></>
    } else {
      return <></>
    }
  }
}

// run_ids: List of run IDs (int) to show.
function showRaw(run_ids) {
  fetch(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/get/`,
	{
	  method: "POST",
	  headers: {
	    "Content-Type": "application/json",
	  },
	  body: JSON.stringify({run_ids: run_ids})
	})
    .then((res) => { return res.json(); })
    .then((resp) => {
      ReactDOM.render(<React.StrictMode>
			<JsonView data={resp} style={defaultStyles}/>
		      </React.StrictMode>,
		      document.getElementById("app-container"));
    });
}

function getMostRecentSelectedRun(left, right) {
  if (left == null) return right;
  if (left.preselected !== right.preselected) {
    return left.preselected ? left : right;
  }
  return left.timestamp < right.timestamp ? right : left;
}

// From a list of RunInfos and of selected run IDs, computes the list
// of runs that should be displayed and matches the indexing and search
// runs that should be displayed together.
// Matching indexing and search runs is a bit tricky, since we might
// not have have an ID linking a search run to the corresponding
// indexing run, and besides, one indexing run can correspond to many
// search runs.
// Here is how the heuristic works:
// - For each search run selected, we match it with:
//   * If it has an index UID (runs after commit 0996acf on engines
//     that support it), to the indexing run with the same index UID
//     and same relevant run_info fields (see
//     `build_full_run_name`). There should not be multiple matching
//     indexing runs, but for robustness, if there are multiple, we
//     pick the most recent one among those whose IDs are in
//     `selected_run_ids` (or among all of them if none is in
//     `selected_run_ids`).
//   * If it does not have an index UID, to the indexing run with the
//     same relevant run_info fields (see `build_run_name`). There can
//     be multiple indexing runs matching (e.g. users ran multiple
//     indexing benchmarks with the same run_info fields), in which
//     case, we pick the most recent one among those whose IDs are
//     in `selected_run_ids` (or among all of them if none is in
//     `selected_run_ids`).
// - Indexing runs whose IDs are in `selected_run_ids` and that are
//   not matched to a selected search runs are displayed
//   independently.
// Note that an indexing run can be showed multiple times if it
// corresponds to multiple selected search runs.
//
// Args:
//   - run_infos: List of schemas.RunInfo.
//   - selected_run_ids: List of selected numerical run IDs.
// Returns:
//   A list of {indexing: run_id or null, search: run_id or null} to display.
function assemble_runs_to_display(run_infos, selected_run_ids) {
  let build_run_name = function(run_info) {
    return `${run_info.track}.${run_info.engine}.${run_info.storage}.${run_info.instance}.${run_info.tag}.${run_info.commit_hash}.${run_info.commit_hash}.${run_info.verified_email}.${run_info.source}`;
  };
  let build_full_run_name = function(run_info) {
    return `${run_info.track}.${run_info.engine}.${run_info.storage}.${run_info.instance}.${run_info.tag}.${run_info.commit_hash}.${run_info.commit_hash}.${run_info.verified_email}.${run_info.source}.${run_info.index_uid}`;
  };

  let selected_run_ids_set = new Set(selected_run_ids);
  let id_to_run_info = {};
  // Name to most recent run (among the selected ones if some are
  // selected). Only for indexing runs.
  let name_to_run_info = {};
  // Full name to most recent run (among the selected ones if some are
  // selected). Only for indexing runs.
  let full_name_to_run_info = {};
  for (let run_info of run_infos) {
    id_to_run_info[run_info.id] = run_info;
    if (run_info.run_type !== "indexing") {
      continue;
    }
    run_info.preselected = selected_run_ids_set.has(run_info.id);
    let name = build_run_name(run_info);
    name_to_run_info[name] = getMostRecentSelectedRun(name_to_run_info[name], run_info);
    let full_name = build_full_run_name(run_info);
    full_name_to_run_info[full_name] = getMostRecentSelectedRun(full_name_to_run_info[full_name], run_info);
  }
  console.debug("name_to_run_info:", name_to_run_info);
  console.debug("full_name_to_run_info:", full_name_to_run_info);

  // List of {indexing: run_id or null, search: run_id or null} to display.
  let to_display = [];
  for (let run_id of selected_run_ids) {
    const run_info = id_to_run_info[run_id];
    if (run_info.run_type !== "search") {
      continue;
    }
    // Now, we use an heuristic to find a corresponding indexing run
    // of this search run.
    if (run_info.index_uid != null) {
      // Use the index_uid to find a corresponding indexing run.
      to_display.push({
	indexing: full_name_to_run_info[build_full_run_name(run_info)]?.id,
	search: run_info.id
      });
    } else {
      // We don't have an index_uid (legacy run, or the engine does
      // not provide one, e.g. Loki). We don't use it, so matching is
      // more error-prone.
      to_display.push({
	indexing: name_to_run_info[build_run_name(run_info)]?.id,
	search: run_info.id
      });
    }
  }

  // We also need to add the selected indexing runs that were not
  // matched to search runs.
  let matched_indexing_ids = new Set(to_display.map((id_pair) => id_pair.indexing));

  for (let run_info of run_infos) {   
    if (run_info.run_type === "indexing" &&
	run_info.preselected &&
	!matched_indexing_ids.has(run_info.id)) {
      to_display.push({indexing: run_info.id, search: null});
    }
  }
  return to_display;
}

// `run_ids_filter` is a Set of integer run ids.
function showComparison(run_ids_filter,
			initial_search_metric_field) {
  console.log("showComparison with filters:",
	      run_ids_filter,
	      initial_search_metric_field);
  document.getElementById("body").style.maxWidth = "1500px";

  fetch(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/list/`)
    .then((res) => res.json())
    .then((list_runs_resp) => {
      const ids_to_display = assemble_runs_to_display(
	list_runs_resp.run_infos, run_ids_filter)
      console.log("Runs to compare:", ids_to_display);

      let el = document.getElementById("app-container");
      console.log("Initial rendering of Benchmark react elmt");

      ReactDOM.render(<React.StrictMode>
			<Benchmark initial_search_metric_field={initial_search_metric_field}
				   ids_to_display={ids_to_display}
			/>
		      </React.StrictMode>, el);
    });
}

$(function () {

  let searchParams = new URLSearchParams(window.location.search)
  if (searchParams.get("page") === "graphs") {
    console.log("Showing graphs");
    showContinuousGraphs(searchParams.get("track"),
			 searchParams.get("run_filter_display_name"));
    return;
  }

  if (searchParams.get("page") === "raw") {
    console.log("Showing raw");
    showRaw(searchParams.get("run_ids")?.split(","));
    return;
  }

  if (searchParams.get("page") === "runlist" ||
      (!searchParams.get("page") && !searchParams.get("run_ids"))) {
    console.log("Showing list of runs");
    showRunList();
    return;
  }
  // It would have been nicer to have this under "page=comparison",
  // but we don't want to break old permalinks.
  showComparison(new Set(searchParams.get("run_ids")?.split(",")?.map((s) => parseInt(s))),
		 searchParams.get("search_metric"));
});

// If you want your app to work offline and load faster, you can change
// unregister() to register() below. Note this comes with some pitfalls.
// Learn more about service workers: https://bit.ly/CRA-PWA
serviceWorker.unregister();
