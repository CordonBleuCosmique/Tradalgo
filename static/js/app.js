/* ── Tradalgo — Application logic ── */

let currentTab = "accueil";
let pollTimer  = null;
let logOffset  = 0;
let currentTf  = "h1";
let backtestResult = null;

/* ═══════════════════════════════════════════════════
   TAB NAVIGATION
   ═══════════════════════════════════════════════════ */

function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab-panel").forEach(p => p.hidden = true);
  document.getElementById("panel-" + name).hidden = false;
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "logs") refreshLogs();
}

document.querySelectorAll(".tab-btn").forEach(btn =>
  btn.addEventListener("click", () => showTab(btn.dataset.tab))
);

/* ═══════════════════════════════════════════════════
   CONFIG FORM — dynamic param sections
   ═══════════════════════════════════════════════════ */

function updateParamVisibility() {
  const mode   = document.getElementById("modeSelect").value;
  const source = document.getElementById("sourceSelect").value;
  document.getElementById("csvRow").hidden         = source === "yfinance";
  document.getElementById("intradayParams").hidden = mode !== "intraday";
  document.getElementById("swingParams").hidden    = mode !== "swing";
  document.getElementById("mtfParams").hidden      = mode !== "mtf";
}
document.getElementById("modeSelect").addEventListener("change",  updateParamVisibility);
document.getElementById("sourceSelect").addEventListener("change", updateParamVisibility);
updateParamVisibility();

/* ═══════════════════════════════════════════════════
   RUN BACKTEST
   ═══════════════════════════════════════════════════ */

document.getElementById("backtestForm").addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const params = Object.fromEntries(fd.entries());

  setRunning(true);
  document.getElementById("noResults").hidden = false;
  document.getElementById("resultsPanel").hidden = true;
  logOffset = 0;
  document.getElementById("logsContent").innerHTML = "";

  const resp = await fetch("/api/run-backtest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });

  if (resp.ok) {
    pollTimer = setInterval(pollStatus, 1500);
  } else {
    const err = await resp.json();
    setRunning(false);
    showError(err.error || "Erreur inconnue");
  }
});

async function pollStatus() {
  try {
    const resp = await fetch("/api/status");
    const s = await resp.json();
    if (currentTab === "logs") refreshLogs();
    if (!s.running) {
      clearInterval(pollTimer);
      pollTimer = null;
      setRunning(false);
      if (s.error) {
        showError(s.error);
      } else if (s.has_result) {
        await loadResults();
        loadBacktestHistory();   // refresh history after new backtest
      }
    }
  } catch (_) {}
}

function setRunning(running) {
  const btn = document.getElementById("btnRun");
  btn.disabled = running;
  btn.innerHTML = running
    ? '<span class="spinner-border spinner-border-sm"></span> En cours…'
    : '<i class="bi bi-play-fill"></i> Lancer le backtest';
  document.getElementById("runStatus").textContent = running ? "Backtest en cours…" : "";
}

function showError(msg) {
  document.getElementById("noResults").hidden = false;
  document.getElementById("noResults").innerHTML =
    `<div class="alert alert-danger text-start small"><pre style="margin:0;white-space:pre-wrap">${escHtml(msg)}</pre></div>`;
  document.getElementById("resultsPanel").hidden = true;
}

/* ═══════════════════════════════════════════════════
   RESULTS DISPLAY
   ═══════════════════════════════════════════════════ */

async function loadResults() {
  const resp = await fetch("/api/results");
  if (!resp.ok) return;
  backtestResult = await resp.json();
  displayResults(backtestResult);
}

