/**
 * Yields — Economic data revisions (site/revisions.js)
 *
 * Lists each first release and every later revision of CPI, PCE, PPI, real GDP
 * and the unemployment rate, computed from ALFRED real-time data.
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, isNum, fmtDate } = Y.fmt;
  const $ = (id) => document.getElementById(id);
  const MONTHS_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  const S = { data: null, filter: "ALL", page: 1, pageSize: 50, ready: null };

  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"];
    const v = n % 100;
    return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
  }

  function periodText(iso, freq) {
    const y = iso.slice(0, 4);
    const m = Number(iso.slice(5, 7));
    if (freq === "quarterly") return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
    return `${MONTHS_LONG[m - 1]} ${y}`;
  }

  function filtered() {
    const events = S.data?.events || [];
    return S.filter === "ALL" ? events : events.filter((e) => e[0] === S.filter);
  }

  function render() {
    const metrics = S.data.metrics || {};
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / S.pageSize));
    S.page = Math.min(Math.max(1, S.page), pages);
    const slice = rows.slice((S.page - 1) * S.pageSize, S.page * S.pageSize);
    $("rev-tbody").innerHTML = slice.length
      ? slice
          .map(([metric, period, date, n, value, previous]) => {
            const meta = metrics[metric] || { label: metric, short: metric, frequency: "monthly" };
            const change = isNum(value) && isNum(previous) ? value - previous : null;
            const cls = !isNum(change) ? "" : change > 0.0001 ? "bps-up" : change < -0.0001 ? "bps-down" : "bps-flat";
            return `<tr>
              <td><strong>${esc(meta.short)}</strong> <span class="muted rev-desc">${esc(meta.label)}</span></td>
              <td>${esc(periodText(period, meta.frequency))}</td>
              <td>${esc(fmtDate(date))}</td>
              <td>${n === 0 ? '<span class="tag">First release</span>' : esc(ordinal(n))}</td>
              <td class="num-col"><strong>${isNum(value) ? value.toFixed(2) : "—"}</strong></td>
              <td class="num-col">${isNum(previous) ? previous.toFixed(2) : "—"}</td>
              <td class="num-col">${isNum(change) ? `<span class="bps-badge ${cls}">${change > 0 ? "+" : ""}${change.toFixed(2)}</span>` : "—"}</td>
            </tr>`;
          })
          .join("")
      : `<tr><td colspan="7">No revisions recorded for this indicator.</td></tr>`;
    $("rev-page").textContent = `Page ${S.page} of ${pages}`;
    $("rev-count").textContent = `${rows.length.toLocaleString()} release${rows.length === 1 ? "" : "s"} and revisions`;
    $("rev-prev").disabled = S.page <= 1;
    $("rev-first").disabled = S.page <= 1;
    $("rev-next").disabled = S.page >= pages;
    $("rev-last").disabled = S.page >= pages;
    $("rev-truncated").hidden = !S.data.truncated;
    Y.router.setParams({ m: S.filter !== "ALL" ? S.filter : "", page: S.page > 1 ? S.page : "" });
  }

  function ensureReady() {
    if (!S.ready) {
      S.ready = Y.data.getJson("revisions.json").then((raw) => {
        S.data = raw;
        const select = $("rev-filter");
        select.innerHTML =
          `<option value="ALL">All indicators</option>` +
          Object.entries(raw.metrics || {})
            .map(([id, meta]) => `<option value="${esc(id)}">${esc(meta.label)}</option>`)
            .join("");
      });
      S.ready.catch(() => {
        S.ready = null;
      });
    }
    return S.ready;
  }

  function init() {
    $("rev-filter").addEventListener("change", (ev) => {
      S.filter = ev.target.value;
      S.page = 1;
      render();
    });
    $("rev-first").addEventListener("click", () => {
      S.page = 1;
      render();
    });
    $("rev-prev").addEventListener("click", () => {
      S.page -= 1;
      render();
    });
    $("rev-next").addEventListener("click", () => {
      S.page += 1;
      render();
    });
    $("rev-last").addEventListener("click", () => {
      S.page = Number.MAX_SAFE_INTEGER;
      render();
    });
  }

  async function show(params) {
    try {
      await ensureReady();
    } catch (err) {
      $("rev-tbody").innerHTML = `<tr><td colspan="7">Revision history appears after the next scheduled data update (it needs the FRED API key).</td></tr>`;
      return;
    }
    if (params.m && S.data.metrics && S.data.metrics[params.m]) S.filter = params.m;
    if (params.page) S.page = Number(params.page) || 1;
    $("rev-filter").value = S.filter;
    render();
  }

  Y.router.register("revisions", {
    title: "Economic Data Revisions",
    init,
    show,
    refresh() {
      S.ready = null;
      show({});
    },
  });
})();
