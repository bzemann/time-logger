'use strict';

// WorkTime Dashboard - part 1 (header, cards, range filter, daily chart).
// The browser only FORMATS data from /api/dashboard; all stats are
// computed server-side (worktime/stats.py). See CLAUDE.md for the JSON
// layout.
(function () {
  // ------------------------------------------------------------------
  // (1) Formatting helpers
  // ------------------------------------------------------------------

  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  // m -> "0m" | "7h 09m" | "25h 12m"
  function fmtDuration(s) {
    const m = Math.floor(Math.max(0, s) / 60);
    const h = Math.floor(m / 60);
    const mm = m % 60;
    return h > 0 ? `${h}h ${pad2(mm)}m` : `${mm}m`;
  }

  // signed hh:mm balance, e.g. "+3:40" / "−1:15" / "±0:00"
  function fmtBalance(s) {
    const m = Math.floor(Math.abs(s) / 60);
    if (m === 0) return '±0:00';
    const sign = s > 0 ? '+' : '−';
    const hh = Math.floor(m / 60);
    const mm = pad2(m % 60);
    return `${sign}${hh}:${mm}`;
  }

  function balanceClass(s) {
    const m = Math.floor(Math.abs(s) / 60);
    if (m === 0) return 'bal-zero';
    return s > 0 ? 'bal-pos' : 'bal-neg';
  }

  // seconds -> "8:30" (used for the target-line label)
  function fmtHM(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}:${pad2(m)}`;
  }

  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  // Split "YYYY-MM-DD" ourselves; new Date("YYYY-MM-DD") parses as UTC and
  // would shift the day in local timezones ahead of UTC.
  function parseYMD(str) {
    const parts = str.split('-');
    return {
      y: Number(parts[0]),
      m: Number(parts[1]),
      d: Number(parts[2]),
      ys: parts[0],
      ms: parts[1],
      ds: parts[2],
    };
  }

  function weekdayFor(p) {
    const jsDay = new Date(p.y, p.m - 1, p.d).getDay(); // 0=Sun..6=Sat
    const idx = (jsDay + 6) % 7; // remap so Monday=0
    return WEEKDAYS[idx];
  }

  // "2026-09-23" -> "23.09.2026"
  function fmtDate(str) {
    const p = parseYMD(str);
    return `${p.ds}.${p.ms}.${p.ys}`;
  }

  // "2026-09-23" -> "Wed 23.09.2026"
  function fmtDateLong(str) {
    const p = parseYMD(str);
    return `${weekdayFor(p)} ${fmtDate(str)}`;
  }

  // "2026-09-21" -> "Mon 21.09."
  function fmtDayShort(str) {
    const p = parseYMD(str);
    return `${weekdayFor(p)} ${p.ds}.${p.ms}.`;
  }

  // "2026-09-23T17:03:00" -> "23.09.2026 17:03"
  function fmtGenerated(iso) {
    const [datePart, timePart] = iso.split('T');
    return `${fmtDate(datePart)} ${timePart.slice(0, 5)}`;
  }

  // ------------------------------------------------------------------
  // (2) Theme tokens - read the CSS custom properties so the canvas
  // chart (which CSS can't reach) matches the current color scheme.
  // ------------------------------------------------------------------

  function getThemeTokens() {
    const cs = getComputedStyle(document.documentElement);
    const get = (name) => cs.getPropertyValue(name).trim();
    return {
      textPrimary: get('--text-primary'),
      textSecondary: get('--text-secondary'),
      textMuted: get('--text-muted'),
      grid: get('--grid'),
      axis: get('--axis'),
      series: get('--series'),
      seriesHover: get('--series-hover'),
      targetLine: get('--target-line'),
      tooltipBg: get('--tooltip-bg'),
      tooltipText: get('--tooltip-text'),
    };
  }

  // ------------------------------------------------------------------
  // (3) Range state + localStorage
  // ------------------------------------------------------------------

  const STORAGE_KEY = 'worktime.range.v1';
  const PRESET_KEYS = ['7d', '30d', '90d', 'year', 'all'];
  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const DEFAULT_RANGE_STATE = { preset: '30d' };

  function loadRangeState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { ...DEFAULT_RANGE_STATE };
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        if (PRESET_KEYS.includes(parsed.preset)) {
          return { preset: parsed.preset };
        }
        if (DATE_RE.test(parsed.start) && DATE_RE.test(parsed.end)) {
          return { start: parsed.start, end: parsed.end };
        }
      }
    } catch (e) {
      // localStorage may be unavailable (private mode, quota, ...) or the
      // stored value may be garbage; fall through to the default.
    }
    return { ...DEFAULT_RANGE_STATE };
  }

  function saveRangeState(state) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // Ignore: nothing sensible to do if storage isn't available.
    }
  }

  let rangeState = loadRangeState();

  // ------------------------------------------------------------------
  // (4) API fetch
  // ------------------------------------------------------------------

  function buildQuery(state) {
    if (state.preset) {
      return `range=${encodeURIComponent(state.preset)}&unit=week`;
    }
    return `start=${encodeURIComponent(state.start)}&end=${encodeURIComponent(state.end)}&unit=week`;
  }

  async function fetchDashboard(state) {
    let res;
    try {
      res = await fetch(`/api/dashboard?${buildQuery(state)}`);
    } catch (e) {
      throw new Error('Dashboard server not reachable. Run `worktime dashboard` again.');
    }
    if (!res.ok) {
      let msg = res.statusText;
      try {
        const body = await res.json();
        if (body && body.error) msg = body.error;
      } catch (e) {
        // Body wasn't JSON; keep the statusText fallback.
      }
      throw new Error(`Could not load data: ${msg}`);
    }
    return res.json();
  }

  // ------------------------------------------------------------------
  // (5) Render header (status pill + generated timestamp)
  // ------------------------------------------------------------------

  function renderHeader(data) {
    const pill = document.getElementById('status-pill');
    pill.textContent = '';
    const running = data.status.running;
    pill.className = 'status-pill' + (running ? ' running' : '');

    const dot = document.createElement('span');
    dot.className = 'status-dot ' + (running ? 'running' : 'idle');
    pill.appendChild(dot);

    const label = document.createElement('span');
    if (running) {
      label.textContent = `Running · ${fmtDuration(data.status.elapsed_s)}`;
      const [datePart, timePartFull] = data.status.since.split('T');
      pill.title = `Since ${fmtDateLong(datePart)} ${timePartFull.slice(0, 5)}`;
    } else {
      label.textContent = 'Not running';
      pill.removeAttribute('title');
    }
    pill.appendChild(label);

    document.getElementById('generated').textContent = `Generated ${fmtGenerated(data.generated)}`;
  }

  // ------------------------------------------------------------------
  // (6) Render summary cards (today / week / month / total)
  // ------------------------------------------------------------------

  function buildCard(labelText) {
    const card = document.createElement('div');
    card.className = 'card';
    const label = document.createElement('span');
    label.className = 'card-label';
    label.textContent = labelText;
    card.appendChild(label);
    return card;
  }

  function buildTodayCard(today) {
    const card = buildCard('TODAY');

    const value = document.createElement('div');
    value.className = 'card-value';
    value.textContent = fmtDuration(today.worked_s);
    card.appendChild(value);

    const sub = document.createElement('div');
    sub.className = 'card-sub';
    sub.appendChild(document.createTextNode('Balance '));
    const bal = document.createElement('span');
    bal.className = balanceClass(today.balance_s);
    bal.textContent = fmtBalance(today.balance_s);
    sub.appendChild(bal);
    if (today.target_s > 0) {
      sub.appendChild(document.createTextNode(` · target ${fmtDuration(today.target_s)}`));
    } else {
      sub.appendChild(document.createTextNode(' · no target today'));
    }
    card.appendChild(sub);

    return card;
  }

  // Used for week/month/total: big value is the balance, sub line is the
  // worked time vs. target. `extraLines` adds muted sub lines (total's
  // "since ...").
  function buildBalanceCard(labelText, summary, extraLines) {
    const card = buildCard(labelText);

    const value = document.createElement('div');
    value.className = `card-value ${balanceClass(summary.balance_s)}`;
    value.textContent = fmtBalance(summary.balance_s);
    card.appendChild(value);

    const sub = document.createElement('div');
    sub.className = 'card-sub';
    sub.textContent = `${fmtDuration(summary.worked_s)} worked · target ${fmtDuration(summary.target_s)}`;
    card.appendChild(sub);

    (extraLines || []).forEach((line) => {
      const extra = document.createElement('div');
      extra.className = 'card-sub-muted';
      extra.textContent = line;
      card.appendChild(extra);
    });

    return card;
  }

  function renderCards(data) {
    const container = document.getElementById('cards');
    container.textContent = '';
    container.appendChild(buildTodayCard(data.overview.today));
    container.appendChild(buildBalanceCard('THIS WEEK', data.overview.week));
    container.appendChild(buildBalanceCard('THIS MONTH', data.overview.month));
    container.appendChild(
      buildBalanceCard('TOTAL', data.overview.total, [`since ${fmtDate(data.overview.total.start)}`])
    );
  }

  // ------------------------------------------------------------------
  // (7) Render range bar + range summary
  // ------------------------------------------------------------------

  const RANGE_PRESETS = [
    { key: '7d', label: '7 days' },
    { key: '30d', label: '30 days' },
    { key: '90d', label: '90 days' },
    { key: 'year', label: 'This year' },
    { key: 'all', label: 'All time' },
  ];

  function buildRangePresetButtons() {
    const container = document.getElementById('range-presets');
    container.textContent = '';
    RANGE_PRESETS.forEach((p) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'range-preset';
      btn.textContent = p.label;
      btn.dataset.key = p.key;
      btn.setAttribute('aria-pressed', 'false');
      btn.addEventListener('click', () => {
        hideRangeError();
        rangeState = { preset: p.key };
        saveRangeState(rangeState);
        loadAndRender();
      });
      container.appendChild(btn);
    });
  }

  function updateRangePresetButtons() {
    const buttons = document.querySelectorAll('#range-presets .range-preset');
    buttons.forEach((btn) => {
      const pressed = rangeState.preset === btn.dataset.key;
      btn.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    });
  }

  function showRangeError(msg) {
    const el = document.getElementById('range-error');
    el.textContent = msg;
    el.hidden = false;
  }

  function hideRangeError() {
    const el = document.getElementById('range-error');
    el.hidden = true;
    el.textContent = '';
  }

  function onRangeDateChange() {
    const startInput = document.getElementById('range-start');
    const endInput = document.getElementById('range-end');
    const startVal = startInput.value;
    const endVal = endInput.value;
    if (!startVal || !endVal) return; // wait until both are filled in

    if (startVal > endVal) {
      showRangeError('Start date is after end date.');
      return;
    }
    hideRangeError();
    rangeState = { start: startVal, end: endVal };
    saveRangeState(rangeState);
    loadAndRender();
  }

  function setupRangeControls() {
    buildRangePresetButtons();
    document.getElementById('range-start').addEventListener('change', onRangeDateChange);
    document.getElementById('range-end').addEventListener('change', onRangeDateChange);
  }

  function renderRangeControls(data) {
    updateRangePresetButtons();
    // The date inputs always reflect the resolved range from the response,
    // whether that range came from a preset or was requested explicitly.
    document.getElementById('range-start').value = data.range.start;
    document.getElementById('range-end').value = data.range.end;
  }

  function renderRangeSummary(data) {
    const rs = data.range_summary;
    const el = document.getElementById('range-summary');
    el.textContent = '';
    el.appendChild(
      document.createTextNode(`${fmtDuration(rs.worked_s)} worked · target ${fmtDuration(rs.target_s)} · balance `)
    );
    const bal = document.createElement('span');
    bal.className = balanceClass(rs.balance_s);
    bal.textContent = fmtBalance(rs.balance_s);
    el.appendChild(bal);
    const sessionsText = rs.sessions === 1 ? '1 session' : `${rs.sessions} sessions`;
    el.appendChild(
      document.createTextNode(
        ` · ${sessionsText} · Ø ${fmtDuration(rs.avg_per_day_worked_s)} per day worked`
      )
    );
  }

  // ------------------------------------------------------------------
  // (8) Daily chart (Chart.js) with a target-line plugin
  // ------------------------------------------------------------------

  // Data the target-line plugin and the tooltip callbacks read; updated on
  // every render so the chart instance itself never needs to be rebuilt.
  let chartDailyTargetS = 0;
  let chartDaysData = [];
  let chartTodayStr = null;
  let chartRunning = false;

  const targetLinePlugin = {
    id: 'targetLine',
    afterDatasetsDraw(chart) {
      if (chartDailyTargetS <= 0) return;
      const { ctx, chartArea, scales } = chart;
      const y = scales.y.getPixelForValue(chartDailyTargetS / 3600);
      const tokens = getThemeTokens();

      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = tokens.targetLine;
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();

      ctx.setLineDash([]);
      ctx.fillStyle = tokens.textSecondary;
      ctx.font = '11px system-ui';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`Target ${fmtHM(chartDailyTargetS)}`, chartArea.right - 4, y - 4);
      ctx.restore();
    },
  };

  let dailyChart = null;

  function createDailyChart() {
    const tokens = getThemeTokens();
    const ctx = document.getElementById('daily-chart').getContext('2d');
    dailyChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [
          {
            data: [],
            backgroundColor: tokens.series,
            hoverBackgroundColor: tokens.seriesHover,
            borderRadius: 4,
            borderSkipped: 'start',
            maxBarThickness: 24,
            categoryPercentage: 0.8,
            barPercentage: 0.9,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { display: false },
            border: { color: tokens.axis },
            ticks: {
              color: tokens.textMuted,
              autoSkip: true,
              maxRotation: 0,
              font: { size: 11 },
              callback(value) {
                return fmtDayShort(this.getLabelForValue(value));
              },
            },
          },
          y: {
            beginAtZero: true,
            suggestedMax: 1,
            grid: { color: tokens.grid, lineWidth: 1 },
            border: { display: false },
            ticks: {
              color: tokens.textMuted,
              font: { size: 11 },
              callback: (v) => `${v}h`,
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: 'index',
            intersect: false,
            displayColors: false,
            backgroundColor: tokens.tooltipBg,
            titleColor: tokens.tooltipText,
            bodyColor: tokens.tooltipText,
            padding: 10,
            titleFont: { weight: 'bold' },
            callbacks: {
              title(items) {
                const day = chartDaysData[items[0].dataIndex];
                return day ? fmtDateLong(day.date) : '';
              },
              label(item) {
                const day = chartDaysData[item.dataIndex];
                if (!day) return '';
                const running = day.date === chartTodayStr && chartRunning ? ' (running)' : '';
                const lines = [`${fmtDuration(day.worked_s)} worked${running}`];
                lines.push(`Target ${day.target_s > 0 ? fmtDuration(day.target_s) : 'none'}`);
                lines.push(`Balance ${fmtBalance(day.worked_s - day.target_s)}`);
                return lines;
              },
            },
          },
        },
      },
      plugins: [targetLinePlugin],
    });
  }

  // Re-applies current theme tokens to the existing chart instance without
  // destroying/recreating it (keeps the canvas flicker-free).
  function applyChartTheme() {
    if (!dailyChart) return;
    const tokens = getThemeTokens();
    const dataset = dailyChart.data.datasets[0];
    dataset.backgroundColor = tokens.series;
    dataset.hoverBackgroundColor = tokens.seriesHover;

    const { scales, plugins } = dailyChart.options;
    scales.x.border.color = tokens.axis;
    scales.x.ticks.color = tokens.textMuted;
    scales.y.grid.color = tokens.grid;
    scales.y.ticks.color = tokens.textMuted;
    plugins.tooltip.backgroundColor = tokens.tooltipBg;
    plugins.tooltip.titleColor = tokens.tooltipText;
    plugins.tooltip.bodyColor = tokens.tooltipText;
  }

  function renderChartHeader(data) {
    document.getElementById('chart-subtitle').textContent =
      `${fmtDate(data.range.start)} – ${fmtDate(data.range.end)}`;
  }

  function updateDailyChart(data) {
    chartDailyTargetS = data.settings.daily_target_s;
    chartDaysData = data.days;
    chartTodayStr = data.generated.split('T')[0];
    chartRunning = data.status.running;

    dailyChart.data.labels = data.days.map((d) => d.date);
    dailyChart.data.datasets[0].data = data.days.map((d) => d.worked_s / 3600);
    dailyChart.options.scales.y.suggestedMax = Math.max((chartDailyTargetS / 3600) * 1.15, 1);
    applyChartTheme();
    dailyChart.update('none');
  }

  // ------------------------------------------------------------------
  // (9) Refresh loop + init
  // ------------------------------------------------------------------

  let lastData = null;
  let seq = 0;
  let inFlight = false;

  function showBanner(msg) {
    const banner = document.getElementById('banner');
    banner.textContent = msg;
    banner.hidden = false;
  }

  function hideBanner() {
    const banner = document.getElementById('banner');
    banner.hidden = true;
    banner.textContent = '';
  }

  function renderAll(data) {
    renderHeader(data);
    renderCards(data);
    renderRangeControls(data);
    renderRangeSummary(data);
    renderChartHeader(data);
    updateDailyChart(data);
  }

  async function loadAndRender() {
    const mySeq = ++seq;
    inFlight = true;
    const main = document.getElementById('main');
    main.classList.add('is-loading');
    try {
      const data = await fetchDashboard(rangeState);
      if (mySeq !== seq) return; // a newer request has since started
      lastData = data;
      renderAll(data);
      hideBanner();
    } catch (err) {
      if (mySeq !== seq) return;
      showBanner(err.message);
    } finally {
      if (mySeq === seq) {
        main.classList.remove('is-loading');
      }
      inFlight = false;
    }
  }

  // Periodic/visibility-triggered refreshes must not pile up requests: if
  // one is already in flight, this tick is simply skipped.
  function scheduledRefresh() {
    if (inFlight) return;
    loadAndRender();
  }

  function init() {
    setupRangeControls();
    createDailyChart();

    const darkMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    darkMediaQuery.addEventListener('change', () => {
      applyChartTheme();
      if (dailyChart) dailyChart.update('none');
    });

    loadAndRender();
    setInterval(scheduledRefresh, 60000);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') scheduledRefresh();
    });
    window.addEventListener('focus', scheduledRefresh);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
