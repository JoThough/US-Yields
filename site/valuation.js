/**
 * Yields — Treasury valuation calculator (site/valuation.js)
 *
 * Prices a fixed-coupon Treasury note or bond at any market yield and shows how
 * its value moves when rates change (duration, convexity, DV01, scenarios).
 */
(function () {
  "use strict";

  const Y = window.Yields;
  const { esc, isNum, fmtDate } = Y.fmt;
  const $ = (id) => document.getElementById(id);

  const SHOCKS_BPS = [-300, -200, -100, -50, -25, 0, 25, 50, 100, 200, 300];

  /** Price of a bond paying `freq` coupons a year, `periods` periods to maturity. */
  function price(face, couponPct, periods, freq, yieldPct) {
    const coupon = (face * couponPct) / 100 / freq;
    const r = yieldPct / 100 / freq;
    if (Math.abs(r) < 1e-12) return coupon * periods + face;
    const discount = Math.pow(1 + r, -periods);
    return (coupon * (1 - discount)) / r + face * discount;
  }

  function analytics(face, couponPct, years, freq, yieldPct) {
    const periods = Math.max(1, Math.round(years * freq));
    const coupon = (face * couponPct) / 100 / freq;
    const r = yieldPct / 100 / freq;
    const p = price(face, couponPct, periods, freq, yieldPct);
    let weighted = 0;
    let convex = 0;
    for (let k = 1; k <= periods; k++) {
      const cf = coupon + (k === periods ? face : 0);
      const pv = cf / Math.pow(1 + r, k);
      weighted += (k / freq) * pv;
      convex += (k * (k + 1) * cf) / Math.pow(1 + r, k + 2);
    }
    const macaulay = weighted / p;
    const modified = macaulay / (1 + r);
    const convexity = convex / (p * freq * freq);
    const dv01 = (price(face, couponPct, periods, freq, yieldPct - 0.01) - price(face, couponPct, periods, freq, yieldPct + 0.01)) / 2;
    return { periods, price: p, macaulay, modified, convexity, dv01 };
  }

  const usd = (v, decimals = 2) =>
    isNum(v) ? `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}` : "—";

  function readInputs() {
    const face = Number($("val-face").value);
    const coupon = Number($("val-coupon").value);
    const years = Number($("val-years").value);
    const freq = Number($("val-freq").value);
    const yld = Number($("val-yield").value);
    const ok = face > 0 && coupon >= 0 && coupon <= 30 && years > 0 && years <= 100 && [1, 2, 4, 12].includes(freq) && yld > -5 && yld <= 30;
    return { ok, face, coupon, years, freq, yld };
  }

  function render() {
    const inp = readInputs();
    const error = $("val-error");
    if (!inp.ok) {
      error.textContent = "Check the inputs: face value above 0, coupon 0–30%, maturity up to 100 years, yield between −5% and 30%.";
      return;
    }
    error.textContent = "";
    const a = analytics(inp.face, inp.coupon, inp.years, inp.freq, inp.yld);
    const premium = a.price - inp.face;
    const cards = [
      { label: "Market value", value: usd(a.price), sub: `${((a.price / inp.face) * 100).toFixed(3)} per 100 of face value` },
      { label: premium >= 0 ? "Premium to face" : "Discount to face", value: usd(Math.abs(premium)), sub: premium >= 0 ? "Coupon is above the market yield" : "Coupon is below the market yield" },
      { label: "Modified duration", value: `${a.modified.toFixed(2)} yrs`, sub: `≈ ${(a.modified).toFixed(2)}% price change per 1% move in yield` },
      { label: "DV01", value: usd(a.dv01), sub: "Value change for a 0.01% (1 bp) move" },
      { label: "Macaulay duration", value: `${a.macaulay.toFixed(2)} yrs`, sub: "Weighted average time to cash flows" },
      { label: "Convexity", value: a.convexity.toFixed(2), sub: "Curvature of the price–yield line" },
    ];
    $("val-results").innerHTML = cards
      .map((c) => `<div class="stat-card"><div class="stat-label">${esc(c.label)}</div><div class="stat-value">${esc(c.value)}</div><div class="stat-sub">${esc(c.sub)}</div></div>`)
      .join("");

    $("val-scenarios").querySelector("tbody").innerHTML = SHOCKS_BPS.map((shock) => {
      const y = inp.yld + shock / 100;
      const p = price(inp.face, inp.coupon, a.periods, inp.freq, y);
      const change = p - a.price;
      const pctChange = (change / a.price) * 100;
      const cls = shock === 0 ? "bps-flat" : change >= 0 ? "bps-up" : "bps-down";
      return `<tr${shock === 0 ? ' class="current-row"' : ""}><td class="num-col">${shock > 0 ? "+" : ""}${shock} bps</td><td class="num-col">${y.toFixed(2)}%</td><td class="num-col">${esc(usd(p))}</td><td class="num-col"><span class="bps-badge ${cls}">${change >= 0 ? "+" : "−"}${esc(usd(Math.abs(change)))}</span></td><td class="num-col">${pctChange >= 0 ? "+" : ""}${pctChange.toFixed(2)}%</td></tr>`;
    }).join("");

    const maxYield = Math.max(10.5, Math.ceil(inp.yld + 4));
    const minYield = Math.min(0, Math.floor(inp.yld - 2));
    const points = [];
    for (let y = minYield; y <= maxYield + 1e-9; y += 0.125) {
      points.push([Number(y.toFixed(3)), price(inp.face, inp.coupon, a.periods, inp.freq, y)]);
    }
    const options = {
      points,
      color: "#2563eb",
      marker: { x: inp.yld, y: a.price, label: `${usd(a.price, 0)} at ${inp.yld.toFixed(2)}%` },
      xFormat: (v, d) => `${v.toFixed(Math.min(d, 2))}%`,
      yFormat: (v) => usd(v, 0),
      xTitle: "Market yield",
      ariaLabel: "Market value of the security at different market yields",
      tooltip: (p) => `<div class="tooltip-title">Yield ${p[0].toFixed(2)}%</div><div class="tooltip-row"><span>Market value</span><strong>${esc(usd(p[1]))}</strong></div><div class="tooltip-row"><span>vs today</span><strong>${esc(((p[1] / a.price - 1) * 100).toFixed(2))}%</strong></div>`,
    };
    const host = $("val-chart");
    if (S.chart && host.querySelector("svg")) {
      S.chart.opts = options;
      S.chart.render();
    } else {
      S.chart = Y.charts.xyChart(host, options);
    }
    $("val-chart-title").textContent = `${inp.years}-year, ${inp.coupon.toFixed(3).replace(/\.?0+$/, "")}% coupon, ${usd(inp.face, 0)} face value`;
    Y.router.setParams({ face: inp.face, c: inp.coupon, yrs: inp.years, f: inp.freq !== 2 ? inp.freq : "", y: inp.yld });
  }

  const S = { chart: null, latest: null };

  async function latestYield(years) {
    const keyFor = { 1: "1Y", 2: "2Y", 3: "3Y", 5: "5Y", 7: "7Y", 10: "10Y", 20: "20Y", 30: "30Y" };
    const candidates = Object.keys(keyFor).map(Number);
    const nearest = candidates.reduce((a, b) => (Math.abs(b - years) < Math.abs(a - years) ? b : a));
    try {
      const series = await Y.data.series(`UST${keyFor[nearest]}`);
      const i = series.v.length - 1;
      return i >= 0 ? { value: series.v[i], date: series.dates[i], key: keyFor[nearest] } : null;
    } catch (_) {
      try {
        const rates = await Y.data.getJson("rates.json");
        const obs = rates.sources.treasury.observations;
        for (let i = obs.length - 1; i >= 0; i--) {
          const v = obs[i].yields[keyFor[nearest]];
          if (isNum(v)) return { value: v, date: obs[i].date, key: keyFor[nearest] };
        }
      } catch (_err) {
        return null;
      }
      return null;
    }
  }

  async function useMarketYield() {
    const years = Number($("val-years").value) || 10;
    const latest = await latestYield(years);
    const note = $("val-market-note");
    if (latest) {
      $("val-yield").value = latest.value.toFixed(2);
      note.textContent = `Latest ${latest.key} Treasury par yield: ${latest.value.toFixed(2)}% on ${fmtDate(latest.date)}.`;
      render();
    } else {
      note.textContent = "Latest Treasury yield is not available.";
    }
  }

  function init() {
    ["val-face", "val-coupon", "val-years", "val-freq", "val-yield"].forEach((id) => {
      $(id).addEventListener("input", () => render());
    });
    $("val-use-market").addEventListener("click", useMarketYield);
    $("val-par").addEventListener("click", () => {
      $("val-coupon").value = $("val-yield").value;
      render();
    });
  }

  async function show(params) {
    const set = (id, v) => {
      if (v !== undefined && v !== "" && Number.isFinite(Number(v))) $(id).value = v;
    };
    set("val-face", params.face);
    set("val-coupon", params.c);
    set("val-years", params.yrs);
    set("val-freq", params.f);
    set("val-yield", params.y);
    if (params.y === undefined && !S.latest) {
      S.latest = await latestYield(Number($("val-years").value) || 10);
      if (S.latest) {
        const rounded = (Math.round(S.latest.value * 8) / 8).toFixed(3);
        if (params.c === undefined) $("val-coupon").value = rounded;
        $("val-yield").value = S.latest.value.toFixed(2);
        $("val-market-note").textContent = `Latest ${S.latest.key} Treasury par yield: ${S.latest.value.toFixed(2)}% on ${fmtDate(S.latest.date)}.`;
      }
    }
    render();
  }

  Y.router.register("valuation", { title: "Treasury Securities Valuation Calculator", init, show, refresh: () => render() });
  Y.valuation = { price, analytics };
})();
