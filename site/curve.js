/**
 * Yields — Yield Curve view (site/curve.js)
 *
 * Shows the Treasury par yield curve for any date back to 1934, lets the user
 * step through dates, pin curves for comparison, and overlay the Fed funds
 * target range, the reverse repo (RRP) rate, TIPS real yields, breakeven
 * inflation and the latest CPI reading known on that date. Also renders the
 * sidebar (upcoming events and macro indicators).
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, isNum, pct, bps, fmtDate } = Y.fmt;
  const { toMs, fromMs, addMonths, lowerBound, floorIndex } = Y.dates;

  const NOMINAL = [
    { key: "1M", label: "1M", longLabel: "1-Month bill", years: 1 / 12 },
    { key: "6W", label: "6W", longLabel: "6-Week bill", years: 1.5 / 12 },
    { key: "2M", label: "2M", longLabel: "2-Month bill", years: 2 / 12 },
    { key: "3M", label: "3M", longLabel: "3-Month bill", years: 0.25 },
    { key: "4M", label: "4M", longLabel: "4-Month bill", years: 4 / 12 },
    { key: "6M", label: "6M", longLabel: "6-Month bill", years: 0.5 },
    { key: "1Y", label: "1Y", longLabel: "1-Year bill", years: 1 },
    { key: "2Y", label: "2Y", longLabel: "2-Year note", years: 2 },
    { key: "3Y", label: "3Y", longLabel: "3-Year note", years: 3 },
    { key: "5Y", label: "5Y", longLabel: "5-Year note", years: 5 },
    { key: "7Y", label: "7Y", longLabel: "7-Year note", years: 7 },
    { key: "10Y", label: "10Y", longLabel: "10-Year note", years: 10 },
    { key: "20Y", label: "20Y", longLabel: "20-Year bond", years: 20 },
    { key: "30Y", label: "30Y", longLabel: "30-Year bond", years: 30 },
  ];
  const RRP_CAT = { key: "RRP", label: "RRP", longLabel: "Overnight reverse repo award rate", years: 0 };
  const REAL_KEYS = ["5Y", "7Y", "10Y", "20Y", "30Y"];
  const MAIN_COLOR = "#0284c7";
  const PIN_COLORS = ["#7c3aed", "#ea580c", "#16a34a", "#db2777", "#ca8a04", "#475569"];
  const TIPS_COLOR = "#0d9488";
  const BREAKEVEN_COLOR = "#be123c";
  const CPI_COLOR = "#dc2626";
  const MAX_PINS = 6;

  const S = {
    manifest: null,
    fallback: false,
    years: [],
    yearInfo: {},
    yearCache: new Map(),
    firstDate: null,
    lastDate: null,
    date: null,
    pins: [],
    spacing: "even",
    overlays: { target: true, rrp: true, tips: false, breakeven: false, cpi: false },
    aux: {},
    chart: null,
    renderToken: 0,
    ready: null,
  };

  const $ = (id) => document.getElementById(id);

  /* ---------------------------------------------------------------------- */
  /* Data                                                                    */
  /* ---------------------------------------------------------------------- */

  function normalizeYear(raw) {
    const dates = raw.dates || [];
    const rows = new Map();
    dates.forEach((d, i) => {
      const nominal = {};
      const real = {};
      for (const [key, column] of Object.entries(raw.nominal || {})) if (isNum(column[i])) nominal[key] = column[i];
      for (const [key, column] of Object.entries(raw.real || {})) if (isNum(column[i])) real[key] = column[i];
      rows.set(d, { nominal, real });
    });
    return { year: raw.year, source: raw.source, notes: raw.notes || "", dates, rows };
  }

  function loadYear(year) {
    if (!S.yearCache.has(year)) {
      const promise = Y.data.getJson(`curve/${year}.json`).then(normalizeYear);
      promise.catch(() => S.yearCache.delete(year));
      S.yearCache.set(year, promise);
    }
    return S.yearCache.get(year);
  }

  /** Fallback when extended data is not published yet: use rates.json (last ~3 years). */
  async function buildFallbackFromRates() {
    const rates = await Y.data.getJson("rates.json");
    const obs = rates?.sources?.treasury?.observations || [];
    const byYear = new Map();
    for (const row of obs) {
      const year = Number(row.date.slice(0, 4));
      if (!byYear.has(year)) byYear.set(year, { year, source: "treasury", dates: [], rows: new Map(), notes: "" });
      const model = byYear.get(year);
      const nominal = {};
      for (const [key, v] of Object.entries(row.yields || {})) if (isNum(v)) nominal[key] = v;
      model.dates.push(row.date);
      model.rows.set(row.date, { nominal, real: {} });
    }
    S.years = Array.from(byYear.keys()).sort((a, b) => a - b);
    S.yearInfo = {};
    for (const year of S.years) {
      const model = byYear.get(year);
      S.yearCache.set(year, Promise.resolve(model));
      S.yearInfo[year] = { first: model.dates[0], last: model.dates[model.dates.length - 1] };
    }
    S.firstDate = S.yearInfo[S.years[0]]?.first || null;
    S.lastDate = S.yearInfo[S.years[S.years.length - 1]]?.last || null;
    S.fallback = true;
  }

  function ensureReady() {
    if (!S.ready) {
      S.ready = (async () => {
        S.manifest = await Y.data.manifest();
        const years = S.manifest?.curve?.years;
        if (years && Object.keys(years).length) {
          S.yearInfo = {};
          S.years = Object.keys(years).map(Number).sort((a, b) => a - b);
          for (const year of S.years) S.yearInfo[year] = years[year];
          S.firstDate = S.manifest.curve.firstDate || S.yearInfo[S.years[0]].first;
          S.lastDate = S.manifest.curve.lastDate || S.yearInfo[S.years[S.years.length - 1]].last;
        } else {
          await buildFallbackFromRates();
        }
        const has = (id) => !!S.manifest?.series?.[id];
        const load = (id) => (has(id) ? Y.data.series(id).catch(() => null) : Promise.resolve(null));
        S.aux = {
          lower: load("FED_TARGET_LOWER"),
          upper: load("FED_TARGET_UPPER"),
          target: load("FED_TARGET"),
          rrp: load("RRP_AWARD"),
          cpi: load("CPI_YOY"),
        };
        renderSidebar();
      })();
      S.ready.catch(() => {
        S.ready = null;
      });
    }
    return S.ready;
  }

  async function rowFor(date) {
    if (!date) return null;
    const model = await loadYear(Number(date.slice(0, 4)));
    return model.rows.get(date) || null;
  }

  /** Latest available curve date on or before `iso` (null if none). */
  async function snapDate(iso) {
    if (!S.firstDate) return null;
    if (iso > S.lastDate) iso = S.lastDate;
    if (iso < S.firstDate) return S.firstDate;
    let year = Number(iso.slice(0, 4));
    while (year >= S.years[0]) {
      if (S.yearInfo[year]) {
        const model = await loadYear(year);
        const i = floorIndex(model.dates, iso);
        if (i >= 0) return model.dates[i];
      }
      year -= 1;
    }
    return S.firstDate;
  }

  async function stepDate(iso, direction) {
    const year = Number(iso.slice(0, 4));
    const model = await loadYear(year);
    const i = model.dates.indexOf(iso);
    const j = i + direction;
    if (j >= 0 && j < model.dates.length) return model.dates[j];
    const yi = S.years.indexOf(year) + direction;
    if (yi < 0 || yi >= S.years.length) return iso;
    const other = await loadYear(S.years[yi]);
    return direction < 0 ? other.dates[other.dates.length - 1] : other.dates[0];
  }

  function valueAsOf(series, iso, maxAgeDays) {
    if (!series || !series.t.length) return null;
    const t = toMs(iso);
    const i = floorIndex(series.t, t);
    if (i < 0) return null;
    if (maxAgeDays && t - series.t[i] > maxAgeDays * Y.dates.DAY_MS) return null;
    return { value: series.v[i], date: series.dates[i], index: i };
  }

  async function targetRangeOn(iso) {
    const [lower, upper, single] = await Promise.all([S.aux.lower, S.aux.upper, S.aux.target]);
    const lo = valueAsOf(lower, iso, 10);
    const hi = valueAsOf(upper, iso, 10);
    if (lo && hi && iso >= "2008-12-16") {
      return { low: lo.value, high: hi.value, label: `Fed funds target ${lo.value.toFixed(2)}–${hi.value.toFixed(2)}%` };
    }
    const one = valueAsOf(single, iso, 10);
    if (one) return { low: one.value, high: one.value, label: `Fed funds target ${one.value.toFixed(2)}%` };
    return null;
  }

  /** CPI y/y as it was known on `iso` (uses first-release dates when available). */
  async function cpiKnownOn(iso) {
    const cpi = await S.aux.cpi;
    if (!cpi || !cpi.dates.length) return null;
    for (let i = cpi.dates.length - 1; i >= 0; i--) {
      const released = cpi.released && cpi.released[i];
      const knownFrom = released || addMonths(cpi.dates[i], 1).slice(0, 8) + "15";
      if (knownFrom <= iso) return { value: cpi.v[i], period: cpi.dates[i] };
    }
    return null;
  }

  /* ---------------------------------------------------------------------- */
  /* Rendering                                                               */
  /* ---------------------------------------------------------------------- */

  function renderSidebar() {
    const eventsEl = $("events-list");
    const indicatorsEl = $("indicators-list");
    const events = S.manifest?.events || [];
    const today = Y.dates.todayIso();
    if (eventsEl) {
      eventsEl.innerHTML = events.length
        ? events
            .slice(0, 8)
            .map((e, i) => {
              const soon = i === 0 || e.date === today;
              return `<li class="event-row${soon ? " event-next" : ""}"><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.label)}</a><time datetime="${esc(e.date)}">${esc(fmtDate(e.date, "weekday"))}</time></li>`;
            })
            .join("")
        : `<li class="muted">${S.manifest && !S.manifest.fredKeyConfigured ? "Release calendar needs the FRED API key." : "No scheduled releases found."}</li>`;
    }
    if (indicatorsEl) {
      const items = S.manifest?.indicators || [];
      indicatorsEl.innerHTML = items.length
        ? items
            .map((item) => `<div class="indicator-row"><dt>${esc(item.label)} <span class="muted">(${esc(item.periodLabel)})</span></dt><dd>${esc(pct(item.value, 1))}</dd></div>`)
            .join("")
        : `<div class="muted">${S.manifest && !S.manifest.fredKeyConfigured ? "Indicators need the FRED API key." : "Indicators unavailable."}</div>`;
    }
  }

  function syncControls() {
    const dateInput = $("curve-date");
    const slider = $("curve-slider");
    if (S.firstDate && S.lastDate) {
      dateInput.min = S.firstDate;
      dateInput.max = S.lastDate;
      const span = Math.round((toMs(S.lastDate) - toMs(S.firstDate)) / Y.dates.DAY_MS);
      slider.max = String(span);
      $("curve-slider-start").textContent = S.firstDate.slice(0, 4);
      $("curve-slider-end").textContent = fmtDate(S.lastDate);
    }
    if (S.date) {
      dateInput.value = S.date;
      slider.value = String(Math.round((toMs(S.date) - toMs(S.firstDate)) / Y.dates.DAY_MS));
      slider.setAttribute("aria-valuetext", fmtDate(S.date, "long"));
    }
    $("curve-spacing").value = S.spacing;
    for (const [key, on] of Object.entries(S.overlays)) {
      const box = $(`ov-${key}`);
      if (box) box.checked = on;
    }
    $("curve-next").disabled = !S.date || S.date >= S.lastDate;
    $("curve-prev").disabled = !S.date || S.date <= S.firstDate;
    $("curve-latest").disabled = !S.date || S.date === S.lastDate;
    $("curve-pin").disabled = !S.date || S.pins.includes(S.date) || S.pins.length >= MAX_PINS;
  }

  function writeParams() {
    const ov = Object.entries(S.overlays)
      .filter(([, on]) => on)
      .map(([k]) => k)
      .join(",");
    Y.router.setParams({
      date: S.date && S.date !== S.lastDate ? S.date : "",
      pins: S.pins.join(","),
      spacing: S.spacing !== "even" ? S.spacing : "",
      ov: ov !== "target,rrp" ? ov || "none" : "",
    });
  }

  async function render() {
    const token = ++S.renderToken;
    const host = $("curve-chart");
    if (!S.date) {
      host.innerHTML = `<div class="chart-empty">No yield curve data is available yet.</div>`;
      return;
    }
    const [row, prevDate] = await Promise.all([rowFor(S.date), stepDate(S.date, -1)]);
    const prevRow = prevDate !== S.date ? await rowFor(prevDate) : null;
    const pinRows = await Promise.all(S.pins.map((d) => rowFor(d)));
    const [target, rrpSeries, cpi, yearModel] = await Promise.all([
      S.overlays.target ? targetRangeOn(S.date) : null,
      S.overlays.rrp ? S.aux.rrp : null,
      S.overlays.cpi ? cpiKnownOn(S.date) : null,
      loadYear(Number(S.date.slice(0, 4))),
    ]);
    if (token !== S.renderToken) return;

    const rrpOn = (iso) => (rrpSeries ? valueAsOf(rrpSeries, iso, 5) : null);
    const mainValues = Object.assign({}, row?.nominal || {});
    const rrpMain = S.overlays.rrp ? rrpOn(S.date) : null;
    if (rrpMain) mainValues.RRP = rrpMain.value;

    const lines = [{ label: fmtDate(S.date), color: MAIN_COLOR, width: 2.8, values: mainValues, area: true }];
    S.pins.forEach((d, i) => {
      const values = Object.assign({}, pinRows[i]?.nominal || {});
      const rrpPin = S.overlays.rrp ? rrpOn(d) : null;
      if (rrpPin) values.RRP = rrpPin.value;
      lines.push({ label: `Pinned ${fmtDate(d)}`, color: PIN_COLORS[i % PIN_COLORS.length], dash: "6 4", width: 1.9, values, pin: d });
    });
    const real = row?.real || {};
    const hasReal = REAL_KEYS.some((k) => isNum(real[k]));
    if (S.overlays.tips && hasReal) {
      lines.push({ label: "TIPS real yield", color: TIPS_COLOR, dash: "2 3", width: 2, values: real, compare: false });
    }
    if (S.overlays.breakeven && hasReal) {
      const be = {};
      for (const k of REAL_KEYS) if (isNum(real[k]) && isNum(mainValues[k])) be[k] = Number((mainValues[k] - real[k]).toFixed(4));
      lines.push({ label: "Breakeven inflation", color: BREAKEVEN_COLOR, dash: "8 3 2 3", width: 2, values: be, compare: false });
    }

    const used = new Set();
    for (const line of lines) for (const [k, v] of Object.entries(line.values)) if (isNum(v)) used.add(k);
    const categories = (used.has("RRP") ? [RRP_CAT] : []).concat(NOMINAL.filter((c) => used.has(c.key)));

    const hline = cpi ? { value: cpi.value, color: CPI_COLOR, label: `CPI y/y (${Y.fmt.periodLabel(cpi.period, "monthly")}): ${cpi.value.toFixed(1)}%` } : null;
    const options = {
      categories,
      spacing: S.spacing,
      lines,
      band: target,
      hline,
      ariaLabel: `Treasury yield curve on ${fmtDate(S.date, "long")}`,
      emptyMessage: "No yields were published for this date.",
    };
    if (S.chart && S.chart.opts && host.querySelector("svg")) {
      S.chart.opts = options;
      S.chart.render();
    } else {
      S.chart = Y.charts.curveChart(host, options);
    }

    // Legend (pins can be removed from here).
    const legendItems = lines.map((line) => ({ label: line.label, color: line.color, dash: !!line.dash, removeId: line.pin }));
    if (target) legendItems.push({ label: target.label, color: "var(--band-target)", band: true });
    if (hline) legendItems.push({ label: hline.label, color: CPI_COLOR, dash: true });
    $("curve-legend").innerHTML = Y.charts.legendHtml(legendItems);

    // Notes about sources and unavailable overlays.
    const notes = [];
    notes.push(yearModel.source === "fred"
      ? `Source for ${S.date.slice(0, 4)}: FRED historical Treasury series. ${yearModel.notes}`
      : `Source: U.S. Treasury daily par yield curve${hasReal ? " and real yield curve" : ""}.`);
    if (S.overlays.target && !target) notes.push(S.manifest?.series?.FED_TARGET_LOWER ? "No Fed funds target is published for this date." : "Fed funds target range needs FRED data.");
    if (S.overlays.rrp && !rrpMain) notes.push(S.manifest?.series?.RRP_AWARD ? "The reverse repo facility rate is available from September 2013." : "Reverse repo rate needs FRED data.");
    if ((S.overlays.tips || S.overlays.breakeven) && !hasReal) notes.push("TIPS real yields are published from 2003.");
    if (S.overlays.cpi && !cpi) notes.push("No CPI reading is available for this date.");
    if (S.fallback) notes.push("Showing recent history only: the full archive appears after the next scheduled data update.");
    $("curve-note").textContent = notes.join(" ");

    renderTable(row, prevDate, prevRow, pinRows, hasReal && (S.overlays.tips || S.overlays.breakeven), rrpMain);
    syncControls();
    writeParams();
  }

  function renderTable(row, prevDate, prevRow, pinRows, showReal, rrpMain) {
    $("curve-table-date").textContent = fmtDate(S.date, "long");
    const thead = $("curve-table").querySelector("thead");
    const tbody = $("curve-table").querySelector("tbody");
    const pinHeads = S.pins.map((d) => `<th scope="col" class="num-col">${esc(fmtDate(d))}</th><th scope="col" class="num-col" title="${esc(fmtDate(S.date))} minus ${esc(fmtDate(d))}">Δ</th>`).join("");
    thead.innerHTML = `<tr><th scope="col">Maturity</th><th scope="col" class="num-col">Yield</th><th scope="col" class="num-col">Δ vs ${esc(prevDate && prevDate !== S.date ? fmtDate(prevDate) : "prior")}</th>${pinHeads}${showReal ? '<th scope="col" class="num-col">TIPS real</th><th scope="col" class="num-col">Breakeven</th>' : ""}</tr>`;
    const nominal = row?.nominal || {};
    const prev = prevRow?.nominal || {};
    const rows = [];
    if (rrpMain) {
      rows.push(`<tr><td><strong>RRP</strong> <span class="muted mat-desc">reverse repo award rate</span></td><td class="num-col"><strong>${esc(pct(rrpMain.value))}</strong></td><td class="num-col">—</td>${S.pins.map(() => "<td></td><td></td>").join("")}${showReal ? "<td></td><td></td>" : ""}</tr>`);
    }
    for (const c of NOMINAL) {
      const v = nominal[c.key];
      const inPins = pinRows.some((r) => isNum(r?.nominal?.[c.key]));
      if (!isNum(v) && !inPins) continue;
      const delta = isNum(v) && isNum(prev[c.key]) ? v - prev[c.key] : null;
      const pinCells = pinRows
        .map((r) => {
          const pv = r?.nominal?.[c.key];
          const d = isNum(v) && isNum(pv) ? v - pv : null;
          return `<td class="num-col">${esc(pct(pv))}</td><td class="num-col">${badge(d)}</td>`;
        })
        .join("");
      const realV = row?.real?.[c.key];
      const realCells = showReal
        ? `<td class="num-col">${esc(pct(realV))}</td><td class="num-col">${isNum(realV) && isNum(v) ? esc(pct(v - realV)) : "—"}</td>`
        : "";
      rows.push(`<tr><td><strong>${esc(c.label)}</strong> <span class="muted mat-desc">${esc(c.longLabel)}</span></td><td class="num-col"><strong>${esc(pct(v))}</strong></td><td class="num-col">${badge(delta)}</td>${pinCells}${realCells}</tr>`);
    }
    tbody.innerHTML = rows.join("") || `<tr><td colspan="3">No yields published for this date.</td></tr>`;
  }

  function badge(diff) {
    if (!isNum(diff)) return "—";
    const b = Math.round(diff * 100);
    const cls = b > 0 ? "bps-up" : b < 0 ? "bps-down" : "bps-flat";
    return `<span class="bps-badge ${cls}">${esc(bps(diff))}</span>`;
  }

  /* ---------------------------------------------------------------------- */
  /* Controls                                                                */
  /* ---------------------------------------------------------------------- */

  async function goTo(iso) {
    const snapped = await snapDate(iso);
    if (!snapped) return;
    S.date = snapped;
    await render();
  }

  function init() {
    $("curve-prev").addEventListener("click", async () => {
      if (S.date) await goTo(await stepDate(S.date, -1));
    });
    $("curve-next").addEventListener("click", async () => {
      if (S.date) await goTo(await stepDate(S.date, 1));
    });
    $("curve-latest").addEventListener("click", () => goTo(S.lastDate));
    $("curve-date").addEventListener("change", (ev) => {
      if (ev.target.value) goTo(ev.target.value);
    });
    let slideTimer = null;
    $("curve-slider").addEventListener("input", (ev) => {
      if (!S.firstDate) return;
      const iso = fromMs(toMs(S.firstDate) + Number(ev.target.value) * Y.dates.DAY_MS);
      $("curve-date").value = iso;
      clearTimeout(slideTimer);
      slideTimer = setTimeout(() => goTo(iso), 120);
    });
    $("curve-pin").addEventListener("click", () => {
      if (S.date && !S.pins.includes(S.date) && S.pins.length < MAX_PINS) {
        S.pins.push(S.date);
        render();
      }
    });
    $("curve-clear-pins").addEventListener("click", () => {
      S.pins = [];
      render();
    });
    $("curve-legend").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-remove]");
      if (!btn) return;
      S.pins = S.pins.filter((d) => d !== btn.getAttribute("data-remove"));
      render();
    });
    $("curve-spacing").addEventListener("change", (ev) => {
      S.spacing = ev.target.value;
      render();
    });
    for (const key of Object.keys(S.overlays)) {
      const box = $(`ov-${key}`);
      if (box) {
        box.addEventListener("change", () => {
          S.overlays[key] = box.checked;
          render();
        });
      }
    }
    // Keyboard shortcuts on the chart: [ and ] step through dates.
    $("curve-chart").addEventListener("keydown", async (ev) => {
      if (!S.date) return;
      if (ev.key === "[" || ev.key === "PageUp") {
        ev.preventDefault();
        await goTo(await stepDate(S.date, -1));
      } else if (ev.key === "]" || ev.key === "PageDown") {
        ev.preventDefault();
        await goTo(await stepDate(S.date, 1));
      }
    });
  }

  async function show(params) {
    try {
      await ensureReady();
    } catch (err) {
      $("curve-chart").innerHTML = `<div class="chart-empty">Could not load yield curve data (${esc(err.message || err)}).</div>`;
      return;
    }
    if (params.spacing && ["even", "sqrt", "linear"].includes(params.spacing)) S.spacing = params.spacing;
    if (params.ov) {
      const on = new Set(params.ov.split(","));
      for (const key of Object.keys(S.overlays)) S.overlays[key] = on.has(key);
    }
    if (params.pins !== undefined) {
      S.pins = params.pins
        .split(",")
        .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d))
        .slice(0, MAX_PINS);
    }
    const wanted = params.date && /^\d{4}-\d{2}-\d{2}$/.test(params.date) ? params.date : S.date || S.lastDate;
    if (wanted) {
      S.date = await snapDate(wanted);
    }
    await render();
  }

  function refresh() {
    S.ready = null;
    S.yearCache.clear();
    S.chart = null;
    show({ date: S.date, pins: S.pins.join(",") });
  }

  Y.router.register("curve", { title: "U.S. Treasury Yield Curve", init, show, refresh });
  Y.curve = { ensureReady, state: S };
})();
