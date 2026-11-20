// A quiet field of points that drift and connect, a hint of people and agents meeting. No dependencies.
document.getElementById("year").textContent = new Date().getFullYear();

const canvas = document.getElementById("field");
const ctx = canvas.getContext("2d");
const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
const scheme = window.matchMedia("(prefers-color-scheme: dark)");
const LINK = 140, LINK2 = LINK * LINK;    // px: points closer than this draw a faint line
let points = [], w = 0, h = 0, colours = {}, running = false, resizeTimer = 0;

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  w = canvas.clientWidth; h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const count = Math.round(Math.min(90, (w * h) / 14000));
  points = Array.from({ length: count }, (_, i) => ({
    x: Math.random() * w, y: Math.random() * h,
    vx: (Math.random() - .5) * .25, vy: (Math.random() - .5) * .25,
    agent: i % 3 === 0,                   // every third point a different colour
  }));
}

function readColours() {             // read once per theme change, not every frame
  const css = getComputedStyle(document.documentElement);
  colours = { accent: css.getPropertyValue("--accent").trim(), accent2: css.getPropertyValue("--accent2").trim(),
              fg: css.getPropertyValue("--fg").trim() };
}

function frame() {
  const { accent, accent2, fg } = colours, still = motion.matches;
  ctx.clearRect(0, 0, w, h);
  for (const p of points) {
    if (!still) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
    }
  }
  ctx.lineWidth = 1;
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      const a = points[i], b = points[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
      if (d2 < LINK2) {
        const d = Math.sqrt(d2);
        ctx.globalAlpha = (1 - d / LINK) * .35;
        ctx.strokeStyle = a.agent !== b.agent ? accent : fg;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
    }
  }
  ctx.globalAlpha = .9;
  for (const p of points) {
    ctx.fillStyle = p.agent ? accent2 : fg;
    ctx.beginPath(); ctx.arc(p.x, p.y, p.agent ? 2 : 1.4, 0, Math.PI * 2); ctx.fill();
  }
  ctx.globalAlpha = 1;
  running = !still;
  if (running) requestAnimationFrame(frame);
}

function redraw() { if (!running) frame(); }   // a still page draws once per change
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { resize(); redraw(); }, 150); });
scheme.addEventListener("change", () => { readColours(); redraw(); });
motion.addEventListener("change", redraw);
readColours();
resize();
frame();
