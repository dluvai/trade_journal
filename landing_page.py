"""Public marketing landing page, served at `/` for anonymous visitors
(server.py's index() redirects logged-in users straight to /overview
instead). Hand-rolled, self-contained HTML -- same reasoning as
auth_pages.py: this must render before a session exists, so it can't
extend base.html or rely on the gated static_asset route.

One-pager: Home/Markets/About/FAQ in the nav are in-page anchors, not
separate routes -- there's nothing to build separate pages around yet, and
a single scrolling page is the honest match for how small this product
still is. Sign In/Sign Up don't navigate away either; they open the
slide-in glass panel at the bottom of this file, which posts straight to
the existing /login and /signup JSON endpoints.

Kept as a single render() with no arguments -- this page has no per-request
state (no form errors, no user data), unlike auth_pages.py's *_PAGE strings.
"""

_STYLE = """
  *{box-sizing:border-box;}
  html{scroll-behavior:smooth;}
  html,body{margin:0;min-height:100%;color:#f5f4f2;font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
    background:linear-gradient(135deg,#0c0c0e 0%,#0f2926 100%);background-attachment:fixed;}
  a{color:inherit;}
  h1,h2,h3,h4{font-weight:700;letter-spacing:-0.01em;}
  @media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none !important;transition:none !important;}}

  /* One translucent "glass" treatment reused for every card on the page --
     the ask was to make cards feel like part of the same background
     instead of flat blocks sitting on top of it. */
  .glass{background:rgba(255,255,255,0.035);border:1px solid rgba(255,255,255,0.09);
    backdrop-filter:blur(22px) saturate(1.3);-webkit-backdrop-filter:blur(22px) saturate(1.3);
    border-radius:14px;}

  .wrap{max-width:1180px;margin:0 auto;padding:0 5vw;}
  .section{padding:88px 0;}
  .section-head{text-align:center;max-width:640px;margin:0 auto 48px;}
  .section-eyebrow{font-size:11.5px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#7fb0f7;margin:0 0 12px;}
  .section-head h2{font-size:clamp(26px,3.4vw,36px);margin:0 0 14px;}
  .section-head p{font-size:15px;color:#a3a0a6;margin:0;line-height:1.65;}

  /* ---------- nav ---------- */
  .nav{position:sticky;top:0;z-index:50;display:flex;align-items:center;justify-content:space-between;
    padding:16px 5vw;backdrop-filter:blur(16px) saturate(1.2);-webkit-backdrop-filter:blur(16px) saturate(1.2);
    background:rgba(10,10,11,0.55);border-bottom:1px solid rgba(255,255,255,0.08);}
  .nav-brand{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:700;letter-spacing:0.2px;}
  .nav-brand svg{flex:none;color:#fff;background:linear-gradient(135deg,#93c5fd 0%,#3b82f6 55%,#1d4ed8 100%);
    border-radius:8px;padding:6px;width:28px;height:28px;box-sizing:border-box;}
  .nav-links{display:flex;align-items:center;gap:30px;font-size:13.5px;font-weight:600;color:#c9c6cc;}
  .nav-links a{text-decoration:none;transition:color .15s ease;}
  .nav-links a:hover{color:#fff;}
  .nav-actions{display:flex;align-items:center;gap:10px;}
  .btn-ghost{padding:9px 16px;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;
    color:#f5f4f2;border:1px solid rgba(245,244,242,0.16);background:transparent;transition:border-color .15s ease,background .15s ease;}
  .btn-ghost:hover{background:rgba(245,244,242,0.06);border-color:rgba(245,244,242,0.28);}
  .btn-accent{padding:9px 18px;border-radius:8px;font-size:13px;font-weight:700;text-decoration:none;color:#fff;
    background:linear-gradient(135deg,#93c5fd 0%,#3b82f6 55%,#1d4ed8 100%);border:none;
    box-shadow:0 8px 24px -10px rgba(59,130,246,0.55);transition:filter .15s ease,transform .15s ease;cursor:pointer;}
  .btn-accent:hover{filter:brightness(1.08);}
  .btn-accent:active{transform:translateY(1px);}
  .btn-large{padding:13px 26px;font-size:14.5px;border-radius:9px;}
  .nav-mobile-toggle{display:none;background:none;border:1px solid rgba(255,255,255,0.16);border-radius:8px;
    color:#f5f4f2;padding:7px 10px;cursor:pointer;}

  /* ---------- hero ---------- */
  .hero{padding:80px 0 40px;text-align:center;}
  .hero h1{font-size:clamp(34px,5vw,54px);line-height:1.1;margin:0 0 22px;}
  .hero h1 .glow{display:block;background:linear-gradient(135deg,#93c5fd,#3b82f6 60%,#1d4ed8);
    -webkit-background-clip:text;background-clip:text;color:transparent;
    filter:drop-shadow(0 0 26px rgba(59,130,246,0.45));}
  .hero p{font-size:16.5px;color:#a3a0a6;max-width:56ch;margin:0 auto 32px;line-height:1.6;}
  .hero-ctas{display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap;margin-bottom:60px;}

  /* Browser-chrome product preview -- a believable frame around a real
     look at the dashboard, standing in for a screenshot without needing an
     actual image asset. */
  .browser-mock{max-width:960px;margin:0 auto;overflow:hidden;box-shadow:0 40px 90px -30px rgba(0,0,0,0.6);}
  .browser-bar{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.08);}
  .browser-bar .dot{width:10px;height:10px;border-radius:50%;flex:none;}
  .browser-bar .dot.r{background:#f0655f;}
  .browser-bar .dot.y{background:#e0954a;}
  .browser-bar .dot.g{background:#45c988;}
  .browser-url{flex:1;text-align:center;font-size:12px;color:#7d7a80;font-family:"IBM Plex Mono",ui-monospace,monospace;}
  .browser-body{display:flex;text-align:left;min-height:360px;}
  .bm-sidebar{width:150px;flex:none;border-right:1px solid rgba(255,255,255,0.07);padding:16px 10px;
    display:flex;flex-direction:column;gap:2px;}
  .bm-side-item{font-size:12.5px;font-weight:600;color:#8b888f;padding:8px 10px;border-radius:7px;}
  .bm-side-item.active{background:rgba(59,130,246,0.16);color:#93c5fd;}
  .bm-main{flex:1;padding:18px 20px;min-width:0;}
  @media (max-width:760px){.bm-sidebar{display:none;}}

  /* ---------- product-preview internals (tiles/chart/table) ---------- */
  .pv-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));border:1px solid rgba(255,255,255,0.08);
    border-radius:11px;overflow:hidden;margin-bottom:16px;}
  .pv-tile{padding:14px 16px;background:rgba(255,255,255,0.02);}
  .pv-tile .l{font-size:10px;color:#68656b;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;}
  .pv-tile .v{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:18px;font-weight:600;margin-top:5px;}
  .pv-tile .v.good{color:#45c988;}
  .pv-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px;}
  .pv-panel{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:11px;padding:14px 16px;}
  .pv-panel h3{margin:0 0 10px;font-size:11px;color:#a3a0a6;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;}
  .pv-line{fill:none;stroke:#3b82f6;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round;
    stroke-dasharray:640;stroke-dashoffset:640;transition:stroke-dashoffset 1.6s cubic-bezier(.2,.7,.3,1);}
  .browser-mock.is-visible .pv-line{stroke-dashoffset:0;}
  .pv-table{width:100%;border-collapse:collapse;font-size:12px;}
  .pv-table th{text-align:left;color:#68656b;font-weight:600;font-size:9.5px;text-transform:uppercase;
    letter-spacing:0.05em;padding:0 0 7px;border-bottom:1px solid rgba(255,255,255,0.08);}
  .pv-table th.num{text-align:right;}
  .pv-table td{padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.05);}
  .pv-table td.num{text-align:right;font-family:"IBM Plex Mono",ui-monospace,monospace;}
  .pv-table td.good{color:#45c988;}
  @media (max-width:760px){.pv-grid{grid-template-columns:1fr;}}

  /* ---------- reveal-on-scroll ---------- */
  .reveal{opacity:0;transform:translateY(18px);transition:opacity .6s ease,transform .6s ease;}
  .reveal.is-visible{opacity:1;transform:translateY(0);}

  /* ---------- markets section ---------- */
  .market-cards{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px;}
  .market-card{padding:26px;}
  .market-card h3{font-size:19px;margin:0 0 8px;}
  .market-card p{font-size:13.5px;color:#a3a0a6;margin:0;line-height:1.6;}
  .check-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;}
  .check-card{padding:20px 22px;}
  .check-card h4{font-size:14.5px;margin:0 0 10px;}
  .check-card ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px;}
  .check-card li{font-size:12.5px;color:#a3a0a6;padding-left:20px;position:relative;line-height:1.5;}
  .check-card li::before{content:"✓";position:absolute;left:0;color:#45c988;font-weight:700;}
  @media (max-width:760px){.market-cards{grid-template-columns:1fr;}}

  /* ---------- how it works ---------- */
  .steps{display:flex;flex-direction:column;gap:20px;}
  .step-row{display:grid;grid-template-columns:64px 1fr;gap:20px;align-items:flex-start;}
  .step-num{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:34px;font-weight:700;
    color:rgba(255,255,255,0.14);line-height:1;padding-top:6px;}
  .step-card{padding:22px 24px;display:flex;gap:16px;align-items:flex-start;}
  .step-icon{width:38px;height:38px;border-radius:10px;flex:none;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(135deg,rgba(147,197,253,0.2),rgba(29,78,216,0.2));color:#7fb0f7;}
  .step-card h3{font-size:16px;margin:0 0 6px;}
  .step-card p{font-size:13.5px;color:#a3a0a6;margin:0;line-height:1.6;}

  /* ---------- why choose ---------- */
  .why-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;}
  .why-card{padding:24px;display:flex;flex-direction:column;gap:14px;
    transition:transform .2s ease,border-color .2s ease;}
  .why-card:hover{transform:translateY(-3px);border-color:rgba(59,130,246,0.35);}
  .why-card h3{font-size:15.5px;margin:0;}
  .why-card p{font-size:13px;color:#a3a0a6;margin:0;line-height:1.6;}
  .why-mock{background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.06);border-radius:9px;padding:12px 14px;font-size:11.5px;}
  .why-mock .row{display:flex;justify-content:space-between;padding:4px 0;color:#c9c6cc;}
  .why-mock .row b{font-family:"IBM Plex Mono",ui-monospace,monospace;color:#f5f4f2;}
  .why-mock .row.good b{color:#45c988;}
  .why-mock .bar-track{height:5px;border-radius:3px;background:rgba(255,255,255,0.08);margin-top:4px;overflow:hidden;}
  .why-mock .bar-fill{height:100%;background:linear-gradient(90deg,#3b82f6,#93c5fd);}

  /* ---------- about ---------- */
  .about-wrap{max-width:720px;margin:0 auto;text-align:center;}
  .about-wrap p{font-size:15.5px;color:#c9c6cc;line-height:1.75;margin:0 0 18px;}
  .about-quote{font-size:20px;font-weight:600;margin:28px 0;color:#f5f4f2;}
  .about-quote span{color:#7fb0f7;}

  /* ---------- faq ---------- */
  .faq-list{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:12px;}
  .faq-item{padding:0;overflow:hidden;}
  .faq-item summary{list-style:none;cursor:pointer;padding:18px 22px;font-size:14.5px;font-weight:600;
    display:flex;justify-content:space-between;align-items:center;gap:12px;}
  .faq-item summary::-webkit-details-marker{display:none;}
  .faq-item summary::after{content:"+";font-size:20px;font-weight:400;color:#7fb0f7;flex:none;transition:transform .2s ease;}
  .faq-item[open] summary::after{transform:rotate(45deg);}
  .faq-item p{margin:0;padding:0 22px 20px;font-size:13.5px;color:#a3a0a6;line-height:1.6;}

  /* ---------- footer ---------- */
  .footer{border-top:1px solid rgba(255,255,255,0.08);padding:56px 0 28px;}
  .footer-top{display:flex;justify-content:space-between;gap:40px;flex-wrap:wrap;margin-bottom:40px;}
  .footer-brand{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:700;}
  .footer-brand svg{flex:none;color:#fff;background:linear-gradient(135deg,#93c5fd 0%,#3b82f6 55%,#1d4ed8 100%);
    border-radius:8px;padding:6px;width:26px;height:26px;box-sizing:border-box;}
  .footer-tagline{font-size:12.5px;color:#68656b;margin-top:8px;max-width:26ch;}
  .footer-cols{display:flex;gap:56px;flex-wrap:wrap;}
  .footer-col h4{font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#68656b;margin:0 0 14px;}
  .footer-col a{display:block;font-size:13px;color:#c9c6cc;text-decoration:none;margin-bottom:10px;cursor:pointer;}
  .footer-col a:hover{color:#fff;}
  .footer-bottom{border-top:1px solid rgba(255,255,255,0.08);padding-top:22px;
    display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;font-size:12px;color:#68656b;}

  @media (max-width:900px){
    .nav-links{display:none;}
  }
"""

