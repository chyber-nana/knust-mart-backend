import os
import httpx

def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    key = os.environ.get("BREVO_API_KEY")
    sender_email = os.environ.get("MAIL_FROM", "noreply@knustmart.com")
    
    if not key:
        print(f"[mail simulation] to={to} subject={subject}\n{body}")
        return

    response = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": key,
            "content-type": "application/json",
            "accept": "application/json"
        },
        json={
            "sender": {"email": sender_email, "name": "KNUST Mart"},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body,
            **({"htmlContent": html_body} if html_body else {})
        },
        timeout=10.0,
    )
    response.raise_for_status()