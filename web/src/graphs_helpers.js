// Copyright (c) 2015 Nick Cameron
// Copyright (c) 2024 The benchmarks Authors
//
// Use of this source code is governed by an MIT-style license that
// can be found in the LICENSE.graphs_helpers_js.md file of this directory
// or at https://opensource.org/licenses/MIT.

// This glue code to call uPlot is mostly copied from:
// https://github.com/rust-lang/rustc-perf/blob/cbc8105e884499f50a0b959c12414148b122ef1c/site/frontend/src/graph/render.ts
// which has an MIT license:
// https://github.com/rust-lang/rustc-perf/blob/cbc8105e884499f50a0b959c12414148b122ef1c/site/LICENSE.md
// copied in LICENSE.graphs_helpers_js.md

import uPlot, {TypedArray} from "uplot";

export function tooltipPlugin({
  onclick,
  commits,
  isInterpolated,
  absoluteMode,
  shiftX = 10,
  shiftY = 10,
}) {
  let tooltipLeftOffset = 0;
  let tooltipTopOffset = 0;

  const tooltip = document.createElement("div");
  tooltip.className = "u-tooltip";

  let seriesIdx = null;
  let dataIdx = null;

  const fmtDate = uPlot.fmtDate("{M}/{D}/{YY} {h}:{mm}:{ss} {AA}");

  let over;

  let tooltipVisible = false;

  function showTooltip() {
    if (!tooltipVisible) {
      tooltip.style.display = "block";
      over.style.cursor = "pointer";
      tooltipVisible = true;
    }
  }

  function hideTooltip() {
    if (tooltipVisible) {
      tooltip.style.display = "none";
      over.style.cursor = null;
      tooltipVisible = false;
    }
  }

  function setTooltip(u) {
    showTooltip();

    let top = u.valToPos(u.data[seriesIdx][dataIdx], "y");
    let lft = u.valToPos(u.data[0][dataIdx], "x");

    tooltip.style.top = tooltipTopOffset + top + shiftX + "px";
    tooltip.style.left = tooltipLeftOffset + lft + shiftY + "px";

    let trailer = "";
    if (absoluteMode) {
      let pctSinceStart = (
        ((u.data[seriesIdx][dataIdx] - u.data[seriesIdx][0]) /
          u.data[seriesIdx][0]) *
        100
      ).toFixed(2);
      trailer =
        uPlot.fmtNum(u.data[seriesIdx][dataIdx]) +
        " (" +
        pctSinceStart +
        "% since start)";
    } else {
      trailer = uPlot.fmtNum(u.data[seriesIdx][dataIdx]) + "% since start";
    }
    tooltip.textContent =
      fmtDate(new Date(u.data[0][dataIdx] * 1e3)) +
      "\ncommit_hash=" +
      commits[dataIdx][0].slice(0, 10) +
      "\n" +
      trailer;
  }

  return {
    hooks: {
      ready: [
        (u) => {
          over = u.root.querySelector(".u-over");

          tooltipLeftOffset = parseFloat(over.style.left);
          tooltipTopOffset = parseFloat(over.style.top);
          u.root.querySelector(".u-wrap").appendChild(tooltip);

          let clientX;
          let clientY;

          over.addEventListener("mousedown", (e) => {
            clientX = e.clientX;
            clientY = e.clientY;
          });

          over.addEventListener("mouseup", (e) => {
            // clicked in-place
            if (e.clientX == clientX && e.clientY == clientY) {
              if (seriesIdx != null && dataIdx != null) {
                onclick(u, seriesIdx, dataIdx);
              }
            }
          });
        },
      ],
      setCursor: [
        (u) => {
          let c = u.cursor;

          if (dataIdx != c.idx) {
            dataIdx = c.idx;

            if (seriesIdx != null) setTooltip(u);
          }
        },
      ],
      setSeries: [
        (u, sidx) => {
          if (seriesIdx != sidx) {
            seriesIdx = sidx;

            if (sidx == null) hideTooltip();
            else if (dataIdx != null) setTooltip(u);
          }
        },
      ],
    },
  };
}

export function genPlotOpts({
  width,
  height,
  yAxisLabel,
  series,
  commits,
  stat,
  isInterpolated,
  alpha = 0.3,
  prox = 5,
  absoluteMode,
  hooks,
}) {
  return {
    width,
    height,
    series,
    legend: {
      live: false,
    },
    focus: {
      alpha,
    },
    cursor: {
      focus: {
        prox,
      },
      drag: {
        x: true,
        y: true,
      },
    },
    scales: {
      y: {
        range: (_self, dataMin, dataMax) =>
          uPlot.rangeNum(absoluteMode ? 0 : dataMin, dataMax, 0.2, true),
      },
    },
    axes: [
      {
        grid: {
          show: false,
        },
      },
      {
        label: yAxisLabel,
        space: 24,
        values: (_self, splits) => {
          return splits.map((v) => {
            return v >= 1e12
              ? v / 1e12 + "T"
              : v >= 1e9
              ? v / 1e9 + "G"
              : v >= 1e6
              ? v / 1e6 + "M"
              : v >= 1e3
              ? v / 1e3 + "k"
              : v;
          });
        },
      },
    ],
    plugins: [
      tooltipPlugin({
        onclick(_u, _seriesIdx, dataIdx) {
	  let run_id = commits[dataIdx][1];
          window.open(`/?page=raw&run_ids=${run_id}`);
        },
        commits,
        isInterpolated,
        absoluteMode,
      }),
    ],
  };
}
