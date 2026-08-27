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
  html,body{margin:0;height:100%;background:#0d0d0d;color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;}
  .auth-shell{display:flex;min-height:100vh;}
  .auth-art{flex:1.7;position:relative;
    background:linear-gradient(135deg,#14121f,#0d0d0d);border-right:1px solid rgba(255,255,255,0.08);}
  .auth-brand{position:absolute;top:28px;left:32px;font-size:15px;font-weight:700;}
  .auth-art-inner{position:absolute;bottom:64px;left:32px;right:32px;}
  .auth-art-inner h2{font-size:26px;margin:0 0 8px;letter-spacing:0.5px;}
  .auth-art-inner p{font-size:13.5px;color:#9a9a97;margin:0;}
  .auth-form-col{flex:1;display:flex;align-items:center;justify-content:flex-end;padding:24px 64px;box-sizing:border-box;}
  .card{background:#1a1a19;border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:32px 28px;width:300px;max-width:100%;box-sizing:border-box;}
  h1{font-size:16px;margin:0 0 6px;}
  .sub{font-size:12.5px;color:#9a9a97;margin:0 0 16px;}
  input,select{width:100%;background:#212120;border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#fff;
    padding:9px 10px;font-size:14px;box-sizing:border-box;margin-bottom:12px;}
  button{width:100%;background:#6d7cf0;border:none;border-radius:8px;color:#fff;font-weight:650;padding:10px;
    font-size:13.5px;cursor:pointer;}
  button.secondary{background:#2a2a28;}
  .error{color:#e66767;font-size:12.5px;margin-bottom:10px;}
  .info{color:#7ec4a3;font-size:12.5px;margin-bottom:10px;}
  .links{margin-top:14px;font-size:12.5px;text-align:center;}
  .links a{color:#8f9cf5;text-decoration:none;}
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
    <div class="auth-brand">SteadFast</div>
    <div class="auth-art-inner">
      <h2>SteadFast</h2>
      <p>Discipline. Patience. Clarity.</p>
    </div>
  </div>
  <div class="auth-form-col"><div class="card">
{body}
  </div></div>
</div>
</body></html>"""


LOGIN_PAGE = """<form method="post">
  <h1>SteadFast</h1>
  {error}
  <input type="text" name="username" placeholder="Username" autofocus autocapitalize="off">
  <input type="password" name="password" placeholder="Password">
  <button type="submit">Enter</button>
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
  <input type="email" name="email" placeholder="Email" value="{email}" autocapitalize="off" required>
  <div class="error" id="err_email" style="display:none;">Enter a valid email address.</div>
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
    email: document.getElementById('err_email'),
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

  var EMAIL_RE = /^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/;

  // Real-time feedback as you type, no round trip -- password match and
  // email shape are the two checks genuinely instant to do client-side.
  function checkMatch() {{
    var mismatch = form.confirm_password.value && form.password.value !== form.confirm_password.value;
    errEls.confirm_password.style.display = mismatch ? 'block' : 'none';
  }}
  form.password.addEventListener('input', checkMatch);
  form.confirm_password.addEventListener('input', checkMatch);
  form.email.addEventListener('input', function() {{
    errEls.email.style.display = (form.email.value && !EMAIL_RE.test(form.email.value)) ? 'block' : 'none';
  }});

  function validate() {{
    clearErrors();
    var ok = true;
    ['first_name', 'last_name', 'username', 'email'].forEach(function(name) {{
      if (!form[name].value.trim()) {{ showError(name); ok = false; }}
    }});
    if (form.email.value.trim() && !EMAIL_RE.test(form.email.value.trim())) {{ showError('email'); ok = false; }}
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
          else if (/email/i.test(msg)) showError('email', msg);
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
