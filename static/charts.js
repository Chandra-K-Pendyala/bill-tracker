// Dependency-free line charts: <canvas data-chart="line" data-source="json-script-id" data-prefix="$" data-suffix="">
// The JSON script holds rows of {label, value}. Hovering shows the nearest point's value.
(() => {
  const COLORS = {line: '#176b4b', fill: 'rgba(23,107,75,.08)', grid: '#dce5df', text: '#68746e', tip: '#17221d'};
  const FONT = '11px system-ui, sans-serif';
  const fmt = (canvas, v) => `${canvas.dataset.prefix ?? '$'}${v.toLocaleString('en-CA', {maximumFractionDigits: v < 100 ? 2 : 0})}${canvas.dataset.suffix ?? ''}`;
  // A round step (1, 2, 2.5 or 5 × 10ⁿ) that splits the range into about four parts.
  const niceStep = v => { const p = 10 ** Math.floor(Math.log10(v)), f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; };

  function layout(canvas) {
    const source = document.getElementById(canvas.dataset.source);
    const points = source ? JSON.parse(source.textContent).map(r => ({label: r.label, value: Number(r.value) || 0})) : [];
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const top = Math.max(...points.map(p => p.value), 1), step = niceStep(top / 4);
    const ticks = Array.from({length: Math.ceil(top / step) + 1}, (_, i) => i * step), max = ticks[ticks.length - 1];
    const c = canvas.getContext('2d'); c.font = FONT;
    const pad = {l: Math.ceil(Math.max(...ticks.map(t => c.measureText(fmt(canvas, t)).width))) + 14, r: 16, t: 16, b: 34};
    const cw = w - pad.l - pad.r, ch = h - pad.t - pad.b;
    points.forEach((p, i) => {
      p.x = pad.l + (points.length > 1 ? i * cw / (points.length - 1) : cw / 2);
      p.y = pad.t + ch - p.value / max * ch;
    });
    return {points, ticks, dpr, w, h, pad, ch, max};
  }

  function draw(canvas, hover) {
    const g = canvas._layout, c = canvas.getContext('2d');
    c.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
    c.clearRect(0, 0, g.w, g.h);
    c.font = FONT; c.lineWidth = 1;
    if (!g.points.length) { c.fillStyle = COLORS.text; c.textAlign = 'center'; c.fillText('No data yet', g.w / 2, g.h / 2); return; }
    g.ticks.forEach(t => {
      const y = g.pad.t + g.ch - t / g.max * g.ch;
      c.strokeStyle = COLORS.grid; c.beginPath(); c.moveTo(g.pad.l, y); c.lineTo(g.w - g.pad.r, y); c.stroke();
      c.fillStyle = COLORS.text; c.textAlign = 'right'; c.fillText(fmt(canvas, t), g.pad.l - 8, y + 4);
    });
    const pts = g.points;
    c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y));
    c.strokeStyle = COLORS.line; c.lineWidth = 3; c.lineJoin = 'round'; c.stroke();
    c.lineTo(pts[pts.length - 1].x, g.pad.t + g.ch); c.lineTo(pts[0].x, g.pad.t + g.ch); c.closePath();
    c.fillStyle = COLORS.fill; c.fill();
    const every = Math.ceil(pts.length / 12);
    pts.forEach((p, i) => {
      c.beginPath(); c.arc(p.x, p.y, p === hover ? 6 : 4, 0, Math.PI * 2); c.fillStyle = COLORS.line; c.fill();
      if (i % every === 0) {  // keep each label inside the canvas
        const half = c.measureText(p.label).width / 2, x = Math.min(Math.max(p.x, half + 2), g.w - half - 2);
        c.fillStyle = COLORS.text; c.textAlign = 'center'; c.fillText(p.label, x, g.h - 10);
      }
    });
    if (hover) {
      const text = `${hover.label}: ${fmt(canvas, hover.value)}`;
      c.font = '600 12px system-ui, sans-serif';
      const tw = c.measureText(text).width + 16, x = Math.min(Math.max(hover.x - tw / 2, 4), g.w - tw - 4), y = Math.max(hover.y - 36, 4);
      c.fillStyle = COLORS.tip; c.beginPath(); c.roundRect(x, y, tw, 24, 6); c.fill();
      c.fillStyle = '#fff'; c.textAlign = 'left'; c.fillText(text, x + 8, y + 16);
    }
  }

  const charts = [...document.querySelectorAll('canvas[data-chart="line"]')];
  const render = () => charts.forEach(canvas => { canvas._layout = layout(canvas); draw(canvas); });
  charts.forEach(canvas => {
    canvas.addEventListener('mousemove', e => {
      const x = e.clientX - canvas.getBoundingClientRect().left, pts = canvas._layout.points;
      draw(canvas, pts.reduce((best, p) => !best || Math.abs(p.x - x) < Math.abs(best.x - x) ? p : best, null));
    });
    canvas.addEventListener('mouseleave', () => draw(canvas));
  });
  render();
  let timer; window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(render, 100); });
})();
