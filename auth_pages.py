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
  html,body{margin:0;height:100%;background:#0d0d0d;color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    display:flex;align-items:center;justify-content:center;}
  .card{background:#1a1a19;border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:32px 28px;width:300px;}
  h1{font-size:16px;margin:0 0 6px;}
  .sub{font-size:12.5px;color:#9a9a97;margin:0 0 16px;}
  input{width:100%;background:#212120;border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#fff;
    padding:9px 10px;font-size:14px;box-sizing:border-box;margin-bottom:12px;}
  button{width:100%;background:#3987e5;border:none;border-radius:8px;color:#fff;font-weight:650;padding:10px;
    font-size:13.5px;cursor:pointer;}
  button.secondary{background:#2a2a28;}
  .error{color:#e66767;font-size:12.5px;margin-bottom:10px;}
  .info{color:#7ec4a3;font-size:12.5px;margin-bottom:10px;}
  .links{margin-top:14px;font-size:12.5px;text-align:center;}
  .links a{color:#7aa8e0;text-decoration:none;}
  .links a:hover{text-decoration:underline;}
  form + form{margin-top:10px;}
"""


def render(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Felix Trade Journal</title>
<style>{_STYLE}</style></head><body>
<div class="card">
{body}
</div>
</body></html>"""


LOGIN_PAGE = """<form method="post">
  <h1>Felix Trade Journal</h1>
  {error}
  <input type="text" name="username" placeholder="Username" autofocus autocapitalize="off">
  <input type="password" name="password" placeholder="Password">
  <button type="submit">Enter</button>
</form>
<div class="links"><a href="/forgot-password">Forgot password?</a> &middot; <a href="/signup">Sign up</a></div>"""


SIGNUP_PAGE = """<form method="post">
  <h1>Create your account</h1>
  {error}
  <input type="text" name="first_name" placeholder="First name" value="{first_name}" autofocus>
  <input type="text" name="last_name" placeholder="Last name" value="{last_name}">
  <input type="text" name="username" placeholder="Username" value="{username}" autocapitalize="off">
  <input type="email" name="email" placeholder="Email" value="{email}" autocapitalize="off">
  <input type="password" name="password" placeholder="Password">
  <input type="password" name="confirm_password" placeholder="Confirm password">
  <button type="submit">Sign Up</button>
</form>
<div class="links"><a href="/login">Already have an account? Log in</a></div>"""


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
