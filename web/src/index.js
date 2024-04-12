import React from 'react';
import { useEffect } from 'react';
import Select from 'react-select';
import $ from 'jquery';
import ReactJson from 'react-json-view'
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

function numberWithCommas(x) {
  x = x.toString();
  let pattern = /(-?\d+)(\d{3})/
  while (pattern.test(x)) {
    x = x.replace(pattern, "$1,$2");
  }
  return x;
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
    "p90": median,
  };
}

function aggregate(query) {
  if (query.duration.length === 0) {
    return { query: query.query, className: "unsupported", unsupported: true, id: query.id }
  }
  // TODO: support other metrics (total_cpu_time_s, etc.).
  var res = stats(query.engine_duration.values);
  res.count = query.count;
  res.query = query.query;
  res.name = query.name;
  return res;
}


class Benchmark extends React.Component {

  // Expects in props:
  // `datasets`
  // `dataset_to_selector_options`
  constructor(props) {
    super(props);
    this.state = {
      tag: null, // TODO: remove.
      dataset: props.datasets[0],
      // List of selected runs, i.e. a list of:
      // {indexing: ID of the indexing run, search: ID of the search run}.
      selected_runs: null,
      // Maps display name to {indexing: run_results, search: run_results}
      runs: {}
    };
  }

  handleChangeTag(evt) {
    var tag = evt.target.value;
    if (tag === "ALL") {
      this.setState({ "tag": null });
    } else {
      this.setState({ "tag": tag });
    }
  }

