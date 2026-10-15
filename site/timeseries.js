/**
 * Yields — Time Series view (site/timeseries.js)
 *
 * Chart any combination of Treasury yields, policy rates, TIPS, breakevens,
 * spreads and economic indicators (latest revision vs. as first reported),
 * with recession shading, shareable links and CSV export.
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, isNum, fmtDate, periodLabel } = Y.fmt;
  const { toMs, fromMs, addMonths } = Y.dates;

  const GROUPS = [
    { id: "yields", title: "Treasury yields (constant maturity)" },
    { id: "fed", title: "Fed funds & reverse repo" },
    { id: "tips", title: "TIPS real yields" },
    { id: "breakeven", title: "Breakeven inflation (nominal − TIPS)" },
    { id: "spreads", title: "Yield spreads" },
    { id: "macro", title: "Inflation & economy" },
  ];
  const MATURITIES = ["1M", "6W", "2M", "3M", "4M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"];
  const REAL = ["5Y", "7Y", "10Y", "20Y", "30Y"];
  const PRESET_SPREADS = [["10Y", "2Y"], ["10Y", "3M"], ["30Y", "5Y"], ["2Y", "3M"], ["5Y", "2Y"]];
  const DEFAULT_SELECTION = ["UST3M", "UST10Y"];
  const DEFAULT_RANGE = "3Y";

  const S = {
    manifest: null,
    catalog: {},
    selected: [],
    colors: {},
    customSpreads: [],
    range: DEFAULT_RANGE,
    from: null,
    to: null,
    recessions: true,
    recessionPeriods: null,
    computed: new Map(),
    chart: null,
    token: 0,
    ready: null,
  };

  const $ = (id) => document.getElementById(id);

  /* ---------------------------------------------------------------------- */
  /* Catalog                                                                 */
  /* ---------------------------------------------------------------------- */

  function spreadId(long, short) {
    return `SPREAD_${long}_${short}`;
  }

  function buildCatalog() {
    const catalog = {};
    const files = S.manifest?.series || {};
    for (const [id, meta] of Object.entries(files)) {
      catalog[id] = { id, label: meta.label, group: meta.group, order: meta.order ?? 999, freq: meta.frequency, first: meta.first, last: meta.last, file: true };
    }
    for (const k of REAL) {
      if (files[`UST${k}`] && files[`TIPS${k}`]) {
        catalog[`BE${k}`] = {
          id: `BE${k}`, label: `${k} breakeven inflation`, group: "breakeven", order: 30 + REAL.indexOf(k), freq: "daily",
          first: files[`TIPS${k}`].first, last: files[`TIPS${k}`].last, parts: [`UST${k}`, `TIPS${k}`],
        };
      }
    }
    const spreads = PRESET_SPREADS.concat(S.customSpreads);
    spreads.forEach(([long, short], i) => {
      if (!files[`UST${long}`] || !files[`UST${short}`]) return;
      const id = spreadId(long, short);
      catalog[id] = {
        id, label: `${long} − ${short} spread`, group: "spreads", order: 50 + i, freq: "daily",
        first: files[`UST${short}`].first > files[`UST${long}`].first ? files[`UST${short}`].first : files[`UST${long}`].first,
        last: files[`UST${long}`].last, parts: [`UST${long}`, `UST${short}`], custom: i >= PRESET_SPREADS.length,
      };
    });
    S.catalog = catalog;
  }

  function ensureReady() {
    if (!S.ready) {
      S.ready = (async () => {
        S.manifest = await Y.data.manifest();
        buildCatalog();
        S.recessionPeriods = await Y.data.getJson("recessions.json").then((r) => r.periods || []).catch(() => []);
        renderPicker();
        renderSpreadBuilder();
      })();
      S.ready.catch(() => {
        S.ready = null;
      });
    }
    return S.ready;
  }

  async function loadSeries(id) {
    const meta = S.catalog[id];
    if (!meta) return null;
    if (meta.file) return Y.data.series(id);
    if (!S.computed.has(id)) {
      const promise = Promise.all(meta.parts.map((p) => Y.data.series(p))).then(([a, b]) => {
        const dates = [];
        const values = [];
        let j = 0;
        for (let i = 0; i < a.t.length; i++) {
          while (j < b.t.length && b.t[j] < a.t[i]) j++;
          if (j < b.t.length && b.t[j] === a.t[i]) {
            dates.push(a.dates[i]);
            values.push(Number((a.v[i] - b.v[j]).toFixed(4)));
          }
        }
        return { id, dates, t: Float64Array.from(dates.map(toMs)), v: Float64Array.from(values) };
      });
      promise.catch(() => S.computed.delete(id));
      S.computed.set(id, promise);
    }
    return S.computed.get(id);
  }

  /* ---------------------------------------------------------------------- */
  /* Picker                                                                  */
  /* ---------------------------------------------------------------------- */

  function renderPicker() {
    const host = $("ts-picker");
    if (!S.manifest) {
      host.innerHTML = `<p class="muted">The time series archive appears after the next scheduled data update. Meanwhile, the Rates Dashboard tab shows recent history.</p>`;
      return;
    }
    const html = GROUPS.map((group) => {
      const items = Object.values(S.catalog)
        .filter((m) => m.group === group.id)
        .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
      if (!items.length) {
        if (group.id === "fed" || group.id === "macro") {
          return `<details class="picker-group"><summary>${esc(group.title)}</summary><p class="muted small">Needs the FRED API key (see Sources).</p></details>`;
        }
        return "";
      }
      const open = items.some((m) => S.selected.includes(m.id)) || group.id === "yields";
      const rows = items
        .map((m) => {
          const checked = S.selected.includes(m.id) ? "checked" : "";
          const color = S.selected.includes(m.id) ? colorFor(m.id) : "transparent";
          const span = m.first ? `${m.first.slice(0, 4)}–${(m.last || "").slice(0, 4)}` : "";
          const remove = m.custom ? `<button type="button" class="link-btn" data-remove-spread="${esc(m.id)}" aria-label="Remove ${esc(m.label)}">remove</button>` : "";
          return `<label class="picker-item"><input type="checkbox" value="${esc(m.id)}" ${checked}/><span class="series-color-dot" style="background:${esc(color)}"></span><span class="picker-label">${esc(m.label)}</span><span class="picker-span">${esc(span)}</span>${remove}</label>`;
        })
        .join("");
      return `<details class="picker-group"${open ? " open" : ""}><summary>${esc(group.title)}</summary><div class="picker-items">${rows}</div></details>`;
    }).join("");
    host.innerHTML = html;
  }

  function renderSpreadBuilder() {
    const available = MATURITIES.filter((k) => S.manifest?.series?.[`UST${k}`]);
    const options = available.map((k) => `<option value="${k}">${k}</option>`).join("");
    $("ts-spread-long").innerHTML = options;
    $("ts-spread-short").innerHTML = options;
    $("ts-spread-long").value = available.includes("30Y") ? "30Y" : available[available.length - 1] || "";
    $("ts-spread-short").value = available.includes("1M") ? "1M" : available[0] || "";
  }

  function colorFor(id) {
    if (!S.colors[id]) {
      const used = new Set(Object.values(S.colors));
      S.colors[id] = Y.charts.PALETTE.find((c) => !used.has(c)) || Y.charts.PALETTE[Object.keys(S.colors).length % Y.charts.PALETTE.length];
    }
    return S.colors[id];
  }

  /* ---------------------------------------------------------------------- */
  /* Range                                                                   */
  /* ---------------------------------------------------------------------- */

  function selectionBounds() {
    let first = null;
    let last = null;
    for (const id of S.selected) {
      const m = S.catalog[id];
      if (!m) continue;
      if (m.first && (!first || m.first < first)) first = m.first;
      if (m.last && (!last || m.last > last)) last = m.last;
    }
    const fallbackLast = S.manifest?.curve?.lastDate || Y.dates.todayIso();
    return [first || addMonths(fallbackLast, -12), last || fallbackLast];
  }

  function currentDomain() {
    const [first, last] = selectionBounds();
    if (S.range && S.range !== "CUSTOM") {
      const [from, to] = Y.ranges.resolve(S.range, last, first);
      S.from = from;
      S.to = to;
    }
    if (!S.from || !S.to || S.from >= S.to) {
      const [from, to] = Y.ranges.resolve(DEFAULT_RANGE, last, first);
      S.from = from;
      S.to = to;
    }
    return [S.from, S.to];
  }

  function syncRangeControls() {
    document.querySelectorAll("#ts-ranges .range-btn").forEach((btn) => {
      const on = btn.getAttribute("data-range") === S.range;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    $("ts-from").value = S.from || "";
    $("ts-to").value = S.to || "";
    $("ts-recessions").checked = S.recessions;
  }

  function writeParams() {
    Y.router.setParams({
      s: S.selected.join(","),
      range: S.range !== "CUSTOM" && S.range !== DEFAULT_RANGE ? S.range : "",
      from: S.range === "CUSTOM" ? S.from : "",
      to: S.range === "CUSTOM" ? S.to : "",
      rec: S.recessions ? "" : "0",
      sp: S.customSpreads.map(([a, b]) => `${a}-${b}`).join(","),
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Chart                                                                   */
  /* ---------------------------------------------------------------------- */

  async function render() {
    const token = ++S.token;
    const host = $("ts-chart");
    if (!S.manifest) {
      host.innerHTML = `<div class="chart-empty">The time series archive appears after the next scheduled data update.</div>`;
      return;
    }
    const [from, to] = currentDomain();
    syncRangeControls();
    writeParams();
    if (!S.selected.length) {
      $("ts-legend").innerHTML = "";
      host.innerHTML = `<div class="chart-empty">Pick one or more series on the left.</div>`;
      $("ts-note").textContent = "";
      return;
    }
    const loaded = await Promise.all(S.selected.map((id) => loadSeries(id).catch(() => null)));
    if (token !== S.token) return;
    const series = [];
    const missing = [];
    S.selected.forEach((id, i) => {
      const data = loaded[i];
      const meta = S.catalog[id];
      if (!data || !meta) {
        missing.push(meta ? meta.label : id);
        return;
      }
      const stepped = meta.freq === "monthly" || meta.freq === "quarterly" || id.startsWith("FED_TARGET");
      series.push({
        id, label: meta.label, color: colorFor(id), t: data.t, v: data.v, freq: meta.freq,
        step: stepped, dash: id.endsWith("_INITIAL") ? "5 3" : null, width: 1.9,
      });
    });
    const bands = S.recessions
      ? (S.recessionPeriods || []).map(([start, end]) => ({ from: toMs(start), to: toMs(end) + Y.dates.DAY_MS, label: `Recession ${start.slice(0, 7)} to ${end.slice(0, 7)}` }))
      : [];
    const options = {
      series,
      xDomain: [toMs(from), toMs(to)],
      bands,
      yFormat: (v, d) => `${v.toFixed(Math.min(d, 2))}%`,
      ariaLabel: `Time series of ${series.map((s) => s.label).join(", ")} from ${from} to ${to}`,
      emptyMessage: "No observations for these series in the selected range.",
    };
    if (S.chart && host.querySelector("svg")) {
      S.chart.opts = options;
      S.chart.render();
    } else {
      S.chart = Y.charts.timeChart(host, options);
    }
    $("ts-legend").innerHTML = Y.charts.legendHtml(series.map((s) => ({ label: s.label, color: s.color, dash: !!s.dash, removeId: s.id })));

    const notes = [];
    if (S.selected.some((id) => id.endsWith("_INITIAL"))) {
      notes.push("Dashed lines show each figure as first reported (ALFRED vintages); solid lines show the latest revision.");
    }
    if (S.selected.some((id) => /^UST/.test(id)) && from < "1990-01-01") {
      notes.push("Before 1990 yields come from FRED's historical series; before 1954 only monthly averages exist, and pre-1982 bill rates are on a discount basis.");
    }
    if (S.recessions && bands.length) notes.push("Shaded areas mark NBER recessions.");
    if (missing.length) notes.push(`Could not load: ${missing.join(", ")}.`);
    $("ts-note").textContent = notes.join(" ");
  }

  async function downloadCsv() {
    const [from, to] = currentDomain();
    const loaded = await Promise.all(S.selected.map((id) => loadSeries(id).catch(() => null)));
    const columns = [];
    const byDate = new Map();
    S.selected.forEach((id, i) => {
      const data = loaded[i];
      if (!data) return;
      columns.push(S.catalog[id].label);
      const col = columns.length - 1;
      for (let k = 0; k < data.dates.length; k++) {
        const d = data.dates[k];
        if (d < from || d > to) continue;
        if (!byDate.has(d)) byDate.set(d, []);
        byDate.get(d)[col] = data.v[k];
      }
    });
    const rows = Array.from(byDate.keys())
      .sort()
      .map((d) => [d].concat(columns.map((_c, i) => (isNum(byDate.get(d)[i]) ? byDate.get(d)[i] : ""))));
    Y.util.downloadCsv(`yields-time-series-${from}-to-${to}.csv`, ["date"].concat(columns), rows);
  }

  /* ---------------------------------------------------------------------- */
  /* Controls                                                                */
  /* ---------------------------------------------------------------------- */

  function toggle(id, on) {
    if (on && !S.selected.includes(id)) S.selected.push(id);
    if (!on) S.selected = S.selected.filter((x) => x !== id);
    if (!on) delete S.colors[id];
    renderPicker();
    render();
  }

  function init() {
    $("ts-ranges").innerHTML = Y.ranges.PRESETS.map(
      (p) => `<button type="button" class="btn range-btn" data-range="${p.id}" aria-pressed="false">${p.id === "MAX" ? "Max" : p.id}</button>`
    ).join("");
    $("ts-ranges").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-range]");
      if (!btn) return;
      S.range = btn.getAttribute("data-range");
      render();
    });
    const onDates = () => {
      const from = $("ts-from").value;
      const to = $("ts-to").value;
      if (from && to && from < to) {
        S.range = "CUSTOM";
        S.from = from;
        S.to = to;
        render();
      }
    };
    $("ts-from").addEventListener("change", onDates);
    $("ts-to").addEventListener("change", onDates);
    $("ts-recessions").addEventListener("change", (ev) => {
      S.recessions = ev.target.checked;
      render();
    });
    $("ts-picker").addEventListener("change", (ev) => {
      const box = ev.target.closest('input[type="checkbox"]');
      if (box) toggle(box.value, box.checked);
    });
    $("ts-picker").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-remove-spread]");
      if (!btn) return;
      ev.preventDefault();
      const id = btn.getAttribute("data-remove-spread");
      S.customSpreads = S.customSpreads.filter(([a, b]) => spreadId(a, b) !== id);
      S.selected = S.selected.filter((x) => x !== id);
      buildCatalog();
      renderPicker();
      render();
    });
    $("ts-legend").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-remove]");
      if (btn) toggle(btn.getAttribute("data-remove"), false);
    });
    $("ts-clear").addEventListener("click", () => {
      S.selected = [];
      S.colors = {};
      renderPicker();
      render();
    });
    $("ts-spread-add").addEventListener("click", () => {
      const long = $("ts-spread-long").value;
      const short = $("ts-spread-short").value;
      if (!long || !short || long === short) return;
      const id = spreadId(long, short);
      const known = PRESET_SPREADS.concat(S.customSpreads).some(([a, b]) => a === long && b === short);
      if (!known) S.customSpreads.push([long, short]);
      buildCatalog();
      if (!S.selected.includes(id)) S.selected.push(id);
      renderPicker();
      render();
    });
    $("ts-share").addEventListener("click", async () => {
      const params = {
        s: S.selected.join(","),
        range: S.range !== "CUSTOM" ? S.range : "",
        from: S.range === "CUSTOM" ? S.from : "",
        to: S.range === "CUSTOM" ? S.to : "",
        rec: S.recessions ? "" : "0",
        sp: S.customSpreads.map(([a, b]) => `${a}-${b}`).join(","),
      };
      const url = Y.router.link("time-series", params);
      const status = $("ts-share-status");
      try {
        await navigator.clipboard.writeText(url);
        status.textContent = "Link copied.";
      } catch (_) {
        window.prompt("Copy this link:", url);
        status.textContent = "";
      }
      setTimeout(() => {
        status.textContent = "";
      }, 2500);
    });
    $("ts-csv").addEventListener("click", downloadCsv);
  }

  async function show(params) {
    try {
      await ensureReady();
    } catch (err) {
      $("ts-chart").innerHTML = `<div class="chart-empty">Could not load the series list (${esc(err.message || err)}).</div>`;
      return;
    }
    if (params.sp) {
      S.customSpreads = params.sp
        .split(",")
        .map((pair) => pair.split("-"))
        .filter((p) => p.length === 2 && MATURITIES.includes(p[0]) && MATURITIES.includes(p[1]) && p[0] !== p[1]);
      buildCatalog();
    }
    if (params.s !== undefined) {
      S.selected = params.s.split(",").filter((id) => S.catalog[id]);
    } else if (!S.selected.length) {
      S.selected = DEFAULT_SELECTION.filter((id) => S.catalog[id]);
    }
    if (params.from && params.to && /^\d{4}-\d{2}-\d{2}$/.test(params.from) && /^\d{4}-\d{2}-\d{2}$/.test(params.to)) {
      S.range = "CUSTOM";
      S.from = params.from;
      S.to = params.to;
    } else if (params.range && Y.ranges.PRESETS.some((p) => p.id === params.range)) {
      S.range = params.range;
    }
    if (params.rec === "0") S.recessions = false;
    renderPicker();
    await render();
  }

  function refresh() {
    S.ready = null;
    S.computed.clear();
    S.chart = null;
    show({});
  }

  Y.router.register("time-series", { title: "Treasuries & Economic Indicators Time Series", init, show, refresh });
  Y.timeseries = { state: S, periodLabel, fmtDate, fromMs };
})();
