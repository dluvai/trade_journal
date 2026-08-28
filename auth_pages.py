"""Hand-rolled, self-contained HTML for every pre-login page (login, signup,
email verification, forgot/reset password).

Deliberately NOT Jinja templates extending base.html: base.html pulls
/dashboard-core.css and .js through the static_asset route, which
require_login() doesn't allow unauthenticated, and its sidebar assumes a
logged-in session. The original LOGIN_PAGE already used this
standalone-string approach for exactly that reason -- these just extend it
so there's one shared style block instead of five copies of the same CSS.

Each *_PAGE string is the inner content only (one or more <form>s); render()
wraps it in the shared shell. Callers build the inner string via .format()
before passing it to render().
"""

_STYLE = """
  html,body{margin:0;height:100%;color:#f5f4f2;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    color-scheme:dark;
    background:radial-gradient(circle at 25% 12%, rgba(193,127,62,0.12), transparent 55%),
      linear-gradient(135deg,#060607 0%,#08080a 100%);
    background-attachment:fixed,fixed;}
  .auth-shell{display:flex;min-height:100vh;}
  .auth-art{flex:1.7;position:relative;overflow:hidden;border-right:1px solid rgba(245,244,242,0.08);
    background-image:
      radial-gradient(circle at 30% 20%,rgba(193,127,62,0.14),transparent 55%),
      linear-gradient(rgba(245,244,242,0.05) 1px,transparent 1px),
      linear-gradient(90deg,rgba(245,244,242,0.05) 1px,transparent 1px);
    background-size:cover,34px 34px,34px 34px;
    background-position:center;}
  .auth-demo-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    padding:88px 44px 220px;box-sizing:border-box;}

  /* Live demo: a trade gets typed into a form, saved, then the dashboard
     behind it updates -- the actual product loop, not decoration. Runs on
     a repeating JS timeline (see render()'s script) since coordinating
     "type field 1, then 2, then save, then swap cards, then draw the new
     point" is far more legible as a short sequence than as one giant CSS
     percentage-keyframe animation. */
  .journal-demo{position:relative;width:100%;max-width:480px;height:360px;margin:0 auto;z-index:2;}
  .demo-card{position:absolute;inset:0;background:rgba(28,27,31,0.4);
    backdrop-filter:blur(30px) saturate(1.7);-webkit-backdrop-filter:blur(30px) saturate(1.7);
    border:1px solid rgba(245,244,242,0.16);border-radius:16px;padding:28px;box-sizing:border-box;
    box-shadow:0 24px 50px -20px rgba(0,0,0,0.55), inset 1px 0 0 rgba(255,255,255,0.06);
    transition:opacity .5s ease,transform .5s ease;overflow:hidden;}
  .demo-card::before{content:"";position:absolute;inset:0;pointer-events:none;z-index:0;
    background:linear-gradient(160deg,rgba(255,255,255,0.08) 0%,rgba(255,255,255,0) 30%,rgba(255,255,255,0) 70%,rgba(255,255,255,0.04) 100%);}
  .demo-card > *{position:relative;z-index:1;}
  .demo-form-head{font-size:13px;font-weight:700;color:#a3a0a6;text-transform:uppercase;letter-spacing:.06em;margin-bottom:22px;}
  .demo-field{display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid rgba(245,244,242,0.08);}
  .demo-label{font-size:14px;color:#68656b;}
  .demo-type{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:15.5px;color:#f5f4f2;
    display:inline-block;overflow:hidden;white-space:nowrap;width:0;transition:width .45s ease;
    border-right:2px solid transparent;}
  .demo-type.filled{border-right-color:#c17f3e;}
  .demo-save-btn{width:100%;margin-top:24px;padding:13px;border-radius:9px;border:1px solid rgba(214,140,74,0.4);color:#f0d9c0;
    font-weight:700;font-size:15px;font-family:inherit;cursor:default;
    background:linear-gradient(135deg,#3a2c22 0%,#1c1512 55%,#0a0807 100%);
    transition:transform .15s ease,box-shadow .15s ease;}
  .demo-save-btn.clicked{transform:scale(.96);box-shadow:0 0 0 7px rgba(193,127,62,0.22);}
  .demo-form.hide{opacity:0;transform:translateY(-12px);}
  .demo-dash{opacity:0;transform:translateY(12px);}
  .demo-dash.show{opacity:1;transform:translateY(0);}
  .demo-dash-tiles{display:flex;gap:14px;margin-bottom:20px;}
  .demo-tile{flex:1;background:rgba(245,244,242,0.04);border-radius:10px;padding:13px 15px;}
  .demo-tile .l{display:block;font-size:10.5px;color:#68656b;text-transform:uppercase;letter-spacing:.05em;}
  .demo-tile .v{display:block;font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:19px;font-weight:600;margin-top:5px;}
  .demo-tile .v.good{color:#45c988;}
  .demo-chart{width:100%;height:92px;margin-bottom:20px;display:block;}
  .demo-chart-base{fill:none;stroke:#c17f3e;stroke-width:2;opacity:.45;}
  .demo-chart-new{fill:none;stroke:#45c988;stroke-width:2.5;stroke-linecap:round;
    stroke-dasharray:60;stroke-dashoffset:60;transition:stroke-dashoffset .6s ease;}
  .demo-chart-new.drawn{stroke-dashoffset:0;}
  .demo-row{display:flex;justify-content:space-between;padding:13px 15px;border-radius:9px;
    background:rgba(69,201,136,0.1);font-size:14px;opacity:0;transform:translateY(6px);
    transition:opacity .4s ease,transform .4s ease;}
  .demo-row.show{opacity:1;transform:translateY(0);}
  .demo-row .good{color:#45c988;font-weight:600;}
  @media (prefers-reduced-motion:reduce){.demo-card,.demo-type,.demo-save-btn,.demo-chart-new,.demo-row{transition:none;}}

  .auth-art-inner{position:absolute;bottom:64px;left:32px;right:32px;z-index:2;}
  .auth-brand-mark{display:flex;align-items:center;gap:16px;margin-bottom:14px;}
  .auth-brand-mark svg{width:46px;height:46px;padding:10px;border-radius:13px;flex:none;color:#1a1210;box-sizing:border-box;
    background:linear-gradient(135deg,#b97b3f 0%,#6b3f1d 100%);
    box-shadow:0 10px 26px -8px rgba(193,127,62,0.55);}
  .auth-art-inner h2{font-size:46px;margin:0;letter-spacing:-0.01em;line-height:1;}
  .auth-art-inner p{font-size:14px;color:#a3a0a6;margin:0;}
  .auth-form-col{flex:1;display:flex;align-items:center;justify-content:center;padding:24px 64px;box-sizing:border-box;}
  /* No boxed card -- the form sits directly on the page's own gradient,
     the same surface as everything else on the page instead of a panel
     floating on top of it. Just a width cap for readable line length. */
  .card{width:380px;max-width:100%;box-sizing:border-box;}
  h1{font-size:26px;margin:0 0 8px;letter-spacing:-0.01em;}
  .sub{font-size:13.5px;color:#a3a0a6;margin:0 0 20px;}
  input,select{width:100%;background:rgba(245,244,242,0.06);border:1px solid rgba(245,244,242,0.14);border-radius:8px;color:#f5f4f2;
    padding:11px 13px;font-size:14.5px;box-sizing:border-box;margin-bottom:13px;}
  input:focus,select:focus{outline:none;border-color:#c17f3e;}
  select option{background:#1c1b1f;color:#f5f4f2;}
  button{width:100%;background:linear-gradient(135deg,#3a2c22 0%,#1c1512 55%,#0a0807 100%);
    border:1px solid rgba(214,140,74,0.4);border-radius:9px;color:#f0d9c0;font-weight:700;padding:12px;
    font-size:14.5px;cursor:pointer;transition:filter .15s ease,transform .15s ease;}
  button:hover{filter:brightness(1.3);}
  button:active{transform:translateY(1px);}
  button.secondary{background:rgba(245,244,242,0.08);color:#f5f4f2;font-weight:600;}
  .error{color:#f0837e;font-size:12.5px;margin-bottom:10px;}
  .info{color:#6fd3a6;font-size:12.5px;margin-bottom:10px;}
  .links{margin-top:14px;font-size:12.5px;text-align:center;}
  .links a{color:#e0a868;text-decoration:none;}
  .links a:hover{text-decoration:underline;}
  form + form{margin-top:10px;}
  @media (max-width:760px){.auth-art{display:none;} .auth-form-col{justify-content:center;padding:16px;}}
"""


