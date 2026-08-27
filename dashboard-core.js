/* Shared rendering logic for both dashboard.html (static, embedded data) and
   live_dashboard.html (fetches trades.db via /api/trades and allows CRUD). */
const DC = (function () {
  const GOOD = 'var(--good)', CRIT = 'var(--critical)', MUTE = 'var(--muted-fill)', SERIES = 'var(--series-1)';
  const DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function fmtPct(x, digits) { digits = digits === undefined ? 1 : digits; return (x >= 0 ? '+' : '') + (x * 100).toFixed(digits) + '%'; }

  // Gradient + glow treatment shared by the win-rate bars and RR histogram --
  // gives bars depth against the dark surface instead of flat fills.
  function barStyle(kind) {
    if (kind === 'good') return 'background:linear-gradient(180deg, #2fe07f 0%, #0ca30c 100%); box-shadow:0 3px 16px -3px rgba(12,163,12,0.55);';
    if (kind === 'bad') return 'background:linear-gradient(180deg, #ff9d9d 0%, #d6403f 100%); box-shadow:0 3px 16px -3px rgba(214,64,63,0.55);';
    return 'background:linear-gradient(180deg, #7a7871 0%, #52514e 100%); box-shadow:0 3px 12px -4px rgba(0,0,0,0.4);';
  }

  // ---------- global hover tooltip (day/session bars, monthly heatmap) ----------
  let gtip;
  function ensureTip() {
    if (gtip) return gtip;
    gtip = document.createElement('div');
    gtip.className = 'gtooltip';
    document.body.appendChild(gtip);
    return gtip;
  }
  function showTip(e, html) { const el = ensureTip(); el.innerHTML = html; el.style.opacity = '1'; moveTip(e); }
  function moveTip(e) {
    const el = ensureTip();
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = e.clientX + 14, top = e.clientY - 12;
    el.style.left = Math.min(left, vw - 220) + 'px';
    el.style.top = Math.max(8, Math.min(top, vh - 90)) + 'px';
  }
  function hideTip() { if (gtip) gtip.style.opacity = '0'; }

  // ---------- data shaping ----------

  function computeStats(trades) {
    const total = trades.length;
    const wins = trades.filter(t => t.result === 'WIN');
    const losses = trades.filter(t => t.result === 'LOSS');
    const be = trades.filter(t => t.result === 'BE');
    const winRate = total ? wins.length / total : 0; // BE counted in the denominator (true win rate)
    const totalReturn = trades.reduce((s, t) => s + t.pnl, 0);
    const grossWin = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : 0);
    const avgRRwin = wins.length ? wins.reduce((s, t) => s + t.rr, 0) / wins.length : 0;
    const expectancy = total ? totalReturn / total : 0;

    let bestStreak = 0, worstStreak = 0, cur = 0;
    trades.forEach(t => {
      if (t.result === 'WIN') cur = cur > 0 ? cur + 1 : 1;
      else if (t.result === 'LOSS') cur = cur < 0 ? cur - 1 : -1;
      bestStreak = Math.max(bestStreak, cur);
      worstStreak = Math.min(worstStreak, cur);
    });

    const violations = trades.filter(isPlanViolation).length;

    return { total, wins: wins.length, losses: losses.length, be: be.length, winRate, totalReturn, profitFactor, avgRRwin, expectancy, streak: cur, bestStreak, worstStreak, violations };
  }

  function computeEquity(trades) {
    let cum = 0;
    return trades.map(t => { cum += t.pnl; return { date: t.date, pair: t.pair, cum, pnl: t.pnl, result: t.result }; });
  }

  // Takes computeEquity's output, not raw trades -- drawdown is a property
  // of the cumulative curve, so there's no reason to re-derive the running
  // total here too. Tracks a running peak ("high-water mark") and the
  // biggest peak-to-trough gap seen anywhere in the sequence; then looks
  // for the first later point that claws back up to that same peak to
  // measure how long the recovery took. Index-based internally (not
  // date-based) because multiple trades can share a date -- searching by
  // date would risk matching the wrong same-day trade.
  function computeDrawdown(equityPoints) {
    if (!equityPoints.length) {
      return { maxDrawdown: 0, peakDate: null, troughDate: null, recoveryDate: null, daysToRecover: null, recovered: true };
    }
    let peakValue = 0, peakIdx = 0;
    let maxDD = 0, ddPeakIdx = 0, ddTroughIdx = 0;

    equityPoints.forEach((p, i) => {
      if (p.cum > peakValue) { peakValue = p.cum; peakIdx = i; }
      const dd = peakValue - p.cum;
      if (dd > maxDD) { maxDD = dd; ddPeakIdx = peakIdx; ddTroughIdx = i; }
    });

    const peakValueAtDD = equityPoints[ddPeakIdx].cum;
    let recoveryIdx = -1;
    for (let i = ddTroughIdx + 1; i < equityPoints.length; i++) {
      if (equityPoints[i].cum >= peakValueAtDD) { recoveryIdx = i; break; }
    }

    const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000);

    return {
      maxDrawdown: maxDD,
      peakDate: equityPoints[ddPeakIdx].date,
      troughDate: equityPoints[ddTroughIdx].date,
      recoveryDate: recoveryIdx >= 0 ? equityPoints[recoveryIdx].date : null,
      daysToRecover: recoveryIdx >= 0 ? daysBetween(equityPoints[ddTroughIdx].date, equityPoints[recoveryIdx].date) : null,
      recovered: recoveryIdx >= 0,
      // raw positions too, so a caller that already has the same points
      // array (the equity chart) can draw the band without re-searching
      peakIdx: ddPeakIdx, troughIdx: ddTroughIdx, recoveryIdx,
    };
  }

  function computeGroupStats(trades, key, order) {
    const map = new Map();
    trades.forEach(t => {
      const k = t[key] || 'Unknown';
      if (!map.has(k)) map.set(k, { key: k, wins: 0, losses: 0, be: 0, total: 0, pnl: 0 });
      const g = map.get(k);
      g.total++; g.pnl += t.pnl;
      if (t.result === 'WIN') g.wins++; else if (t.result === 'LOSS') g.losses++; else g.be++;
    });
    let groups = Array.from(map.values());
    groups.forEach(g => { g.winRate = g.total ? g.wins / g.total : 0; });
    if (order) groups = order.filter(k => map.has(k)).map(k => map.get(k));
    else groups.sort((a, b) => b.total - a.total);
    return groups;
  }

  function computeMonthGrid(trades) {
    const map = new Map();
    trades.forEach(t => {
      const k = t.date.slice(0, 7);
      if (!map.has(k)) map.set(k, { wins: 0, losses: 0, be: 0, total: 0, pnl: 0 });
      const g = map.get(k);
      g.total++; g.pnl += t.pnl;
      if (t.result === 'WIN') g.wins++; else if (t.result === 'LOSS') g.losses++; else g.be++;
    });
    return map;
  }

  function computeByPair(trades) {
    const map = new Map();
    trades.forEach(t => {
      const k = t.pair || 'Unknown';
      if (!map.has(k)) map.set(k, { pair: k, total: 0, wins: 0, losses: 0, pnl: 0 });
      const g = map.get(k);
      g.total++; g.pnl += t.pnl;
      if (t.result === 'WIN') g.wins++; else if (t.result === 'LOSS') g.losses++;
    });
    return Array.from(map.values()).sort((a, b) => b.total - a.total);
  }

  function computeBestWorst(trades) {
    if (!trades.length) return null;
    const byPair = computeByPair(trades);
    let bestPair = byPair[0], worstPair = byPair[0];
    byPair.forEach(p => { if (p.pnl > bestPair.pnl) bestPair = p; if (p.pnl < worstPair.pnl) worstPair = p; });
    let bestTrade = trades[0], worstTrade = trades[0];
    trades.forEach(t => { if (t.pnl > bestTrade.pnl) bestTrade = t; if (t.pnl < worstTrade.pnl) worstTrade = t; });
    return { bestPair, worstPair, bestTrade, worstTrade };
  }

  // ---------- rendering: tiles / equity / donut ----------

  function renderTiles(hostId, stats, drawdown) {
    const host0 = document.getElementById(hostId);
    if (!host0) return;
    // With zero trades in range, every ratio computes to a meaningless 0 --
    // coloring that red/green (a 0% win rate reading as a "bad" critical
    // result, a break-even return reading as "good") makes an empty range
    // look like a losing day instead of just an unfilled one. Neutral
    // dashes instead, matching how "Current streak" already handles zero.
    const noData = stats.total === 0;
    const pf = isFinite(stats.profitFactor) ? stats.profitFactor.toFixed(2) : '∞';
    const tiles = [
      { label: 'Total trades', value: stats.total },
      { label: 'Win rate', value: noData ? '—' : (stats.winRate * 100).toFixed(1) + '%', cls: noData ? '' : (stats.winRate >= 0.5 ? 'good' : 'critical') },
      { label: 'Total return', value: noData ? '—' : fmtPct(stats.totalReturn), cls: noData ? '' : (stats.totalReturn >= 0 ? 'good' : 'critical') },
      { label: 'Profit factor', value: noData ? '—' : pf, cls: noData ? '' : (stats.profitFactor >= 1 ? 'good' : 'critical') },
      { label: 'Avg RR (wins)', value: noData ? '—' : stats.avgRRwin.toFixed(2) + 'R' },
      { label: 'Expectancy / trade', value: noData ? '—' : fmtPct(stats.expectancy, 2), cls: noData ? '' : (stats.expectancy >= 0 ? 'good' : 'critical') },
      { label: 'Current streak', value: (stats.streak > 0 ? stats.streak + 'W' : stats.streak < 0 ? Math.abs(stats.streak) + 'L' : '—'), cls: stats.streak > 0 ? 'good' : (stats.streak < 0 ? 'critical' : '') },
      { label: 'Best streak', value: noData ? '—' : stats.bestStreak + 'W', cls: noData ? '' : 'good' },
      { label: 'Plan violations', value: stats.violations, cls: stats.violations > 0 ? 'critical' : '' },
    ];
    if (drawdown && drawdown.maxDrawdown > 0) {
      tiles.push({ label: 'Max drawdown', value: '-' + (drawdown.maxDrawdown * 100).toFixed(1) + '%', cls: 'critical' });
      tiles.push({
        label: 'Recovery',
        value: drawdown.recovered ? `${drawdown.daysToRecover}d` : 'Ongoing',
        cls: drawdown.recovered ? 'good' : 'critical',
      });
    }
    host0.innerHTML = tiles.map(t => `
      <div class="tile"><div class="label">${t.label}</div><div class="value ${t.cls || ''}">${t.value}</div></div>
    `).join('');
  }

  function renderEquity(hostId, points, drawdown) {
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!points.length) { host.innerHTML = '<div class="empty">No trades in this range.</div>'; return; }
    const W = 1000, H = 280, PAD_L = 46, PAD_R = 10, PAD_T = 14, PAD_B = 26;
    const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
    const vals = points.map(p => p.cum);
    let min = Math.min(0, ...vals), max = Math.max(0, ...vals);
    if (min === max) { min -= 0.01; max += 0.01; }
    const pad = (max - min) * 0.08;
    min -= pad; max += pad;
    const x = i => PAD_L + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
    const y = v => PAD_T + innerH - ((v - min) / (max - min)) * innerH;

    const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(2)} ${y(p.cum).toFixed(2)}`).join(' ');
    const zeroY = y(0);
    const areaPath = `${path} L ${x(points.length - 1).toFixed(2)} ${zeroY.toFixed(2)} L ${x(0).toFixed(2)} ${zeroY.toFixed(2)} Z`;

    let gridSvg = '';
    for (let i = 0; i <= 4; i++) {
      const v = min + (i / 4) * (max - min);
      const yy = y(v);
      gridSvg += `<line class="gridline" x1="${PAD_L}" x2="${W - PAD_R}" y1="${yy.toFixed(2)}" y2="${yy.toFixed(2)}"/>`;
      gridSvg += `<text class="axis-label" x="${PAD_L - 8}" y="${(yy + 3).toFixed(2)}" text-anchor="end">${(v * 100).toFixed(0)}%</text>`;
    }
    let xLabelsSvg = '';
    for (let i = 0; i < 6; i++) {
      const idx = Math.round((i / 5) * (points.length - 1));
      xLabelsSvg += `<text class="axis-label" x="${x(idx).toFixed(2)}" y="${H - 6}" text-anchor="middle">${points[idx].date.slice(5)}</text>`;
    }

    // Shade the worst peak-to-trough stretch directly on the curve -- a
    // number in a tile tells you "how deep"; seeing exactly where and how
    // long tells you a lot more about what happened in the account.
    let drawdownSvg = '';
    if (drawdown && drawdown.maxDrawdown > 0) {
      const ddEndIdx = drawdown.recoveryIdx >= 0 ? drawdown.recoveryIdx : points.length - 1;
      const bandX1 = x(drawdown.peakIdx), bandX2 = x(ddEndIdx);
      const troughX = x(drawdown.troughIdx), troughY = y(points[drawdown.troughIdx].cum);
      drawdownSvg = `
        <rect x="${bandX1.toFixed(2)}" y="${PAD_T}" width="${(bandX2 - bandX1).toFixed(2)}" height="${innerH}" fill="${CRIT}" opacity="0.07"/>
        <circle cx="${troughX.toFixed(2)}" cy="${troughY.toFixed(2)}" r="4" fill="${CRIT}"/>
        <circle cx="${troughX.toFixed(2)}" cy="${troughY.toFixed(2)}" r="8" fill="none" stroke="${CRIT}" stroke-width="1.5" opacity="0.5"/>
      `;
    }

    host.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="${hostId}Svg">
        ${gridSvg}
        ${drawdownSvg}
        <line class="baseline" x1="${PAD_L}" x2="${W - PAD_R}" y1="${zeroY.toFixed(2)}" y2="${zeroY.toFixed(2)}"/>
        <path d="${areaPath}" fill="${SERIES}" opacity="0.10" stroke="none"/>
        <path d="${path}" fill="none" stroke="${SERIES}" stroke-width="2"/>
        ${xLabelsSvg}
        <g id="${hostId}Cursor" style="display:none">
          <line x1="0" x2="0" y1="${PAD_T}" y2="${PAD_T + innerH}" stroke="var(--text-muted)" stroke-width="1" stroke-dasharray="3,3"/>
          <circle r="4" fill="${SERIES}" stroke="var(--surface-1)" stroke-width="2"/>
        </g>
      </svg>
      <div class="tooltip" id="${hostId}Tooltip"></div>
    `;

    const svg = document.getElementById(hostId + 'Svg');
    const cursor = document.getElementById(hostId + 'Cursor');
    const tooltip = document.getElementById(hostId + 'Tooltip');
    const cursorLine = cursor.querySelector('line');
    const cursorDot = cursor.querySelector('circle');

    svg.addEventListener('mousemove', (e) => {
      const rect = svg.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width * W;
      let idx = Math.round(((relX - PAD_L) / innerW) * (points.length - 1));
      idx = Math.max(0, Math.min(points.length - 1, idx));
      const p = points[idx];
      const px = x(idx), py = y(p.cum);
      cursor.style.display = '';
      cursorLine.setAttribute('x1', px); cursorLine.setAttribute('x2', px);
      cursorDot.setAttribute('cx', px); cursorDot.setAttribute('cy', py);
      tooltip.style.opacity = '1';
      tooltip.style.left = Math.min(rect.width - 150, Math.max(0, (px / W) * rect.width + 12)) + 'px';
      tooltip.style.top = Math.max(0, (py / H) * rect.height - 46) + 'px';
      tooltip.innerHTML = `<div class="t-title">${p.date} · ${p.pair || ''}</div>
        <div class="t-row">Trade P/L: <b style="color:${p.pnl >= 0 ? GOOD : CRIT}">${fmtPct(p.pnl, 2)}</b></div>
        <div class="t-row">Cumulative: <b>${fmtPct(p.cum)}</b></div>`;
    });
    svg.addEventListener('mouseleave', () => { cursor.style.display = 'none'; tooltip.style.opacity = '0'; });
  }

  function renderDonut(hostId, stats) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const total = stats.total || 0;
    const R = 40, CX = 50, CY = 50, SW = 14;
    const circumference = 2 * Math.PI * R;
    const segs = total ? [
      { key: 'Wins', n: stats.wins, color: GOOD },
      { key: 'Losses', n: stats.losses, color: CRIT },
      { key: 'Breakeven', n: stats.be, color: MUTE },
    ] : [];
    let cum = 0;
    const arcs = segs.filter(s => s.n > 0).map(s => {
      const frac = s.n / total;
      const len = frac * circumference;
      const offset = -cum * circumference;
      cum += frac;
      return `<circle class="donut-seg" data-key="${s.key}" cx="${CX}" cy="${CY}" r="${R}" fill="none" stroke="${s.color}"
        stroke-width="${SW}" stroke-dasharray="${len.toFixed(2)} ${(circumference - len).toFixed(2)}"
        stroke-dashoffset="${offset.toFixed(2)}" transform="rotate(-90 ${CX} ${CY})" style="cursor:pointer;"/>`;
    }).join('');

    host.innerHTML = `
      <div class="donutRow">
        <div class="donut">
          <svg viewBox="0 0 100 100" width="128" height="128">
            <circle cx="${CX}" cy="${CY}" r="${R}" fill="none" stroke="var(--surface-2)" stroke-width="${SW}"/>
            ${arcs}
          </svg>
          <div class="center"><b>${(stats.winRate * 100).toFixed(0)}%</b><span>win rate</span></div>
        </div>
        <div class="legend" style="flex-direction:column; gap:10px;">
          <div class="item"><span class="swatch" style="background:${GOOD}"></span>Wins <b style="color:var(--text-primary); margin-left:4px;">${stats.wins}</b></div>
          <div class="item"><span class="swatch" style="background:${CRIT}"></span>Losses <b style="color:var(--text-primary); margin-left:4px;">${stats.losses}</b></div>
          <div class="item"><span class="swatch" style="background:${MUTE}"></span>Breakeven <b style="color:var(--text-primary); margin-left:4px;">${stats.be}</b></div>
        </div>
      </div>`;

    host.querySelectorAll('.donut-seg').forEach(seg => {
      const s = segs.find(x => x.key === seg.dataset.key);
      const tooltipHtml = () => `<div class="t-title">${s.key}</div>
        <div class="t-row">${s.n} of ${total} trades</div>
        <div class="t-row">Share: <b>${(s.n / total * 100).toFixed(1)}%</b></div>`;
      seg.addEventListener('mouseenter', (e) => showTip(e, tooltipHtml()));
      seg.addEventListener('mousemove', moveTip);
      seg.addEventListener('mouseleave', hideTip);
    });
  }

  // ---------- win-rate bar charts (day of week / session) ----------

  function renderWinRateBars(hostId, groups) {
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!groups.length) { host.innerHTML = '<div class="empty">No data.</div>'; return; }
    const CH = 180;
    const cols = groups.map(g => {
      const h = Math.max(2, Math.round(g.winRate * CH));
      const style = barStyle(g.winRate >= 0.5 ? 'good' : 'bad');
      return `<div class="wr-col">
          <div class="wr-pct">${(g.winRate * 100).toFixed(0)}%</div>
          <div class="wr-baraxis">
            <div class="wr-refline"></div>
            <div class="wr-bar" data-key="${g.key}" style="height:${h}px; ${style}"></div>
          </div>
          <div class="wr-label">${g.key}<br><span class="wr-count">${g.total} trade${g.total === 1 ? '' : 's'}</span></div>
        </div>`;
    }).join('');
    host.innerHTML = `<div class="wr-chart">${cols}</div>`;
    host.querySelectorAll('.wr-bar').forEach((el, i) => {
      const g = groups[i];
      const tooltipHtml = () => `<div class="t-title">${g.key}</div>
        <div class="t-row">Win rate: <b>${(g.winRate * 100).toFixed(1)}%</b></div>
        <div class="t-row">${g.wins}W / ${g.losses}L / ${g.be}BE · ${g.total} trades</div>
        <div class="t-row">Net return: <b style="color:${g.pnl >= 0 ? GOOD : CRIT}">${fmtPct(g.pnl, 2)}</b></div>`;
      el.addEventListener('mouseenter', (e) => showTip(e, tooltipHtml()));
      el.addEventListener('mousemove', moveTip);
      el.addEventListener('mouseleave', hideTip);
    });
  }

  // ---------- monthly return heatmap ----------

  function renderMonthHeatmap(hostId, trades) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const map = computeMonthGrid(trades);
    if (!map.size) { host.innerHTML = '<div class="empty">No data.</div>'; return; }
    const years = [...new Set([...map.keys()].map(k => k.slice(0, 4)))].sort().reverse();
    const maxAbs = Math.max(0.005, ...[...map.values()].map(g => Math.abs(g.pnl)));

    const header = `<div class="hm-row hm-header"><div class="hm-yearlabel"></div>${MONTH_NAMES.map(m => `<div class="hm-monthlabel">${m}</div>`).join('')}</div>`;
    let rows = '';
    years.forEach(y => {
      let cells = `<div class="hm-yearlabel">${y}</div>`;
      for (let m = 1; m <= 12; m++) {
        const key = `${y}-${String(m).padStart(2, '0')}`;
        const g = map.get(key);
        if (!g) { cells += `<div class="hm-cell hm-empty"></div>`; continue; }
        const intensity = Math.min(1, Math.abs(g.pnl) / maxAbs);
        const alphaPct = Math.round(20 + intensity * 65);
        const base = g.pnl >= 0 ? GOOD : CRIT;
        const bg = `linear-gradient(135deg, color-mix(in srgb, ${base} ${Math.min(100, alphaPct + 18)}%, var(--surface-2)) 0%, color-mix(in srgb, ${base} ${alphaPct}%, var(--surface-2)) 100%)`;
        const textColor = alphaPct > 45 ? '#fff' : 'var(--text-primary)';
        cells += `<div class="hm-cell" data-key="${key}" style="background:${bg}; color:${textColor};">${(g.pnl * 100).toFixed(1)}%</div>`;
      }
      rows += `<div class="hm-row">${cells}</div>`;
    });

    host.innerHTML = `<div class="heatmap">${header}${rows}</div>`;
    host.querySelectorAll('.hm-cell[data-key]').forEach(cell => {
      const g = map.get(cell.dataset.key);
      const [y, m] = cell.dataset.key.split('-');
      const tooltipHtml = () => `<div class="t-title">${MONTH_NAMES[parseInt(m, 10) - 1]} ${y}</div>
        <div class="t-row">Return: <b style="color:${g.pnl >= 0 ? GOOD : CRIT}">${fmtPct(g.pnl, 2)}</b></div>
        <div class="t-row">${g.wins}W / ${g.losses}L / ${g.be}BE · ${g.total} trades</div>`;
      cell.addEventListener('mouseenter', (e) => showTip(e, tooltipHtml()));
      cell.addEventListener('mousemove', moveTip);
      cell.addEventListener('mouseleave', hideTip);
    });
  }

  // ---------- best / worst ----------

  function renderBestWorstPairs(hostId, bw) {
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!bw) { host.innerHTML = '<div class="empty">No data.</div>'; return; }
    const cards = [
      { label: 'Best pair', title: bw.bestPair.pair, sub: `${bw.bestPair.total} trades · ${(bw.bestPair.wins / bw.bestPair.total * 100).toFixed(0)}% win rate`, value: fmtPct(bw.bestPair.pnl, 2), good: bw.bestPair.pnl >= 0 },
      { label: 'Worst pair', title: bw.worstPair.pair, sub: `${bw.worstPair.total} trades · ${(bw.worstPair.wins / bw.worstPair.total * 100).toFixed(0)}% win rate`, value: fmtPct(bw.worstPair.pnl, 2), good: bw.worstPair.pnl >= 0 },
    ];
    host.innerHTML = renderBwCards(cards);
  }

  function renderBestWorstTrades(hostId, bw) {
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!bw) { host.innerHTML = '<div class="empty">No data.</div>'; return; }
    const cards = [
      { label: 'Best trade', title: bw.bestTrade.pair || '—', sub: `${bw.bestTrade.date} · ${bw.bestTrade.session || ''} · ${bw.bestTrade.direction || ''}`, value: fmtPct(bw.bestTrade.pnl, 2), good: bw.bestTrade.pnl >= 0 },
      { label: 'Worst trade', title: bw.worstTrade.pair || '—', sub: `${bw.worstTrade.date} · ${bw.worstTrade.session || ''} · ${bw.worstTrade.direction || ''}`, value: fmtPct(bw.worstTrade.pnl, 2), good: bw.worstTrade.pnl >= 0 },
    ];
    host.innerHTML = renderBwCards(cards);
  }

  function renderBwCards(cards) {
    return `<div class="bw-grid">${cards.map(c => `
      <div class="bw-card ${c.good ? 'bw-good' : 'bw-bad'}">
        <div class="bw-label">${c.label}</div>
        <div class="bw-title">${c.title}</div>
        <div class="bw-value ${c.good ? 'good' : 'critical'}">${c.value}</div>
        <div class="bw-sub">${c.sub}</div>
      </div>`).join('')}</div>`;
  }

  // ---------- reflection & screenshots popups ----------

  const SPARKLE_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z"/></svg>';
  const IMAGE_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>';

  let modalsReady = false;
  function ensureTradeModals() {
    if (modalsReady) return;
    modalsReady = true;
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <div class="modal-backdrop hidden" id="dcReflectionBackdrop">
        <div class="modal reflection-modal">
          <button class="modal-close-x" id="dcReflectionClose">&times;</button>
          <div class="reflection-modal-header">
            <div class="reflection-modal-date" id="dcReflectionMeta"></div>
            <div class="reflection-modal-pair" id="dcReflectionPair"></div>
          </div>
          <div class="reflection-modal-body" id="dcReflectionBody"></div>
        </div>
      </div>
      <div class="modal-backdrop hidden" id="dcScreensBackdrop">
        <div class="modal screenshots-modal">
          <button class="modal-close-x" id="dcScreensClose">&times;</button>
          <div class="screenshots-modal-header" id="dcScreensHeader"></div>
          <div class="screenshots-modal-body" id="dcScreensBody"></div>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const closeAll = () => {
      document.getElementById('dcReflectionBackdrop').classList.add('hidden');
      document.getElementById('dcScreensBackdrop').classList.add('hidden');
    };
    document.getElementById('dcReflectionClose').addEventListener('click', closeAll);
    document.getElementById('dcScreensClose').addEventListener('click', closeAll);
    document.getElementById('dcReflectionBackdrop').addEventListener('click', (e) => { if (e.target.id === 'dcReflectionBackdrop') closeAll(); });
    document.getElementById('dcScreensBackdrop').addEventListener('click', (e) => { if (e.target.id === 'dcScreensBackdrop') closeAll(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });
  }

  function openReflectionModal(t) {
    ensureTradeModals();
    document.getElementById('dcReflectionMeta').textContent = `${t.date} · ${t.session || ''}`;
    document.getElementById('dcReflectionPair').innerHTML = `${t.pair || ''} <span class="badge ${t.result}">${t.result}</span>`;
    document.getElementById('dcReflectionBody').textContent = t.notes || '';
    document.getElementById('dcReflectionBackdrop').classList.remove('hidden');
  }

  function openScreenshotsModal(t) {
    ensureTradeModals();
    document.getElementById('dcScreensHeader').textContent = `${t.date} · ${t.pair || ''} · ${t.direction || ''}`;
    document.getElementById('dcScreensBody').innerHTML = (t.charts || []).map(c => `
      <a class="screenshot-block" href="${c.link}" target="_blank" rel="noopener" title="Opens the original on TradingView">
        <div class="screenshot-label">${c.label}</div>
        <img src="${c.img}" alt="${c.label} chart" loading="lazy">
      </a>`).join('');
    document.getElementById('dcScreensBackdrop').classList.remove('hidden');
  }

  // ---------- pair table ----------

  function renderPairTable(hostId, pairs, labelHeader, extraColumns) {
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!pairs.length) { host.innerHTML = '<div class="empty">No data.</div>'; return; }
    extraColumns = extraColumns || [];
    const rows = pairs.map(p => {
      const wr = p.total ? p.wins / p.total : 0;
      const extraCells = extraColumns.map(c => `<td class="num">${c.format(p[c.key])}</td>`).join('');
      return `<tr>
        <td class="strong">${p.pair}</td>
        <td class="num">${p.total}</td>
        <td class="num"><span class="winrate-cell"><span class="winbar-track"><span class="winbar-fill" style="width:${(wr * 100).toFixed(0)}%"></span></span>${(wr * 100).toFixed(0)}%</span></td>
        <td class="num" style="color:${p.pnl >= 0 ? 'var(--good)' : 'var(--critical)'}">${fmtPct(p.pnl, 2)}</td>
        ${extraCells}
      </tr>`;
    }).join('');
    const extraHeaders = extraColumns.map(c => `<th class="num">${c.header}</th>`).join('');
    host.innerHTML = `<table>
      <thead><tr><th>${labelHeader || 'Pair'}</th><th class="num">Trades</th><th class="num">Win rate</th><th class="num">Return</th>${extraHeaders}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  // ---------- trades table ----------

  const VIOLATION_RE = /plan violation|didn.?t stick to (the |my )?plan|did not stick to (the |my )?plan|gambl|revenge trad|fomo/i;
  function isPlanViolation(t) { return !!(t.notes && VIOLATION_RE.test(t.notes)); }

  function renderTradesTable(hostId, trades, hooks) {
    hooks = hooks || {};
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!trades.length) { host.innerHTML = '<div class="empty">No trades in this range.</div>'; return; }
    const ordered = [...trades].reverse();
    const canExpand = !!(hooks.onEdit || hooks.onDelete);
    const rows = ordered.map((t, i) => {
      const hasNotes = t.notes && t.notes.trim().length > 0;
      const hasCharts = t.charts && t.charts.length > 0;
      const flagged = isPlanViolation(t);
      const reflectCell = hasNotes ? `<button class="row-icon-btn reflect-icon" data-reflect="${i}" title="View reflection">${SPARKLE_SVG}</button>` : '<span class="icon-cell-empty">—</span>';
      const screensCell = hasCharts ? `<button class="row-icon-btn screens-icon" data-screens="${i}" title="View screenshots">${IMAGE_SVG}</button>` : '<span class="icon-cell-empty">—</span>';
      const expandCell = canExpand ? '<td>▾</td>' : '';
      const expandRow = canExpand ? `<tr class="notes-row" id="${hostId}-notes-${t.id}"><td colspan="10">
          <div class="row-actions">
            ${hooks.onEdit ? `<button class="iconbtn small" data-edit="${t.id}">Edit</button>` : ''}
            ${hooks.onDelete ? `<button class="iconbtn small danger" data-delete="${t.id}">Delete</button>` : ''}
          </div>
        </td></tr>` : '';
      return `<tr class="trade-row${canExpand ? '' : ' no-expand'}" data-id="${t.id != null ? t.id : ''}">
          <td>${t.date}</td>
          <td>${t.session || ''}</td>
          <td class="strong">${t.pair || ''}</td>
          <td>${t.direction || ''}</td>
          <td class="num">${t.rr.toFixed(2)}R</td>
          <td class="num" style="color:${t.pnl >= 0 ? 'var(--good)' : 'var(--critical)'}">${fmtPct(t.pnl, 2)}</td>
          <td><span class="badge ${t.result}">${t.result}</span>${flagged ? '<span class="badge FLAG" title="Notes mention a plan violation">⚠ PLAN</span>' : ''}</td>
          <td class="icon-cell">${reflectCell}</td>
          <td class="icon-cell">${screensCell}</td>
          ${expandCell}
        </tr>
        ${expandRow}`;
    }).join('');
    host.innerHTML = `<table>
      <thead><tr><th>Date</th><th>Session</th><th>Pair</th><th>Dir</th><th class="num">RR</th><th class="num">P/L</th><th>Result</th><th>Reflect</th><th>Charts</th>${canExpand ? '<th></th>' : ''}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    if (canExpand) {
      host.querySelectorAll('.trade-row').forEach(row => {
        row.addEventListener('click', () => document.getElementById(hostId + '-notes-' + row.dataset.id).classList.toggle('open'));
      });
      if (hooks.onEdit) host.querySelectorAll('[data-edit]').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); hooks.onEdit(Number(btn.dataset.edit)); }));
      if (hooks.onDelete) host.querySelectorAll('[data-delete]').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); hooks.onDelete(Number(btn.dataset.delete)); }));
    }
    host.querySelectorAll('[data-reflect]').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); openReflectionModal(ordered[Number(btn.dataset.reflect)]); }));
    host.querySelectorAll('[data-screens]').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); openScreenshotsModal(ordered[Number(btn.dataset.screens)]); }));
  }

  // ---------- RR distribution histogram ----------

  const RR_BUCKETS = [
    { label: '≤-2R', test: r => r <= -2, kind: 'bad' },
    { label: '-2 to -1R', test: r => r > -2 && r < -1, kind: 'bad' },
    { label: '-1 to 0R', test: r => r >= -1 && r < 0, kind: 'bad' },
    { label: '0R', test: r => r === 0, kind: 'neutral' },
    { label: '0 to 1R', test: r => r > 0 && r < 1, kind: 'good' },
    { label: '1 to 2R', test: r => r >= 1 && r < 2, kind: 'good' },
    { label: '2 to 3R', test: r => r >= 2 && r < 3, kind: 'good' },
    { label: '>3R', test: r => r >= 3, kind: 'good' },
  ];

  function renderRRHistogram(hostId, trades) {
    const host = document.getElementById(hostId);
    if (!host) return;
    if (!trades.length) { host.innerHTML = '<div class="empty">No data.</div>'; return; }
    const buckets = RR_BUCKETS.map(b => ({ ...b, list: trades.filter(t => b.test(t.rr)) }));
    const maxCount = Math.max(1, ...buckets.map(b => b.list.length));
    const CH = 150;
    const cols = buckets.map(b => {
      const h = b.list.length ? Math.max(4, Math.round(b.list.length / maxCount * CH)) : 0;
      return `<div class="hist-col">
          <div class="hist-count">${b.list.length}</div>
          <div class="hist-baraxis"><div class="hist-bar" style="height:${h}px; ${barStyle(b.kind)}"></div></div>
          <div class="hist-label">${b.label}</div>
        </div>`;
    }).join('');
    host.innerHTML = `<div class="hist-chart">${cols}</div>`;
    host.querySelectorAll('.hist-bar').forEach((el, i) => {
      const b = buckets[i];
      if (!b.list.length) return;
      const wins = b.list.filter(t => t.result === 'WIN').length;
      const netPnl = b.list.reduce((s, t) => s + t.pnl, 0);
      const tooltipHtml = () => `<div class="t-title">${b.label}</div>
        <div class="t-row">${b.list.length} trades (${wins} wins)</div>
        <div class="t-row">Net return: <b style="color:${netPnl >= 0 ? GOOD : CRIT}">${fmtPct(netPnl, 2)}</b></div>`;
      el.addEventListener('mouseenter', (e) => showTip(e, tooltipHtml()));
      el.addEventListener('mousemove', moveTip);
      el.addEventListener('mouseleave', hideTip);
    });
  }

  // ---------- trade calendar ----------

  const MONTH_NAMES_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  let calCursor = null;   // 'YYYY-MM'
  let calSelected = null; // 'YYYY-MM-DD'

  function renderCalendar(gridId, detailsId, allTrades, hooks, accountBalance) {
    const host = document.getElementById(gridId);
    const detailsHost = document.getElementById(detailsId);
    if (!host || !detailsHost) return;
    if (!allTrades.length) { host.innerHTML = '<div class="empty">No trades yet.</div>'; detailsHost.innerHTML = ''; return; }

    const byDate = new Map();
    allTrades.forEach(t => { if (!byDate.has(t.date)) byDate.set(t.date, []); byDate.get(t.date).push(t); });

    if (!calCursor) calCursor = [...byDate.keys()].sort().slice(-1)[0].slice(0, 7);
    const [cy, cm] = calCursor.split('-').map(Number);
    const totalDays = new Date(cy, cm, 0).getDate();
    const startOffset = (new Date(cy, cm - 1, 1).getDay() + 6) % 7; // Monday-start week

    let cells = '';
    for (let i = 0; i < startOffset; i++) cells += `<div class="cal-cell cal-empty"></div>`;
    for (let d = 1; d <= totalDays; d++) {
      const key = `${cy}-${String(cm).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const dayTrades = byDate.get(key) || [];
      const hasTrades = dayTrades.length > 0;
      let style = '', amountHtml = '';
      if (hasTrades) {
        const netPnl = dayTrades.reduce((s, t) => s + t.pnl, 0);
        const base = netPnl > 0 ? GOOD : (netPnl < 0 ? CRIT : MUTE);
        style = `style="background:color-mix(in srgb, ${base} 16%, var(--surface-1));"`;
        const moneyHtml = accountBalance ? `<div class="cal-money" style="color:${base};">${netPnl >= 0 ? '+' : '-'}$${Math.abs(netPnl * accountBalance).toFixed(0)}</div>` : '';
        amountHtml = `<div class="cal-amount" style="color:${base};">${fmtPct(netPnl, 1)}</div>${moneyHtml}`;
      }
      const sel = key === calSelected ? ' cal-selected' : '';
      cells += `<div class="cal-cell${hasTrades ? ' cal-hastrades' : ''}${sel}" ${style} data-date="${key}">
          <div class="cal-daynum">${d}</div>${amountHtml}
        </div>`;
    }

    host.innerHTML = `
      <div class="cal-header">
        <div class="cal-title">${MONTH_NAMES_FULL[cm - 1]} ${cy}</div>
        <div class="cal-nav">
          <button class="iconbtn small" id="calPrev">‹ Prev</button>
          <button class="iconbtn small" id="calLatest">Latest</button>
          <button class="iconbtn small" id="calNext">Next ›</button>
        </div>
      </div>
      <div class="cal-grid">
        ${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(w => `<div class="cal-weekday">${w}</div>`).join('')}
        ${cells}
      </div>`;

    const rerender = () => renderCalendar(gridId, detailsId, allTrades, hooks, accountBalance);
    document.getElementById('calPrev').addEventListener('click', () => { shiftCalMonth(-1); rerender(); });
    document.getElementById('calNext').addEventListener('click', () => { shiftCalMonth(1); rerender(); });
    document.getElementById('calLatest').addEventListener('click', () => { calCursor = null; rerender(); });
    host.querySelectorAll('.cal-hastrades').forEach(cell => {
      cell.addEventListener('click', () => {
        calSelected = calSelected === cell.dataset.date ? null : cell.dataset.date;
        rerender();
        if (calSelected) {
          detailsHost.innerHTML = `<div class="panel"><h2>Trades on ${calSelected}</h2><div id="${detailsId}Table"></div></div>`;
          renderTradesTable(detailsId + 'Table', byDate.get(calSelected), hooks);
        } else {
          detailsHost.innerHTML = '';
        }
      });
    });
  }

  function shiftCalMonth(delta) {
    let [y, m] = calCursor.split('-').map(Number);
    m += delta;
    if (m < 1) { m = 12; y--; } else if (m > 12) { m = 1; y++; }
    calCursor = `${y}-${String(m).padStart(2, '0')}`;
  }

  // ---------- CSV export ----------

  function exportCsv(trades, filename) {
    const cols = [
      'id', 'date', 'day', 'year', 'session', 'pair', 'direction', 'risk_pct', 'rr', 'pnl_pct', 'result',
      'plan_violation', 'notes', 'chart_daily', 'chart_4h', 'chart_30m',
    ];
    const esc = (v) => { if (v == null) return ''; const s = String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const lines = [cols.join(',')];
    const chartLink = (t, label) => (t.charts.find(c => c.label === label) || {}).link || '';
    trades.forEach(t => {
      lines.push([
        t.id != null ? t.id : '', t.date, t.day || '', t.year || '', t.session || '', t.pair || '', t.direction || '',
        t.risk != null ? (t.risk * 100).toFixed(2) : '',
        t.rr.toFixed(2),
        (t.pnl * 100).toFixed(2),
        t.result, isPlanViolation(t) ? 'yes' : 'no', t.notes || '',
        chartLink(t, 'Daily'), chartLink(t, '4H'), chartLink(t, '30M'),
      ].map(esc).join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // ---------- date-range filter, tabs ----------

  const DATE_RANGE_PRESETS = [
    ['today', 'Today'], ['yesterday', 'Yesterday'], ['7d', 'Past 7 Days'],
    ['1m', '1 Month'], ['3m', '3 Months'], ['6m', '6 Months'], ['12m', '12 Months'], ['24m', '24 Months'],
    ['custom', 'Custom'], ['all', 'All Time'],
  ];
  const ALL_TIME_RANGE = { preset: 'all', start: '0000-01-01', end: '9999-12-31' };

  function defaultDateRange() {
    // Overview/Trades both open on the last 7 days, not All Time -- a
    // freshly-loaded dashboard should read as "recent activity," not a
    // lifetime dump that has to be manually narrowed every session.
    const bounds = computeDateRangeBounds('7d', null, null, localTodayStr());
    return { preset: '7d', start: bounds.start, end: bounds.end };
  }

  function computeDateRangeBounds(preset, customStart, customEnd, todayStr) {
    if (preset === 'all') return { start: '0000-01-01', end: '9999-12-31' };
    if (preset === 'custom') return { start: customStart || '0000-01-01', end: customEnd || '9999-12-31' };
    if (preset === 'today') return { start: todayStr, end: todayStr };
    // Pure calendar-date arithmetic, anchored via Date.UTC and read back via
    // toISOString -- never parses todayStr as local time. Mixing a local-time
    // Date (e.g. `new Date(todayStr + 'T00:00:00')`) with a UTC-based
    // toISOString() read-back silently shifts the result by a day whenever
    // the browser's timezone offset isn't zero (confirmed by hand: broke
    // "Yesterday" specifically, since "Today" bypasses this math entirely).
    const [y, m, d] = todayStr.split('-').map(Number);
    const DAY_MS = 86400000;
    const todayUTC = Date.UTC(y, m - 1, d);
    const fmt = (ms) => new Date(ms).toISOString().slice(0, 10);
    if (preset === 'yesterday') {
      const day = fmt(todayUTC - DAY_MS);
      return { start: day, end: day };
    }
    // Fixed day-counts rather than calendar-month arithmetic (setMonth),
    // to sidestep month-length/leap-year edge cases -- "1 Month" is 30
    // days back, not "the same day last calendar month".
    const daysBack = { '7d': 6, '1m': 30, '3m': 90, '6m': 182, '12m': 365, '24m': 730 }[preset];
    return { start: fmt(todayUTC - daysBack * DAY_MS), end: todayStr };
  }

  function filterTradesByRange(allTrades, range) {
    return allTrades.filter(t => t.date >= range.start && t.date <= range.end);
  }

  function localTodayStr() {
    // Local calendar date, not UTC -- toISOString() reflects UTC, which can
    // land on a different calendar day than the trader's own "today"
    // depending on timezone and time of day.
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function renderDateRangeFilter(hostId, currentRange, onChange) {
    const el = document.getElementById(hostId);
    if (!el) return;
    const todayStr = localTodayStr();
    el.innerHTML = `
      <select class="date-range-select">
        ${DATE_RANGE_PRESETS.map(([v, label]) => `<option value="${v}" ${v === currentRange.preset ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
      <span class="date-range-custom" style="${currentRange.preset === 'custom' ? '' : 'display:none;'}">
        <input type="date" class="date-range-start" value="${currentRange.preset === 'custom' ? currentRange.start : ''}">
        <input type="date" class="date-range-end" value="${currentRange.preset === 'custom' ? currentRange.end : ''}">
        <button type="button" class="date-range-apply">Apply</button>
      </span>`;
    const select = el.querySelector('.date-range-select');
    const customEl = el.querySelector('.date-range-custom');
    const startEl = el.querySelector('.date-range-start');
    const endEl = el.querySelector('.date-range-end');
    select.addEventListener('change', () => {
      if (select.value === 'custom') {
        customEl.style.display = '';
        return; // wait for Apply -- picking "Custom" alone has no start/end yet
      }
      customEl.style.display = 'none';
      const bounds = computeDateRangeBounds(select.value, null, null, todayStr);
      onChange({ preset: select.value, start: bounds.start, end: bounds.end });
    });
    el.querySelector('.date-range-apply').addEventListener('click', () => {
      if (!startEl.value || !endEl.value) return;
      onChange({ preset: 'custom', start: startEl.value, end: endEl.value });
    });
  }

  // ---------- account filter (Overview/Trades only) ----------

  // selectedIds holds real account ids (numbers) plus the sentinel string
  // 'unassigned' for trades with no account_id (or one pointing at a
  // since-deleted account -- see renderAccountComparisonTable). Mutated in
  // place on every checkbox toggle rather than rebuilt, so the popover
  // doesn't visually collapse mid-interaction while ticking several boxes.
  function renderAccountFilter(hostId, accounts, selectedIds, onChange) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const allValues = accounts.map(a => a.id).concat(['unassigned']);

    function labelFor() {
      const isAll = allValues.length > 0 && allValues.every(v => selectedIds.includes(v));
      if (isAll) return 'All accounts';
      if (selectedIds.length === 0) return 'No accounts';
      if (selectedIds.length === 1) {
        const only = selectedIds[0];
        if (only === 'unassigned') return 'Unassigned';
        const acc = accounts.find(a => a.id === only);
        return acc ? acc.name : '1 account';
      }
      return `${selectedIds.length} accounts`;
    }

    host.innerHTML = `
      <div class="account-filter">
        <button type="button" class="iconbtn account-filter-trigger" aria-expanded="false" aria-haspopup="true">
          <span class="account-filter-label">${labelFor()}</span><span class="account-filter-caret">▾</span>
        </button>
        <div class="account-filter-popover">
          <label class="account-filter-option account-filter-all">
            <input type="checkbox" data-all><span>All accounts</span>
          </label>
          <div class="account-filter-divider"></div>
          ${accounts.map(a => `
            <label class="account-filter-option">
              <input type="checkbox" data-id="${a.id}"><span>${a.name}</span>
            </label>`).join('')}
          <label class="account-filter-option">
            <input type="checkbox" data-id="unassigned"><span>Unassigned</span>
          </label>
          <div class="account-filter-divider"></div>
          <a href="#" class="account-filter-manage">+ Manage accounts</a>
        </div>
      </div>`;

    const root = host.querySelector('.account-filter');
    const labelEl = root.querySelector('.account-filter-label');
    const allCheckbox = root.querySelector('[data-all]');
    const itemCheckboxes = Array.from(root.querySelectorAll('.account-filter-option input:not([data-all])'));
    itemCheckboxes.forEach(cb => {
      const value = cb.dataset.id === 'unassigned' ? 'unassigned' : Number(cb.dataset.id);
      cb.checked = selectedIds.includes(value);
    });
    allCheckbox.checked = allValues.length > 0 && allValues.every(v => selectedIds.includes(v));

    const trigger = root.querySelector('.account-filter-trigger');

    function closePopover() {
      root.classList.remove('is-open');
      trigger.setAttribute('aria-expanded', 'false');
      document.removeEventListener('click', onDocClick);
    }
    function onDocClick(e) { if (!root.contains(e.target)) closePopover(); }
    trigger.addEventListener('click', () => {
      const opening = !root.classList.contains('is-open');
      root.classList.toggle('is-open', opening);
      trigger.setAttribute('aria-expanded', String(opening));
      if (opening) setTimeout(() => document.addEventListener('click', onDocClick), 0);
      else document.removeEventListener('click', onDocClick);
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && root.classList.contains('is-open')) closePopover(); });

    allCheckbox.addEventListener('change', () => {
      selectedIds.length = 0;
      if (allCheckbox.checked) selectedIds.push(...allValues);
      itemCheckboxes.forEach(cb => { cb.checked = allCheckbox.checked; });
      labelEl.textContent = labelFor();
      onChange(selectedIds, accounts);
    });

    itemCheckboxes.forEach(cb => {
      cb.addEventListener('change', () => {
        const value = cb.dataset.id === 'unassigned' ? 'unassigned' : Number(cb.dataset.id);
        if (cb.checked) { if (!selectedIds.includes(value)) selectedIds.push(value); }
        else { const idx = selectedIds.indexOf(value); if (idx >= 0) selectedIds.splice(idx, 1); }
        allCheckbox.checked = allValues.every(v => selectedIds.includes(v));
        labelEl.textContent = labelFor();
        onChange(selectedIds, accounts);
      });
    });

    root.querySelector('.account-filter-manage').addEventListener('click', (e) => {
      e.preventDefault();
      closePopover();
      openAccountManageModal(() => {
        fetchTradingAccounts().then(freshAccounts => {
          // Keep existing selections, and auto-select any brand new account
          // (matches "nothing is hidden by default" -- see renderAll's
          // ticked-by-default rule at page init).
          freshAccounts.forEach(a => { if (!selectedIds.includes(a.id)) selectedIds.push(a.id); });
          renderAccountFilter(hostId, freshAccounts, selectedIds, onChange);
          onChange(selectedIds, freshAccounts);
        });
      });
    });
  }

  // Groups trades ticked-account-first: real accounts get an exact
  // account_id match, and the 'unassigned' bucket (if ticked) catches
  // everything else -- including trades whose account_id points at an
  // account that's since been deleted, matching row_to_dict's own
  // server-side "Unassigned" fallback for the same case. Returns false
  // (and renders nothing) when fewer than 2 groups are actually ticked,
  // so the caller knows whether to show or hide the comparison panel.
  function renderAccountComparisonTable(hostId, trades, accounts, selectedIds) {
    const groups = [];
    accounts.forEach(a => {
      if (selectedIds.includes(a.id)) {
        groups.push({ name: a.name, trades: trades.filter(t => t.account_id === a.id) });
      }
    });
    if (selectedIds.includes('unassigned')) {
      const knownIds = accounts.map(a => a.id);
      groups.push({ name: 'Unassigned', trades: trades.filter(t => !knownIds.includes(t.account_id)) });
    }
    if (groups.length < 2) return false;

    const rows = groups.map(g => {
      const stats = computeStats(g.trades);
      return { pair: g.name, total: stats.total, wins: stats.wins, pnl: stats.totalReturn, profitFactor: stats.profitFactor, expectancy: stats.expectancy };
    });
    renderPairTable(hostId, rows, 'Account', [
      { key: 'profitFactor', header: 'Profit Factor', format: v => isFinite(v) ? v.toFixed(2) : '∞' },
      { key: 'expectancy', header: 'Expectancy', format: v => v.toFixed(2) },
    ]);
    return true;
  }

  // ---------- manage-accounts modal (shared markup lives in base.html) ----------

  let accountModalInit = false;
  let accountModalState = null; // { editingId, onChanged }

  function _loadAccountManageList() {
    const list = document.getElementById('accountManageList');
    fetchTradingAccounts().then(accounts => {
      if (!accounts.length) { list.innerHTML = '<div class="empty">No accounts yet.</div>'; return; }
      list.innerHTML = accounts.map(a => `
        <div class="account-manage-row">
          <span>${a.name}</span>
          <span class="form-actions">
            <button type="button" class="iconbtn small" data-edit-account="${a.id}" data-name="${a.name}">Edit</button>
            <button type="button" class="iconbtn small danger" data-delete-account="${a.id}">Delete</button>
          </span>
        </div>`).join('');
      list.querySelectorAll('[data-delete-account]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm('Delete this account? Its trades move to Unassigned.')) return;
          await apiSend('DELETE', `/api/trading-accounts/${btn.dataset.deleteAccount}`, {});
          _loadAccountManageList();
          if (accountModalState.onChanged) accountModalState.onChanged();
        });
      });
      list.querySelectorAll('[data-edit-account]').forEach(btn => {
        btn.addEventListener('click', () => {
          accountModalState.editingId = btn.dataset.editAccount;
          document.getElementById('accountFormTitle').textContent = 'Edit Account';
          document.getElementById('accountForm').elements.name.value = btn.dataset.name;
        });
      });
    });
  }

  function openAccountManageModal(onChanged) {
    const backdrop = document.getElementById('accountModalBackdrop');
    if (!backdrop) return;
    accountModalState = { editingId: null, onChanged };
    const form = document.getElementById('accountForm');
    const formError = document.getElementById('accountFormError');
    form.reset();
    document.getElementById('accountFormTitle').textContent = 'Create Account';
    formError.textContent = '';
    backdrop.classList.remove('hidden');
    _loadAccountManageList();

    if (!accountModalInit) {
      accountModalInit = true;
      document.getElementById('cancelAccountForm').addEventListener('click', () => backdrop.classList.add('hidden'));
      backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.classList.add('hidden'); });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !backdrop.classList.contains('hidden')) backdrop.classList.add('hidden'); });
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = form.elements.name.value.trim();
        if (!name) return;
        try {
          if (accountModalState.editingId) await apiSend('PUT', `/api/trading-accounts/${accountModalState.editingId}`, { name });
          else await apiSend('POST', '/api/trading-accounts', { name });
          accountModalState.editingId = null;
          document.getElementById('accountFormTitle').textContent = 'Create Account';
          form.reset();
          _loadAccountManageList();
          if (accountModalState.onChanged) accountModalState.onChanged();
        } catch (err) {
          formError.textContent = err.message;
        }
      });
    }
  }

  function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + btn.dataset.tab));
      });
    });
  }

  // ---------- orchestration ----------

  function renderAll(allTrades, dateRange, opts) {
    opts = opts || {};
    dateRange = dateRange || ALL_TIME_RANGE;
    const trades = filterTradesByRange(allTrades, dateRange);
    const stats = computeStats(trades);
    const bw = computeBestWorst(trades);
    const equity = computeEquity(trades);
    const drawdown = computeDrawdown(equity);

    renderDateRangeFilter('dateRangeFilter', dateRange, opts.onRangeChange);
    renderTiles('tiles', stats, drawdown);
    renderEquity('equityChart', equity, drawdown);
    renderDonut('donutChart', stats);
    renderWinRateBars('dayChart', computeGroupStats(trades, 'day', DAY_ORDER));
    renderWinRateBars('sessionChart', computeGroupStats(trades, 'session'));
    renderMonthHeatmap('monthChart', trades);
    renderRRHistogram('rrHistogram', trades);
    renderCalendar('calendarGrid', 'calendarDetails', allTrades, { onEdit: opts.onEdit, onDelete: opts.onDelete }, opts.accountBalance);
    renderBestWorstPairs('bestWorstPairs', bw);
    renderBestWorstTrades('bestWorstTrades', bw);
    renderPairTable('pairTable', computeByPair(trades));
    renderTradesTable('tradesTable', trades, { onEdit: opts.onEdit, onDelete: opts.onDelete });

    if (opts.subheadText) {
      const subhead = document.getElementById('subhead');
      if (subhead) subhead.textContent = opts.subheadText(allTrades.length);
    }
    if (opts.footerText) {
      const footer = document.getElementById('footer');
      if (footer) footer.textContent = opts.footerText;
    }
  }

  // ---------- shared page utilities (multi-page live app) ----------

  async function apiSend(method, url, payload) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!r.ok) { const body = await r.json().catch(() => ({})); throw new Error(body.error || 'Request failed'); }
    return r.json();
  }

  async function fetchTrades() { const r = await fetch('/api/trades'); return r.json(); }
  async function fetchStrategies() { const r = await fetch('/api/strategies'); return r.json(); }
  async function fetchTradingAccounts() { const r = await fetch('/api/trading-accounts'); return r.json(); }
  async function deleteTrade(id) { await fetch(`/api/trades/${id}`, { method: 'DELETE' }); }

  // ---------- live price ticker (global chrome, present on every page) ----------

  function fmtPrice(v) {
    if (v == null) return '—';
    const decimals = v >= 100 ? 2 : (v >= 10 ? 3 : 4);
    return v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function tickerChangeParts(q) {
    const cls = q.changePct == null ? 'flat' : (q.changePct > 0 ? 'good' : (q.changePct < 0 ? 'critical' : 'flat'));
    const arrow = q.changePct > 0 ? '▲' : (q.changePct < 0 ? '▼' : '·');
    const pct = q.changePct == null ? '—' : `${q.changePct > 0 ? '+' : ''}${q.changePct.toFixed(2)}%`;
    return { cls, text: `${arrow} ${pct}` };
  }

  function tickerItemHtml(q) {
    const { cls, text } = tickerChangeParts(q);
    return `<div class="ticker-item" data-symbol="${q.symbol}">
      <span class="ti-label">${q.label}</span>
      <span class="ti-price">${fmtPrice(q.price)}</span>
      <span class="ti-change ${cls}">${text}</span>
    </div>`;
  }

  // The strip scrolls via a single continuous CSS animation on .ticker-track
  // (see .ticker-strip:hover pausing it in dashboard-core.css). Replacing that
  // element's innerHTML on every refresh would restart the animation from 0%
  // every time -- a visible stutter every 20s. So after the first paint, later
  // refreshes patch each item's text in place and never touch .ticker-track
  // itself, keeping the scroll running with zero interruption.
  async function loadTicker() {
    const host = document.getElementById('tickerStrip');
    if (!host) return;
    let quotes;
    try {
      quotes = await (await fetch('/api/prices')).json();
    } catch (e) {
      if (!host.querySelector('.ticker-track')) {
        host.innerHTML = '<div class="ticker-item ti-label">Live prices unavailable right now</div>';
      }
      return; // a transient failure keeps whatever was already scrolling, rather than wiping it
    }

    if (!host.querySelector('.ticker-track')) {
      const itemsHtml = quotes.map(tickerItemHtml).join('');
      // duplicated back-to-back so the loop has no visible seam
      host.innerHTML = `<div class="ticker-track">${itemsHtml}${itemsHtml}</div>`;
      return;
    }

    quotes.forEach(q => {
      const { cls, text } = tickerChangeParts(q);
      host.querySelectorAll(`.ticker-item[data-symbol="${q.symbol}"]`).forEach(el => {
        el.querySelector('.ti-price').textContent = fmtPrice(q.price);
        const changeEl = el.querySelector('.ti-change');
        changeEl.textContent = text;
        changeEl.className = `ti-change ${cls}`;
      });
    });
  }

  function initTicker() {
    if (!document.getElementById('tickerStrip')) return;
    loadTicker();
    // 15s, not 3s -- this app polls 10 external tickers on every tick
    // (market_data.py fans them out in parallel, ~1-3s round trip each),
    // so a 3s interval meant a fresh externally-bound fetch was almost
    // always in flight, and its matching 3s server-side cache TTL never
    // actually got a chance to serve a cached hit. 15s still feels live
    // for a personal journal, not a scalping terminal.
    setInterval(loadTicker, 15000);
  }

  // ---------- global add/edit trade modal (global chrome, present on every page) ----------

  let tradeModalState = null; // { editingId } once initialized

  // Exposed so a page's own script can look a trade up by id (from its own
  // locally-fetched ALL_TRADES) and hand the full object to this shared modal --
  // decouples the modal from any one page owning the trades array.
  function openEditTradeModal(trade) {
    if (!tradeModalState) return;
    const tradeForm = document.getElementById('tradeForm');
    tradeModalState.editingId = trade.id;
    document.getElementById('formTitle').textContent = `Edit Trade #${trade.id} — ${trade.date} ${trade.pair || ''}`;
    tradeForm.date.value = trade.date;
    tradeForm.session.value = trade.session || 'London';
    tradeForm.pair.value = trade.pair || '';
    tradeForm.direction.value = trade.direction || 'Long';
    tradeForm.risk.value = trade.risk != null ? (trade.risk * 100).toFixed(2) : '';
    tradeForm.rr.value = trade.rr;
    tradeForm.pnl.value = (trade.pnl * 100).toFixed(2);
    tradeForm.strategy_id.value = trade.strategy_id != null ? trade.strategy_id : '';
    tradeForm.account_id.value = trade.account_id != null ? trade.account_id : '';
    tradeForm.notes.value = trade.notes || '';
    tradeForm.chart_daily.value = (trade.charts.find(c => c.label === 'Daily') || {}).link || '';
    tradeForm.chart_4h.value = (trade.charts.find(c => c.label === '4H') || {}).link || '';
    tradeForm.chart_30m.value = (trade.charts.find(c => c.label === '30M') || {}).link || '';
    document.getElementById('formError').textContent = '';
    document.getElementById('modalBackdrop').classList.remove('hidden');
  }

  function openAddTradeModal() {
    if (!tradeModalState) return;
    tradeModalState.editingId = null;
    const tradeForm = document.getElementById('tradeForm');
    document.getElementById('formTitle').textContent = 'Add Trade';
    tradeForm.reset();
    tradeForm.date.value = new Date().toISOString().slice(0, 10);
    document.getElementById('formError').textContent = '';
    document.getElementById('modalBackdrop').classList.remove('hidden');
    tradeForm.pair.focus();
  }

  // Wires the global Add/Edit Trade modal (present in base.html on every
  // page). Populates the strategy dropdown itself so no page needs to fetch
  // strategies just to support the modal. On save, reloads the current page
  // rather than trying to maintain a cross-page re-render contract -- a
  // deliberate simplification now that navigation is real page loads.
  function initTradeModal() {
    const modalBackdrop = document.getElementById('modalBackdrop');
    if (!modalBackdrop) return;
    tradeModalState = { editingId: null };
    const tradeForm = document.getElementById('tradeForm');
    const formError = document.getElementById('formError');

    fetchStrategies().then(strategies => {
      const select = document.getElementById('tradeStrategySelect');
      select.innerHTML = '<option value="">No strategy</option>' +
        strategies.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    }).catch(() => {});

    fetchTradingAccounts().then(accounts => {
      const select = document.getElementById('tradeAccountSelect');
      if (!select) return;
      select.innerHTML = '<option value="">Unassigned</option>' +
        accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
    }).catch(() => {});

    function closeForm() {
      modalBackdrop.classList.add('hidden');
      tradeModalState.editingId = null;
      tradeForm.reset();
    }

    const addBtn = document.getElementById('addTradeBtn');
    if (addBtn) addBtn.addEventListener('click', openAddTradeModal);
    document.getElementById('cancelForm').addEventListener('click', closeForm);
    modalBackdrop.addEventListener('click', (e) => { if (e.target === modalBackdrop) closeForm(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modalBackdrop.classList.contains('hidden')) closeForm(); });

    tradeForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(tradeForm);
      const num = (v) => (v === '' || v == null) ? null : parseFloat(v);
      const payload = {
        date: fd.get('date'),
        session: fd.get('session'),
        pair: fd.get('pair'),
        direction: fd.get('direction'),
        risk: num(fd.get('risk')) != null ? num(fd.get('risk')) / 100 : null,
        rr: num(fd.get('rr')) || 0,
        pnl: num(fd.get('pnl')) != null ? num(fd.get('pnl')) / 100 : 0,
        strategy_id: fd.get('strategy_id') || null,
        account_id: fd.get('account_id') || null,
        notes: fd.get('notes'),
        chart_daily: fd.get('chart_daily') || null,
        chart_4h: fd.get('chart_4h') || null,
        chart_30m: fd.get('chart_30m') || null,
      };
      try {
        if (tradeModalState.editingId) await apiSend('PUT', `/api/trades/${tradeModalState.editingId}`, payload);
        else await apiSend('POST', '/api/trades', payload);
        window.location.reload();
      } catch (err) {
        formError.textContent = err.message;
      }
    });
  }

  // ---------- mobile sidebar drawer (touch devices have no :hover) ----------

  function initMobileSidebar() {
    const sidebar = document.getElementById('sidebar');
    const hamburger = document.getElementById('sidebarHamburger');
    const backdrop = document.getElementById('sidebarBackdrop');
    if (!sidebar || !hamburger || !backdrop) return;

    function close() {
      sidebar.classList.remove('mobile-open');
      backdrop.classList.remove('mobile-open');
    }
    function open() {
      sidebar.classList.add('mobile-open');
      backdrop.classList.add('mobile-open');
    }

    hamburger.addEventListener('click', () => {
      sidebar.classList.contains('mobile-open') ? close() : open();
    });
    backdrop.addEventListener('click', close);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  }

  // ---------- theme / contrast (client-only, localStorage) ----------
  // The first persisted UI preference in this app -- deliberately kept to
  // localStorage only, not synced to the DB or across devices, since this
  // is a per-browser cosmetic choice, not account data.
  function applyTheme(theme) {
    theme ? document.documentElement.setAttribute('data-theme', theme) : document.documentElement.removeAttribute('data-theme');
  }
  function applyContrast(level) {
    level === 'high' ? document.documentElement.setAttribute('data-contrast', 'high') : document.documentElement.removeAttribute('data-contrast');
  }
  function setTheme(theme) {
    theme ? localStorage.setItem('sf-theme', theme) : localStorage.removeItem('sf-theme');
    applyTheme(theme);
  }
  function setContrast(level) {
    level === 'high' ? localStorage.setItem('sf-contrast', 'high') : localStorage.removeItem('sf-contrast');
    applyContrast(level);
  }
  function getStoredTheme() { return localStorage.getItem('sf-theme'); }
  function getStoredContrast() { return localStorage.getItem('sf-contrast'); }

  return {
    fmtPct, computeStats, computeEquity, computeDrawdown, computeGroupStats, computeByPair, computeBestWorst,
    renderAll, setupTabs, exportCsv, isPlanViolation, DAY_ORDER, renderPairTable,
    apiSend, fetchTrades, fetchStrategies, fetchTradingAccounts, deleteTrade,
    renderAccountFilter, renderAccountComparisonTable, openAccountManageModal,
    initTicker, initTradeModal, openAddTradeModal, openEditTradeModal, initMobileSidebar,
    setTheme, setContrast, getStoredTheme, getStoredContrast,
    computeDateRangeBounds, filterTradesByRange, renderDateRangeFilter, ALL_TIME_RANGE, DATE_RANGE_PRESETS, localTodayStr,
    defaultDateRange,
  };
})();