function displayResults(data) {
  document.getElementById("noResults").hidden = true;
  document.getElementById("resultsPanel").hidden = false;

  const m = data.metrics;

  /* Metrics grid */
  const grid = document.getElementById("metricsGrid");
  const defs = [
    { label: "Trades",      value: m.total_trades,       fmt: v => v,                          cls: "neutral" },
    { label: "Win Rate",    value: m.win_rate,            fmt: v => v + "%",                    cls: m.win_rate >= 50 ? "positive" : "negative" },
    { label: "Profit Factor",value: m.profit_factor,     fmt: v => v,                          cls: m.profit_factor >= 1 ? "positive" : "negative" },
    { label: "Sharpe",      value: m.sharpe_ratio,        fmt: v => v,                          cls: m.sharpe_ratio >= 1 ? "positive" : m.sharpe_ratio >= 0 ? "warning" : "negative" },
    { label: "Max Drawdown",value: m.max_drawdown_pct,   fmt: v => v + "%",                    cls: "negative" },
    { label: "Retour Total",value: m.total_return_pct,   fmt: v => v + "%",                    cls: m.total_return_pct >= 0 ? "positive" : "negative" },
    { label: "Moy R:R",     value: m.avg_rr,             fmt: v => v,                          cls: m.avg_rr >= 2 ? "positive" : "warning" },
    { label: "PnL Total",   value: m.total_pnl_usd,      fmt: v => "$" + v.toLocaleString("fr-FR", { maximumFractionDigits: 0 }), cls: m.total_pnl_usd >= 0 ? "positive" : "negative" },
  ];
  grid.innerHTML = defs.map(d => `
    <div class="col-6 col-sm-4 col-xl-3">
      <div class="metric-card ${d.cls}">
        <div class="metric-label">${d.label}</div>
        <div class="metric-value">${d.fmt(d.value)}</div>
      </div>
    </div>`).join("");

  /* Equity curve */
  const ec = data.equity_curve;
  Plotly.newPlot("equityCurve",
    [{ type: "scatter", x: ec.map(d => d.t), y: ec.map(d => d.v),
       mode: "lines", line: { color: "#58a6ff", width: 2 },
       fill: "tozeroy", fillcolor: "rgba(88,166,255,0.08)",
       hovertemplate: "%{y:$,.0f}<extra></extra>",
    }],
    { paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      font: { color: "#8b949e", size: 10 },
      margin: { l: 60, r: 8, t: 4, b: 28 },
      xaxis: { gridcolor: "#21262d", linecolor: "#30363d" },
      yaxis: { gridcolor: "#21262d", linecolor: "#30363d", tickprefix: "$", tickformat: ",.0f" },
      showlegend: false, hovermode: "x unified",
    },
    { responsive: true, displayModeBar: false }
  );

  /* Trade table */
  buildTradeTable(data.trades);
}

function buildTradeTable(trades) {
  document.getElementById("tradeCount").textContent = trades.length + " trades";
  const tbody = document.getElementById("tradeTableBody");

  tbody.innerHTML = trades.map(t => {
    const bull   = t.direction === "bullish";
    const slPips = Math.abs(t.entry_price - t.stop_loss) / 0.0001;
    const tpPips = Math.abs(t.take_profit - t.entry_price) / 0.0001;
    const pCls   = (t.pnl_pips || 0) >= 0 ? "text-bull" : "text-bear";
    const exitIco = { tp_hit: "✓", sl_hit: "✗", eod_close: "⏱", friday_close: "📅" }[t.exit_reason] || "";
    return `
    <tr class="trade-row" data-id="${t.id}">
      <td class="text-muted-c">${t.id}</td>
      <td class="${bull ? "text-bull" : "text-bear"}">${bull ? "▲" : "▼"} ${t.direction}</td>
      <td>${fmtDT(t.entry_time)}</td>
      <td>${fmtDT(t.exit_time)}</td>
      <td class="text-muted-c">${slPips.toFixed(0)}</td>
      <td class="text-muted-c">${tpPips.toFixed(0)}</td>
      <td class="text-muted-c">${exitIco} ${t.exit_reason}</td>
      <td class="${pCls}">${(t.pnl_pips || 0).toFixed(1)}</td>
      <td class="${pCls}">${fmt$(t.pnl_usd || 0)}</td>
      <td class="${pCls}">${(t.r_multiple || 0).toFixed(2)}R</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll(".trade-row").forEach(row => {
    row.addEventListener("click", () => {
      const id = parseInt(row.dataset.id);
      // Deselect previous
      tbody.querySelectorAll(".trade-row.selected").forEach(r => r.classList.remove("selected"));
      row.classList.add("selected");
      // Switch to chart tab if needed
      if (currentTab !== "graphiques") showTab("graphiques");
      window.highlightTradeOnChart(id);
    });
  });
}

/* ═══════════════════════════════════════════════════
   CHART TAB
   ═══════════════════════════════════════════════════ */

document.querySelectorAll(".tf-btn").forEach(btn =>
  btn.addEventListener("click", function () {
    document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
    this.classList.add("active");
    currentTf = this.dataset.tf;
  })
);

document.getElementById("btnLoadChart").addEventListener("click", loadChart);

async function loadChart() {
  const statusEl = document.getElementById("chartStatus");
  statusEl.textContent = "Chargement…";
  try {
    const resp = await fetch("/api/chart-data/" + currentTf);
    if (!resp.ok) {
      const err = await resp.json();
      statusEl.textContent = "⚠ " + err.error;
      return;
    }
    const data = await resp.json();
    renderChart(data);   // defined in charts.js
    statusEl.textContent = `${data.tf} — ${data.ohlcv.length.toLocaleString("fr-FR")} barres | ${(data.obs || []).length} OBs détectés`;
  } catch (e) {
    statusEl.textContent = "Erreur: " + e;
  }
}

/* ═══════════════════════════════════════════════════
   LOGS TAB
   ═══════════════════════════════════════════════════ */

async function refreshLogs() {
  const resp = await fetch("/api/logs?offset=" + logOffset);
  const data = await resp.json();
  if (!data.lines || data.lines.length === 0) return;

  const container = document.getElementById("logsContent");
  data.lines.forEach(line => {
    const div = document.createElement("div");
    div.className = "log-line" + (
      line.includes("✓")                    ? " success" :
      line.includes("✗") || line.toLowerCase().includes("erreur") || line.includes("Error") ? " error" :
      line.startsWith("=")                   ? " sep" :
      line.startsWith("▶")                   ? " info" : ""
    );
    div.textContent = line;
    container.appendChild(div);
  });
  logOffset = data.total;
  const box = document.getElementById("logsContainer");
  box.scrollTop = box.scrollHeight;
}

document.getElementById("btnClearLogs").addEventListener("click", () => {
  document.getElementById("logsContent").innerHTML = "";
  logOffset = 0;
});

document.getElementById("btnScrollBottom").addEventListener("click", () => {
  const box = document.getElementById("logsContainer");
  box.scrollTop = box.scrollHeight;
});

/* ═══════════════════════════════════════════════════
   GIT PULL (Sync Pi)
   ═══════════════════════════════════════════════════ */

document.getElementById("btnGitPull").addEventListener("click", async () => {
  const modal  = new bootstrap.Modal(document.getElementById("gitModal"));
  const output = document.getElementById("gitOutput");
  const badge  = document.getElementById("gitBadge");
  output.textContent = "git pull origin claude/eurusd-trading-algo-T7p4x…";
  badge.className = "badge bg-secondary";
  badge.textContent = "…";
  modal.show();

  try {
    const resp = await fetch("/api/git-pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ branch: "claude/eurusd-trading-algo-T7p4x" }),
    });
    const data = await resp.json();

    if (data.error) {
      output.textContent = "Erreur: " + data.error;
      badge.className = "badge bg-danger";
      badge.textContent = "Erreur";
    } else {
      output.textContent =
        (data.stdout || "(aucune sortie)") +
        (data.stderr ? "\n\nSTDERR:\n" + data.stderr : "");
      if (data.success) {
        badge.className = "badge bg-success";
        badge.textContent = "OK";
      } else {
        badge.className = "badge bg-warning text-dark";
        badge.textContent = "Warning";
      }
    }
  } catch (e) {
    output.textContent = "Erreur réseau: " + e;
    badge.className = "badge bg-danger";
    badge.textContent = "Erreur";
  }
});

/* ═══════════════════════════════════════════════════
   BACKTEST HISTORY
   ═══════════════════════════════════════════════════ */

async function loadBacktestHistory() {
  const sel = document.getElementById("historySelect");
  try {
    const resp = await fetch("/api/backtests");
    const list = await resp.json();
    sel.innerHTML = '<option value="">— Sélectionner un backtest —</option>' +
      list.map(b => {
        const dt  = b.saved_at
          ? new Date(b.saved_at).toLocaleString("fr-FR", {
              day: "2-digit", month: "2-digit", year: "2-digit",
              hour: "2-digit", minute: "2-digit"
            })
          : "?";
        const ret = (b.total_return_pct >= 0 ? "+" : "") + b.total_return_pct + "%";
        const mode = (b.mode || "?").toUpperCase();
        return `<option value="${b.bt_id}">#${b.bt_id} · ${dt} · ${mode} · ${ret} · ${b.total_trades} trades</option>`;
      }).join("");
    _updateHistoryButtons();
  } catch (e) {
    console.warn("loadBacktestHistory:", e);
  }
}