def render(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — SteadFast</title>
<style>{_STYLE}</style></head><body>
<div class="auth-shell">
  <div class="auth-art">
    <div class="auth-demo-wrap">
      <div class="journal-demo">
        <div class="demo-card demo-form" id="demoForm">
          <div class="demo-form-head">New Trade</div>
          <div class="demo-field"><span class="demo-label">Pair</span><span class="demo-type" id="demoField0" data-full="XAUUSD"></span></div>
          <div class="demo-field"><span class="demo-label">Direction</span><span class="demo-type" id="demoField1" data-full="Long"></span></div>
          <div class="demo-field"><span class="demo-label">RR</span><span class="demo-type" id="demoField2" data-full="2.4R"></span></div>
          <button type="button" class="demo-save-btn" id="demoSaveBtn">Save Trade</button>
        </div>
        <div class="demo-card demo-dash" id="demoDash">
          <div class="demo-dash-tiles">
            <div class="demo-tile"><span class="l">Win Rate</span><span class="v">67%</span></div>
            <div class="demo-tile"><span class="l">Total Return</span><span class="v good">+20.4%</span></div>
          </div>
          <svg class="demo-chart" viewBox="0 0 300 90" preserveAspectRatio="none" aria-hidden="true">
            <path class="demo-chart-base" d="M0,72 L40,64 L80,68 L120,50 L160,56 L200,36 L240,44"/>
            <path class="demo-chart-new" id="demoChartNew" d="M240,44 L280,18"/>
          </svg>
          <div class="demo-row" id="demoNewRow">
            <span>XAUUSD</span><span>Long</span><span class="good">+2.4R</span>
          </div>
        </div>
      </div>
    </div>
    <div class="auth-art-inner">
      <div class="auth-brand-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M3 17l6-6 4 4 8-8"/>
          <path d="M17 7h4v4"/>
          <circle cx="9" cy="11" r="1.4" fill="currentColor" stroke="none"/>
          <circle cx="13" cy="15" r="1.4" fill="currentColor" stroke="none"/>
        </svg>
        <h2>SteadFast</h2>
      </div>
      <p>Discipline. Patience. Clarity.</p>
    </div>
  </div>
  <div class="auth-form-col"><div class="card">
{body}
  </div></div>
</div>
<script>
(function() {{
  var form = document.getElementById('demoForm');
  var dash = document.getElementById('demoDash');
  if (!form || !dash) return;
  var fields = [document.getElementById('demoField0'), document.getElementById('demoField1'), document.getElementById('demoField2')];
  var saveBtn = document.getElementById('demoSaveBtn');
  var chartNew = document.getElementById('demoChartNew');
  var newRow = document.getElementById('demoNewRow');
  fields.forEach(function(f) {{ f.textContent = f.dataset.full; }});

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) {{
    fields.forEach(function(f) {{ f.style.width = f.dataset.full.length + 'ch'; f.classList.add('filled'); }});
    dash.classList.add('show'); form.classList.add('hide');
    chartNew.classList.add('drawn'); newRow.classList.add('show');
    return;
  }}

  function resetCycle() {{
    fields.forEach(function(f) {{ f.style.width = '0'; f.classList.remove('filled'); }});
    saveBtn.classList.remove('clicked');
    form.classList.remove('hide');
    dash.classList.remove('show');
    chartNew.classList.remove('drawn');
    newRow.classList.remove('show');
  }}

  function runCycle() {{
    resetCycle();
    var t = 400;
    fields.forEach(function(f) {{
      setTimeout(function() {{ f.style.width = f.dataset.full.length + 'ch'; f.classList.add('filled'); }}, t);
      t += 550;
    }});
    setTimeout(function() {{ saveBtn.classList.add('clicked'); }}, t + 200);
    setTimeout(function() {{ saveBtn.classList.remove('clicked'); form.classList.add('hide'); }}, t + 500);
    setTimeout(function() {{ dash.classList.add('show'); }}, t + 900);
    setTimeout(function() {{ chartNew.classList.add('drawn'); newRow.classList.add('show'); }}, t + 1300);
  }}

  runCycle();
  setInterval(runCycle, 7000);
}})();
</script>
</body></html>"""


LOGIN_PAGE = """<form method="post">
  <h1>Welcome Back</h1>
  {error}
  <input type="text" name="username" placeholder="Username" autofocus autocapitalize="off">
  <input type="password" name="password" placeholder="Password">
  <button type="submit">Login</button>
