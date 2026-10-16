/**
 * Yields — Yield Curve sidebar (site/sidebar.js)
 *
 * Three cards next to the yield curve:
 *   - Upcoming Events: Fed rate decisions and minutes plus major data releases,
 *     shown in the visitor's own time zone with a countdown;
 *   - Latest Economic Data: each indicator with the reading before it and the
 *     date it was published;
 *   - Fed Policy: target range, effective rate, last move and next decision.
 * Countdowns refresh every minute, and events drop off once they have happened.
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, isNum, fmtDate } = Y.fmt;
  const T = Y.time;
  const $ = (id) => document.getElementById(id);

  const MAX_EVENTS = 6;
  const FED_TIME_ET = "14:00";
  const DATA_TIME_ET = "08:30";

  const ABOUT = {
    fomc: "The Fed announces its interest-rate decision",
    minutes: "Detailed record of a Fed policy meeting",
    cpi: "Consumer price inflation (Bureau of Labor Statistics)",
    ppi: "Producer price inflation (Bureau of Labor Statistics)",
    jobs: "Payrolls and the unemployment rate (Bureau of Labor Statistics)",
    pce: "The Fed's preferred inflation gauge (Bureau of Economic Analysis)",
    gdp: "Economic growth (Bureau of Economic Analysis)",
  };

  // Calendars saved before the redesign had no kind or time.
  const OLD_LABELS = { "FOMC decision": "fomc", "FOMC minutes": "minutes", CPI: "cpi", PPI: "ppi", "Jobs report": "jobs", GDP: "gdp", "PCE inflation": "pce" };

  let manifest = null;

  const kindOf = (e) => e.kind || OLD_LABELS[e.label] || "other";
  const isFed = (kind) => kind === "fomc" || kind === "minutes";

  function eventTitle(e, kind) {
    if (kind === "fomc") return e.projections ? "Fed decision + projections" : "Fed rate decision";
    if (kind === "minutes") return "Fed meeting minutes";
    const label = e.label || "Data release";
    if (!e.periodLabel) return label;
    const period = String(e.periodLabel).split(" ")[0]; // "Oct" or "Q3"
    if (kind === "gdp") return `${label} (${period}, ${e.estimate === "advance" ? "advance" : "revised"})`;
    return `${label} (${period})`;
  }

  function eventTooltip(e, kind) {
    const parts = [ABOUT[kind] || e.label || ""];
    if (kind === "fomc" && e.projections) parts.push("with the Fed's economic projections");
    if (e.meeting) parts.push(`${e.meeting} meeting`);
    if (e.periodLabel) parts.push(`data for ${e.periodLabel}${e.estimate ? ` (${e.estimate} estimate)` : ""}`);
    return parts.filter(Boolean).join(" · ");
  }

  /* ---------------------------------------------------------------------- */
  /* Upcoming Events                                                         */
  /* ---------------------------------------------------------------------- */

  function renderEvents(now) {
    const list = $("events-list");
    const note = $("events-note");
    if (!list) return;
    const upcoming = (manifest?.events || [])
      // Old calendars listed an FOMC "release" every day; those dates are not meetings.
      .filter((e) => e && e.date && (e.kind || !isFed(kindOf(e))))
      .map((e) => {
        const kind = kindOf(e);
        return { e, kind, at: T.easternToDate(e.date, e.time || (isFed(kind) ? FED_TIME_ET : DATA_TIME_ET)) };
      })
      .filter((item) => item.at.getTime() > now)
      .sort((a, b) => a.at - b.at)
      .slice(0, MAX_EVENTS);

    if (!upcoming.length) {
      const needsKey = manifest && !manifest.fredKeyConfigured;
      list.innerHTML = `<li class="muted">${needsKey ? "The release calendar needs the FRED API key." : "No scheduled releases in the coming weeks."}</li>`;
    } else {
      list.innerHTML = upcoming
        .map(({ e, kind, at }) => {
          const month = at.toLocaleDateString("en-US", { month: "short" });
          return `<li class="event-row${kind === "fomc" ? " event-fed" : ""}" title="${esc(eventTooltip(e, kind))}">
            <span class="event-date" aria-hidden="true"><span class="event-month">${esc(month)}</span><span class="event-day">${at.getDate()}</span></span>
            <span class="event-body">
              <a class="event-title" href="${esc(e.url || "#")}" target="_blank" rel="noopener noreferrer">${esc(eventTitle(e, kind))}</a>
              <span class="event-meta"><time datetime="${esc(at.toISOString())}">${esc(T.localWeekday(at))}<span class="visually-hidden"> ${esc(month)} ${at.getDate()},</span> ${esc(T.localTime(at))}</time> · ${esc(T.countdown(at, now))}</span>
            </span>
          </li>`;
        })
        .join("");
    }
    if (note) {
      const zone = T.localZone();
      note.textContent = `Times are in your time zone${zone ? ` (${zone})` : ""}.`;
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Latest Economic Data                                                    */
  /* ---------------------------------------------------------------------- */

  function changeText(item) {
    if (!isNum(item.value) || !isNum(item.previous)) return "";
    const now = item.value.toFixed(1);
    const before = item.previous.toFixed(1);
    const when = item.previousPeriodLabel ? `<span class="visually-hidden"> in ${esc(item.previousPeriodLabel)}</span>` : "";
    if (Number(now) > Number(before)) {
      return `<span class="ind-change"><span aria-hidden="true">▲</span><span class="visually-hidden">up</span> from ${esc(before)}%${when}</span>`;
    }
    if (Number(now) < Number(before)) {
      return `<span class="ind-change"><span aria-hidden="true">▼</span><span class="visually-hidden">down</span> from ${esc(before)}%${when}</span>`;
    }
    const since = item.previousPeriodLabel ? `<span class="visually-hidden"> from ${esc(item.previousPeriodLabel)}</span>` : "";
    return `<span class="ind-change">unchanged${since}</span>`;
  }

  function renderIndicators() {
    const list = $("indicators-list");
    if (!list) return;
    const seen = new Set();
    // Older data listed GDP twice (two quarters); keep the latest row of each indicator.
    const items = (manifest?.indicators || []).filter((item) => item && !seen.has(item.id) && seen.add(item.id));
    if (!items.length) {
      const needsKey = manifest && !manifest.fredKeyConfigured;
      list.innerHTML = `<div class="muted">${needsKey ? "Indicators need the FRED API key." : "Indicators unavailable."}</div>`;
      return;
    }
    list.innerHTML = items
      .map((item) => {
        const published = item.released ? ` · ${item.revised ? "revised" : "released"} ${fmtDate(item.released, "short")}` : "";
        return `<div class="indicator-row"${item.about ? ` title="${esc(item.about)}"` : ""}>
          <dt><span class="ind-label">${esc(item.label)}</span><span class="ind-meta">${esc(item.periodLabel || "")}${esc(published)}</span></dt>
          <dd><span class="ind-value">${isNum(item.value) ? `${item.value.toFixed(1)}%` : "—"}</span>${changeText(item)}</dd>
        </div>`;
      })
      .join("");
  }

  /* ---------------------------------------------------------------------- */
  /* Fed Policy                                                              */
  /* ---------------------------------------------------------------------- */

  function policyRow(label, value, labelMeta = "", valueMeta = "") {
    return `<div class="indicator-row">
      <dt><span class="ind-label">${esc(label)}</span>${labelMeta ? `<span class="ind-meta">${esc(labelMeta)}</span>` : ""}</dt>
      <dd><span class="ind-value">${esc(value)}</span>${valueMeta ? `<span class="ind-change">${esc(valueMeta)}</span>` : ""}</dd>
    </div>`;
  }

  function meetingName(label, endIso) {
    const year = String(endIso || "").slice(0, 4);
    return year && year !== String(new Date().getFullYear()) ? `${label}, ${year} meeting` : `${label} meeting`;
  }

  function renderFed(now) {
    const list = $("fed-policy");
    if (!list) return;
    const fed = manifest?.fed;
    const rows = [];
    if (fed && isNum(fed.lower) && isNum(fed.upper)) {
      rows.push(policyRow("Target range", `${fed.lower.toFixed(2)}–${fed.upper.toFixed(2)}%`, "Fed funds rate"));
    }
    if (fed?.effr && isNum(fed.effr.value)) {
      rows.push(policyRow("Effective rate", `${fed.effr.value.toFixed(2)}%`, `Fed funds (EFFR), ${fmtDate(fed.effr.date, "short")}`));
    }
    const change = fed?.lastChange;
    if (change && isNum(change.bp)) {
      const size = Math.abs(change.bp);
      const what = change.bp > 0 ? `Raised ${size} bp` : change.bp < 0 ? `Cut ${size} bp` : "Range changed";
      const where = change.meeting ? meetingName(change.meeting, change.meetingEnd) : `Effective ${fmtDate(change.effective)}`;
      rows.push(policyRow("Last move", what, where));
    }
    const next = (manifest?.fomc?.meetings || [])
      .map((m) => ({ m, at: T.easternToDate(m.end, FED_TIME_ET) }))
      .find(({ at }) => at.getTime() > now);
    if (next) {
      const label = next.m.start === next.m.end ? fmtDate(next.m.start, "short") : meetingLabel(next.m);
      rows.push(policyRow("Next decision", T.localDay(next.at), `${label} meeting`, `${T.localTime(next.at)} · ${T.countdown(next.at, now)}`));
    }
    list.innerHTML = rows.length
      ? rows.join("")
      : `<div class="muted">${manifest && !manifest.fredKeyConfigured ? "Fed policy data needs the FRED API key." : "Fed policy data unavailable."}</div>`;
  }

  function meetingLabel(m) {
    const start = fmtDate(m.start, "short");
    const sameMonth = m.start.slice(0, 7) === m.end.slice(0, 7);
    return `${start}–${sameMonth ? Number(m.end.slice(8, 10)) : fmtDate(m.end, "short")}`;
  }

  /* ---------------------------------------------------------------------- */

  function draw() {
    const now = Date.now();
    renderEvents(now);
    renderIndicators();
    renderFed(now);
  }

  // Keep countdowns current; skip while the Yield Curve view is hidden.
  setInterval(() => {
    const view = $("view-curve");
    if (manifest && view && !view.hidden && document.visibilityState !== "hidden") draw();
  }, 60000);

  Y.sidebar = {
    render(data) {
      manifest = data || null;
      draw();
    },
  };
})();
