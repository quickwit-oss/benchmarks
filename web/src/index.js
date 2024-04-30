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
  if (query.duration.length === 0) {
    return { query: query.query, className: "unsupported", unsupported: true, id: query.id }
  }
  let res = stats(query[metric].values.map((x) => x * multiplier));
  res.count = query.count;
  res.query = query.query;
  res.name = query.name;
  return res;
}


class Benchmark extends React.Component {

  // Expects in props:
  // `datasets`
  // `dataset_to_selector_options`
  // `initial_dataset`
  // `initial_selector_options`
  // `initial_search_metric_field`
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
      }
    ];
    this.initial_search_metric = this.search_metrics[0];
    for (let metric of this.search_metrics) {
      if (metric.value.field == this.props.initial_search_metric_field) {
	this.initial_search_metric = metric;
	break;
      }
    }
    this.state = {
      dataset: this.props.initial_dataset,
      // Array of selected runs, i.e. an array of:
      // {indexing: ID of the indexing run, search: ID of the search run}.
      selected_runs: this.props.initial_selector_options.map((opt) => opt.value),
      // Maps display name to {indexing: run_results, search: run_results}
      runs: {},
      // This search metric to display.
      search_metric: this.initial_search_metric,
    };
    this.handleChangeRun(this.props.initial_selector_options);
  }

  handleChangeDataset(evt) {
    let dataset = evt.value;
    if (dataset) {
      this.setState({ "dataset": dataset });
    } else {
      this.setState({ "dataset": null });
    }
    // TODO: clear the run multi-select.
  }

  handleChangeSearchMetric(evt) {
    this.setState({ "search_metric": evt });
//    window.history.pushState(
//      "", "",
//      "?search_metric=" + evt.value.field);
  }

  // get_runs_response: schemas.GetRunsResponse.
  handleGetRunsResponse(get_runs_response) {
    // Maps run display names to {indexing: run, search: run}.
    let name_to_runs = {};
    for (const run of get_runs_response.runs) {
      const name = getRunDisplayName(run.run_info);
      if (!(name in name_to_runs)) {
	name_to_runs[name] = {indexing: null, search: null};
      }
      if (run.run_info.run_type !== "indexing" &&
	  run.run_info.run_type !== "search") {
	console.error("Got run with unexpected type:", run);
	continue;
      }
      name_to_runs[name][run.run_info.run_type] = run;
    }
    this.setState({"runs": name_to_runs});
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
	      body: JSON.stringify({run_ids: all_run_ids.filter(x => x !== null)})
	    })
	.then((res) => { return res.json(); })
	.then((resp) => { this.handleGetRunsResponse(resp); });
    } catch (error) {
      console.error('Error fetching data:', error);
    }
  }
  
  handleChangeRun(evt) {
    // Array of {indexing: numerical run ID, search: numerical run ID}.
    let selected_runs = [];
    for (let run_info of evt) {
      selected_runs.push(run_info.value);
    }
    this.setState({"selected_runs": selected_runs})
    this.fetchRuns(selected_runs);
  }
  
  // TODO: make sure the order of display is the same as the order in
  // which the runs were selected. It's not the case right now (seems
  // lexicographic in the run name).
  generateDataView() {
    // Maps display name to indexing run results, together with extra
    // info computed in this function.
    let engines = {}
    // query_name -> (engine display name -> query stats).
    let queries = {}
    for (let display_name in this.state.runs) {
      // {search: run, indexing: run}.
      let engine_results = this.state.runs[display_name].indexing?.run_results;
      if (engine_results == null) {
	engine_results = {};
      }
      let engine_queries = this.state.runs[display_name].search?.run_results.queries
      if (engine_queries == null) {
	engine_queries = {};
      }
      
      let taggedEngine = display_name;
      engine_queries = Array.from(engine_queries);
      engine_queries = engine_queries.map(
	(query_results) => aggregate(query_results,
				     this.state.search_metric.value.field,
				     this.state.search_metric.value.field_multiplier));
      let metric_average_over_queries = 0
      let unsupported = false
      for (let query of engine_queries) {
	if (query.unsupported) {
	  unsupported = true;
        } else {
          metric_average_over_queries += query.median;
        }
      }
      if (unsupported) {
        metric_average_over_queries = undefined;
      } else {
        metric_average_over_queries = (metric_average_over_queries / engine_queries.length) | 0;
      }
      engines[taggedEngine] = engine_results;
      engines[taggedEngine].indexing_run_info = this.state.runs[display_name].indexing?.run_info;
      engines[taggedEngine].search_run_info = this.state.runs[display_name].search?.run_info;
      engines[taggedEngine].metric_average_over_queries = metric_average_over_queries;
      // Ratios with a similar formula as in the clickhouse benchmark.
      // https://github.com/ClickHouse/ClickBench/?tab=readme-ov-file#results-usage-and-scoreboards
      // Sum(log(ratio)) for each query comparing the current engine to the first engine.
      let sum_log_ratios = 0;
      for (let query of engine_queries) {
        let query_data = {};
        if (queries[query.name] !== undefined) {
          query_data = queries[query.name];
        }
        query_data[taggedEngine] = query
        queries[query.name] = query_data
        let reference_time_micros = Object.values(query_data)[0].median;
        let ratio = 1.0;
        if (Math.abs(reference_time_micros - query.median) >=
	    this.state.search_metric.value.ratio_ignore_diff_lt) {
          ratio = (query.median + this.state.search_metric.value.ratio_num_denum_constant) /
	    (reference_time_micros + this.state.search_metric.value.ratio_num_denum_constant);
        }
        sum_log_ratios += Math.log(ratio);
      }
      const num_queries = Object.keys(engine_queries).length;
      if (num_queries >= 1) {
	engines[taggedEngine].avg_ratios = Math.exp(sum_log_ratios / num_queries);
      } else {
	engines[taggedEngine].avg_ratios = 1;
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
    if (this.state.selected_runs != null) {
      for (let run of this.state.selected_runs) {
	all_run_ids.push(run.indexing);
	all_run_ids.push(run.search);
      }
    }
    window.history.pushState(
      "", "",
      "?run_ids=" + all_run_ids.filter(x => x !== null).map(x => x.toString()).join(",") +
	"&search_metric=" + this.state.search_metric.value.field);
  }

  render() {
    this.setPermalink();
    let data_view = this.generateDataView();
    return <div>
	     <form>
               <fieldset>
		 <label htmlFor="datasetField">Dataset</label>
		 <Select id="datasetField2" options={
			   this.props.datasets.map((ds) => ({value: ds, label: ds}))
			 }
			 defaultValue={({value: this.props.initial_dataset, label: this.props.initial_dataset})}
			 onChange={(evt) => this.handleChangeDataset(evt)}
		 />		 
		 <label>Runs to compare (format: &lt;engine&gt;.&lt;storage&gt;.&lt;instance&gt;.&lt;short_commit_hash&gt;.&lt;tag&gt;)</label>
		 <Select options={this.props.dataset_to_selector_options[this.state.dataset]}
			 isMulti className="basic-multi-select" classNamePrefix="select"
			 defaultValue={this.props.initial_selector_options}
			 onChange={(evt) => this.handleChangeRun(evt)}/>
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
		       let params = new URLSearchParams({
			 page: "raw",
			 run_ids: [kv[1].indexing_run_info?.id, kv[1].search_run_info?.id].filter(x => x != null)
		       });
		       return (<th key={"col-" + engine}><a href={"?" + params}>{engine}</a></th>);
		     })
		   }
		 </tr>
               </thead>
               <tbody>
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
		     return <tr key={query_name}>
			      <td><Display name={query_name} query={engine_queries[ref_engine].query}></Display></td>
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