</form>
<div class="links"><a href="/forgot-password">Forgot password?</a> &middot; <a href="/signup">Sign up</a></div>"""


SIGNUP_PAGE = """<form method="post" id="signupForm" novalidate>
  <h1>Create your account</h1>
  <div id="signupTopError">{error}</div>
  <input type="text" name="first_name" placeholder="First name" value="{first_name}" autofocus required>
  <div class="error" id="err_first_name" style="display:none;">First name is required.</div>
  <input type="text" name="last_name" placeholder="Last name" value="{last_name}" required>
  <div class="error" id="err_last_name" style="display:none;">Last name is required.</div>
  <input type="text" name="username" placeholder="Username" value="{username}" autocapitalize="off" required>
  <div class="error" id="err_username" style="display:none;">Username is required.</div>
  <select name="country" id="signupCountry">{country_options}</select>
  <div id="signupAddressFields" style="display:none;">
    <input type="text" name="address_line1" placeholder="Street address" value="{address_line1}">
    <input type="text" name="address_city" placeholder="City" value="{address_city}">
    <input type="text" name="address_postal_code" placeholder="Postal code" value="{address_postal_code}">
  </div>
  <input type="text" name="phone" placeholder="Phone number (optional)" value="{phone}">
  <input type="password" name="password" placeholder="Password" required minlength="8">
  <div class="error" id="err_password" style="display:none;">Password must be at least 8 characters.</div>
  <input type="password" name="confirm_password" placeholder="Confirm password" required>
  <div class="error" id="err_confirm_password" style="display:none;">Passwords don't match.</div>
  <button type="submit" id="signupSubmit">Sign Up</button>
