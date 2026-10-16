/**
 * Yields — page shell (site/main.js)
 *
 * Navigation (tabs and the Federal Debt menu), extended source status in the
 * header, the Sources view, the reload button for extended data, and router start.
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, fmtDate, relTime, fmtTimestamp } = Y.fmt;
  const $ = (id) => document.getElementById(id);

  const STATUS_TEXT = {
    ok: { icon: "✓", cls: "status-pill-ok", text: "Updated normally" },
    partial: { icon: "!", cls: "status-pill-warn", text: "Partly updated" },
    error: { icon: "!", cls: "status-pill-error", text: "Last update failed — showing saved data" },
    missing_key: { icon: "–", cls: "status-pill-muted", text: "Not configured (FRED API key missing)" },
  };

  /* ---------------------------------------------------------------------- */
  /* Header: extended source pills and alerts                                */
  /* ---------------------------------------------------------------------- */

  function renderExtendedStatus(manifest) {
    const pills = $("ext-status-pills");
    const alerts = $("ext-alerts");
    if (!pills || !alerts) return;
    if (!manifest) {
      pills.innerHTML = "";
      alerts.innerHTML = `<div class="alert-banner alert-info"><div><strong>Extended history is not published yet.</strong> The curve archive back to 1934, Fed and inflation data, and federal debt charts are created by the next scheduled data update. Recent Treasury, EFFR and SOFR rates are shown meanwhile.</div></div>`;
      return;
    }
    Y.refresh.report("extended", manifest.updatedAt);
    const order = ["treasuryReal", "fred", "fiscal"];
    pills.innerHTML = order
      .filter((key) => manifest.sources && manifest.sources[key])
      .map((key) => {
        const src = manifest.sources[key];
        const st = STATUS_TEXT[src.status] || STATUS_TEXT.error;
        const label = key === "fred" && src.status === "missing_key" ? `${src.shortName} (no key)` : src.shortName;
        const title = `${src.name}: ${st.text}${src.latestObservationDate ? `; latest observation ${src.latestObservationDate}` : ""}${src.errorMessage ? ` — ${src.errorMessage}` : ""}`;
        return `<span class="status-pill ${st.cls}" title="${esc(title)}">${st.icon} ${esc(label)}</span>`;
      })
      .join("");
    const banners = [];
    for (const [key, src] of Object.entries(manifest.sources || {})) {
      if (src.status === "error") {
        banners.push(`<div class="alert-banner alert-error" role="alert"><div><strong>Partial source update issue (${esc(src.shortName || key)}):</strong> the latest scheduled fetch failed (<code>${esc(src.errorMessage || "fetch error")}</code>). Showing the last saved data${src.latestObservationDate ? ` through <strong>${esc(src.latestObservationDate)}</strong>` : ""}.</div></div>`);
      }
    }
    alerts.innerHTML = banners.join("");
  }

  /* ---------------------------------------------------------------------- */
  /* Sources view                                                            */
  /* ---------------------------------------------------------------------- */

  async function renderSources() {
    const tbody = $("sources-status-tbody");
    const [manifest, rates] = await Promise.all([Y.data.manifest(), Y.data.getJson("rates.json").catch(() => null)]);
    const rows = [];
    const add = (name, url, status, latest, lastSuccess, note) => {
      const st = STATUS_TEXT[status] || { icon: "?", cls: "status-pill-muted", text: status || "unknown" };
      rows.push(`<tr>
        <td><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(name)}</a>${note ? `<div class="muted small">${esc(note)}</div>` : ""}</td>
        <td><span class="status-pill ${st.cls}">${st.icon} ${esc(st.text)}</span></td>
        <td>${esc(latest ? fmtDate(latest) : "—")}</td>
        <td>${lastSuccess ? `<span title="${esc(fmtTimestamp(lastSuccess))}">${esc(relTime(lastSuccess))}</span>` : "—"}</td>
      </tr>`);
    };
    for (const key of ["treasury", "effr", "sofr"]) {
      const src = rates?.sources?.[key];
      if (src) add(src.name, src.sourcePageUrl || src.documentationUrl, src.status, src.latestObservationDate, src.lastSuccessAt, key === "treasury" ? "Recent history for the Rates Dashboard" : "");
    }
    for (const key of ["treasuryNominal", "treasuryReal", "fred", "fiscal", "fedCalendar"]) {
      const src = manifest?.sources?.[key];
      if (src) add(src.name, src.url, src.status, src.latestObservationDate, src.lastSuccessAt, src.status === "missing_key" ? "Add the FRED_API_KEY repository secret to enable it." : src.errorMessage || "");
    }
    tbody.innerHTML = rows.join("") || `<tr><td colspan="4">Status unavailable.</td></tr>`;
    const keyNote = $("sources-fred-note");
    if (keyNote) {
      keyNote.textContent = !manifest
        ? "Extended datasets have not been generated yet."
        : manifest.fredKeyConfigured
          ? "The FRED API key is configured as a GitHub Actions secret; it is used only by the scheduled update job and never published with the site."
          : "The FRED API key is not configured. Add a repository secret named FRED_API_KEY (Settings → Secrets and variables → Actions) to enable Fed funds, RRP, inflation, GDP, unemployment, recession, release-calendar and pre-1990 yield data.";
    }
  }

  Y.router.register("sources", { title: "Data Sources", show: renderSources, refresh: renderSources });
  Y.router.register("dashboard", { title: "Rates Dashboard", show() {} });

  /* ---------------------------------------------------------------------- */
  /* Navigation                                                              */
  /* ---------------------------------------------------------------------- */

  function initNav() {
    const toggle = document.querySelector(".nav-dropdown-toggle");
    const menu = $("debt-menu");
    if (!toggle || !menu) return;
    const close = () => {
      menu.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    };
    // Keep the open menu inside the window when the tab sits near the right edge.
    const fitToViewport = () => {
      menu.style.left = "";
      const rect = menu.getBoundingClientRect();
      const margin = 8;
      const width = document.documentElement.clientWidth;
      let shift = 0;
      if (rect.right > width - margin) shift = width - margin - rect.right;
      if (rect.left + shift < margin) shift = margin - rect.left;
      if (shift) menu.style.left = `${Math.round(shift)}px`;
    };
    toggle.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        fitToViewport();
        menu.querySelector("a")?.focus();
      }
    });
    window.addEventListener("resize", () => {
      if (!menu.hidden) fitToViewport();
    });
    menu.addEventListener("click", close);
    document.addEventListener("click", (ev) => {
      if (!menu.hidden && !menu.contains(ev.target) && ev.target !== toggle) close();
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !menu.hidden) {
        close();
        toggle.focus();
      }
    });
  }

  async function loadExtendedHeader() {
    renderExtendedStatus(await Y.data.manifest());
  }

  document.addEventListener("DOMContentLoaded", () => {
    initNav();
    const reloadBtn = $("reload-data-btn");
    if (reloadBtn) {
      reloadBtn.addEventListener("click", async () => {
        Y.data.reload();
        await loadExtendedHeader();
        Y.router.rerenderCurrent();
      });
    }
    loadExtendedHeader();
    Y.router.start();
  });
})();
