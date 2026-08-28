"""Sends transactional email via Resend's REST API.

Uses stdlib urllib, not the `requests` package -- this runs under gunicorn
on Render, and requests isn't in requirements.txt (seed_remote.py already
imports it without declaring it, which only works by accident since that
script never runs anywhere but locally; not repeating that gap here).
"""
import json
import os
import urllib.error
import urllib.request

RESEND_API_URL = "https://api.resend.com/emails"


def send_email(to, subject, html_body):
    # Read lazily, not as a module constant, since server.py imports this module before loading its .env.
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("RESEND_API_KEY not set -- cannot send email")
        return False
    from_address = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

    payload = json.dumps({
        "from": from_address,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        # Custom User-Agent needed since Cloudflare silently 403s Python's default one -- same fix already used for FRED in fred_sync.py.
        headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "User-Agent": "curl/8.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as e:
        print(f"Resend send failed: {e.code} {e.read().decode('utf-8', errors='replace')[:300]}")
        return False
    except urllib.error.URLError as e:
        print(f"Resend send failed: {e}")
        return False


def _code_email_html(heading, code):
    return f"""<div style="font-family:system-ui,sans-serif;max-width:400px;margin:0 auto;">
<h2>{heading}</h2>
<p style="font-size:32px;font-weight:700;letter-spacing:6px;">{code}</p>
<p style="color:#666;font-size:13px;">This code expires in 15 minutes. If you didn't request this, you can ignore this email.</p>
</div>"""


def send_verification_code(to, code):
    return send_email(to, "Verify your email — SteadFast", _code_email_html("Verify your email", code))


def send_reset_code(to, code):
    return send_email(to, "Reset your password — SteadFast", _code_email_html("Your password reset code", code))