</form>
<div class="links"><a href="/login">Already have an account? Log in</a></div>
<script>
(function() {{
  var sel = document.getElementById('signupCountry');
  var addr = document.getElementById('signupAddressFields');
  function sync() {{ addr.style.display = sel.value ? 'block' : 'none'; }}
  sel.addEventListener('change', sync);
  sync();

  var form = document.getElementById('signupForm');
  var topError = document.getElementById('signupTopError');
  // Fields with an inline error slot right below them (in DOM order) --
  // "which field does this error belong under" is a lookup, not a guess,
  // for every check this page can actually attribute to one field.
  var errEls = {{
    first_name: document.getElementById('err_first_name'),
    last_name: document.getElementById('err_last_name'),
    username: document.getElementById('err_username'),
    password: document.getElementById('err_password'),
    confirm_password: document.getElementById('err_confirm_password'),
  }};
  function clearErrors() {{
    topError.innerHTML = '';
    Object.keys(errEls).forEach(function(k) {{ errEls[k].style.display = 'none'; }});
  }}
  function showError(field, text) {{
    if (errEls[field]) {{
      if (text) errEls[field].textContent = text;
      errEls[field].style.display = 'block';
    }} else {{
      topError.innerHTML = '<div class="error">' + text + '</div>';
    }}
  }}

  // Real-time feedback as you type, no round trip -- password match is the
  // one check genuinely instant to do client-side.
  function checkMatch() {{
    var mismatch = form.confirm_password.value && form.password.value !== form.confirm_password.value;
    errEls.confirm_password.style.display = mismatch ? 'block' : 'none';
  }}
  form.password.addEventListener('input', checkMatch);
  form.confirm_password.addEventListener('input', checkMatch);

  function validate() {{
    clearErrors();
    var ok = true;
    ['first_name', 'last_name', 'username'].forEach(function(name) {{
      if (!form[name].value.trim()) {{ showError(name); ok = false; }}
    }});
    if (!form.password.value) {{ showError('password', 'Password is required.'); ok = false; }}
    else if (form.password.value.length < 8) {{ showError('password'); ok = false; }}
    if (!form.confirm_password.value) {{ showError('confirm_password', 'Confirm your password.'); ok = false; }}
    else if (form.password.value !== form.confirm_password.value) {{ showError('confirm_password'); ok = false; }}
    return ok;
  }}

  form.addEventListener('submit', function(e) {{
    e.preventDefault();
    if (!validate()) return;
    var submitBtn = document.getElementById('signupSubmit');
    submitBtn.disabled = true;
    var fd = new FormData(form);
    var payload = Object.fromEntries(fd.entries());
    fetch('/signup', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload),
    }}).then(function(r) {{ return r.json().then(function(data) {{ return {{ ok: r.ok, data: data }}; }}); }})
      .then(function(res) {{
        if (!res.ok) {{
          clearErrors();
          // Route the server's error under the field it's actually about
          // when we can tell, instead of always dumping it at the top.
          var msg = res.data.error || 'Something went wrong -- try again.';
          if (/username/i.test(msg)) showError('username', msg);
          else if (/password/i.test(msg)) showError('confirm_password', msg);
          else showError(null, msg);
          submitBtn.disabled = false;
          return;
        }}
        window.location.href = res.data.redirect;
      }})
      .catch(function() {{
        clearErrors();
        showError(null, 'Something went wrong -- try again.');
        submitBtn.disabled = false;
      }});
  }});
}})();
</script>"""


VERIFY_EMAIL_PAGE = """<form method="post" action="/verify-email">
  <h1>Verify your email</h1>
  <p class="sub">We sent a 6-digit code to {masked_email}. Enter it below -- it expires in 15 minutes.</p>
  {error}
  <input type="text" name="code" placeholder="6-digit code" autofocus inputmode="numeric" maxlength="6">
  <button type="submit">Verify</button>