_ICON_JOURNAL = """<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M9 8h6M9 12h6M9 16h3"/></svg>"""
_ICON_SCALE = """<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M5 7l-3 6a3 3 0 0 0 6 0l-3-6Zm14 0l-3 6a3 3 0 0 0 6 0l-3-6ZM5 7h14M8 21h8"/></svg>"""
_ICON_TARGET = """<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>"""
_ICON_LAYERS = """<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 8l10 5 10-5-10-5Z"/><path d="M2 13l10 5 10-5M2 18l10 5 10-5"/></svg>"""
_ICON_USER = """<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/></svg>"""
_ICON_PEN = """<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>"""
_ICON_INSIGHT = """<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M17 7h4v4"/></svg>"""


def render():
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SteadFast — Trade Journal &amp; Market Intelligence</title>
<meta name="description" content="Journal every trade, score currency bias from live macro data, and see which strategies actually work.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_STYLE}</style></head><body>

<nav class="nav">
  <div class="nav-brand">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M17 7h4v4"/><circle cx="9" cy="11" r="1.3" fill="currentColor" stroke="none"/><circle cx="13" cy="15" r="1.3" fill="currentColor" stroke="none"/></svg>
    SteadFast
  </div>
  <div class="nav-links">
    <a href="#top">Home</a>
    <a href="#markets">Markets</a>
    <a href="#about">About</a>
    <a href="#faq">FAQ</a>
  </div>
  <div class="nav-actions">
    <a class="btn-ghost" href="/login">Sign In</a>
    <a class="btn-accent" href="/signup">Sign Up</a>
  </div>
