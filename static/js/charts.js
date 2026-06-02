/* ── Tradalgo — Plotly chart rendering ── */

let _chartData = null;
let _selectedTradeId = null;

const BULL_COLOR = "#3fb950";
const BEAR_COLOR = "#f85149";
const OB_BULL_FILL   = "rgba(63,185,80,0.18)";
const OB_BEAR_FILL   = "rgba(248,81,73,0.18)";
const OB_BULL_FADED  = "rgba(63,185,80,0.04)";
const OB_BEAR_FADED  = "rgba(248,81,73,0.04)";

function renderChart(data) {
  _chartData = data;
  _drawChart();
}

function _drawChart() {
  const data = _chartData;
  if (!data) return;

  const showMit  = document.getElementById("showMitigated").checked;
  const showSLTP = document.getElementById("showSlTp").checked;
  const ohlcv    = data.ohlcv;
  const times    = ohlcv.map(d => d.t);
  const lastTime = times[times.length - 1];

  /* ── 1. Candlestick ── */
  const candles = {
    type: "candlestick",
    x: times,
    open:  ohlcv.map(d => d.o),
    high:  ohlcv.map(d => d.h),
    low:   ohlcv.map(d => d.l),
    close: ohlcv.map(d => d.c),
    name: data.tf,
    increasing: { line: { color: BULL_COLOR, width: 1 }, fillcolor: BULL_COLOR },
    decreasing: { line: { color: BEAR_COLOR, width: 1 }, fillcolor: BEAR_COLOR },
    showlegend: false,
    hoverinfo: "x+y",
  };

  /* ── 2. EMA lines ── */
  const emaTraces = (data.emas || []).map(ema => ({
    type: "scatter",
    x: ema.values.map(d => d.t),
    y: ema.values.map(d => d.v),
    name: ema.name,
    line: { color: ema.color, width: 1.5 },
    mode: "lines",
    hoverinfo: "skip",
  }));

  /* ── 3. Trade markers ── */
  const trades = data.trades || [];
  const entryX = [], entryY = [], entrySymbol = [], entryColor = [], entryText = [];
  const exitX  = [], exitY  = [], exitColor = [];

  trades.forEach(t => {
    if (!t.entry_time) return;
    entryX.push(t.entry_time);
    entryY.push(t.entry_price);
    entrySymbol.push(t.direction === "bullish" ? "triangle-up" : "triangle-down");
    entryColor.push(t.direction === "bullish" ? BULL_COLOR : BEAR_COLOR);
    entryText.push(`#${t.id} ${t.direction.toUpperCase()}<br>R:R ${t.rr_ratio} | ${t.exit_reason}<br>PnL: ${t.pnl_pips} pips / ${t.pnl_usd}$`);
    if (t.exit_time && t.exit_price) {
      exitX.push(t.exit_time);
      exitY.push(t.exit_price);
      exitColor.push(
        t.exit_reason === "tp_hit"       ? BULL_COLOR :
        t.exit_reason === "sl_hit"       ? BEAR_COLOR : "#888"
      );
    }
  });

  const entryTrace = {
    type: "scatter", mode: "markers", name: "Entrées",
    x: entryX, y: entryY,
    marker: { symbol: entrySymbol, color: entryColor, size: 11, line: { color: "#fff", width: 1 } },
    text: entryText, hovertemplate: "%{text}<extra></extra>",
  };
  const exitTrace = {
    type: "scatter", mode: "markers", name: "Sorties",
    x: exitX, y: exitY,
    marker: { symbol: "x", color: exitColor, size: 8 },
    hoverinfo: "skip",
  };

  const traces = [candles, ...emaTraces, entryTrace, exitTrace];

  /* ── 4. Shapes (OBs + SL/TP) ── */
  const shapes = [];
  const annotations = [];

  (data.obs || []).forEach(ob => {
    if (ob.mitigated && !showMit) return;
    const isBull = ob.direction === "bullish";
    const fill   = ob.mitigated
      ? (isBull ? OB_BULL_FADED  : OB_BEAR_FADED)
      : (isBull ? OB_BULL_FILL   : OB_BEAR_FILL);
    const lc     = isBull ? BULL_COLOR : BEAR_COLOR;
    shapes.push({
      type: "rect", xref: "x", yref: "y",
      x0: ob.start_time, x1: ob.end_time || lastTime,
      y0: ob.zone_low,   y1: ob.zone_high,
      fillcolor: fill,
      line: { color: lc, width: ob.mitigated ? 0.5 : 1.5, dash: ob.mitigated ? "dot" : "solid" },
      layer: "below",
    });
    if (!ob.mitigated) {
      annotations.push({
        x: ob.start_time, y: ob.zone_high,
        xref: "x", yref: "y",
        text: isBull ? "B↑" : "B↓",
        showarrow: false,
        font: { size: 9, color: lc },
        xanchor: "left", yanchor: "bottom",
        bgcolor: "rgba(0,0,0,0.5)", borderpad: 2,
      });
    }
  });

  if (showSLTP) {
    trades.forEach(t => {
      if (!t.entry_time) return;
      const x1 = t.exit_time || lastTime;
      shapes.push(
        { type: "line", xref: "x", yref: "y",
          x0: t.entry_time, x1, y0: t.stop_loss, y1: t.stop_loss,
          line: { color: BEAR_COLOR, width: 1, dash: "dash" } },
        { type: "line", xref: "x", yref: "y",
          x0: t.entry_time, x1, y0: t.take_profit, y1: t.take_profit,
          line: { color: BULL_COLOR, width: 1, dash: "dash" } },
      );
    });
  }

  /* ── 5. Highlight selected trade ── */
  if (_selectedTradeId !== null) {
    const tr = trades.find(t => t.id === _selectedTradeId);
    if (tr && tr.entry_time) {
      const x1 = tr.exit_time || lastTime;
      shapes.push({
        type: "rect", xref: "x", yref: "paper",
        x0: tr.entry_time, x1,
        y0: 0, y1: 1,
        fillcolor: "rgba(88,166,255,0.06)",
        line: { color: "#58a6ff", width: 1, dash: "dot" },
        layer: "below",
      });
    }
  }

  /* ── 6. Layout ── */
  const layout = {
    paper_bgcolor: "#0d1117",
    plot_bgcolor:  "#161b22",
    font: { color: "#c9d1d9", family: "monospace", size: 11 },
    margin: { l: 65, r: 12, t: 18, b: 36 },
    xaxis: {
      gridcolor: "#21262d",
      linecolor: "#30363d",
      rangeslider: { visible: true, thickness: 0.03, bgcolor: "#161b22", bordercolor: "#30363d" },
      type: "date",
    },
    yaxis: {
      gridcolor: "#21262d",
      linecolor: "#30363d",
      fixedrange: false,
      tickformat: ".5f",
    },
    shapes, annotations,
    legend: { bgcolor: "rgba(22,27,34,0.85)", bordercolor: "#30363d", borderwidth: 1, x: 0.01, y: 0.99 },
    dragmode: "pan",
    hovermode: "x unified",
    hoverlabel: { bgcolor: "#21262d", bordercolor: "#30363d", font: { color: "#c9d1d9", size: 11 } },
    selectdirection: "h",
  };

  const config = {
    responsive: true,
    scrollZoom: true,
    displayModeBar: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "toImage", "autoScale2d"],
    displaylogo: false,
  };

  Plotly.newPlot("mainChart", traces, layout, config);
}

/* Called from app.js when a trade row is clicked */
window.highlightTradeOnChart = function(tradeId) {
  _selectedTradeId = (tradeId === _selectedTradeId) ? null : tradeId;

  if (!_chartData) return;
  _drawChart();

  const tr = (_chartData.trades || []).find(t => t.id === tradeId);
  if (!tr || !tr.entry_time) return;

  const entry = new Date(tr.entry_time).getTime();
  const exit  = tr.exit_time ? new Date(tr.exit_time).getTime() : entry + 7 * 86400000;
  const pad   = Math.max((exit - entry) * 0.5, 12 * 3600000);

  Plotly.relayout("mainChart", {
    "xaxis.range": [
      new Date(entry - pad).toISOString(),
      new Date(exit  + pad).toISOString(),
    ],
  });
};

/* Re-render on toggle change */
document.getElementById("showMitigated").addEventListener("change", () => { if (_chartData) _drawChart(); });
document.getElementById("showSlTp").addEventListener("change",      () => { if (_chartData) _drawChart(); });
