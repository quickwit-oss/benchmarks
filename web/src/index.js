import React from 'react';
import $ from 'jquery';
import ReactDOM from 'react-dom';
import './style.css'
import * as serviceWorker from './serviceWorker';

function getEngineWithTag(engine_results) {
  return engine_results["engine"] + "." + engine_results["tag"];
}

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
  var res = stats(query.engine_duration.values);
  res.count = query.count;
  res.query = query.query;
  res.name = query.name;
  return res;
}


class Benchmark extends React.Component {

  constructor(props) {
    super(props);
    this.state = {
      tag: null,
      dataset: props.datasets[0]
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

  generateDataView() {
    var engines = {}
    var queries = {}
    let dataset = this.state.dataset;
    var data = this.props.data[dataset];
    for (var engine_results of data) {
      var engine_queries = engine_results.queries;
      var taggedEngine = getEngineWithTag(engine_results);
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
      engines[taggedEngine].total = total;
      for (let query of engine_queries) {
        var query_data = {};
        if (queries[query.name] !== undefined) {
          query_data = queries[query.name];
        }
        query_data[taggedEngine] = query
        queries[query.name] = query_data
      }
    }

    for (let query in queries) {
      let query_data = queries[query];
      var min_engine = null;
      var min_microsecs = 0;
      var max_engine = null;
      var max_microsecs = 0;
      for (let engine in query_data) {
        var engine_data = query_data[engine];
        if (engine_data.unsupported)
          continue;
        if (min_engine == null || engine_data.p90 < min_microsecs) {
          min_engine = engine;
          min_microsecs = engine_data.p90;
        }
        if (max_engine == null || engine_data.p90 > max_microsecs) {
          max_engine = engine;
          max_microsecs = engine_data.p90;
        }
      }
      for (let engine in query_data) {
        let engine_data = query_data[engine];
        if (engine_data.unsupported) continue;
        if (engine !== min_engine) {
          engine_data.variation = (engine_data.p90 - min_microsecs) / min_microsecs;
        }
      }
      if (min_engine != null) {
        // Only useful if at least one engine supports this query 
        query_data[min_engine].className = "fastest";
        query_data[max_engine].className = "slowest";
      }
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
        </fieldset>
      </form>
      <hr />
      <table>
        <thead>
          <tr>
            <th></th>
            {
              Object.keys(data_view.engines).map((engine) => <th key={"col-" + engine}>{engine}</th>)
            }
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Indexing time</td>
            {
              Object.entries(data_view.engines).map(kv => {
                var engine = kv[0];
                var engine_stats = kv[1].indexing_duration_secs.toFixed(2);
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
                var engine_stats = kv[1].mb_bytes_per_second.toFixed(2);
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
                var engine = kv[0];
                var engine_stats = (kv[1].num_indexed_bytes / 1000000000).toFixed(2);
                if (engine_stats !== undefined) {
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
                var engine_stats = (kv[1].num_indexed_docs / 1000000).toFixed(2);
                if (engine_stats !== undefined) {
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
                var engine_stats = (kv[1].num_indexed_docs / 1000 / kv[1].indexing_duration_secs).toFixed(2);
                if (engine_stats !== undefined) {
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
          {
            Object.entries(data_view.queries).map(kv => {
              var query_name = kv[0];
              var engine_queries = kv[1];
              let ref_engine = Object.keys(engine_queries)[0];
              console.log("query", query_name);
              return <tr key={query_name}>
                <td><Display name={query_name} query={engine_queries[ref_engine].query}></Display></td>
                {
                  Object.keys(data_view.engines).map(engine => {
                    var cell_data = engine_queries[engine];
                    if (cell_data.unsupported) {
                      return <td key={query_name + engine} className={"data " + cell_data.className}></td>;
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


$(function () {
  $.getJSON(process.env.PUBLIC_URL + "/results.json", (data) => {
    var datasets = [];

    for (var dataset in data) {
      datasets.push(dataset);
    }
    datasets.sort();
    console.log("datasets", datasets);

    var tagged_engines = [];
    var tags_set = new Set();
    for (var engine_results of data[datasets[0]]) {
      tagged_engines.push(getEngineWithTag(engine_results));
    }
    
    for (var engine_results of data[datasets[0]]) {
      for (var query of engine_results.queries) {
        // for (var tag of query.tags) {
        //   tags_set.add(tag);
        // }
      }
    }
    
    var tags = Array.from(tags_set);
    tags.sort();
    var el = document.getElementById("app-container");
    ReactDOM.render(<React.StrictMode>
      <Benchmark data={data} tags={tags} engines={tagged_engines} datasets={datasets}/>
    </React.StrictMode>, el);
  });
});

// If you want your app to work offline and load faster, you can change
// unregister() to register() below. Note this comes with some pitfalls.
// Learn more about service workers: https://bit.ly/CRA-PWA
serviceWorker.unregister();
