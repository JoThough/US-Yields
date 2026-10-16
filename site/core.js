/**
 * Yields — shared helpers (site/core.js)
 *
 * Formatting, date helpers, cached data loading, the hash router, the
 * "data refreshed … ago" indicator and a small dependency-free SVG chart
 * engine used by every view. Exposes everything on window.Yields.
 */
(function () {
  "use strict";

  const Y = (window.Yields = window.Yields || {});
  const DAY_MS = 86400000;

  /* ------------------------------------------------------------------------
   * Formatting
   * ---------------------------------------------------------------------- */

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTHS_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function isNum(v) {
    return typeof v === "number" && Number.isFinite(v);
  }

  function pct(v, decimals = 2) {
    return isNum(v) ? `${v.toFixed(decimals)}%` : "—";
  }

  function signed(v, decimals = 2, suffix = "") {
    if (!isNum(v)) return "—";
    const fixed = v.toFixed(decimals);
    const isZero = Number(fixed) === 0;
    return `${!isZero && v > 0 ? "+" : ""}${isZero ? (0).toFixed(decimals) : fixed}${suffix}`;
  }

  function bps(v) {
    return isNum(v) ? `${signed(Math.round(v * 100), 0)} bps` : "—";
  }

  /** $ amount given in millions -> "$40.15T" / "$958.5B". */
  function money(millions, decimals) {
    if (!isNum(millions)) return "—";
    const abs = Math.abs(millions);
    const sign = millions < 0 ? "−" : "";
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(decimals ?? 2)}T`;
    if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(decimals ?? 1)}B`;
    return `${sign}$${abs.toFixed(decimals ?? 0)}M`;
  }

  function parseIso(iso) {
    const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
    return { y, m, d };
  }

  function fmtDate(iso, style = "medium") {
    if (!iso) return "—";
    const { y, m, d } = parseIso(iso);
    if (!y || !m) return String(iso);
    if (style === "short") return `${MONTHS[m - 1]} ${d}`;
    if (style === "weekday") {
      const wd = WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
      return `${wd} ${MONTHS[m - 1]} ${d}`;
    }
    if (style === "long") return `${MONTHS_LONG[m - 1]} ${d}, ${y}`;
    return `${MONTHS[m - 1]} ${d}, ${y}`;
  }

  function periodLabel(iso, freq) {
    if (!iso) return "—";
    const { y, m } = parseIso(iso);
    if (freq === "quarterly") return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
    if (freq === "monthly") return `${MONTHS[m - 1]} ${y}`;
    return fmtDate(iso);
  }

  function relTime(isoTimestamp, nowMs = Date.now()) {
    const t = Date.parse(isoTimestamp);
    if (!Number.isFinite(t)) return "unknown";
    const seconds = Math.max(0, Math.round((nowMs - t) / 1000));
    if (seconds < 45) return "just now";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
      const rem = minutes - hours * 60;
      return rem >= 5 && hours < 6 ? `${hours} h ${rem} min ago` : `${hours} hour${hours === 1 ? "" : "s"} ago`;
    }
    const days = Math.floor(hours / 24);
    if (days < 14) return `${days} day${days === 1 ? "" : "s"} ago`;
    const weeks = Math.floor(days / 7);
    if (days < 60) return `${weeks} weeks ago`;
    return `${Math.floor(days / 30)} months ago`;
  }

  function fmtTimestamp(isoTimestamp) {
    const t = new Date(isoTimestamp);
    if (Number.isNaN(t.getTime())) return String(isoTimestamp || "unknown");
    const utc = t.toISOString().replace("T", " ").replace(/:\d\d\.\d+Z$/, " UTC").replace(/:\d\dZ$/, " UTC");
    let local = "";
    try {
      local = t.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    } catch (_) {
      local = t.toString();
    }
    return `${utc} (your time: ${local})`;
  }

  Y.fmt = { esc, isNum, pct, signed, bps, money, fmtDate, periodLabel, relTime, fmtTimestamp, MONTHS };

  /* ------------------------------------------------------------------------
   * Dates (ISO YYYY-MM-DD strings in UTC)
   * ---------------------------------------------------------------------- */

  function toMs(iso) {
    const { y, m, d } = parseIso(iso);
    return Date.UTC(y, (m || 1) - 1, d || 1);
  }

  function fromMs(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  function addDays(iso, n) {
    return fromMs(toMs(iso) + n * DAY_MS);
  }

  function addMonths(iso, n) {
    const { y, m, d } = parseIso(iso);
    const index = y * 12 + (m - 1) + n;
    const ny = Math.floor(index / 12);
    const nm = index - ny * 12;
    const last = new Date(Date.UTC(ny, nm + 1, 0)).getUTCDate();
    return fromMs(Date.UTC(ny, nm, Math.min(d, last)));
  }

  function todayIso() {
    return new Date().toISOString().slice(0, 10);
  }

  /** Index of the first element >= target in a sorted numeric array. */
  function lowerBound(arr, target) {
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /** Index of the last element <= target, or -1. */
  function floorIndex(arr, target) {
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] <= target) lo = mid + 1;
      else hi = mid;
    }
    return lo - 1;
  }

  Y.dates = { DAY_MS, toMs, fromMs, addDays, addMonths, todayIso, lowerBound, floorIndex };

  /* ------------------------------------------------------------------------
   * Release times: official U.S. Eastern times shown in the visitor's zone
   * ---------------------------------------------------------------------- */

  /** U.S. Eastern offset from UTC on a date: -4 from the second Sunday of March to the first Sunday of November, else -5. */
  function easternOffset(y, m, d) {
    const sunday = (month, nth) => {
      const firstWeekday = new Date(Date.UTC(y, month - 1, 1)).getUTCDay();
      return 1 + ((7 - firstWeekday) % 7) + (nth - 1) * 7;
    };
    const day = Date.UTC(y, m - 1, d);
    return day >= Date.UTC(y, 2, sunday(3, 2)) && day < Date.UTC(y, 10, sunday(11, 1)) ? -4 : -5;
  }

  /** The instant of an Eastern wall-clock time, e.g. ("2026-10-28", "14:00"). */
  function easternToDate(iso, hhmm) {
    const { y, m, d } = parseIso(iso);
    const [hh, mm] = String(hhmm || "00:00").split(":").map(Number);
    return new Date(Date.UTC(y, m - 1, d, (hh || 0) - easternOffset(y, m, d), mm || 0));
  }

  /** "Thu, Oct 29" in the visitor's time zone. */
  function localDay(date) {
    return date.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

  /** "Thu" in the visitor's time zone. */
  function localWeekday(date) {
    return date.toLocaleDateString("en-US", { weekday: "short" });
  }

  /** "2:00 am" in the visitor's time zone. */
  function localTime(date) {
    return date
      .toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
      .replace(/\s*([AP])M$/i, (_m, p) => ` ${p.toLowerCase()}m`);
  }

  /** Short name of the visitor's time zone, e.g. "GMT+8" or "EDT". */
  function localZone(date = new Date()) {
    try {
      const part = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
        .formatToParts(date)
        .find((p) => p.type === "timeZoneName");
      return part ? part.value : "";
    } catch (_) {
      return "";
    }
  }

  /** "in 5 hours", "tomorrow", "in 12 days" (calendar days in the visitor's zone). */
  function countdown(date, nowMs = Date.now()) {
    const diff = date.getTime() - nowMs;
    if (diff <= 0) return "now";
    const minutes = Math.round(diff / 60000);
    if (minutes < 60) return `in ${Math.max(1, minutes)} min`;
    const hours = Math.round(diff / 3600000);
    if (hours < 24) return `in ${hours} hour${hours === 1 ? "" : "s"}`;
    const now = new Date(nowMs);
    const days = Math.round(
      (Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / DAY_MS
    );
    return days <= 1 ? "tomorrow" : `in ${days} days`;
  }

  Y.time = { easternToDate, localDay, localWeekday, localTime, localZone, countdown };

  /* ------------------------------------------------------------------------
   * Data loading (relative URLs so the site works under any path)
   * ---------------------------------------------------------------------- */

  const cache = new Map();
  let cacheBust = "";

  function getJson(path) {
    const key = path;
    if (!cache.has(key)) {
      const url = `./data/${path}${cacheBust ? `?t=${cacheBust}` : ""}`;
      const promise = fetch(url, { headers: { Accept: "application/json" }, cache: cacheBust ? "no-store" : "default" })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status} loading data/${path}`);
          return res.json();
        })
        .catch((err) => {
          cache.delete(key);
          throw err;
        });
      cache.set(key, promise);
    }
    return cache.get(key);
  }

  /** Series file -> { id, dates, t (ms), v (Float64Array), released }. */
  function series(id) {
    const key = `__series:${id}`;
    if (!cache.has(key)) {
      const promise = getJson(`series/${encodeURIComponent(id)}.json`).then((raw) => {
        const dates = [];
        const values = [];
        const released = [];
        const rawDates = raw.dates || [];
        const rawValues = raw.values || [];
        for (let i = 0; i < rawDates.length; i++) {
          const v = rawValues[i];
          if (isNum(v)) {
            dates.push(rawDates[i]);
            values.push(v);
            released.push(raw.released ? raw.released[i] : null);
          }
        }
        const t = new Float64Array(dates.length);
        for (let i = 0; i < dates.length; i++) t[i] = toMs(dates[i]);
        return { id, dates, t, v: Float64Array.from(values), released: raw.released ? released : null };
      });
      promise.catch(() => cache.delete(key));
      cache.set(key, promise);
    }
    return cache.get(key);
  }

  function manifest() {
    return getJson("manifest.json").catch(() => null);
  }

  function reload() {
    cache.clear();
    cacheBust = String(Date.now());
  }

  Y.data = { getJson, series, manifest, reload };

  /* ------------------------------------------------------------------------
   * "Data refreshed … ago" indicator
   * ---------------------------------------------------------------------- */

  const refreshSources = {};
  let refreshTimer = null;

  function renderRefresh() {
    const el = document.getElementById("data-refreshed-ago");
    if (!el) return;
    const stamps = Object.values(refreshSources).filter((s) => Number.isFinite(Date.parse(s)));
    if (!stamps.length) {
      el.textContent = "unknown";
      el.removeAttribute("title");
      return;
    }
    const latest = stamps.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b));
    el.textContent = relTime(latest);
    el.setAttribute("datetime", latest);
    el.title = `Last refreshed ${fmtTimestamp(latest)}. New official data is fetched automatically on business days.`;
  }

  Y.refresh = {
    report(name, iso) {
      if (iso) refreshSources[name] = iso;
      renderRefresh();
      if (!refreshTimer) refreshTimer = setInterval(renderRefresh, 30000);
    },
    render: renderRefresh,
  };

  /* ------------------------------------------------------------------------
   * Hash router: #/view?key=value&key2=value2
   * ---------------------------------------------------------------------- */

  const views = {};
  const viewOrder = [];
  let current = null;

  function parseHash() {
    const raw = (location.hash || "").replace(/^#\/?/, "");
    const [name, query] = raw.split("?");
    const params = {};
    new URLSearchParams(query || "").forEach((value, key) => {
      params[key] = value;
    });
    return { name: name || "", params };
  }

  function buildHash(name, params) {
    const query = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") query.set(k, v);
    });
    const qs = query.toString();
    return `#/${name}${qs ? `?${qs}` : ""}`;
  }

  const router = {
    defaultView: "curve",
    register(name, view) {
      views[name] = Object.assign({ initialized: false }, view);
      viewOrder.push(name);
    },
    get current() {
      return current;
    },
    /** Replace the URL params of the current view without re-rendering. */
    setParams(params) {
      if (!current) return;
      const hash = buildHash(current, params);
      if (location.hash !== hash) history.replaceState(null, "", hash);
    },
    link(name, params) {
      return `${location.origin}${location.pathname}${buildHash(name, params)}`;
    },
    navigate(name, params) {
      location.hash = buildHash(name, params);
    },
    apply() {
      const { name, params } = parseHash();
      const target = views[name] ? name : router.defaultView;
      if (current && current !== target && views[current].hide) views[current].hide();
      document.querySelectorAll(".view").forEach((section) => {
        section.hidden = section.getAttribute("data-view") !== target;
      });
      document.querySelectorAll("[data-route]").forEach((link) => {
        const active = link.getAttribute("data-route") === target;
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      document.querySelectorAll("[data-route-group]").forEach((btn) => {
        const group = btn.getAttribute("data-route-group");
        btn.classList.toggle("active", (views[target]?.group || "") === group);
      });
      const view = views[target];
      current = target;
      document.title = `${view.title} — Yields`;
      try {
        if (!view.initialized) {
          view.initialized = true;
          if (view.init) view.init();
        }
        if (view.show) view.show(params);
      } catch (err) {
        console.error(err);
      }
    },
    start() {
      window.addEventListener("hashchange", router.apply);
      router.apply();
    },
    rerenderCurrent() {
      const view = current && views[current];
      if (view && view.refresh) view.refresh();
    },
  };

  Y.router = router;

  /* ------------------------------------------------------------------------
   * Charts
   * ---------------------------------------------------------------------- */

  const PALETTE = [
    "#0284c7", "#dc2626", "#16a34a", "#7c3aed", "#ea580c", "#0d9488",
    "#db2777", "#4f46e5", "#ca8a04", "#475569", "#2563eb", "#9333ea",
    "#059669", "#e11d48", "#0891b2", "#a16207",
  ];

  function stepDecimals(step) {
    for (let d = 0; d <= 6; d++) {
      const scaled = step * Math.pow(10, d);
      if (Math.abs(Math.round(scaled) - scaled) < 1e-6) return d;
    }
    return 6;
  }

  function niceTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1, ticks: [0, 1], step: 1, decimals: 0 };
    if (max - min < 1e-9) {
      const pad = Math.abs(min) > 1 ? Math.abs(min) * 0.05 : 0.5;
      min -= pad;
      max += pad;
    }
    const raw = (max - min) / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 2.25 ? 2 : norm < 3.5 ? 2.5 : norm < 7 ? 5 : 10) * mag;
    const lo = Math.floor(min / step + 1e-9) * step;
    const hi = Math.ceil(max / step - 1e-9) * step;
    const decimals = stepDecimals(step);
    const ticks = [];
    for (let i = 0; lo + i * step <= hi + step * 1e-6 && i < 50; i++) {
      ticks.push(Number((lo + i * step).toFixed(decimals)));
    }
    return { min: lo, max: hi, ticks, step, decimals };
  }

  const TIME_STEPS = [
    { unit: "day", n: 1, approx: 1 },
    { unit: "day", n: 2, approx: 2 },
    { unit: "day", n: 7, approx: 7 },
    { unit: "day", n: 14, approx: 14 },
    { unit: "month", n: 1, approx: 30.4 },
    { unit: "month", n: 2, approx: 60.8 },
    { unit: "month", n: 3, approx: 91.3 },
    { unit: "month", n: 6, approx: 182.6 },
    { unit: "year", n: 1, approx: 365.25 },
    { unit: "year", n: 2, approx: 730.5 },
    { unit: "year", n: 5, approx: 1826 },
    { unit: "year", n: 10, approx: 3652 },
    { unit: "year", n: 20, approx: 7305 },
    { unit: "year", n: 25, approx: 9131 },
    { unit: "year", n: 50, approx: 18262 },
  ];

  function timeTicks(t0, t1, maxTicks) {
    const spanDays = (t1 - t0) / DAY_MS;
    const choice = TIME_STEPS.find((s) => spanDays / s.approx <= maxTicks) || TIME_STEPS[TIME_STEPS.length - 1];
    const ticks = [];
    const start = new Date(t0);
    if (choice.unit === "day") {
      let t = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate());
      if (choice.n === 7 || choice.n === 14) {
        while (new Date(t).getUTCDay() !== 1) t += DAY_MS; // Mondays
      }
      for (; t <= t1; t += choice.n * DAY_MS) if (t >= t0) ticks.push(t);
    } else if (choice.unit === "month") {
      let y = start.getUTCFullYear();
      let m = start.getUTCMonth();
      let t = Date.UTC(y, m, 1);
      while (t < t0 || m % choice.n !== 0) {
        m += 1;
        if (m > 11) {
          m = 0;
          y += 1;
        }
        t = Date.UTC(y, m, 1);
      }
      for (; t <= t1; ) {
        ticks.push(t);
        m += choice.n;
        while (m > 11) {
          m -= 12;
          y += 1;
        }
        t = Date.UTC(y, m, 1);
      }
    } else {
      let y = start.getUTCFullYear();
      while (Date.UTC(y, 0, 1) < t0 || y % choice.n !== 0) y += 1;
      for (; Date.UTC(y, 0, 1) <= t1; y += choice.n) ticks.push(Date.UTC(y, 0, 1));
    }
    const sameYear = new Date(t0).getUTCFullYear() === new Date(t1).getUTCFullYear();
    const label = (t) => {
      const d = new Date(t);
      if (choice.unit === "day") return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
      if (choice.unit === "month") {
        if (d.getUTCMonth() === 0 || choice.n >= 6 || !sameYear) {
          return `${MONTHS[d.getUTCMonth()]} ${sameYear && choice.n < 6 ? "" : `'${String(d.getUTCFullYear()).slice(2)}`}`.trim();
        }
        return MONTHS[d.getUTCMonth()];
      }
      return String(d.getUTCFullYear());
    };
    return ticks.map((t) => ({ t, label: label(t) }));
  }

  /**
   * Prepare points for drawing: insert a NaN separator wherever the ORIGINAL data
   * has a gap longer than `gap` ms, and (optionally) keep only the first, minimum,
   * maximum and last point per pixel column so long histories stay fast.
   */
  function preparePoints(t, v, i0, i1, t0, msPerPx, gap, thin) {
    const outT = [];
    const outV = [];
    let bucket = null;
    let first = -1;
    let last = -1;
    let minI = -1;
    let maxI = -1;
    const flush = () => {
      if (first < 0) return;
      const idx = Array.from(new Set([first, minI, maxI, last])).sort((a, b) => a - b);
      for (const i of idx) {
        outT.push(t[i]);
        outV.push(v[i]);
      }
      first = -1;
    };
    for (let i = i0; i < i1; i++) {
      if (i > i0 && t[i] - t[i - 1] > gap) {
        flush();
        bucket = null;
        outT.push(t[i]);
        outV.push(NaN);
      }
      if (!thin) {
        outT.push(t[i]);
        outV.push(v[i]);
        continue;
      }
      const b = Math.floor((t[i] - t0) / msPerPx);
      if (b !== bucket || first < 0) {
        flush();
        bucket = b;
        first = last = minI = maxI = i;
      } else {
        last = i;
        if (v[i] < v[minI]) minI = i;
        if (v[i] > v[maxI]) maxI = i;
      }
    }
    flush();
    return { t: outT, v: outV };
  }

  function gapMsFor(freq) {
    if (freq === "quarterly") return 200 * DAY_MS;
    if (freq === "monthly") return 75 * DAY_MS;
    return 45 * DAY_MS; // daily series: holidays never break the line, missing years do
  }

  const observers = new WeakMap();
  const resizeObserver =
    typeof ResizeObserver !== "undefined"
      ? new ResizeObserver((entries) => {
          for (const entry of entries) {
            const chart = observers.get(entry.target);
            if (!chart) continue;
            const w = Math.round(entry.contentRect.width);
            if (w > 0 && w !== chart._lastWidth) {
              clearTimeout(chart._resizeTimer);
              chart._resizeTimer = setTimeout(() => chart.render(), 80);
            }
          }
        })
      : null;

  function mountChart(host, chart) {
    const prev = observers.get(host);
    if (prev && prev !== chart) prev.disposed = true;
    observers.set(host, chart);
    if (resizeObserver) resizeObserver.observe(host);
    chart.render();
    return chart;
  }

  function hostWidth(host) {
    return Math.max(280, Math.floor(host.clientWidth || host.getBoundingClientRect().width || 800));
  }

  function chartHeight(host, width, fallback) {
    const attr = Number(host.getAttribute("data-height"));
    const base = attr || fallback || 380;
    return width < 560 ? Math.round(base * 0.78) : base;
  }

  function positionTooltip(tip, host, x, y) {
    tip.hidden = false;
    const hostW = host.clientWidth;
    const tipW = tip.offsetWidth || 200;
    let left = x + 14;
    if (left + tipW > hostW - 4) left = Math.max(4, x - tipW - 14);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(4, y)}px`;
  }

  /**
   * Time series chart.
   * opts: { series: [{ id, label, color, t, v, freq, step, dash, width, valueFormat }],
   *         xDomain: [ms, ms], bands: [{ from, to }], zeroLine, yFormat, emptyMessage, height }
   */
  function timeChart(host, opts) {
    const chart = {
      opts,
      render() {
        if (chart.disposed) return;
        const o = chart.opts;
        const width = hostWidth(host);
        chart._lastWidth = host.clientWidth;
        const height = chartHeight(host, width, o.height);
        const m = { top: 12, right: 16, bottom: 30, left: width < 480 ? 44 : 54 };
        const pw = width - m.left - m.right;
        const ph = height - m.top - m.bottom;
        const [t0, t1] = o.xDomain;
        const yFormat = o.yFormat || ((v, d) => `${v.toFixed(d)}%`);

        const visible = [];
        let lo = Infinity;
        let hi = -Infinity;
        for (const s of o.series) {
          if (!s.t || !s.t.length) continue;
          let i0 = lowerBound(s.t, t0);
          const i1 = lowerBound(s.t, t1 + 1);
          if (s.step && i0 > 0) i0 -= 1;
          if (i1 <= i0) continue;
          for (let i = i0; i < i1; i++) {
            if (s.t[i] < t0 && !s.step) continue;
            const val = s.v[i];
            if (val < lo) lo = val;
            if (val > hi) hi = val;
          }
          const msPerPx = (t1 - t0) / Math.max(1, pw);
          const gap = s.gapMs || gapMsFor(s.freq);
          const pts = preparePoints(s.t, s.v, i0, i1, t0, msPerPx, gap, i1 - i0 > pw * 2.5);
          visible.push({ s, pts });
        }

        if (!visible.length || !Number.isFinite(lo)) {
          host.innerHTML = `<div class="chart-empty">${esc(o.emptyMessage || "No data in the selected range.")}</div>`;
          return;
        }
        if (o.includeZero) {
          lo = Math.min(lo, 0);
          hi = Math.max(hi, 0);
        }
        const padding = (hi - lo) * 0.06 || 0.25;
        const yt = niceTicks(lo - padding, hi + padding, ph < 260 ? 4 : 6);
        const x = (t) => m.left + ((t - t0) / (t1 - t0 || 1)) * pw;
        const y = (v) => m.top + ph - ((v - yt.min) / (yt.max - yt.min || 1)) * ph;
        const clipId = `clip-${Math.random().toString(36).slice(2, 9)}`;

        let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="${esc(o.ariaLabel || "Time series chart")}">`;
        svg += `<defs><clipPath id="${clipId}"><rect x="${m.left}" y="${m.top}" width="${pw}" height="${ph}"/></clipPath></defs>`;

        for (const band of o.bands || []) {
          const bx0 = Math.max(m.left, x(band.from));
          const bx1 = Math.min(m.left + pw, x(band.to));
          if (bx1 > bx0) {
            svg += `<rect class="chart-band" x="${bx0.toFixed(1)}" y="${m.top}" width="${(bx1 - bx0).toFixed(1)}" height="${ph}"><title>${esc(band.label || "Recession")}</title></rect>`;
          }
        }

        for (const tick of yt.ticks) {
          const ty = y(tick);
          svg += `<line class="chart-grid" x1="${m.left}" x2="${m.left + pw}" y1="${ty.toFixed(1)}" y2="${ty.toFixed(1)}"/>`;
          svg += `<text class="chart-axis-label" x="${m.left - 6}" y="${(ty + 4).toFixed(1)}" text-anchor="end">${esc(yFormat(tick, yt.decimals, yt.step))}</text>`;
        }
        if (o.zeroLine !== false && yt.min < 0 && yt.max > 0) {
          svg += `<line class="chart-zero" x1="${m.left}" x2="${m.left + pw}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>`;
        }
        const xt = timeTicks(t0, t1, Math.max(2, Math.floor(pw / 90)));
        for (const tick of xt) {
          const tx = x(tick.t);
          svg += `<line class="chart-grid chart-grid-v" x1="${tx.toFixed(1)}" x2="${tx.toFixed(1)}" y1="${m.top}" y2="${m.top + ph}"/>`;
          svg += `<text class="chart-axis-label" x="${tx.toFixed(1)}" y="${m.top + ph + 18}" text-anchor="middle">${esc(tick.label)}</text>`;
        }

        svg += `<g clip-path="url(#${clipId})">`;
        for (const { s, pts } of visible) {
          let d = "";
          let prevT = null;
          let prevY = null;
          let segmentLength = 0;
          const endSegment = () => {
            if (segmentLength === 1) d += "h0.1"; // a lone point still shows as a dot
            segmentLength = 0;
          };
          for (let i = 0; i < pts.t.length; i++) {
            if (Number.isNaN(pts.v[i])) {
              endSegment();
              prevT = null;
              prevY = null;
              continue;
            }
            const px = x(pts.t[i]);
            const py = y(pts.v[i]);
            if (prevT === null) {
              d += `M${px.toFixed(1)},${py.toFixed(1)}`;
            } else if (s.step) {
              d += `H${px.toFixed(1)}V${py.toFixed(1)}`;
            } else {
              d += `L${px.toFixed(1)},${py.toFixed(1)}`;
            }
            segmentLength += 1;
            prevT = pts.t[i];
            prevY = py;
          }
          if (!s.step) endSegment();
          if (s.step && prevT !== null && prevY !== null) {
            const extend = Math.min(x(Math.min(t1, prevT + (s.freq === "quarterly" ? 91 : s.freq === "monthly" ? 30 : 1) * DAY_MS)), m.left + pw);
            d += `H${extend.toFixed(1)}`;
          }
          svg += `<path class="chart-line" d="${d}" stroke="${esc(s.color)}" stroke-width="${s.width || 1.9}"${s.dash ? ` stroke-dasharray="${esc(s.dash)}"` : ""}/>`;
        }
        svg += `</g>`;
        svg += `<line class="chart-crosshair" x1="0" x2="0" y1="${m.top}" y2="${m.top + ph}" visibility="hidden"/>`;
        svg += `<g class="chart-dots"></g>`;
        svg += `<rect class="chart-overlay" x="${m.left}" y="${m.top}" width="${pw}" height="${ph}" fill="transparent"/>`;
        svg += `</svg><div class="chart-tooltip" hidden></div>`;
        host.innerHTML = svg;
        host.classList.add("chart-host-ready");

        const svgEl = host.querySelector("svg");
        const overlay = host.querySelector(".chart-overlay");
        const cross = host.querySelector(".chart-crosshair");
        const dots = host.querySelector(".chart-dots");
        const tip = host.querySelector(".chart-tooltip");
        const primary = visible.find((item) => !item.s.step) || visible[0];

        const show = (tHover) => {
          let snap = tHover;
          const pi = nearestIndex(primary.s.t, tHover);
          if (pi >= 0) snap = primary.s.t[pi];
          if (snap < t0 || snap > t1) snap = Math.min(t1, Math.max(t0, snap));
          const px = x(snap);
          cross.setAttribute("x1", px.toFixed(1));
          cross.setAttribute("x2", px.toFixed(1));
          cross.setAttribute("visibility", "visible");
          let rows = "";
          let dotSvg = "";
          for (const { s } of visible) {
            const idx = floorIndex(s.t, snap + DAY_MS / 2);
            const gap = s.gapMs || gapMsFor(s.freq);
            const ok = idx >= 0 && snap - s.t[idx] <= gap;
            const val = ok ? s.v[idx] : null;
            const when = ok && s.freq && s.freq !== "daily" ? ` <span class="tip-period">${esc(periodLabel(fromMs(s.t[idx]), s.freq))}</span>` : "";
            const fmtVal = s.valueFormat || ((v) => yFormat(v, 2));
            rows += `<div class="tooltip-row"><span><span class="series-color-dot" style="background:${esc(s.color)}"></span>${esc(s.label)}${when}</span><strong>${val === null ? "—" : esc(fmtVal(val))}</strong></div>`;
            if (val !== null) {
              dotSvg += `<circle cx="${x(Math.max(t0, s.t[idx])).toFixed(1)}" cy="${y(val).toFixed(1)}" r="3.5" fill="${esc(s.color)}" stroke="var(--bg-surface)" stroke-width="1.5"/>`;
            }
          }
          dots.innerHTML = dotSvg;
          tip.innerHTML = `<div class="tooltip-title">${esc(fmtDate(fromMs(snap)))}</div>${rows}`;
          const rect = svgEl.getBoundingClientRect();
          const scale = rect.width / width;
          positionTooltip(tip, host, px * scale, m.top * scale);
          chart._hoverT = snap;
        };
        const hide = () => {
          tip.hidden = true;
          cross.setAttribute("visibility", "hidden");
          dots.innerHTML = "";
        };
        overlay.addEventListener("pointermove", (ev) => {
          const rect = svgEl.getBoundingClientRect();
          const px = ((ev.clientX - rect.left) / rect.width) * width;
          show(t0 + ((px - m.left) / pw) * (t1 - t0));
        });
        overlay.addEventListener("pointerleave", hide);
        host.tabIndex = 0;
        host.onkeydown = (ev) => {
          if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") {
            if (ev.key === "Escape") hide();
            return;
          }
          ev.preventDefault();
          const arr = primary.s.t;
          let i = nearestIndex(arr, chart._hoverT ?? t1);
          i = Math.max(0, Math.min(arr.length - 1, i + (ev.key === "ArrowLeft" ? -1 : 1)));
          show(arr[i]);
        };
        host.onblur = hide;
      },
    };
    return mountChart(host, chart);
  }

  function nearestIndex(arr, target) {
    if (!arr.length) return -1;
    const i = lowerBound(arr, target);
    if (i <= 0) return 0;
    if (i >= arr.length) return arr.length - 1;
    return target - arr[i - 1] <= arr[i] - target ? i - 1 : i;
  }

  /**
   * Stacked area chart (monthly composition).
   * opts: { t: [ms], layers: [{ label, color, values }], xDomain, yFormat, percent }
   */
  function stackedChart(host, opts) {
    const chart = {
      opts,
      render() {
        if (chart.disposed) return;
        const o = chart.opts;
        const width = hostWidth(host);
        chart._lastWidth = host.clientWidth;
        const height = chartHeight(host, width, o.height);
        const m = { top: 12, right: 16, bottom: 30, left: width < 480 ? 50 : 62 };
        const pw = width - m.left - m.right;
        const ph = height - m.top - m.bottom;
        const [t0, t1] = o.xDomain;
        const idx = [];
        for (let i = 0; i < o.t.length; i++) if (o.t[i] >= t0 && o.t[i] <= t1) idx.push(i);
        if (idx.length < 2) {
          host.innerHTML = `<div class="chart-empty">Not enough data in the selected range.</div>`;
          return;
        }
        const totals = idx.map((i) => o.layers.reduce((sum, layer) => sum + (layer.values[i] || 0), 0));
        const hi = o.percent ? 100 : Math.max(...totals);
        const yt = niceTicks(0, hi, ph < 260 ? 4 : 6);
        const xs = (t) => m.left + ((t - o.t[idx[0]]) / (o.t[idx[idx.length - 1]] - o.t[idx[0]] || 1)) * pw;
        const ys = (v) => m.top + ph - (v / (yt.max || 1)) * ph;
        const yFormat = o.yFormat || ((v) => String(v));

        let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="${esc(o.ariaLabel || "Stacked area chart")}">`;
        for (const tick of yt.ticks) {
          const ty = ys(tick);
          svg += `<line class="chart-grid" x1="${m.left}" x2="${m.left + pw}" y1="${ty.toFixed(1)}" y2="${ty.toFixed(1)}"/>`;
          svg += `<text class="chart-axis-label" x="${m.left - 6}" y="${(ty + 4).toFixed(1)}" text-anchor="end">${esc(yFormat(tick, yt.decimals))}</text>`;
        }
        const xt = timeTicks(o.t[idx[0]], o.t[idx[idx.length - 1]], Math.max(2, Math.floor(pw / 90)));
        for (const tick of xt) {
          const tx = xs(tick.t);
          svg += `<line class="chart-grid chart-grid-v" x1="${tx.toFixed(1)}" x2="${tx.toFixed(1)}" y1="${m.top}" y2="${m.top + ph}"/>`;
          svg += `<text class="chart-axis-label" x="${tx.toFixed(1)}" y="${m.top + ph + 18}" text-anchor="middle">${esc(tick.label)}</text>`;
        }
        const base = idx.map(() => 0);
        o.layers.forEach((layer) => {
          const top = idx.map((i, k) => {
            const raw = layer.values[i] || 0;
            const val = o.percent ? (totals[k] ? (raw / totals[k]) * 100 : 0) : raw;
            return base[k] + val;
          });
          let d = "";
          idx.forEach((i, k) => {
            d += `${k === 0 ? "M" : "L"}${xs(o.t[i]).toFixed(1)},${ys(top[k]).toFixed(1)}`;
          });
          for (let k = idx.length - 1; k >= 0; k--) d += `L${xs(o.t[idx[k]]).toFixed(1)},${ys(base[k]).toFixed(1)}`;
          svg += `<path d="${d}Z" fill="${esc(layer.color)}" fill-opacity="0.82" stroke="${esc(layer.color)}" stroke-width="0.6"><title>${esc(layer.label)}</title></path>`;
          top.forEach((v, k) => {
            base[k] = v;
          });
        });
        svg += `<line class="chart-crosshair" x1="0" x2="0" y1="${m.top}" y2="${m.top + ph}" visibility="hidden"/>`;
        svg += `<rect class="chart-overlay" x="${m.left}" y="${m.top}" width="${pw}" height="${ph}" fill="transparent"/>`;
        svg += `</svg><div class="chart-tooltip" hidden></div>`;
        host.innerHTML = svg;

        const svgEl = host.querySelector("svg");
        const tip = host.querySelector(".chart-tooltip");
        const cross = host.querySelector(".chart-crosshair");
        const pickIndex = (k) => {
          const i = idx[k];
          const px = xs(o.t[i]);
          cross.setAttribute("x1", px.toFixed(1));
          cross.setAttribute("x2", px.toFixed(1));
          cross.setAttribute("visibility", "visible");
          const rows = o.layers
            .slice()
            .reverse()
            .map((layer) => {
              const raw = layer.values[i] || 0;
              const share = totals[k] ? (raw / totals[k]) * 100 : 0;
              return `<div class="tooltip-row"><span><span class="series-color-dot" style="background:${esc(layer.color)}"></span>${esc(layer.label)}</span><strong>${esc(o.valueFormat ? o.valueFormat(raw) : String(raw))} <span class="tip-period">${share.toFixed(1)}%</span></strong></div>`;
            })
            .join("");
          tip.innerHTML = `<div class="tooltip-title">${esc(o.dateLabel ? o.dateLabel(fromMs(o.t[i])) : fmtDate(fromMs(o.t[i])))}</div>${rows}<div class="tooltip-row tooltip-total"><span>Total</span><strong>${esc(o.valueFormat ? o.valueFormat(totals[k]) : String(totals[k]))}</strong></div>`;
          const rect = svgEl.getBoundingClientRect();
          positionTooltip(tip, host, px * (rect.width / width), m.top);
          chart._k = k;
        };
        const overlay = host.querySelector(".chart-overlay");
        overlay.addEventListener("pointermove", (ev) => {
          const rect = svgEl.getBoundingClientRect();
          const px = ((ev.clientX - rect.left) / rect.width) * width;
          const ratio = (px - m.left) / pw;
          const target = o.t[idx[0]] + ratio * (o.t[idx[idx.length - 1]] - o.t[idx[0]]);
          let best = 0;
          for (let k = 1; k < idx.length; k++) if (Math.abs(o.t[idx[k]] - target) < Math.abs(o.t[idx[best]] - target)) best = k;
          pickIndex(best);
        });
        overlay.addEventListener("pointerleave", () => {
          tip.hidden = true;
          cross.setAttribute("visibility", "hidden");
        });
        host.tabIndex = 0;
        host.onkeydown = (ev) => {
          if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
          ev.preventDefault();
          const k = Math.max(0, Math.min(idx.length - 1, (chart._k ?? idx.length - 1) + (ev.key === "ArrowLeft" ? -1 : 1)));
          pickIndex(k);
        };
        host.onblur = () => {
          tip.hidden = true;
          cross.setAttribute("visibility", "hidden");
        };
      },
    };
    return mountChart(host, chart);
  }

  /**
   * Yield curve chart with maturities on the x-axis.
   * opts: { categories: [{ key, label, years }], spacing: "even"|"sqrt"|"linear",
   *         lines: [{ label, color, dash, values: { key: number }, width, markers, area }],
   *         band: { low, high, label }, hline: { value, label, color } }
   */
  function curveChart(host, opts) {
    const chart = {
      opts,
      render() {
        if (chart.disposed) return;
        const o = chart.opts;
        const width = hostWidth(host);
        chart._lastWidth = host.clientWidth;
        const height = chartHeight(host, width, o.height || 400);
        const m = { top: 14, right: width < 480 ? 14 : 22, bottom: 38, left: width < 480 ? 40 : 50 };
        const pw = width - m.left - m.right;
        const ph = height - m.top - m.bottom;
        const cats = o.categories;
        const n = cats.length;
        const maxYears = Math.max(...cats.map((c) => c.years));
        const inset = Math.min(18, pw * 0.03);
        const xOf = (cat, i) => {
          if (o.spacing === "linear") return m.left + inset + (cat.years / maxYears) * (pw - 2 * inset);
          if (o.spacing === "sqrt") return m.left + inset + (Math.sqrt(cat.years) / Math.sqrt(maxYears)) * (pw - 2 * inset);
          return m.left + inset + (n === 1 ? 0.5 : i / (n - 1)) * (pw - 2 * inset);
        };
        const vals = [];
        for (const line of o.lines) for (const c of cats) if (isNum(line.values[c.key])) vals.push(line.values[c.key]);
        if (o.band) vals.push(o.band.low, o.band.high);
        if (o.hline && isNum(o.hline.value)) vals.push(o.hline.value);
        if (!vals.length) {
          host.innerHTML = `<div class="chart-empty">${esc(o.emptyMessage || "No yields published for this date.")}</div>`;
          return;
        }
        let lo = Math.min(...vals);
        let hi = Math.max(...vals);
        const padding = Math.max((hi - lo) * 0.08, 0.15);
        const yt = niceTicks(Math.max(lo - padding, lo >= 0 ? 0 : -Infinity), hi + padding, ph < 260 ? 4 : 6);
        const y = (v) => m.top + ph - ((v - yt.min) / (yt.max - yt.min || 1)) * ph;

        let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="${esc(o.ariaLabel || "Yield curve")}">`;
        for (const tick of yt.ticks) {
          const ty = y(tick);
          svg += `<line class="chart-grid" x1="${m.left}" x2="${m.left + pw}" y1="${ty.toFixed(1)}" y2="${ty.toFixed(1)}"/>`;
          svg += `<text class="chart-axis-label" x="${m.left - 6}" y="${(ty + 4).toFixed(1)}" text-anchor="end">${tick.toFixed(yt.decimals)}%</text>`;
        }
        if (yt.min < 0 && yt.max > 0) {
          svg += `<line class="chart-zero" x1="${m.left}" x2="${m.left + pw}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>`;
        }
        // Thin out maturity labels that would overlap.
        let lastLabelX = -Infinity;
        cats.forEach((c, i) => {
          const cx = xOf(c, i);
          svg += `<line class="chart-grid chart-grid-v" x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${m.top}" y2="${m.top + ph}"/>`;
          const minGap = c.label.length * 7 + 6;
          if (cx - lastLabelX >= minGap || i === n - 1) {
            svg += `<text class="chart-axis-label" x="${cx.toFixed(1)}" y="${m.top + ph + 18}" text-anchor="middle">${esc(c.label)}</text>`;
            lastLabelX = cx;
          }
        });
        svg += `<text class="chart-axis-title" x="${(m.left + pw / 2).toFixed(1)}" y="${height - 4}" text-anchor="middle">Maturity${o.spacing === "even" ? "" : o.spacing === "sqrt" ? " (√ years scale)" : " (years, linear)"}</text>`;

        if (o.band && isNum(o.band.low) && isNum(o.band.high)) {
          const by0 = y(o.band.high);
          const by1 = y(o.band.low);
          const h = Math.max(3, by1 - by0);
          svg += `<rect class="curve-band" x="${m.left}" y="${(h === 3 ? by0 - 1.5 : by0).toFixed(1)}" width="${pw}" height="${h.toFixed(1)}"><title>${esc(o.band.label)}</title></rect>`;
        }
        if (o.hline && isNum(o.hline.value)) {
          const hy = y(o.hline.value);
          svg += `<line class="curve-hline" x1="${m.left}" x2="${m.left + pw}" y1="${hy.toFixed(1)}" y2="${hy.toFixed(1)}" stroke="${esc(o.hline.color || "#dc2626")}"/>`;
          svg += `<text class="curve-hline-label" x="${(m.left + 6).toFixed(1)}" y="${(hy - 5).toFixed(1)}" fill="${esc(o.hline.color || "#dc2626")}">${esc(o.hline.label)}</text>`;
        }

        o.lines.forEach((line) => {
          // A curve joins the maturities published that day (missing ones are skipped).
          let d = "";
          let started = false;
          const pts = [];
          cats.forEach((c, i) => {
            const v = line.values[c.key];
            if (!isNum(v)) return;
            const px = xOf(c, i);
            const py = y(v);
            pts.push([px, py]);
            d += `${started ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
            started = true;
          });
          if (pts.length === 1) d += "h0.1";
          if (line.area && pts.length > 1) {
            const area = `M${pts[0][0].toFixed(1)},${(m.top + ph).toFixed(1)}` + pts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("") + `L${pts[pts.length - 1][0].toFixed(1)},${(m.top + ph).toFixed(1)}Z`;
            svg += `<path d="${area}" fill="${esc(line.color)}" fill-opacity="0.08" stroke="none"/>`;
          }
          svg += `<path class="chart-line" d="${d}" stroke="${esc(line.color)}" stroke-width="${line.width || 2}"${line.dash ? ` stroke-dasharray="${esc(line.dash)}"` : ""}/>`;
          if (line.markers !== false) {
            pts.forEach((p) => {
              svg += `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${line.width >= 2.5 ? 4 : 3}" fill="var(--bg-surface)" stroke="${esc(line.color)}" stroke-width="2"/>`;
            });
          }
        });
        svg += `<line class="chart-crosshair" x1="0" x2="0" y1="${m.top}" y2="${m.top + ph}" visibility="hidden"/>`;
        svg += `<rect class="chart-overlay" x="${m.left}" y="${m.top}" width="${pw}" height="${ph}" fill="transparent"/>`;
        svg += `</svg><div class="chart-tooltip" hidden></div>`;
        host.innerHTML = svg;

        const svgEl = host.querySelector("svg");
        const tip = host.querySelector(".chart-tooltip");
        const cross = host.querySelector(".chart-crosshair");
        const pick = (i) => {
          const c = cats[i];
          const cx = xOf(c, i);
          cross.setAttribute("x1", cx.toFixed(1));
          cross.setAttribute("x2", cx.toFixed(1));
          cross.setAttribute("visibility", "visible");
          const rows = o.lines
            .map((line) => {
              const v = line.values[c.key];
              return `<div class="tooltip-row"><span><span class="series-color-dot" style="background:${esc(line.color)}"></span>${esc(line.label)}</span><strong>${isNum(v) ? `${v.toFixed(2)}%` : "—"}</strong></div>`;
            })
            .join("");
          tip.innerHTML = `<div class="tooltip-title">${esc(c.longLabel || c.label)}</div>${rows}`;
          const rect = svgEl.getBoundingClientRect();
          positionTooltip(tip, host, cx * (rect.width / width), m.top);
          chart._i = i;
        };
        const overlay = host.querySelector(".chart-overlay");
        overlay.addEventListener("pointermove", (ev) => {
          const rect = svgEl.getBoundingClientRect();
          const px = ((ev.clientX - rect.left) / rect.width) * width;
          let best = 0;
          cats.forEach((c, i) => {
            if (Math.abs(xOf(c, i) - px) < Math.abs(xOf(cats[best], best) - px)) best = i;
          });
          pick(best);
        });
        overlay.addEventListener("pointerleave", () => {
          tip.hidden = true;
          cross.setAttribute("visibility", "hidden");
        });
        host.tabIndex = 0;
        host.onkeydown = (ev) => {
          if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
          ev.preventDefault();
          pick(Math.max(0, Math.min(n - 1, (chart._i ?? -1) + (ev.key === "ArrowLeft" ? -1 : 1))));
        };
        host.onblur = () => {
          tip.hidden = true;
          cross.setAttribute("visibility", "hidden");
        };
      },
    };
    return mountChart(host, chart);
  }

  /**
   * Numeric x/y chart (used by the valuation calculator).
   * opts: { points: [[x, y]], color, label, marker: { x, y, label }, xFormat, yFormat }
   */
  function xyChart(host, opts) {
    const chart = {
      opts,
      render() {
        if (chart.disposed) return;
        const o = chart.opts;
        const width = hostWidth(host);
        chart._lastWidth = host.clientWidth;
        const height = chartHeight(host, width, o.height || 340);
        const m = { top: 14, right: 18, bottom: 40, left: width < 480 ? 58 : 70 };
        const pw = width - m.left - m.right;
        const ph = height - m.top - m.bottom;
        const pts = o.points.filter((p) => isNum(p[0]) && isNum(p[1]));
        if (pts.length < 2) {
          host.innerHTML = `<div class="chart-empty">Enter valid inputs to draw the chart.</div>`;
          return;
        }
        const xsVals = pts.map((p) => p[0]);
        const ysVals = pts.map((p) => p[1]);
        const xt = niceTicks(Math.min(...xsVals), Math.max(...xsVals), Math.max(3, Math.floor(pw / 70)));
        const yt = niceTicks(Math.min(...ysVals), Math.max(...ysVals), ph < 260 ? 4 : 6);
        const x = (v) => m.left + ((v - xt.min) / (xt.max - xt.min || 1)) * pw;
        const y = (v) => m.top + ph - ((v - yt.min) / (yt.max - yt.min || 1)) * ph;
        const xFormat = o.xFormat || ((v, d) => v.toFixed(d));
        const yFormat = o.yFormat || ((v, d) => v.toFixed(d));
        let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="${esc(o.ariaLabel || "Chart")}">`;
        for (const tick of yt.ticks) {
          const ty = y(tick);
          svg += `<line class="chart-grid" x1="${m.left}" x2="${m.left + pw}" y1="${ty.toFixed(1)}" y2="${ty.toFixed(1)}"/>`;
          svg += `<text class="chart-axis-label" x="${m.left - 6}" y="${(ty + 4).toFixed(1)}" text-anchor="end">${esc(yFormat(tick, yt.decimals))}</text>`;
        }
        for (const tick of xt.ticks) {
          const tx = x(tick);
          svg += `<line class="chart-grid chart-grid-v" x1="${tx.toFixed(1)}" x2="${tx.toFixed(1)}" y1="${m.top}" y2="${m.top + ph}"/>`;
          svg += `<text class="chart-axis-label" x="${tx.toFixed(1)}" y="${m.top + ph + 18}" text-anchor="middle">${esc(xFormat(tick, xt.decimals))}</text>`;
        }
        if (o.xTitle) svg += `<text class="chart-axis-title" x="${(m.left + pw / 2).toFixed(1)}" y="${height - 4}" text-anchor="middle">${esc(o.xTitle)}</text>`;
        const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
        svg += `<path class="chart-line" d="${d}" stroke="${esc(o.color || PALETTE[0])}" stroke-width="2.2"/>`;
        if (o.marker && isNum(o.marker.x) && isNum(o.marker.y)) {
          svg += `<line class="chart-marker-line" x1="${x(o.marker.x).toFixed(1)}" x2="${x(o.marker.x).toFixed(1)}" y1="${m.top}" y2="${m.top + ph}"/>`;
          svg += `<circle cx="${x(o.marker.x).toFixed(1)}" cy="${y(o.marker.y).toFixed(1)}" r="5" fill="${esc(o.color || PALETTE[0])}" stroke="var(--bg-surface)" stroke-width="2"/>`;
          if (o.marker.label) {
            const right = x(o.marker.x) > m.left + pw * 0.7;
            svg += `<text class="chart-marker-label" x="${(x(o.marker.x) + (right ? -8 : 8)).toFixed(1)}" y="${(y(o.marker.y) - 10).toFixed(1)}" text-anchor="${right ? "end" : "start"}">${esc(o.marker.label)}</text>`;
          }
        }
        svg += `<line class="chart-crosshair" x1="0" x2="0" y1="${m.top}" y2="${m.top + ph}" visibility="hidden"/>`;
        svg += `<circle class="chart-hover-dot" r="4" cx="-10" cy="-10" fill="${esc(o.color || PALETTE[0])}"/>`;
        svg += `<rect class="chart-overlay" x="${m.left}" y="${m.top}" width="${pw}" height="${ph}" fill="transparent"/>`;
        svg += `</svg><div class="chart-tooltip" hidden></div>`;
        host.innerHTML = svg;
        const svgEl = host.querySelector("svg");
        const tip = host.querySelector(".chart-tooltip");
        const cross = host.querySelector(".chart-crosshair");
        const dot = host.querySelector(".chart-hover-dot");
        host.querySelector(".chart-overlay").addEventListener("pointermove", (ev) => {
          const rect = svgEl.getBoundingClientRect();
          const px = ((ev.clientX - rect.left) / rect.width) * width;
          const xv = xt.min + ((px - m.left) / pw) * (xt.max - xt.min);
          let best = 0;
          pts.forEach((p, i) => {
            if (Math.abs(p[0] - xv) < Math.abs(pts[best][0] - xv)) best = i;
          });
          const p = pts[best];
          cross.setAttribute("x1", x(p[0]).toFixed(1));
          cross.setAttribute("x2", x(p[0]).toFixed(1));
          cross.setAttribute("visibility", "visible");
          dot.setAttribute("cx", x(p[0]).toFixed(1));
          dot.setAttribute("cy", y(p[1]).toFixed(1));
          tip.innerHTML = o.tooltip ? o.tooltip(p) : `<div class="tooltip-row"><span>${esc(xFormat(p[0], 2))}</span><strong>${esc(yFormat(p[1], 2))}</strong></div>`;
          positionTooltip(tip, host, x(p[0]) * (rect.width / width), m.top);
        });
        host.querySelector(".chart-overlay").addEventListener("pointerleave", () => {
          tip.hidden = true;
          cross.setAttribute("visibility", "hidden");
          dot.setAttribute("cx", "-10");
        });
      },
    };
    return mountChart(host, chart);
  }

  function legendHtml(items) {
    return items
      .map((item) => {
        const swatch = item.dash
          ? `<span class="legend-swatch legend-swatch-dashed" style="color:${esc(item.color)}"></span>`
          : item.band
            ? `<span class="legend-swatch legend-swatch-band" style="background:${esc(item.color)}"></span>`
            : `<span class="legend-swatch" style="background:${esc(item.color)}"></span>`;
        const remove = item.removeId
          ? ` <button type="button" class="legend-remove" data-remove="${esc(item.removeId)}" aria-label="Remove ${esc(item.label)}">×</button>`
          : "";
        return `<span class="legend-item">${swatch}${esc(item.label)}${remove}</span>`;
      })
      .join("");
  }

  function downloadCsv(filename, header, rows) {
    const quote = (s) => `"${String(s).replace(/"/g, '""')}"`;
    const lines = [header.map(quote).join(",")].concat(rows.map((r) => r.map((c) => (c === null || c === undefined ? "" : c)).join(",")));
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  Y.charts = { PALETTE, niceTicks, timeTicks, timeChart, stackedChart, curveChart, xyChart, legendHtml, nearestIndex, decimalsFor: stepDecimals };
  Y.util = { downloadCsv };

  /* Range presets shared by the time-based views. */
  Y.ranges = {
    PRESETS: [
      { id: "1M", months: 1 },
      { id: "3M", months: 3 },
      { id: "6M", months: 6 },
      { id: "1Y", months: 12 },
      { id: "2Y", months: 24 },
      { id: "3Y", months: 36 },
      { id: "5Y", months: 60 },
      { id: "10Y", months: 120 },
      { id: "20Y", months: 240 },
      { id: "MAX", months: null },
    ],
    /** "3M" / "4Y" / "MAX" -> [from, to] ending at lastIso, clipped to firstIso. */
    resolve(id, lastIso, firstIso) {
      const match = /^(\d+)([MY])$/.exec(String(id || ""));
      if (!match) return [firstIso, lastIso];
      const months = Number(match[1]) * (match[2] === "Y" ? 12 : 1);
      const from = addMonths(lastIso, -months);
      return [from < firstIso ? firstIso : from, lastIso];
    },
  };
})();