</nav>

<div id="top"></div>
<section class="hero wrap">
  <h1>Trade with discipline.<span class="glow">Journal with clarity.</span></h1>
  <p>Log every trade with full context, score currency bias from real central-bank
     data, and see exactly which strategies are actually working — all in one place.</p>
  <div class="hero-ctas">
    <a class="btn-accent btn-large" href="/signup">Start Journaling</a>
    <a class="btn-ghost btn-large" href="#how-it-works">How It Works</a>
  </div>

  <div class="browser-mock glass reveal" id="pvFrame">
    <div class="browser-bar">
      <span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
      <span class="browser-url">🔒 app.steadfast.app/overview</span>
    </div>
    <div class="browser-body">
      <div class="bm-sidebar">
        <div class="bm-side-item active">Overview</div>
        <div class="bm-side-item">Trades</div>
        <div class="bm-side-item">Macros</div>
        <div class="bm-side-item">Calendar</div>
        <div class="bm-side-item">Strategy</div>
      </div>
      <div class="bm-main">
        <div class="pv-tiles">
          <div class="pv-tile"><div class="l">Win Rate</div><div class="v good">64.2%</div></div>
          <div class="pv-tile"><div class="l">Total Return</div><div class="v good">+21.4%</div></div>
          <div class="pv-tile"><div class="l">Profit Factor</div><div class="v">1.86</div></div>
          <div class="pv-tile"><div class="l">Avg RR (Wins)</div><div class="v">2.10R</div></div>
        </div>
        <div class="pv-grid">
          <div class="pv-panel">
            <h3>Equity Curve</h3>
            <svg viewBox="0 0 400 120" width="100%" height="120" preserveAspectRatio="none" aria-hidden="true">
              <path class="pv-line" d="M0,95 L30,88 L60,92 L90,70 L120,78 L150,55 L180,64 L210,40 L240,50 L270,28 L300,38 L330,15 L360,24 L390,10"/>
            </svg>
          </div>
          <div class="pv-panel">
            <h3>Performance by Pair</h3>
            <table class="pv-table">
              <thead><tr><th>Pair</th><th class="num">Trades</th><th class="num">Return</th></tr></thead>
              <tbody>
                <tr><td>XAUUSD</td><td class="num">12</td><td class="num good">+11.2%</td></tr>
                <tr><td>EURUSD</td><td class="num">24</td><td class="num good">+8.4%</td></tr>
                <tr><td>GBPUSD</td><td class="num">18</td><td class="num good">+3.1%</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="section" id="markets">
  <div class="wrap">
    <div class="section-head reveal">
      <p class="section-eyebrow">Markets</p>
      <h2>Every market you trade, one journal</h2>
      <p>You're not locked into a broker or a platform — log trades from wherever you already trade,
         and let SteadFast do the analysis.</p>
    </div>
    <div class="market-cards">
      <div class="market-card glass reveal">
        <h3>Forex &amp; Metals</h3>
        <p>Majors, minors, and Gold (XAU) — currency bias scored straight from live interest rate,
           inflation, and employment data pulled directly from each central bank.</p>
      </div>
      <div class="market-card glass reveal">
        <h3>Indices &amp; Futures</h3>
        <p>Track NQ and ES alongside your FX trades, with the same news feed and journal tools —
           no separate spreadsheet for a separate market.</p>
      </div>
    </div>
    <div class="check-cards">
      <div class="check-card glass reveal">
        <h4>Macro Bias Scoring</h4>
        <ul>
          <li>Interest rates, CPI, employment, PMI</li>
          <li>Pulled from each central bank directly</li>
          <li>Automatic bull/bear verdict per currency</li>
        </ul>
      </div>
      <div class="check-card glass reveal">
        <h4>Economic Calendar</h4>
        <ul>
          <li>Cross-check upcoming releases</li>
          <li>Built-in embedded calendar view</li>
          <li>Know what's moving your pairs</li>
        </ul>
      </div>
      <div class="check-card glass reveal">
        <h4>Strategy Tagging</h4>
        <ul>
          <li>Tag every trade to a strategy</li>
          <li>Compare win rate and return per setup</li>
          <li>Edit and refine strategies over time</li>
        </ul>
      </div>
      <div class="check-card glass reveal">
        <h4>Multi-Account Tracking</h4>
        <ul>
          <li>Tag trades to different accounts</li>
          <li>Switch between them instantly</li>
          <li>Compare accounts side by side</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<section class="section" id="how-it-works">
  <div class="wrap">
    <div class="section-head reveal">
      <p class="section-eyebrow">How It Works</p>
      <h2>Three steps to a clearer trading process</h2>
      <p>No broker connection, no complicated setup — just a straightforward journal that gets
         out of your way.</p>
    </div>
    <div class="steps">
      <div class="step-row reveal">
        <div class="step-num">01</div>
        <div class="step-card glass">
          <div class="step-icon">{_ICON_USER}</div>
          <div>
            <h3>Create Your Account</h3>
            <p>Sign up in seconds — no credit card, no broker connection required.</p>
          </div>
        </div>
      </div>
      <div class="step-row reveal">
        <div class="step-num">02</div>
        <div class="step-card glass">
          <div class="step-icon">{_ICON_PEN}</div>
          <div>
            <h3>Log Every Trade</h3>
            <p>Record entries, exits, charts, and notes, then tag each trade to a strategy or account.</p>
          </div>
        </div>
      </div>
      <div class="step-row reveal">
        <div class="step-num">03</div>
        <div class="step-card glass">
          <div class="step-icon">{_ICON_INSIGHT}</div>
          <div>
            <h3>Get Real Insights</h3>
            <p>See your equity curve, currency bias, and exactly which strategies are actually working.</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="section" id="why-choose">
  <div class="wrap">
    <div class="section-head reveal">
      <p class="section-eyebrow">Why Choose SteadFast</p>
      <h2>Built around how you actually trade</h2>
      <p>Not a generic spreadsheet template — every piece is designed around real trading decisions.</p>
    </div>
    <div class="why-grid">
      <div class="why-card glass reveal">
        <div class="feature-icon" style="width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,rgba(147,197,253,0.18),rgba(29,78,216,0.18));color:#7fb0f7;">{_ICON_JOURNAL}</div>
        <h3>Complete Trade Journal</h3>
        <p>Full context on every trade — charts, notes, RR, and plan-violation flags.</p>
        <div class="why-mock">
          <div class="row"><span>XAUUSD · Long</span><b>2.4R</b></div>
          <div class="row good"><span>EURUSD · Long</span><b>+$184</b></div>
          <div class="row"><span>GBPUSD · Short</span><b>-0.8R</b></div>
        </div>
      </div>
      <div class="why-card glass reveal">
        <div class="feature-icon" style="width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,rgba(147,197,253,0.18),rgba(29,78,216,0.18));color:#7fb0f7;">{_ICON_SCALE}</div>
        <h3>Macro &amp; Fundamentals Bias</h3>
        <p>No more guessing which side of a pair is fundamentally stronger.</p>
        <div class="why-mock">
          <div class="row good"><span>USD Bias</span><b>Bullish</b></div>
          <div class="bar-track"><div class="bar-fill" style="width:72%"></div></div>
        </div>
      </div>
      <div class="why-card glass reveal">
        <div class="feature-icon" style="width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,rgba(147,197,253,0.18),rgba(29,78,216,0.18));color:#7fb0f7;">{_ICON_TARGET}</div>
        <h3>Strategy Performance</h3>
        <p>See, in hard numbers, which setups actually make you money.</p>
        <div class="why-mock">
          <div class="row"><span>LQ Sweep + FA</span><b>68% WR</b></div>
          <div class="bar-track"><div class="bar-fill" style="width:68%"></div></div>
        </div>
      </div>
      <div class="why-card glass reveal">
        <div class="feature-icon" style="width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,rgba(147,197,253,0.18),rgba(29,78,216,0.18));color:#7fb0f7;">{_ICON_LAYERS}</div>
        <h3>Multi-Account Tracking</h3>
        <p>Running a prop-firm account and a personal account? Compare them side by side.</p>
        <div class="why-mock">
          <div class="row"><span>Prop Firm A</span><b>+18.2%</b></div>
          <div class="row"><span>Personal</span><b>+9.4%</b></div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="section" id="about">
  <div class="wrap about-wrap reveal">
    <p class="section-eyebrow">About</p>
    <h2>Why SteadFast</h2>
    <p>Most trading journals are either a spreadsheet nobody keeps updated, or a broker dashboard
       that only shows you numbers, never the "why." SteadFast was built to sit in the middle —
       a real journal for every trade you take, backed by the same macro data professional
       desks use to size up a currency.</p>
    <p class="about-quote">Discipline. <span>Patience.</span> Clarity.</p>
    <p>That's the whole philosophy. Log the trade, understand the context, and let the numbers
       tell you what's actually working.</p>
  </div>