</form>
<form method="post" action="/verify-email/resend">
  <button type="submit" class="secondary">Resend code</button>
</form>"""


VERIFY_EMAIL_RESUME_PAGE = """<form method="post" action="/verify-email/resume">
  <h1>Verify your email</h1>
  <p class="sub">Enter your username to continue verifying your account.</p>
  {error}
  <input type="text" name="username" placeholder="Username" autofocus autocapitalize="off">
  <button type="submit">Continue</button>
</form>"""


FORGOT_PASSWORD_PAGE = """<form method="post">
  <h1>Reset your password</h1>
  <p class="sub">Enter your username or email and we'll send you a reset code.</p>
  {error}
  <input type="text" name="identifier" placeholder="Username or email" value="{identifier}" autofocus autocapitalize="off">
  <button type="submit">Send Reset Code</button>
</form>
<div class="links"><a href="/login">Back to login</a></div>"""


RESET_PASSWORD_PAGE = """<form method="post">
  <h1>Enter your reset code</h1>
  <p class="sub">Check your email for the 6-digit code, then set a new password.</p>
  {info}
  {error}
  <input type="text" name="code" placeholder="6-digit code" autofocus inputmode="numeric" maxlength="6">
  <input type="password" name="password" placeholder="New password">
  <input type="password" name="confirm_password" placeholder="Confirm new password">
  <button type="submit">Reset Password</button>
</form>"""
