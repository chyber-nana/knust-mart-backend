import os
import httpx

def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    key = os.environ.get("BREVO_API_KEY")
    raw_sender = os.environ.get("MAIL_FROM", "messenger@knustmart.site")
    
    # Parse sender name and email safely
    if "<" in raw_sender and ">" in raw_sender:
        sender_email = raw_sender.split("<")[1].split(">")[0].strip()
        sender_name = raw_sender.split("<")[0].strip()
    else:
        sender_email = raw_sender.strip()
        sender_name = "KNUST Mart"

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
            "sender": {"email": sender_email, "name": sender_name},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body,
            **({"htmlContent": html_body} if html_body else {})
        },
        timeout=10.0,
    )
    
    # This will print Brevo's exact rejection reason in your Render logs
    if response.status_code >= 400:
        print(f"❌ BREVO API ERROR [{response.status_code}]: {response.text}")
        
    response.raise_for_status()