</section>

<section class="section" id="faq">
  <div class="wrap">
    <div class="section-head reveal">
      <p class="section-eyebrow">FAQ</p>
      <h2>Questions, answered</h2>
    </div>
    <div class="faq-list">
      <details class="faq-item glass reveal">
        <summary>Is my trading data private?</summary>
        <p>Yes — your journal is private to your account. Nobody else can see your trades, notes, or strategies.</p>
      </details>
      <details class="faq-item glass reveal">
        <summary>Which markets can I track?</summary>
        <p>Forex majors and minors, Gold, and NQ/ES futures today, with more instruments planned.</p>
      </details>
      <details class="faq-item glass reveal">
        <summary>Can I track multiple trading accounts?</summary>
        <p>Yes — tag trades to different accounts, switch between them, or compare them side by side.</p>
      </details>
      <details class="faq-item glass reveal">
        <summary>Do I need to connect a broker?</summary>
        <p>No. Trades are logged manually, so you stay in full control of your own data.</p>
      </details>
    </div>
  </div>
</section>

<footer class="footer">
  <div class="wrap">
    <div class="footer-top">
      <div>
        <div class="footer-brand">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M17 7h4v4"/><circle cx="9" cy="11" r="1.3" fill="currentColor" stroke="none"/><circle cx="13" cy="15" r="1.3" fill="currentColor" stroke="none"/></svg>
          SteadFast
        </div>
        <p class="footer-tagline">Discipline. Patience. Clarity.</p>
      </div>
      <div class="footer-cols">
        <div class="footer-col">
          <h4>Product</h4>
          <a href="#markets">Trade Journal</a>
          <a href="#markets">Macro Bias</a>
          <a href="#why-choose">Strategy Performance</a>
        </div>
        <div class="footer-col">
          <h4>Company</h4>
          <a href="#about">About</a>
          <a href="#faq">FAQ</a>
        </div>
        <div class="footer-col">
          <h4>Get Started</h4>
          <a href="/login">Sign In</a>
          <a href="/signup">Sign Up</a>
        </div>
      </div>
    </div>
    <div class="footer-bottom">
      <span>© 2026 SteadFast</span>
      <span>Discipline. Patience. Clarity.</span>
    </div>
  </div>
</footer>

<script>
(function() {{
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var targets = document.querySelectorAll('.reveal');
  if (reduceMotion || !('IntersectionObserver' in window)) {{
    targets.forEach(function(el) {{ el.classList.add('is-visible'); }});
    return;
  }}
  var io = new IntersectionObserver(function(entries) {{
    entries.forEach(function(entry) {{
      if (entry.isIntersecting) {{
        entry.target.classList.add('is-visible');
        io.unobserve(entry.target);
      }}
    }});
  }}, {{ threshold: 0.15 }});
  targets.forEach(function(el) {{ io.observe(el); }});
}})();
</script>
</body></html>"""