  handleChangeDataset(evt) {
    var dataset = evt.target.value;
    if (dataset) {
      this.setState({ "dataset": dataset });
    } else {
      this.setState({ "dataset": null });
    }
    // TODO: clear the run multi-select.
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
      if (run.run_info.run_type != "indexing" &&
	  run.run_info.run_type != "search") {
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
    var selected_runs = [];
    for (var run_info of evt) {
      selected_runs.push(run_info.value);
    }
    this.setState({"selected_runs": selected_runs})
    this.fetchRuns(selected_runs);
  }
  
  filterQueries(queries) {
    let tag = this.state.tag;
    if (tag !== undefined && tag !== null) {
      // return queries.filter(query => query.tags.indexOf(tag) >= 0);
      return queries;
    } else {
      return queries;
    }
  }

  // TODO: make sure the order of display is the same as the order in
  // which the runs were selected. It's not the case right now (seems
  // lexicographic in the run name).
  generateDataView() {
    // Maps display name to indexing run results, together with extra
    // info computed in this function.
    var engines = {}
    // query_name -> (engine display name -> query stats).
    var queries = {}
    let dataset = this.state.dataset;
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
      
      // var engine_queries = engine_results.queries;
      let taggedEngine = display_name;
      engine_queries = Array.from(this.filterQueries(engine_queries));
      engine_queries = engine_queries.map(aggregate);
      var total = 0
      var unsupported = false
      for (var query of engine_queries) {
	if (query.unsupported) {
	  unsupported = true;
        } else {
          total += query.p90;
        }
      }
      if (unsupported) {
        total = undefined;
      } else {
        total = (total / engine_queries.length) | 0;
      }
      engines[taggedEngine] = engine_results;
      engines[taggedEngine].indexing_run_id = this.state.runs[display_name].indexing?.run_info.id;
      engines[taggedEngine].search_run_id = this.state.runs[display_name].search?.run_info.id;
      engines[taggedEngine].total = total;
      // Ratios with a similar formula as in the clickhouse benchmark.
      // https://github.com/ClickHouse/ClickBench/?tab=readme-ov-file#results-usage-and-scoreboards
      // Sum(log(ratio)) for each query comparing the current engine to the first engine.
      var sum_log_ratios = 0;
      for (let query of engine_queries) {
        var query_data = {};
        if (queries[query.name] !== undefined) {
          query_data = queries[query.name];
        }
        query_data[taggedEngine] = query
        queries[query.name] = query_data
        var reference_time_micros = Object.values(query_data)[0].p90;
        var ratio = 1.0;
        if (Math.abs(reference_time_micros - query.p90) >= 3 * 1000) {
          ratio = (query.p90 + 10 * 1000) / (reference_time_micros + 10 * 1000);
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
      let min_microsecs = Math.min(...supportedEngines.map(engine => query_data[engine].p90));
      let max_microsecs = Math.max(...supportedEngines.map(engine => query_data[engine].p90));

      // Mark engines as fastest and slowest, and calculate variation
      supportedEngines.forEach(engine => {
        let engine_data = query_data[engine];
        if (engine_data.p90 === min_microsecs) {
          engine_data.className = "fastest";
        } else if (engine_data.p90 === max_microsecs) {
          engine_data.className = "slowest";
        } 

        if (engine_data.p90 !== min_microsecs) {
          // Calculate variation for engines that are not fastest
          engine_data.variation = (engine_data.p90 - min_microsecs) / min_microsecs;
        }
      });
    }
    return { engines, queries };
  }

  render() {
    var data_view = this.generateDataView();
    return <div>
	     <form>
               <fieldset>
		 <label htmlFor="datasetField">Dataset</label>
		 <select id="datasetField" onChange={(evt) => this.handleChangeDataset(evt)}>
		   {this.props.datasets.map((dataset) => <option value={dataset} key={dataset}>{dataset}</option>)}
		 </select>
		 <label htmlFor="queryTagField">Type of Query</label>
		 <select id="queryTagField" onChange={(evt) => this.handleChangeTag(evt)}>
		   <option value="ALL" key="all">ALL</option>
		   {this.props.tags.map((tag) => <option value={tag} key={tag}>{tag}</option>)}
		 </select>
		 <label>Runs to compare</label>
		 <Select options={this.props.dataset_to_selector_options[this.state.dataset]}
			 isMulti className="basic-multi-select" classNamePrefix="select"
			 onChange={(evt) => this.handleChangeRun(evt)}/>
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
			 run_ids: [kv[1].indexing_run_id, kv[1].search_run_id].filter(x => x != null)
		       });
		       return (<th key={"col-" + engine}><a href={"?" + params}>{engine}</a></th>);
		     })
		   }
		 </tr>
               </thead>
               <tbody>
		 <tr>
		   <td>Indexing time</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       var engine = kv[0];
                       var engine_stats = kv[1].indexing_duration_secs?.toFixed(2);
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
                       var engine = kv[0];
                       var engine_stats = kv[1].mb_bytes_per_second?.toFixed(2);
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
                       var engine = kv[0];
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
                       var engine = kv[0];
                       var engine_stats = kv[1].num_splits;
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
                       var engine = kv[0];
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
		   <td>Query time AVERAGE</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       var engine = kv[0];
                       var engine_stats = kv[1].total;
                       if (engine_stats !== undefined) {
			 return <td key={"result-" + engine}>
				  {numberWithCommas(engine_stats / 1000)} ms
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
		   <td>Geometric average of query time ratios</td>
		   {
		     Object.entries(data_view.engines).map(kv => {
                       var engine = kv[0];
                       var engine_stats = kv[1].avg_ratios?.toFixed(2);
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
		 {
		   Object.entries(data_view.queries).map(kv => {
		     var query_name = kv[0];
		     var engine_queries = kv[1];
		     let ref_engine = Object.keys(engine_queries)[0];
		     return <tr key={query_name}>
			      <td><Display name={query_name} query={engine_queries[ref_engine].query}></Display></td>
			      {
				Object.keys(data_view.engines).map(engine => {
				  var cell_data = engine_queries[engine];
				  if (cell_data == null || cell_data.unsupported) {
				    return <td key={query_name + engine} className={"data"}></td>;
				  } else {
				    return <td key={query_name + engine} className={"data " + cell_data.className}>
					     <div className="timing">{numberWithCommas(cell_data.p90 / 1000 )}  ms</div>
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
    var query = this.props.query;
    var name = this.props.name;
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
    var query = this.props.query;
    if (query.start_timestamp !== undefined) {
      var start_date = new Date(query.start_timestamp * 1000).toISOString().substring(0, 10);
      var end_date = new Date(query.end_timestamp * 1000).toISOString().substring(0, 10);
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
			<ReactJson src={resp} />
		      </React.StrictMode>,
		      document.getElementById("app-container"));
    });
}

// TODO: make it work with local jsons as well if needed. Doable (just
// have a global param 'local_json')
$(function () {

  let searchParams = new URLSearchParams(window.location.search)
  if (searchParams.get("page") == "graphs") {
    console.log("Showing graphs");
    showContinuousGraphs();
    return;
  }

  if (searchParams.get("page") == "raw") {
    console.log("Showing raw");
    showRaw(searchParams.get("run_ids").split(","));
    return;
  }
  
  $.getJSON(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/list/`, (list_runs_resp) => {
    var data = {};
    // Maps dataset name to:
    // {display_name -> {indexing: most recent indexing run,
    //                   search: most recent search run}
    // }
    let dataset_to_most_recent_runs = {};
    for (const run_info of list_runs_resp.run_infos) {
      let dataset = run_info.track;
      let display_name = getRunDisplayName(run_info);
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
      if (previous_run_info === null ||
	  previous_run_info.timestamp < run_info.timestamp) {
	most_recent_runs[display_name][run_info.run_type] = run_info;
      }
    }
    
    // Pick the latest for each combination.
    // Map from dataset name to an array of {value: {indexing: ID of the indexing run, search: ID of the search run}, label: display name of the run}
    let dataset_to_selector_options = {};
    for (const dataset in dataset_to_most_recent_runs) {
      if (!(dataset in dataset_to_selector_options)) {
	dataset_to_selector_options[dataset] = [];
      }
      for (const display_name in dataset_to_most_recent_runs[dataset]) {
	const search_run_info = dataset_to_most_recent_runs[dataset][display_name].search;
	const indexing_run_info = dataset_to_most_recent_runs[dataset][display_name].indexing;
	dataset_to_selector_options[dataset].push({
	  value: {
	    indexing: indexing_run_info === null ? null : indexing_run_info.id,
	    search: search_run_info === null ? null : search_run_info.id
	  },
	  label: display_name});
      }
    }
    
    var tagged_engines = [];
    // TODO: Remove logic around tags, it actually has no effect.
    var tags_set = new Set();
    const datasets = Object.keys(dataset_to_most_recent_runs).sort();
    var tags = Array.from(tags_set);
    tags.sort();
    var el = document.getElementById("app-container");
    console.log("Initial rendering of Benchmark react elmt");
    ReactDOM.render(<React.StrictMode>
		      <Benchmark data={data} tags={tags} engines={tagged_engines} datasets={datasets} dataset_to_selector_options={dataset_to_selector_options}/>
		    </React.StrictMode>, el);
  });
});

// If you want your app to work offline and load faster, you can change
// unregister() to register() below. Note this comes with some pitfalls.
// Learn more about service workers: https://bit.ly/CRA-PWA
serviceWorker.unregister();