function _updateHistoryButtons() {
  const has = !!document.getElementById("historySelect").value;
  ["btnLoadHistory", "btnDlCsv", "btnDlPng", "btnDlZip"].forEach(id => {
    document.getElementById(id).disabled = !has;
  });
}

document.getElementById("historySelect").addEventListener("change", _updateHistoryButtons);

document.getElementById("btnRefreshHistory").addEventListener("click", loadBacktestHistory);

document.getElementById("btnLoadHistory").addEventListener("click", async () => {
  const bt_id = document.getElementById("historySelect").value;
  if (!bt_id) return;

  const btn = document.getElementById("btnLoadHistory");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

  try {
    const resp = await fetch(`/api/backtest/${bt_id}`);
    if (!resp.ok) {
      const err = await resp.json();
      showError(err.error || "Erreur de chargement");
      return;
    }
    backtestResult = await resp.json();
    displayResults(backtestResult);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-folder2-open"></i> Charger ce backtest';
    _updateHistoryButtons();
  }
});

function _dlBacktest(filetype) {
  const bt_id = document.getElementById("historySelect").value;
  if (bt_id) window.location.href = `/api/download/${bt_id}/${filetype}`;
}
document.getElementById("btnDlCsv").addEventListener("click", () => _dlBacktest("csv"));
document.getElementById("btnDlPng").addEventListener("click", () => _dlBacktest("png"));
document.getElementById("btnDlZip").addEventListener("click", () => _dlBacktest("zip"));

// Populate history on page load
loadBacktestHistory();


/* ═══════════════════════════════════════════════════
   UTILS
   ═══════════════════════════════════════════════════ */

function fmtDT(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })
       + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function fmt$(v) {
  return (v >= 0 ? "+" : "") + Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 0 }) + "$";
}

function escHtml(str) {
  return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