// `run_info` corresponds to schemas.RunInfo of the service.
// `run_ids_filter` is a Set of integer run ids.
function shouldPreselectRun(opt_track_filter,
			    opt_commit_hash_filter,
			    opt_storage_filter,
			    opt_source_filter,
			    run_ids_filter,
			    run_info) {
  if (!opt_commit_hash_filter &&
      run_ids_filter.size == 0) {
    // Require strong filters to be set to avoid preselecting too many
    // runs.
    return false;
  }
  if (opt_track_filter && opt_track_filter !== run_info.track) {
    return false;
  }
  if (opt_commit_hash_filter && opt_commit_hash_filter !== run_info.commit_hash) {
    return false;
  }
  if (opt_storage_filter && opt_storage_filter !== run_info.storage) {
    return false;
  }
  if (opt_source_filter && opt_source_filter !== run_info.source) {
    return false;
  }
  if (run_ids_filter.size > 0 && !run_ids_filter.has(run_info.id)) {
    return false;
  }
  return true;
}

function getMostRecentSelectedRun(left, right) {
  if (left === null) return right;
  if (left.preselected != right.preselected) {
    return left.preselected ? left : right;
  }
  return left.timestamp < right.timestamp ? right : left;
}

// `run_ids_filter` is a Set of integer run ids.
function showComparison(opt_track_filter,
			opt_commit_hash_filter,
			opt_storage_filter,
			opt_source_filter,
			run_ids_filter,
			initial_search_metric_field) {
  console.log("showComparison with filters:",
	      opt_track_filter,
	      opt_commit_hash_filter,
	      opt_storage_filter,
	      opt_source_filter,
	      run_ids_filter,
	      initial_search_metric_field);
  // TODO: consider only fetching the last N runs, or runs in the last
  // D days (but ideally, we should make sure to filter the list of
  // run IDs provided even if they are old).
  $.getJSON(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/list/`, (list_runs_resp) => {
    // Maps dataset name to:
    // {display_name -> {indexing: most recent indexing run,
    //                   search: most recent search run}
    // }
    let dataset_to_most_recent_runs = {};
    for (const run_info of list_runs_resp.run_infos) {
      let dataset = run_info.track;
      let display_name = getRunDisplayName(run_info);
      run_info.preselected = shouldPreselectRun(
	opt_track_filter,
	opt_commit_hash_filter,
	opt_storage_filter,
	opt_source_filter,
	run_ids_filter,
	run_info);
      if (!(dataset in dataset_to_most_recent_runs)) {
	dataset_to_most_recent_runs[dataset] = {};
      }
      let most_recent_runs = dataset_to_most_recent_runs[dataset];
      if (!(display_name in most_recent_runs)) {
	most_recent_runs[display_name] = {
	  indexing: null,
	  search: null
	};
      }
      let previous_run_info = most_recent_runs[display_name][run_info.run_type];
      most_recent_runs[display_name][run_info.run_type] = getMostRecentSelectedRun(previous_run_info, run_info);
    }
    let initial_dataset = null;
    // Pick the latest for each combination.
    // Map from dataset name to an array of
    // {value: {indexing: ID of the indexing run, search: ID of the search run}, label: display name of the run}
    let dataset_to_selector_options = {};
    // Array of preselected selector options.
    let initial_selector_options = [];
    for (const dataset in dataset_to_most_recent_runs) {
      if (!(dataset in dataset_to_selector_options)) {
	dataset_to_selector_options[dataset] = [];
      }
      for (const display_name in dataset_to_most_recent_runs[dataset]) {
	const search_run_info = dataset_to_most_recent_runs[dataset][display_name].search;
	const indexing_run_info = dataset_to_most_recent_runs[dataset][display_name].indexing;
	let preselected = false;
	if (indexing_run_info !== null && indexing_run_info.preselected) {
	  preselected = true;
	}
	if (search_run_info !== null && search_run_info.preselected) {
	  preselected = true;
	}
	const selector_option = {
	  value: {
	    indexing: indexing_run_info === null ? null : indexing_run_info.id,
	    search: search_run_info === null ? null : search_run_info.id
	  },
	  label: display_name
	};
	dataset_to_selector_options[dataset].push(selector_option);
	if (preselected) {
	  initial_selector_options.push(selector_option);
	  initial_dataset = dataset;
	}
      }
    }
    
    const datasets = Object.keys(dataset_to_most_recent_runs).sort();
    if (initial_dataset === null) {
      initial_dataset = datasets[0];
    }
    let el = document.getElementById("app-container");
    console.log("Initial rendering of Benchmark react elmt");

    ReactDOM.render(<React.StrictMode>
		      <Benchmark datasets={datasets} initial_dataset={initial_dataset}
				 dataset_to_selector_options={dataset_to_selector_options}
				 initial_selector_options={initial_selector_options}
				 initial_search_metric_field={initial_search_metric_field}
		      />
		    </React.StrictMode>, el);
  });
}

// TODO: make it work with local jsons as well if needed. Doable (just
// have a global param 'local_json')
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

  showComparison(searchParams.get("track"),
		 searchParams.get("commit_hash"),
		 searchParams.get("storage"),
		 searchParams.get("source"),
		 new Set(searchParams.get("run_ids")?.split(",")?.map((s) => parseInt(s))),
		 searchParams.get("search_metric"));
});

// If you want your app to work offline and load faster, you can change
// unregister() to register() below. Note this comes with some pitfalls.
// Learn more about service workers: https://bit.ly/CRA-PWA
serviceWorker.unregister();
