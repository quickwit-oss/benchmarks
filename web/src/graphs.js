// Copyright 2024 The benchmarks Authors
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.
//
// Generates a page that shows graphs to track the performance over
// time of an engine.
// We use uPlot for generating graphs.
//
// TODO: If using uPlot becomes too cumbersome, consider replacing it
// with a better documented library such as dygraph, chart.js. See also:
// https://cprimozic.net/notes/posts/my-thoughts-on-the-uplot-charting-library/
// https://github.com/rubyzhao/LineChartPerformanceCompare

import React from 'react';
import ReactDOM from 'react-dom';
import Select from 'react-select';
import "uplot/dist/uPlot.min.css"
import './style.css'
import UplotReact from 'uplot-react';
import {BENCHMARK_SERVICE_ADDRESS, getRunDisplayName} from "./utils.js";
import {tooltipPlugin, genPlotOpts} from "./graphs_helpers.js";

class GraphsWithSelector extends React.Component {
  // Expects in props:
  // `dataset_to_selector_options`
  constructor(props) {
    super(props);
    let datasets = Object.keys(props.dataset_to_selector_options).sort();
    this.state = {
      dataset: datasets[0],
      run_filter: null,
      run_filter_display_name: null,
      timeseries: null,
    };
  }

  fetchTimeseries(run_filter) {
    fetch(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/get_as_timeseries/?` + new URLSearchParams(run_filter))
      .then((res) => { return res.json(); })
      .then((resp) => {
	this.setState({"timeseries": resp.timeseries});
      });
  }
  
  generateDataView() {
    if (!this.state.timeseries) {
      return {
	search_metrics: [],
	row_to_col_to_series: {},
      }
    }
    let search_metrics = new Set();
    for (let series of this.state.timeseries) {
      if (series.name != "indexing") {
	search_metrics.add(series.metric_name);
      }
    }
    // Maps row name (query name or "indexing") to a dict that maps
    // the metric name to the series (schemas.Timeseries).
    let row_to_col_to_series = {};
    for (let series of this.state.timeseries) {
      if (!(series.name in row_to_col_to_series)) {
	row_to_col_to_series[series.name] = {}
      }
      row_to_col_to_series[series.name][series.metric_name] = series;
    }
    return {
      search_metrics: Array.from(search_metrics.values()),
      row_to_col_to_series
    };
  }

  handleChangeDataset(evt) {
    console.log("Change dataset:", evt);
    let dataset = evt.target.value;
    if (dataset) {
      this.setState({ "dataset": dataset });
    } else {
      this.setState({ "dataset": null });
    }
    // TODO: clear the second selector.
  }

  handleChangeRunFilter(evt) {
    console.log("Change run evt:", evt);
    let run_filter = evt.value;
    if (run_filter) {
      this.setState({ "run_filter": run_filter});
      this.setState({ "run_filter_display_name": evt.label});
      this.fetchTimeseries(run_filter);
    } else {
      this.setState({ "run_filter": null });
      this.setState({ "run_filter_display_name": null});
    }
  }
 
  render() {
    let {search_metrics, row_to_col_to_series} = this.generateDataView();
    console.log("Graphs.render:", search_metrics, row_to_col_to_series);
    let datasets = Object.keys(this.props.dataset_to_selector_options).sort()
    return (
      <div>
      <form>
        <fieldset>
          <label htmlFor="datasetField">Dataset</label>
          <select id="datasetField" onChange={(evt) => this.handleChangeDataset(evt)}>
            {datasets.map((dataset) => <option value={dataset} key={dataset}>{dataset}</option>)}
          </select>
	  <label>Run filter</label>
	  <Select options={this.props.dataset_to_selector_options[this.state.dataset]} className="basic-multi-select" classNamePrefix="select" onChange={(evt) => this.handleChangeRunFilter(evt)}/>
        </fieldset>
      </form>
	<table>
	  <tbody>
	    {
	      Object.entries(row_to_col_to_series).map(row_name_and_col_to_series => {
		console.log("Rendering graph row:", row_name_and_col_to_series);
		let row_name = row_name_and_col_to_series[0];
		let col_to_series = row_name_and_col_to_series[1];
		let td_graph_elements = [];
		if (row_name != "indexing") {
		  // Iterate over search_metrics to make sure the
		  // order is consistent across rows (and an empty
		  // <td> is used if there is no data for a given
		  // metric).
		  for (let metric_name of search_metrics) {
		    if (metric_name in col_to_series) {
		      let {uplot_options, uplot_data} = uplotParamsFromSeries(
			this.state.run_filter_display_name, col_to_series[metric_name]);
		      td_graph_elements.push(
			<td>
			  <UplotReact options={uplot_options} data={uplot_data} />
			</td>
		      );
		    } else {
		      td_graph_elements.push(<td>NO DATA</td>);
		    }
		  }
		} else { // "indexing" row.
		  row_name = row_name.toUpperCase();
		  for (let series of Object.values(col_to_series)) {
		    let {uplot_options, uplot_data} = uplotParamsFromSeries(
		      this.state.run_filter_display_name, series);
		      td_graph_elements.push(
			<td>
			  <UplotReact options={uplot_options} data={uplot_data} />
			</td>
		      );
		  }
		}
		return (<tr>
			  <td>{row_name}</td>
			  {
			    td_graph_elements
			  }
			</tr>
		       );
	      })
	    }
	  </tbody>
	</table>
      </div>
    );
  }
}

// `run_filter_display_name`: e.g. "quickwit.SSD.c3-standard-4.continuous".
// `series`: schemas.Timeseries.
function uplotParamsFromSeries(run_filter_display_name, series) {
  let yAxisLabel = series.metric_name;
  let title = series.metric_name;
  let data_points = series.data_points;
  if (series.metric_name == "engine_duration") {
    yAxisLabel = "Engine Duration milliseconds";
    title = "Engine latency";
    data_points = series.data_points.map(micros => { return micros / 1e3; });
  } else if (series.metric_name == "total_cpu_time_s") {
    yAxisLabel = "CPU Time seconds";
    title = "Engine CPU Time";
  }
  
  let uplot_series = [
    {},  // Timestamps
    { label: run_filter_display_name, stroke: 'red' }
  ];
  let commits = [];
  for (let tag of series.tags) {
    commits.push([tag, tag]);
  }
  let plotOpts = genPlotOpts({
    width: 500,
    height: 300,
    yAxisLabel,
    series: uplot_series,
    commits,
    stat: "",  // Only used for a link that is disabled.
    isInterpolated: null,  // Only used for a link that is disabled.
    absoluteMode: true,
    hooks: {},
  });
  plotOpts["title"] = title;
  
  let plotData = [
    series.timestamps_s,
    data_points
  ];
  return {uplot_options: plotOpts,
	  uplot_data: plotData};
}

export function showContinuousGraphs() {

  fetch(`${BENCHMARK_SERVICE_ADDRESS}/api/v1/all_runs/list/?source=continuous_benchmarking`)
    .then((res) => { return res.json(); })
    .then((resp) => {
      // Maps datasets a map of display names
      // (e.g. "quickwit.ssd.c3-standard-4.continuous") to:
      // {track, engine, storage, instance}.
      let dataset_to_runs = {};
      for (const run_info of resp.run_infos) {
	let dataset = run_info.track;
	let display_name = getRunDisplayName(run_info);
	if (!(dataset in dataset_to_runs)) {
	  dataset_to_runs[dataset] = {};
	}
	dataset_to_runs[dataset][display_name] = {
	  track: run_info.track, engine: run_info.engine,
	  storage: run_info.storage, instance: run_info.instance,
	  tag: run_info.tag, source: run_info.source};
      }
      // Maps dataset to a list of:
      // {label: display names (e.g. "quickwit.ssd.c3-standard-4.continuous"),
      //  value: {track, engine, storage, instance}}
      // To be passed to a react Select.
      let dataset_to_selector_options = {}
      for (const dataset in dataset_to_runs) {
	dataset_to_selector_options[dataset] = [];
	for (const display_name in dataset_to_runs[dataset]) {
	  dataset_to_selector_options[dataset].push(
	    {label: display_name, value: dataset_to_runs[dataset][display_name]});
	}
      }
      
      var el = document.getElementById("app-container");
      ReactDOM.render(<React.StrictMode>
			<GraphsWithSelector dataset_to_selector_options={dataset_to_selector_options}/>
		      </React.StrictMode>, el);
      
    });
}
