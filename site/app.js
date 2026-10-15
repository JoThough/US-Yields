/**
 * Yields — Official U.S. Interest Rates Dashboard (site/app.js)
 *
 * Pure vanilla JavaScript (no external frameworks or runtime dependencies).
 * Uses relative data URL (./data/rates.json) so the website works at both
 * username.github.io/repository-name/ and a custom subdomain.
 */

(function () {
  "use strict";

  const DATA_URL = "./data/rates.json";
  const THEME_STORAGE_KEY = "yields_theme_preference";
  const STALE_STORAGE_KEY = "yields_stale_threshold_days";

  const BENCHMARK_CARDS = [
    { id: "3M", label: "3-Month Treasury", source: "treasury", maturityKey: "3M", tag: "Treasury" },
    { id: "2Y", label: "2-Year Treasury", source: "treasury", maturityKey: "2Y", tag: "Treasury" },
    { id: "5Y", label: "5-Year Treasury", source: "treasury", maturityKey: "5Y", tag: "Treasury" },
    { id: "10Y", label: "10-Year Treasury", source: "treasury", maturityKey: "10Y", tag: "Treasury" },
    { id: "30Y", label: "30-Year Treasury", source: "treasury", maturityKey: "30Y", tag: "Treasury" },
    { id: "EFFR", label: "Effective Fed Funds (EFFR)", source: "effr", tag: "NY Fed" },
    { id: "SOFR", label: "Secured Overnight (SOFR)", source: "sofr", tag: "NY Fed" },
  ];

  const HISTORICAL_SERIES_DEFS = [
    { id: "T_10Y", label: "10-Year Treasury", color: "#0284c7", defaultChecked: true, type: "treasury", key: "10Y" },
    { id: "T_2Y", label: "2-Year Treasury", color: "#7c3aed", defaultChecked: true, type: "treasury", key: "2Y" },
    { id: "T_3M", label: "3-Month Treasury", color: "#0d9488", defaultChecked: false, type: "treasury", key: "3M" },
    { id: "T_5Y", label: "5-Year Treasury", color: "#2563eb", defaultChecked: false, type: "treasury", key: "5Y" },
    { id: "T_30Y", label: "30-Year Treasury", color: "#4f46e5", defaultChecked: false, type: "treasury", key: "30Y" },
    { id: "EFFR", label: "EFFR (Overnight)", color: "#ea580c", defaultChecked: true, type: "effr" },
    { id: "SOFR", label: "SOFR (Overnight)", color: "#16a34a", defaultChecked: false, type: "sofr" },
    { id: "SPREAD_10Y_2Y", label: "10Y − 2Y Spread (%)", color: "#dc2626", defaultChecked: true, type: "spread" },
  ];

  const state = {
    dataset: null,
    selectedRange: "1Y",
    selectedSeriesIds: new Set(
      HISTORICAL_SERIES_DEFS.filter((s) => s.defaultChecked).map((s) => s.id)
    ),
    curveCompareMode: "1m",
    curveScaleMode: "sqrt",
    staleThresholdDays: 5,
    unifiedHistory: [], // [{ date, values: { T_10Y, T_2Y, ..., SPREAD_10Y_2Y } }]
  };

  /* --------------------------------------------------------------------------
   * Utility & Formatting Helpers
   * -------------------------------------------------------------------------- */

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatPercent(val, decimals = 2) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) {
      return "N/A";
    }
    return `${Number(val).toFixed(decimals)}%`;
  }

  function formatNumber(val, decimals = 2) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) {
      return "N/A";
    }
    return Number(val).toFixed(decimals);
  }

  function computeBpsChange(currentVal, previousVal) {
    if (
      currentVal === null ||
      currentVal === undefined ||
      previousVal === null ||
      previousVal === undefined
    ) {
      return null;
    }
    return Math.round((Number(currentVal) - Number(previousVal)) * 10000) / 100;
  }

  function formatBpsBadge(bps) {
    if (bps === null || bps === undefined || Number.isNaN(bps)) {
      return `<span class="bps-badge bps-flat" title="Comparison unavailable">N/A bps</span>`;
    }
    const rounded = Math.round(bps * 10) / 10;
    const formatted = Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1);
    if (rounded > 0) {
      return `<span class="bps-badge bps-up" title="Up ${formatted} basis points from previous observation">+${formatted} bps</span>`;
    }
    if (rounded < 0) {
      return `<span class="bps-badge bps-down" title="Down ${Math.abs(rounded)} basis points from previous observation">${formatted} bps</span>`;
    }
    return `<span class="bps-badge bps-flat" title="Unchanged from previous observation">0 bps</span>`;
  }

  function formatIsoTimestamp(isoStr) {
    if (!isoStr) return "Unavailable";
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return String(isoStr);
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC").replace(/Z$/, " UTC");
  }

  function calendarDaysBetween(olderDateStr, newerDateObj) {
    if (!olderDateStr) return null;
    const parts = olderDateStr.split("-").map(Number);
    if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
    const obsUtc = Date.UTC(parts[0], parts[1] - 1, parts[2]);
    const nowUtc = Date.UTC(
      newerDateObj.getUTCFullYear(),
      newerDateObj.getUTCMonth(),
      newerDateObj.getUTCDate()
    );
    return Math.floor((nowUtc - obsUtc) / (1000 * 60 * 60 * 24));
  }

  /* --------------------------------------------------------------------------
   * Theme & Controls Initialization
   * -------------------------------------------------------------------------- */

  function initTheme() {
    let saved = null;
    try {
      saved = localStorage.getItem(THEME_STORAGE_KEY);
    } catch (_) {
      saved = null;
    }
    if (saved === "dark" || saved === "light") {
      document.documentElement.setAttribute("data-theme", saved);
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.setAttribute("data-theme", "dark");
    }

    const toggleBtn = document.getElementById("theme-toggle-btn");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => {
        const current = document.documentElement.getAttribute("data-theme") || "light";
        const next = current === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        try {
          localStorage.setItem(THEME_STORAGE_KEY, next);
        } catch (_) {
          // ignore storage errors
        }
      });
    }
  }

  function initControls() {
    const staleSelect = document.getElementById("stale-threshold-select");
    if (staleSelect) {
      try {
        const savedDays = Number(localStorage.getItem(STALE_STORAGE_KEY));
        if ([4, 5, 7, 10, 14].includes(savedDays)) {
          state.staleThresholdDays = savedDays;
          staleSelect.value = String(savedDays);
        }
      } catch (_) {
        // ignore
      }
      staleSelect.addEventListener("change", () => {
        const val = Number(staleSelect.value);
        if (!Number.isNaN(val) && val >= 1) {
          state.staleThresholdDays = val;
          try {
            localStorage.setItem(STALE_STORAGE_KEY, String(val));
          } catch (_) {
            // ignore
          }
          if (state.dataset) {
            renderAlerts(state.dataset);
          }
        }
      });
    }

    const reloadBtn = document.getElementById("reload-data-btn");
    if (reloadBtn) {
      reloadBtn.addEventListener("click", () => {
        loadAndRenderData(true);
      });
    }

    const compareSelect = document.getElementById("curve-compare-select");
    if (compareSelect) {
      compareSelect.addEventListener("change", () => {
        state.curveCompareMode = compareSelect.value;
        if (state.dataset) {
          renderYieldCurveAndTable(state.dataset);
        }
      });
    }

    const scaleSelect = document.getElementById("curve-scale-select");
    if (scaleSelect) {
      scaleSelect.addEventListener("change", () => {
        state.curveScaleMode = scaleSelect.value;
        if (state.dataset) {
          renderYieldCurveAndTable(state.dataset);
        }
      });
    }

    const rangeButtons = document.querySelectorAll(".range-btn");
    rangeButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const range = btn.getAttribute("data-range");
        if (!range) return;
        state.selectedRange = range;
        rangeButtons.forEach((b) => {
          const isMatch = b.getAttribute("data-range") === range;
          b.classList.toggle("active", isMatch);
          b.setAttribute("aria-pressed", isMatch ? "true" : "false");
        });
        if (state.dataset) {
          renderHistoricalExplorer();
        }
      });
    });

    const csvBtn = document.getElementById("download-csv-btn");
    if (csvBtn) {
      csvBtn.addEventListener("click", () => {
        downloadSelectedCsv();
      });
    }

    renderSeriesCheckboxes();
  }

  function renderSeriesCheckboxes() {
    const container = document.getElementById("history-series-checkboxes");
    if (!container) return;

    container.innerHTML = HISTORICAL_SERIES_DEFS.map((series) => {
      const checkedAttr = state.selectedSeriesIds.has(series.id) ? "checked" : "";
      return `
        <label class="series-checkbox-label">
          <input
            type="checkbox"
            value="${escapeHtml(series.id)}"
            ${checkedAttr}
          />
          <span class="series-color-dot" style="background-color: ${escapeHtml(series.color)}"></span>
          <span>${escapeHtml(series.label)}</span>
        </label>
      `;
    }).join("");

    container.querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.addEventListener("change", () => {
        const id = input.value;
        if (input.checked) {
          state.selectedSeriesIds.add(id);
        } else {
          // Keep at least one series selected
          if (state.selectedSeriesIds.size <= 1) {
            input.checked = true;
            return;
          }
          state.selectedSeriesIds.delete(id);
        }
        if (state.dataset) {
          renderHistoricalExplorer();
        }
      });
    });
  }

  /* --------------------------------------------------------------------------
   * Data Extraction & Normalization
   * -------------------------------------------------------------------------- */

  function getLatestTwoTreasuryMaturityPoints(treasuryObs, maturityKey) {
    let latest = null;
    let previous = null;
    for (let i = treasuryObs.length - 1; i >= 0; i--) {
      const row = treasuryObs[i];
      const val = row?.yields?.[maturityKey];
      if (val !== null && val !== undefined && !Number.isNaN(Number(val))) {
        if (!latest) {
          latest = { date: row.date, value: Number(val) };
        } else {
          previous = { date: row.date, value: Number(val) };
          break;
        }
      }
    }
    return { latest, previous };
  }

  function getLatestTwoNyFedPoints(observations) {
    let latest = null;
    let previous = null;
    for (let i = observations.length - 1; i >= 0; i--) {
      const row = observations[i];
      const val = row?.rate;
      if (val !== null && val !== undefined && !Number.isNaN(Number(val))) {
        if (!latest) {
          latest = {
            date: row.date,
            value: Number(val),
            targetRateFrom: row.targetRateFrom ?? null,
            targetRateTo: row.targetRateTo ?? null,
          };
        } else {
          previous = {
            date: row.date,
            value: Number(val),
          };
          break;
        }
      }
    }
    return { latest, previous };
  }

  function buildUnifiedHistory(dataset) {
    const treasuryObs = dataset?.sources?.treasury?.observations || [];
    const effrObs = dataset?.sources?.effr?.observations || [];
    const sofrObs = dataset?.sources?.sofr?.observations || [];

    const byDate = new Map();

    function ensureDate(dateStr) {
      if (!byDate.has(dateStr)) {
        byDate.set(dateStr, {
          date: dateStr,
          values: {
            T_3M: null,
            T_2Y: null,
            T_5Y: null,
            T_10Y: null,
            T_30Y: null,
            EFFR: null,
            SOFR: null,
            SPREAD_10Y_2Y: null,
          },
        });
      }
      return byDate.get(dateStr);
    }

    for (const obs of treasuryObs) {
      if (!obs?.date || !obs.yields) continue;
      const entry = ensureDate(obs.date);
      const y3m = obs.yields["3M"];
      const y2y = obs.yields["2Y"];
      const y5y = obs.yields["5Y"];
      const y10y = obs.yields["10Y"];
      const y30y = obs.yields["30Y"];

      entry.values.T_3M = y3m !== null && y3m !== undefined ? Number(y3m) : null;
      entry.values.T_2Y = y2y !== null && y2y !== undefined ? Number(y2y) : null;
      entry.values.T_5Y = y5y !== null && y5y !== undefined ? Number(y5y) : null;
      entry.values.T_10Y = y10y !== null && y10y !== undefined ? Number(y10y) : null;
      entry.values.T_30Y = y30y !== null && y30y !== undefined ? Number(y30y) : null;

      // 10Y minus 2Y spread calculated strictly from observations on the exact same date
      if (entry.values.T_10Y !== null && entry.values.T_2Y !== null) {
        entry.values.SPREAD_10Y_2Y = Math.round((entry.values.T_10Y - entry.values.T_2Y) * 10000) / 10000;
      } else {
        entry.values.SPREAD_10Y_2Y = null;
      }
    }

    for (const obs of effrObs) {
      if (!obs?.date) continue;
      const entry = ensureDate(obs.date);
      entry.values.EFFR = obs.rate !== null && obs.rate !== undefined ? Number(obs.rate) : null;
    }

    for (const obs of sofrObs) {
      if (!obs?.date) continue;
      const entry = ensureDate(obs.date);
      entry.values.SOFR = obs.rate !== null && obs.rate !== undefined ? Number(obs.rate) : null;
    }

    return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
  }

  /* --------------------------------------------------------------------------
   * Rendering: Metadata Header & Alerts
   * -------------------------------------------------------------------------- */

  function renderHeaderMetadata(dataset) {
    const updatedEl = document.getElementById("dataset-updated-at");
    const obsSummaryEl = document.getElementById("latest-obs-summary");
    const statusPillsEl = document.getElementById("source-status-pills");

    if (updatedEl) {
      updatedEl.textContent = formatIsoTimestamp(dataset.updatedAt || dataset.lastCheckedAt);
    }

    const tDate = dataset?.sources?.treasury?.latestObservationDate || "N/A";
    const eDate = dataset?.sources?.effr?.latestObservationDate || "N/A";
    const sDate = dataset?.sources?.sofr?.latestObservationDate || "N/A";

    if (obsSummaryEl) {
      obsSummaryEl.textContent = `Treasury: ${tDate} · EFFR: ${eDate} · SOFR: ${sDate}`;
    }

    if (statusPillsEl) {
      const sources = [
        { key: "treasury", label: "Treasury" },
        { key: "effr", label: "EFFR" },
        { key: "sofr", label: "SOFR" },
      ];
      statusPillsEl.innerHTML = sources
        .map(({ key, label }) => {
          const src = dataset?.sources?.[key];
          const ok = src?.status === "ok";
          const cls = ok ? "status-pill status-pill-ok" : "status-pill status-pill-error";
          const icon = ok ? "✓" : "!";
          const title = ok
            ? `${label} source updated normally (latest observation ${src?.latestObservationDate || "N/A"})`
            : `${label} source encountered an error; displaying retained history (${src?.errorMessage || "unknown error"})`;
          return `<span class="${cls}" title="${escapeHtml(title)}">${icon} ${escapeHtml(label)}</span>`;
        })
        .join("");
    }
  }

  function renderAlerts(dataset, manualReloaded = false) {
    const container = document.getElementById("alerts-container");
    if (!container) return;

    const banners = [];
    const now = new Date();
    const utcDay = now.getUTCDay(); // 0 = Sun, 6 = Sat

    // 1. Partial source failure warnings
    for (const [key, src] of Object.entries(dataset?.sources || {})) {
      if (src && src.status !== "ok") {
        banners.push(`
          <div class="alert-banner alert-error" role="alert">
            <div>
              <strong>Partial Source Update Issue (${escapeHtml(src.shortName || key.toUpperCase())}):</strong>
              The latest scheduled fetch reported an issue (<code>${escapeHtml(src.errorMessage || "fetch error")}</code>).
              Displaying the last valid retained history through <strong>${escapeHtml(src.latestObservationDate || "N/A")}</strong>.
            </div>
          </div>
        `);
      }
    }

    // 2. Configurable staleness warning (distinguished from normal weekend gaps)
    const threshold = state.staleThresholdDays || 5;
    const staleSources = [];
    let hasWeekendGap = false;

    for (const [key, src] of Object.entries(dataset?.sources || {})) {
      const latestDate = src?.latestObservationDate;
      const ageDays = calendarDaysBetween(latestDate, now);
      if (ageDays !== null) {
        if (ageDays > threshold) {
          staleSources.push(`${src.shortName || key.toUpperCase()} (${latestDate}, ${ageDays} calendar days old)`);
        } else if (ageDays >= 2 && (utcDay === 0 || utcDay === 6 || utcDay === 1)) {
          hasWeekendGap = true;
        }
      }
    }

    if (staleSources.length > 0) {
      banners.push(`
        <div class="alert-banner alert-warning" role="alert">
          <div>
            <strong>Stale Observation Warning (&gt;${threshold} calendar days):</strong>
            One or more official series have not published a newer observation within your configured ${threshold}-day threshold:
            ${escapeHtml(staleSources.join("; "))}. Check if scheduled GitHub Actions updates are active or if markets were closed for an extended holiday.
          </div>
        </div>
      `);
    } else if (hasWeekendGap) {
      banners.push(`
        <div class="alert-banner alert-info">
          <div>
            <strong>Publication Schedule Note:</strong>
            U.S. Treasury yields, EFFR, and SOFR are published only on federal business days. Multi-day gaps over weekends and federal holidays are normal and are not fetch errors.
          </div>
        </div>
      `);
    }

    if (manualReloaded) {
      banners.push(`
        <div class="alert-banner alert-info">
          <div>
            <strong>Displayed Data Reloaded:</strong>
            Re-read <code>data/rates.json</code> at ${escapeHtml(formatIsoTimestamp(new Date().toISOString()))}.
            (New official market observations are fetched by scheduled GitHub Actions runs.)
          </div>
        </div>
      `);
    }

    container.innerHTML = banners.join("");
  }

  /* --------------------------------------------------------------------------
   * Rendering: Benchmark Cards, FOMC Target Range, and 10Y-2Y Spread Callout
   * -------------------------------------------------------------------------- */

  function renderBenchmarkCards(dataset) {
    const grid = document.getElementById("rate-cards-grid");
    if (!grid) return;

    const treasuryObs = dataset?.sources?.treasury?.observations || [];
    const effrObs = dataset?.sources?.effr?.observations || [];
    const sofrObs = dataset?.sources?.sofr?.observations || [];

    grid.innerHTML = BENCHMARK_CARDS.map((card) => {
      let latest = null;
      let previous = null;

      if (card.source === "treasury") {
        ({ latest, previous } = getLatestTwoTreasuryMaturityPoints(treasuryObs, card.maturityKey));
      } else if (card.source === "effr") {
        ({ latest, previous } = getLatestTwoNyFedPoints(effrObs));
      } else if (card.source === "sofr") {
        ({ latest, previous } = getLatestTwoNyFedPoints(sofrObs));
      }

      const rateStr = latest ? formatPercent(latest.value) : "Unavailable";
      const bps = latest && previous ? computeBpsChange(latest.value, previous.value) : null;
      const obsDate = latest?.date || "Unavailable";
      const prevMeta = previous
        ? `Prior: ${formatPercent(previous.value)} on ${previous.date}`
        : "Prior observation unavailable";

      return `
        <article class="card" role="listitem">
          <div class="card-top">
            <span class="card-series-label">${escapeHtml(card.label)}</span>
            <span class="card-source-tag">${escapeHtml(card.tag)}</span>
          </div>
          <div class="card-rate-row">
            <span class="card-rate-value">${escapeHtml(rateStr)}</span>
            ${formatBpsBadge(bps)}
          </div>
          <div class="card-meta">
            <span>Observation date: <strong>${escapeHtml(obsDate)}</strong></span>
            <span>${escapeHtml(prevMeta)}</span>
          </div>
        </article>
      `;
    }).join("");

    // Render FOMC Target Range Callout from EFFR observations
    const targetValEl = document.getElementById("target-range-value");
    const targetMetaEl = document.getElementById("target-range-meta");
    const { latest: latestEffr } = getLatestTwoNyFedPoints(effrObs);

    if (targetValEl && targetMetaEl) {
      if (
        latestEffr &&
        latestEffr.targetRateFrom !== null &&
        latestEffr.targetRateTo !== null
      ) {
        targetValEl.textContent = `${formatPercent(latestEffr.targetRateFrom)} – ${formatPercent(latestEffr.targetRateTo)}`;
        targetMetaEl.innerHTML = `Observation date: <strong>${escapeHtml(latestEffr.date)}</strong> (EFFR on same date: <strong>${escapeHtml(formatPercent(latestEffr.value))}</strong>)`;
      } else {
        targetValEl.textContent = "Not reported in latest EFFR observation";
        targetMetaEl.textContent = `Observation date: ${latestEffr?.date || "Unavailable"}`;
      }
    }

    // Render 10Y - 2Y Treasury Spread Callout (strictly same-date observations)
    const spreadValEl = document.getElementById("spread-latest-value");
    const spreadMetaEl = document.getElementById("spread-latest-meta");
    if (spreadValEl && spreadMetaEl) {
      let latestSpreadPoint = null;
      let prevSpreadPoint = null;

      for (let i = state.unifiedHistory.length - 1; i >= 0; i--) {
        const item = state.unifiedHistory[i];
        const sp = item.values.SPREAD_10Y_2Y;
        if (sp !== null && sp !== undefined) {
          if (!latestSpreadPoint) {
            latestSpreadPoint = {
              date: item.date,
              spread: sp,
              y10: item.values.T_10Y,
              y2: item.values.T_2Y,
            };
          } else {
            prevSpreadPoint = {
              date: item.date,
              spread: sp,
            };
            break;
          }
        }
      }

      if (latestSpreadPoint) {
        const sign = latestSpreadPoint.spread > 0 ? "+" : "";
        const spreadBps = Math.round(latestSpreadPoint.spread * 100);
        const dayChangeBps = prevSpreadPoint
          ? computeBpsChange(latestSpreadPoint.spread, prevSpreadPoint.spread)
          : null;
        spreadValEl.innerHTML = `${sign}${latestSpreadPoint.spread.toFixed(2)}% <span class="bps-badge ${latestSpreadPoint.spread >= 0 ? "bps-up" : "bps-down"}">${sign}${spreadBps} bps</span>`;
        spreadMetaEl.innerHTML = `Same-date observation: <strong>${escapeHtml(latestSpreadPoint.date)}</strong> (10Y: ${formatPercent(latestSpreadPoint.y10)} − 2Y: ${formatPercent(latestSpreadPoint.y2)}) · Daily Δ: ${dayChangeBps !== null ? (dayChangeBps > 0 ? `+${dayChangeBps}` : dayChangeBps) + " bps" : "N/A"}`;
      } else {
        spreadValEl.textContent = "Unavailable";
        spreadMetaEl.textContent = "No same-date 10Y and 2Y observation pair available.";
      }
    }
  }

  /* --------------------------------------------------------------------------
   * Rendering: Treasury Yield Curve SVG Chart & Full Maturities Table
   * -------------------------------------------------------------------------- */

  function findComparisonTreasuryObservation(treasuryObs, mode) {
    if (!treasuryObs || treasuryObs.length < 2 || mode === "none") {
      return null;
    }
    const latestObs = treasuryObs[treasuryObs.length - 1];
    if (mode === "prev") {
      return treasuryObs[treasuryObs.length - 2];
    }

    const latestParts = latestObs.date.split("-").map(Number);
    const targetUtc = new Date(Date.UTC(latestParts[0], latestParts[1] - 1, latestParts[2]));
    if (mode === "1m") {
      targetUtc.setUTCDate(targetUtc.getUTCDate() - 30);
    } else if (mode === "1y") {
      targetUtc.setUTCFullYear(targetUtc.getUTCFullYear() - 1);
    }
    const targetDateStr = targetUtc.toISOString().slice(0, 10);

    // Find closest observation on or before targetDateStr
    let candidate = null;
    for (let i = treasuryObs.length - 2; i >= 0; i--) {
      if (treasuryObs[i].date <= targetDateStr) {
        candidate = treasuryObs[i];
        break;
      }
    }
    return candidate || treasuryObs[0];
  }

  function renderYieldCurveAndTable(dataset) {
    const treasury = dataset?.sources?.treasury;
    const maturities = treasury?.maturities || [];
    const observations = treasury?.observations || [];
    const svg = document.getElementById("yield-curve-svg");
    const legendEl = document.getElementById("yield-curve-legend");
    const tbody = document.getElementById("treasury-maturities-tbody");
    const compareColHeader = document.getElementById("curve-compare-col-header");
    const tooltip = document.getElementById("yield-curve-tooltip");

    if (!observations.length || !maturities.length) {
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8">No Treasury yield curve observations available.</td></tr>`;
      }
      return;
    }

    const latestCurveObs = observations[observations.length - 1];
    const compareObs = findComparisonTreasuryObservation(observations, state.curveCompareMode);
    const tableSubtitle = document.getElementById("treasury-table-subtitle");

    if (tableSubtitle) {
      tableSubtitle.textContent = `Latest published Treasury curve observation date: ${latestCurveObs.date} (14 maturities ordered by duration from 1-Month to 30-Year; accessible table alternative for the chart above).`;
    }

    if (compareColHeader) {
      compareColHeader.textContent = compareObs
        ? `Comparison (${compareObs.date})`
        : "Comparison Yield (%)";
    }

    // Build table rows across all configured maturities (1M, 6W, 2M, ..., 30Y)
    const tableRowsHtml = maturities
      .map((mat) => {
        const { latest, previous } = getLatestTwoTreasuryMaturityPoints(observations, mat.key);
        const bps = latest && previous ? computeBpsChange(latest.value, previous.value) : null;
        const compVal = compareObs?.yields?.[mat.key];
        return `
          <tr>
            <td><strong>${escapeHtml(mat.label)}</strong> <code>${escapeHtml(mat.key)}</code></td>
            <td class="num-col">${Number(mat.years).toFixed(3)}</td>
            <td class="num-col"><strong>${latest ? escapeHtml(formatPercent(latest.value)) : "N/A"}</strong></td>
            <td>${escapeHtml(latest?.date || "N/A")}</td>
            <td class="num-col">${previous ? escapeHtml(formatPercent(previous.value)) : "N/A"}</td>
            <td>${escapeHtml(previous?.date || "N/A")}</td>
            <td class="num-col">${formatBpsBadge(bps)}</td>
            <td class="num-col">${compVal !== null && compVal !== undefined ? escapeHtml(formatPercent(compVal)) : "N/A"}</td>
          </tr>
        `;
      })
      .join("");

    if (tbody) {
      tbody.innerHTML = tableRowsHtml;
    }

    // Prepare SVG Yield Curve points with numeric duration axis (years)
    const latestPoints = [];
    const comparePoints = [];

    for (const mat of maturities) {
      const yLatest = latestCurveObs?.yields?.[mat.key];
      if (yLatest !== null && yLatest !== undefined && !Number.isNaN(Number(yLatest))) {
        latestPoints.push({
          key: mat.key,
          label: mat.label,
          years: Number(mat.years),
          yield: Number(yLatest),
          date: latestCurveObs.date,
        });
      }
      if (compareObs) {
        const yComp = compareObs?.yields?.[mat.key];
        if (yComp !== null && yComp !== undefined && !Number.isNaN(Number(yComp))) {
          comparePoints.push({
            key: mat.key,
            label: mat.label,
            years: Number(mat.years),
            yield: Number(yComp),
            date: compareObs.date,
          });
        }
      }
    }

    if (legendEl) {
      let html = `
        <span class="legend-item">
          <span class="legend-swatch" style="background: var(--accent-primary)"></span>
          Latest Curve (${escapeHtml(latestCurveObs.date)})
        </span>
      `;
      if (compareObs && comparePoints.length > 0) {
        html += `
          <span class="legend-item">
            <span class="legend-swatch legend-swatch-dashed" style="color: var(--text-muted)"></span>
            Comparison Curve (${escapeHtml(compareObs.date)})
          </span>
        `;
      }
      legendEl.innerHTML = html;
    }

    if (!svg || !latestPoints.length) return;

    const width = 860;
    const height = 340;
    const pad = { top: 24, right: 32, bottom: 52, left: 56 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;

    const allYields = [...latestPoints.map((p) => p.yield), ...comparePoints.map((p) => p.yield)];
    const minYRaw = Math.min(...allYields);
    const maxYRaw = Math.max(...allYields);
    const yMargin = Math.max((maxYRaw - minYRaw) * 0.18, 0.25);
    const yMin = Math.max(0, Math.floor((minYRaw - yMargin) * 4) / 4);
    const yMax = Math.ceil((maxYRaw + yMargin) * 4) / 4;

    const maxYears = 30.0;
    function xScale(years) {
      if (state.curveScaleMode === "linear") {
        return pad.left + (years / maxYears) * plotW;
      }
      // Square-root numeric duration spacing keeps short bills readable while remaining strictly numeric in years
      return pad.left + (Math.sqrt(years) / Math.sqrt(maxYears)) * plotW;
    }

    function yScale(val) {
      if (yMax === yMin) return pad.top + plotH / 2;
      return pad.top + plotH - ((val - yMin) / (yMax - yMin)) * plotH;
    }

    // Horizontal Y-axis gridlines
    const yTicks = 5;
    let gridHtml = "";
    for (let i = 0; i <= yTicks; i++) {
      const val = yMin + ((yMax - yMin) * i) / yTicks;
      const y = yScale(val);
      gridHtml += `
        <line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${width - pad.right}" y2="${y.toFixed(1)}" stroke="var(--chart-grid)" stroke-width="1" />
        <text x="${pad.left - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="var(--text-muted)" font-size="11.5">${val.toFixed(2)}%</text>
      `;
    }

    // Numeric horizontal axis ticks (in years)
    const xTickMarkers =
      state.curveScaleMode === "linear"
        ? [
            { years: 0.25, label: "0.25y" },
            { years: 2, label: "2y" },
            { years: 5, label: "5y" },
            { years: 10, label: "10y" },
            { years: 20, label: "20y" },
            { years: 30, label: "30y" },
          ]
        : [
            { years: 1 / 12, label: "1M (0.08y)" },
            { years: 0.25, label: "3M (0.25y)" },
            { years: 0.5, label: "6M (0.5y)" },
            { years: 1, label: "1Y" },
            { years: 2, label: "2Y" },
            { years: 5, label: "5Y" },
            { years: 10, label: "10Y" },
            { years: 20, label: "20Y" },
            { years: 30, label: "30Y" },
          ];

    for (const tick of xTickMarkers) {
      const x = xScale(tick.years);
      gridHtml += `
        <line x1="${x.toFixed(1)}" y1="${pad.top}" x2="${x.toFixed(1)}" y2="${pad.top + plotH}" stroke="var(--chart-grid)" stroke-dasharray="2,3" stroke-width="1" />
        <text x="${x.toFixed(1)}" y="${pad.top + plotH + 20}" text-anchor="middle" fill="var(--text-muted)" font-size="11">${escapeHtml(tick.label)}</text>
      `;
    }

    gridHtml += `
      <text x="${pad.left + plotW / 2}" y="${height - 8}" text-anchor="middle" fill="var(--text-secondary)" font-size="12" font-weight="600">
        Maturity Duration in Years (${state.curveScaleMode === "linear" ? "Linear 0–30 Years Scale" : "Numeric √Years Scale"})
      </text>
    `;

    function buildPath(pts) {
      return pts
        .map((p, idx) => `${idx === 0 ? "M" : "L"}${xScale(p.years).toFixed(1)},${yScale(p.yield).toFixed(1)}`)
        .join(" ");
    }

    let pathsHtml = "";
    if (comparePoints.length > 1) {
      pathsHtml += `
        <path d="${buildPath(comparePoints)}" fill="none" stroke="var(--text-muted)" stroke-width="2" stroke-dasharray="5,4" />
      `;
    }

    if (latestPoints.length > 1) {
      const areaPath =
        `${buildPath(latestPoints)} ` +
        `L${xScale(latestPoints[latestPoints.length - 1].years).toFixed(1)},${(pad.top + plotH).toFixed(1)} ` +
        `L${xScale(latestPoints[0].years).toFixed(1)},${(pad.top + plotH).toFixed(1)} Z`;
      pathsHtml += `
        <path d="${areaPath}" fill="var(--accent-primary)" fill-opacity="0.09" />
        <path d="${buildPath(latestPoints)}" fill="none" stroke="var(--accent-primary)" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" />
      `;
    }

    const compByKey = new Map(comparePoints.map((p) => [p.key, p]));
    const dotsHtml = latestPoints
      .map((p) => {
        const cx = xScale(p.years).toFixed(1);
        const cy = yScale(p.yield).toFixed(1);
        const compPt = compByKey.get(p.key);
        const compVal = compPt ? compPt.yield : "";
        const compDate = compPt ? compPt.date : "";
        return `
          <circle
            class="curve-point"
            cx="${cx}"
            cy="${cy}"
            r="5"
            fill="var(--bg-surface)"
            stroke="var(--accent-primary)"
            stroke-width="2.5"
            tabindex="0"
            role="button"
            aria-label="${escapeHtml(p.label)} (${p.years.toFixed(2)} years): ${p.yield.toFixed(2)}% on ${escapeHtml(p.date)}"
            data-label="${escapeHtml(p.label)}"
            data-key="${escapeHtml(p.key)}"
            data-years="${p.years.toFixed(3)}"
            data-yield="${p.yield.toFixed(2)}"
            data-date="${escapeHtml(p.date)}"
            data-comp-yield="${compVal !== "" ? Number(compVal).toFixed(2) : ""}"
            data-comp-date="${escapeHtml(compDate)}"
          />
        `;
      })
      .join("");

    svg.innerHTML = `
      <title id="yield-curve-svg-title">U.S. Treasury Par Yield Curve (${escapeHtml(latestCurveObs.date)})</title>
      <desc id="yield-curve-svg-desc">Par yield curve across ${latestPoints.length} maturities from ${latestPoints[0].label} to ${latestPoints[latestPoints.length - 1].label}.</desc>
      ${gridHtml}
      ${pathsHtml}
      ${dotsHtml}
    `;

    // Interactive hover and keyboard focus tooltip for curve points
    if (tooltip) {
      const showPointTooltip = (circle) => {
        const label = circle.getAttribute("data-label");
        const years = circle.getAttribute("data-years");
        const yld = circle.getAttribute("data-yield");
        const dt = circle.getAttribute("data-date");
        const compYld = circle.getAttribute("data-comp-yield");
        const compDt = circle.getAttribute("data-comp-date");

        let html = `
          <div class="tooltip-title">${escapeHtml(label)} (${escapeHtml(years)} yrs)</div>
          <div class="tooltip-row"><span>Latest (${escapeHtml(dt)}):</span><strong>${escapeHtml(yld)}%</strong></div>
        `;
        if (compYld) {
          const diffBps = computeBpsChange(Number(yld), Number(compYld));
          const sign = diffBps > 0 ? "+" : "";
          html += `
            <div class="tooltip-row"><span>Compare (${escapeHtml(compDt)}):</span><span>${escapeHtml(compYld)}%</span></div>
            <div class="tooltip-row"><span>Difference:</span><strong>${sign}${diffBps} bps</strong></div>
          `;
        }
        tooltip.innerHTML = html;
        tooltip.hidden = false;

        const cx = Number(circle.getAttribute("cx"));
        const cy = Number(circle.getAttribute("cy"));
        const leftPct = (cx / width) * 100;
        const topPct = (cy / height) * 100;
        tooltip.style.left = `clamp(8px, calc(${leftPct}% - 90px), calc(100% - 210px))`;
        tooltip.style.top = `${Math.max(4, topPct - 24)}%`;
      };

      const hidePointTooltip = () => {
        tooltip.hidden = true;
      };

      svg.querySelectorAll(".curve-point").forEach((circle, idx, allCircles) => {
        circle.addEventListener("mouseenter", () => showPointTooltip(circle));
        circle.addEventListener("focus", () => showPointTooltip(circle));
        circle.addEventListener("mouseleave", hidePointTooltip);
        circle.addEventListener("blur", hidePointTooltip);
        circle.addEventListener("keydown", (ev) => {
          if (ev.key === "ArrowRight" || ev.key === "ArrowUp") {
            ev.preventDefault();
            const next = allCircles[Math.min(allCircles.length - 1, idx + 1)];
            if (next) next.focus();
          } else if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") {
            ev.preventDefault();
            const prev = allCircles[Math.max(0, idx - 1)];
            if (prev) prev.focus();
          } else if (ev.key === "Escape") {
            hidePointTooltip();
          }
        });
      });
    }
  }

  /* --------------------------------------------------------------------------
   * Rendering: Historical Explorer SVG Chart, Table Alternative, and CSV Export
   * -------------------------------------------------------------------------- */

  function getFilteredHistoryRows() {
    if (!state.unifiedHistory.length) return [];
    const latestDateStr = state.unifiedHistory[state.unifiedHistory.length - 1].date;
    const parts = latestDateStr.split("-").map(Number);
    const cutoff = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));

    switch (state.selectedRange) {
      case "1M":
        cutoff.setUTCMonth(cutoff.getUTCMonth() - 1);
        break;
      case "3M":
        cutoff.setUTCMonth(cutoff.getUTCMonth() - 3);
        break;
      case "1Y":
        cutoff.setUTCFullYear(cutoff.getUTCFullYear() - 1);
        break;
      case "2Y":
      default:
        cutoff.setUTCFullYear(cutoff.getUTCFullYear() - 2);
        break;
    }

    const cutoffStr = cutoff.toISOString().slice(0, 10);
    return state.unifiedHistory.filter((row) => row.date >= cutoffStr);
  }

  function renderHistoricalExplorer() {
    const svg = document.getElementById("history-chart-svg");
    const legendEl = document.getElementById("history-chart-legend");
    const tooltip = document.getElementById("history-chart-tooltip");
    const thead = document.getElementById("history-table-thead");
    const tbody = document.getElementById("history-table-tbody");
    const countEl = document.getElementById("history-table-count");

    const rows = getFilteredHistoryRows();
    const activeDefs = HISTORICAL_SERIES_DEFS.filter((s) => state.selectedSeriesIds.has(s.id));

    if (countEl) {
      countEl.textContent = String(rows.length);
    }

    // Render Legend
    if (legendEl) {
      legendEl.innerHTML = activeDefs
        .map(
          (s) => `
          <span class="legend-item">
            <span class="legend-swatch" style="background: ${escapeHtml(s.color)}"></span>
            ${escapeHtml(s.label)}
          </span>
        `
        )
        .join("");
    }

    // Render Accessible Table Alternative (newest first for quick inspection)
    if (thead && tbody) {
      thead.innerHTML = `
        <tr>
          <th scope="col">Observation Date</th>
          ${activeDefs.map((s) => `<th scope="col" class="num-col">${escapeHtml(s.label)}</th>`).join("")}
        </tr>
      `;
      const reversed = [...rows].reverse();
      tbody.innerHTML = reversed
        .map((r) => {
          const cells = activeDefs
            .map((s) => {
              const v = r.values[s.id];
              return `<td class="num-col">${v !== null && v !== undefined ? escapeHtml(formatNumber(v, 2) + "%") : "N/A"}</td>`;
            })
            .join("");
          return `<tr><td><strong>${escapeHtml(r.date)}</strong></td>${cells}</tr>`;
        })
        .join("");
    }

    if (!svg || !rows.length || !activeDefs.length) return;

    const width = 860;
    const height = 360;
    const pad = { top: 24, right: 32, bottom: 46, left: 58 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;

    const allVals = [];
    for (const r of rows) {
      for (const s of activeDefs) {
        const v = r.values[s.id];
        if (v !== null && v !== undefined && !Number.isNaN(v)) {
          allVals.push(v);
        }
      }
    }

    if (!allVals.length) {
      svg.innerHTML = `<text x="430" y="180" text-anchor="middle" fill="var(--text-muted)">No observations available for the selected range and series.</text>`;
      return;
    }

    const minVal = Math.min(...allVals);
    const maxVal = Math.max(...allVals);
    const margin = Math.max((maxVal - minVal) * 0.12, 0.2);
    const yMin = Math.floor((minVal - margin) * 4) / 4;
    const yMax = Math.ceil((maxVal + margin) * 4) / 4;

    function xScale(idx) {
      if (rows.length <= 1) return pad.left + plotW / 2;
      return pad.left + (idx / (rows.length - 1)) * plotW;
    }

    function yScale(val) {
      if (yMax === yMin) return pad.top + plotH / 2;
      return pad.top + plotH - ((val - yMin) / (yMax - yMin)) * plotH;
    }

    let gridHtml = "";
    const yTicks = 5;
    for (let i = 0; i <= yTicks; i++) {
      const val = yMin + ((yMax - yMin) * i) / yTicks;
      const y = yScale(val);
      gridHtml += `
        <line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${width - pad.right}" y2="${y.toFixed(1)}" stroke="var(--chart-grid)" stroke-width="1" />
        <text x="${pad.left - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="var(--text-muted)" font-size="11.5">${val.toFixed(2)}%</text>
      `;
    }

    // Highlight 0.00% line when negative values or spread are in range
    if (yMin <= 0 && yMax >= 0) {
      const zeroY = yScale(0);
      gridHtml += `
        <line x1="${pad.left}" y1="${zeroY.toFixed(1)}" x2="${width - pad.right}" y2="${zeroY.toFixed(1)}" stroke="var(--chart-zero)" stroke-width="1.5" stroke-dasharray="4,3" />
      `;
    }

    // X-axis date ticks (~6 evenly spaced labels)
    const xTickCount = Math.min(6, rows.length);
    for (let i = 0; i < xTickCount; i++) {
      const idx = xTickCount === 1 ? 0 : Math.round((i * (rows.length - 1)) / (xTickCount - 1));
      const x = xScale(idx);
      const dateLabel = rows[idx].date;
      gridHtml += `
        <line x1="${x.toFixed(1)}" y1="${pad.top}" x2="${x.toFixed(1)}" y2="${pad.top + plotH}" stroke="var(--chart-grid)" stroke-dasharray="2,3" stroke-width="1" />
        <text x="${x.toFixed(1)}" y="${pad.top + plotH + 22}" text-anchor="middle" fill="var(--text-muted)" font-size="11">${escapeHtml(dateLabel)}</text>
      `;
    }

    // Series polylines (skip nulls without inventing values)
    let linesHtml = "";
    for (const s of activeDefs) {
      const segments = [];
      let currentSeg = [];
      rows.forEach((r, idx) => {
        const v = r.values[s.id];
        if (v !== null && v !== undefined && !Number.isNaN(v)) {
          currentSeg.push(`${xScale(idx).toFixed(1)},${yScale(v).toFixed(1)}`);
        } else if (currentSeg.length) {
          segments.push(currentSeg);
          currentSeg = [];
        }
      });
      if (currentSeg.length) {
        segments.push(currentSeg);
      }

      const dAttr = segments
        .filter((seg) => seg.length > 0)
        .map((seg) => `M${seg.join(" L")}`)
        .join(" ");

      if (dAttr) {
        linesHtml += `
          <path d="${dAttr}" fill="none" stroke="${escapeHtml(s.color)}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
        `;
      }
    }

    // Crosshair & overlay rect for pointer/keyboard inspection
    svg.innerHTML = `
      <title id="history-svg-title">Historical U.S. Interest Rates (${escapeHtml(state.selectedRange)} range)</title>
      <desc id="history-svg-desc">Showing ${activeDefs.map((s) => s.label).join(", ")} from ${escapeHtml(rows[0].date)} to ${escapeHtml(rows[rows.length - 1].date)}.</desc>
      ${gridHtml}
      ${linesHtml}
      <line id="history-hover-line" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="var(--text-muted)" stroke-width="1" stroke-dasharray="3,3" visibility="hidden" />
      <rect id="history-overlay-rect" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor: crosshair;" />
    `;

    const overlay = document.getElementById("history-overlay-rect");
    const hoverLine = document.getElementById("history-hover-line");

    if (overlay && tooltip && hoverLine) {
      let currentFocusIdx = rows.length - 1;

      const showIndexTooltip = (idx) => {
        const clamped = Math.max(0, Math.min(rows.length - 1, idx));
        const row = rows[clamped];
        const x = xScale(clamped);
        hoverLine.setAttribute("x1", x.toFixed(1));
        hoverLine.setAttribute("x2", x.toFixed(1));
        hoverLine.setAttribute("visibility", "visible");

        const rowsHtml = activeDefs
          .map((s) => {
            const val = row.values[s.id];
            const valText = val !== null && val !== undefined ? `${val.toFixed(2)}%` : "N/A";
            return `
              <div class="tooltip-row">
                <span><span class="series-color-dot" style="background:${escapeHtml(s.color)};width:8px;height:8px;margin-right:5px;"></span>${escapeHtml(s.label)}:</span>
                <strong>${escapeHtml(valText)}</strong>
              </div>
            `;
          })
          .join("");

        tooltip.innerHTML = `
          <div class="tooltip-title">Observation Date: ${escapeHtml(row.date)}</div>
          ${rowsHtml}
        `;
        tooltip.hidden = false;

        const leftPct = (x / width) * 100;
        tooltip.style.left = `clamp(8px, calc(${leftPct}% - 105px), calc(100% - 235px))`;
        tooltip.style.top = "10%";
      };

      const hideHistoryTooltip = () => {
        tooltip.hidden = true;
        hoverLine.setAttribute("visibility", "hidden");
      };

      overlay.addEventListener("mousemove", (ev) => {
        const rect = svg.getBoundingClientRect();
        const relX = ((ev.clientX - rect.left) / rect.width) * width;
        const ratio = (relX - pad.left) / plotW;
        const idx = Math.round(ratio * (rows.length - 1));
        currentFocusIdx = Math.max(0, Math.min(rows.length - 1, idx));
        showIndexTooltip(currentFocusIdx);
      });

      overlay.addEventListener("mouseleave", hideHistoryTooltip);

      svg.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowLeft") {
          ev.preventDefault();
          currentFocusIdx = Math.max(0, currentFocusIdx - 1);
          showIndexTooltip(currentFocusIdx);
        } else if (ev.key === "ArrowRight") {
          ev.preventDefault();
          currentFocusIdx = Math.min(rows.length - 1, currentFocusIdx + 1);
          showIndexTooltip(currentFocusIdx);
        } else if (ev.key === "Escape") {
          hideHistoryTooltip();
        }
      });

      svg.addEventListener("blur", hideHistoryTooltip);
    }
  }

  function downloadSelectedCsv() {
    const rows = getFilteredHistoryRows();
    const activeDefs = HISTORICAL_SERIES_DEFS.filter((s) => state.selectedSeriesIds.has(s.id));
    if (!rows.length || !activeDefs.length) return;

    const headers = ["date", ...activeDefs.map((s) => s.label)];
    const csvLines = [headers.map((h) => `"${String(h).replace(/"/g, '""')}"`).join(",")];

    for (const r of rows) {
      const vals = activeDefs.map((s) => {
        const v = r.values[s.id];
        return v !== null && v !== undefined && !Number.isNaN(v) ? Number(v).toFixed(4) : "";
      });
      csvLines.push([r.date, ...vals].join(","));
    }

    const csvContent = csvLines.join("\n") + "\n";
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);

    const startDate = rows[0].date;
    const endDate = rows[rows.length - 1].date;
    const link = document.createElement("a");
    link.href = url;
    link.download = `yields-us-rates-${state.selectedRange.toLowerCase()}-${startDate}-to-${endDate}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  /* --------------------------------------------------------------------------
   * Data Fetching & Initialization Entry Point
   * -------------------------------------------------------------------------- */

  async function loadAndRenderData(isManualReload = false) {
    const alertsContainer = document.getElementById("alerts-container");
    try {
      const fetchUrl = isManualReload ? `${DATA_URL}?t=${Date.now()}` : DATA_URL;
      const response = await fetch(fetchUrl, {
        headers: { Accept: "application/json" },
        cache: isManualReload ? "no-store" : "default",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status} (${response.statusText}) while loading ${DATA_URL}`);
      }

      const data = await response.json();
      if (!data || typeof data !== "object" || !data.sources) {
        throw new Error("Invalid dataset schema in data/rates.json");
      }

      state.dataset = data;
      state.unifiedHistory = buildUnifiedHistory(data);

      renderHeaderMetadata(data);
      renderAlerts(data, isManualReload);
      renderBenchmarkCards(data);
      renderYieldCurveAndTable(data);
      renderHistoricalExplorer();
    } catch (err) {
      if (alertsContainer) {
        alertsContainer.innerHTML = `
          <div class="alert-banner alert-error" role="alert">
            <div>
              <strong>Unable to Load Saved Interest Rate Data:</strong>
              Could not read <code>data/rates.json</code> (${escapeHtml(err.message || String(err))}).
              If you are running locally, make sure you have generated the dataset with <code>python3 scripts/update_data.py</code> and are serving the project over HTTP rather than <code>file://</code>.
            </div>
          </div>
        `;
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initControls();
    loadAndRenderData(false);
  });
})();
