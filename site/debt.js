/**
 * Yields — Federal Debt views (site/debt.js)
 *
 * Debt Ceiling: debt subject to the statutory limit, the limit itself and the
 * Treasury General Account cash balance (Daily Treasury Statement).
 * Debt Maturity Composition: marketable and nonmarketable debt by instrument
 * type (Monthly Statement of the Public Debt).
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, isNum, money, fmtDate, MONTHS } = Y.fmt;
  const { toMs, addMonths } = Y.dates;
  const $ = (id) => document.getElementById(id);

  const trillions = (millions, decimals = 2) => `$${(millions / 1e6).toFixed(decimals)}T`;

  function rangeButtons(hostId, presets, current) {
    const host = $(hostId);
    host.innerHTML = presets
      .map((p) => `<button type="button" class="btn range-btn${p === current ? " active" : ""}" data-range="${p}" aria-pressed="${p === current}">${p === "MAX" ? "Max" : p}</button>`)
      .join("");
  }

  function syncRangeButtons(hostId, current) {
    document.querySelectorAll(`#${hostId} .range-btn`).forEach((btn) => {
      const on = btn.getAttribute("data-range") === current;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function toSeries(dates, values) {
    const d = [];
    const v = [];
    dates.forEach((date, i) => {
      if (isNum(values[i])) {
        d.push(date);
        v.push(values[i]);
      }
    });
    return { dates: d, t: Float64Array.from(d.map(toMs)), v: Float64Array.from(v) };
  }

  /* ---------------------------------------------------------------------- */
  /* Debt ceiling                                                            */
  /* ---------------------------------------------------------------------- */

  const C = { data: null, range: "4Y", from: null, to: null, chart: null, ready: null };

  function ceilingReady() {
    if (!C.ready) {
      C.ready = Y.data.getJson("debt/ceiling.json").then((raw) => {
        C.data = raw;
        C.series = {
          subject: toSeries(raw.dates, raw.debtSubjectToLimit),
          limit: toSeries(raw.dates, raw.statutoryLimit),
          net: toSeries(
            raw.dates,
            raw.dates.map((_d, i) => (isNum(raw.debtSubjectToLimit[i]) && isNum(raw.cashBalance[i]) ? raw.debtSubjectToLimit[i] - raw.cashBalance[i] : null))
          ),
        };
      });
      C.ready.catch(() => {
        C.ready = null;
      });
    }
    return C.ready;
  }

  function renderCeilingStats() {
    const raw = C.data;
    const last = raw.dates.length - 1;
    const date = raw.dates[last];
    const subject = raw.debtSubjectToLimit[last];
    const limit = raw.statutoryLimit[last];
    const cash = raw.cashBalance[last];
    const headroom = isNum(limit) && isNum(subject) ? limit - subject : null;
    const withCash = isNum(headroom) && isNum(cash) ? headroom + cash : null;
    const headline = $("ceiling-headline");
    if (isNum(limit)) {
      headline.innerHTML = `The U.S. government has about <strong>${esc(money(headroom))}</strong> of borrowing room left under the <strong>${esc(trillions(limit))}</strong> debt limit` +
        (isNum(cash) ? `, plus <strong>${esc(money(cash))}</strong> of cash in the Treasury General Account (<strong>${esc(money(withCash))}</strong> in total)` : "") +
        `. <span class="muted">Figures as of ${esc(fmtDate(date, "long"))}.</span>`;
    } else {
      headline.innerHTML = `The statutory debt limit is currently <strong>suspended</strong>, so there is no fixed cap on new borrowing. <span class="muted">Figures as of ${esc(fmtDate(date, "long"))}.</span>`;
    }
    const cards = [
      { label: "Statutory debt limit", value: isNum(limit) ? trillions(limit) : "Suspended" },
      { label: "Debt subject to limit", value: isNum(subject) ? trillions(subject) : "—" },
      { label: "Room under the limit", value: isNum(headroom) ? money(headroom) : "—", tone: isNum(headroom) && headroom < 2e5 ? "warn" : "" },
      { label: "Cash in Treasury General Account", value: isNum(cash) ? money(cash) : "—" },
      { label: "Room including cash", value: isNum(withCash) ? money(withCash) : "—" },
      { label: "Total public debt outstanding", value: isNum(raw.totalOutstanding[last]) ? trillions(raw.totalOutstanding[last]) : "—" },
    ];
    $("ceiling-stats").innerHTML = cards
      .map((c) => `<div class="stat-card${c.tone ? ` stat-${c.tone}` : ""}"><div class="stat-label">${esc(c.label)}</div><div class="stat-value">${esc(c.value)}</div></div>`)
      .join("");
  }

  function renderCeilingChart() {
    const raw = C.data;
    const first = raw.dates[0];
    const last = raw.dates[raw.dates.length - 1];
    if (C.range !== "CUSTOM") [C.from, C.to] = Y.ranges.resolve(C.range, last, first);
    $("ceiling-from").value = C.from;
    $("ceiling-to").value = C.to;
    syncRangeButtons("ceiling-ranges", C.range);
    const fmt = (v) => trillions(v, 2);
    const series = [
      { id: "subject", label: "Debt subject to limit", color: "#2563eb", ...C.series.subject, freq: "daily", valueFormat: fmt },
      { id: "net", label: "Debt subject to limit minus cash (TGA)", color: "#16a34a", ...C.series.net, freq: "daily", valueFormat: fmt },
      { id: "limit", label: "Statutory debt limit", color: "#dc2626", ...C.series.limit, freq: "daily", step: true, width: 2.4, valueFormat: fmt },
    ];
    const options = {
      series,
      xDomain: [toMs(C.from), toMs(C.to)],
      yFormat: (v, _d, step) => `$${(v / 1e6).toFixed(Math.max(1, Y.charts.decimalsFor((step || 1e5) / 1e6)))}T`,
      ariaLabel: "Debt subject to limit, debt minus cash, and the statutory debt limit over time",
    };
    const host = $("ceiling-chart");
    if (C.chart && host.querySelector("svg")) {
      C.chart.opts = options;
      C.chart.render();
    } else {
      C.chart = Y.charts.timeChart(host, options);
    }
    $("ceiling-legend").innerHTML = Y.charts.legendHtml(series.map((s) => ({ label: s.label, color: s.color })));
    Y.router.setParams({ range: C.range !== "CUSTOM" && C.range !== "4Y" ? C.range : "", from: C.range === "CUSTOM" ? C.from : "", to: C.range === "CUSTOM" ? C.to : "" });
  }

  async function showCeiling(params) {
    try {
      await ceilingReady();
    } catch (err) {
      $("ceiling-headline").textContent = "Debt data appears after the next scheduled data update.";
      $("ceiling-chart").innerHTML = `<div class="chart-empty">Could not load debt data (${esc(err.message || err)}).</div>`;
      return;
    }
    if (params.from && params.to) {
      C.range = "CUSTOM";
      C.from = params.from;
      C.to = params.to;
    } else if (params.range) {
      C.range = params.range;
    }
    renderCeilingStats();
    renderCeilingChart();
  }

  function initCeiling() {
    rangeButtons("ceiling-ranges", ["1Y", "2Y", "4Y", "10Y", "MAX"], C.range);
    $("ceiling-ranges").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-range]");
      if (!btn || !C.data) return;
      C.range = btn.getAttribute("data-range");
      renderCeilingChart();
    });
    const onDates = () => {
      const from = $("ceiling-from").value;
      const to = $("ceiling-to").value;
      if (from && to && from < to && C.data) {
        C.range = "CUSTOM";
        C.from = from;
        C.to = to;
        renderCeilingChart();
      }
    };
    $("ceiling-from").addEventListener("change", onDates);
    $("ceiling-to").addEventListener("change", onDates);
  }

  Y.router.register("debt-ceiling", {
    title: "U.S. Federal Debt and the Debt Ceiling",
    group: "debt",
    init: initCeiling,
    show: showCeiling,
    refresh() {
      C.ready = null;
      C.chart = null;
      showCeiling({});
    },
  });

  /* ---------------------------------------------------------------------- */
  /* Debt maturity composition                                               */
  /* ---------------------------------------------------------------------- */

  const LAYERS = [
    { key: "bills", label: "Bills (under 1 year)", color: "#0ea5e9" },
    { key: "frn", label: "Floating rate notes (2 years)", color: "#14b8a6" },
    { key: "notes", label: "Notes (1–10 years)", color: "#6366f1" },
    { key: "tips", label: "TIPS (inflation-protected)", color: "#f59e0b" },
    { key: "bonds", label: "Bonds (over 10 years)", color: "#ef4444" },
    { key: "otherMarketable", label: "Other marketable", color: "#a855f7" },
    { key: "nonmarketable", label: "Nonmarketable", color: "#94a3b8" },
  ];

  const M = { data: null, range: "10Y", percent: false, chart: null, ready: null };

  function maturitiesReady() {
    if (!M.ready) {
      M.ready = Y.data.getJson("debt/maturities.json").then((raw) => {
        M.data = raw;
        M.t = raw.dates.map(toMs);
      });
      M.ready.catch(() => {
        M.ready = null;
      });
    }
    return M.ready;
  }

  function monthLabel(iso) {
    return `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
  }

  function renderMaturities() {
    const raw = M.data;
    const first = raw.dates[0];
    const last = raw.dates[raw.dates.length - 1];
    const [from, to] = Y.ranges.resolve(M.range, last, first);
    syncRangeButtons("maturities-ranges", M.range);
    $("maturities-percent").checked = M.percent;
    const layers = LAYERS.map((l) => ({ label: l.label, color: l.color, values: raw[l.key] || [] }));
    const options = {
      t: M.t,
      layers,
      xDomain: [toMs(from), toMs(to)],
      percent: M.percent,
      yFormat: M.percent ? (v) => `${v.toFixed(0)}%` : (v) => `$${(v / 1e6).toFixed(v % 1e6 === 0 ? 0 : 1)}T`,
      valueFormat: (v) => trillions(v, 2),
      dateLabel: monthLabel,
      ariaLabel: "Federal debt composition by instrument type over time",
    };
    const host = $("maturities-chart");
    if (M.chart && host.querySelector("svg")) {
      M.chart.opts = options;
      M.chart.render();
    } else {
      M.chart = Y.charts.stackedChart(host, options);
    }
    $("maturities-legend").innerHTML = Y.charts.legendHtml(LAYERS.slice().reverse().map((l) => ({ label: l.label, color: l.color })));
    $("maturities-asof").textContent = `Figures as of ${fmtDate(last, "long")} (Monthly Statement of the Public Debt).`;

    // Latest composition table with 12-month change.
    const li = raw.dates.length - 1;
    const prevIso = addMonths(last, -12).slice(0, 7);
    const pi = raw.dates.findIndex((d) => d.slice(0, 7) === prevIso);
    const total = raw.total[li];
    const rows = LAYERS.map((l) => {
      const v = raw[l.key][li];
      const p = pi >= 0 ? raw[l.key][pi] : null;
      const change = isNum(v) && isNum(p) ? v - p : null;
      return `<tr><td><span class="series-color-dot" style="background:${esc(l.color)}"></span> ${esc(l.label)}</td><td class="num-col">${esc(isNum(v) ? trillions(v) : "—")}</td><td class="num-col">${isNum(v) && total ? `${((v / total) * 100).toFixed(1)}%` : "—"}</td><td class="num-col">${isNum(change) ? `${change >= 0 ? "+" : "−"}${esc(money(Math.abs(change)))}` : "—"}</td></tr>`;
    }).join("");
    $("maturities-table").querySelector("tbody").innerHTML =
      rows + `<tr class="total-row"><td><strong>Total public debt outstanding</strong></td><td class="num-col"><strong>${esc(isNum(total) ? trillions(total) : "—")}</strong></td><td class="num-col">100%</td><td class="num-col"></td></tr>`;
    $("maturities-table-head").textContent = `Composition on ${fmtDate(last, "long")}`;
    Y.router.setParams({ range: M.range !== "10Y" ? M.range : "", pct: M.percent ? "1" : "" });
  }

  async function showMaturities(params) {
    try {
      await maturitiesReady();
    } catch (err) {
      $("maturities-chart").innerHTML = `<div class="chart-empty">Could not load debt composition data (${esc(err.message || err)}).</div>`;
      return;
    }
    if (params.range) M.range = params.range;
    if (params.pct) M.percent = params.pct === "1";
    renderMaturities();
  }

  function initMaturities() {
    rangeButtons("maturities-ranges", ["1Y", "3Y", "5Y", "10Y", "MAX"], M.range);
    $("maturities-ranges").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-range]");
      if (!btn || !M.data) return;
      M.range = btn.getAttribute("data-range");
      renderMaturities();
    });
    $("maturities-percent").addEventListener("change", (ev) => {
      M.percent = ev.target.checked;
      if (M.data) renderMaturities();
    });
  }

  Y.router.register("debt-maturities", {
    title: "U.S. Federal Debt Maturity Composition",
    group: "debt",
    init: initMaturities,
    show: showMaturities,
    refresh() {
      M.ready = null;
      M.chart = null;
      showMaturities({});
    },
  });
})();
