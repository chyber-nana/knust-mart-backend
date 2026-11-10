import os, httpx

def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        print(f"[mail] to={to} subject={subject}\n{body}")
        return
    r = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}"},
        json={"from": os.environ["MAIL_FROM"], "to": [to], "subject": subject,
              "text": body, **({"html": html_body} if html_body else {})},
        timeout=10,
    )
    r.raise_for_status()