import os
import httpx

def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    """Compatibility wrapper for routers expecting send_email."""
    # If html_body isn't provided, use text body as a basic HTML fallback or vice versa
    send_otp_email(to=to, code=body, user_name="Valued User") # Or pass through your full logic below

def send_otp_email(to: str, code: str, user_name: str = "Student") -> None:
    key = os.environ.get("BREVO_API_KEY")
    raw_sender = os.environ.get("MAIL_FROM", "messenger@knustmart.site")
    
    # Parse sender name and email safely
    if "<" in raw_sender and ">" in raw_sender:
        sender_email = raw_sender.split("<")[1].split(">")[0].strip()
        sender_name = raw_sender.split("<")[0].strip()
    else:
        sender_email = raw_sender.strip()
        sender_name = "KNUST Mart"

    subject = "Verify Your KNUST Mart Account 🚀"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Verify Your Email</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                background-color: #f4f6f8;
                margin: 0;
                padding: 0;
                -webkit-font-smoothing: antialiased;
            }}
            .email-wrapper {{
                width: 100%;
                background-color: #f4f6f8;
                padding: 40px 0;
            }}
            .email-content {{
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
            }}
            .email-header {{
                background-color: #0f172a;
                padding: 30px;
                text-align: center;
            }}
            .email-header h1 {{
                color: #ffffff;
                margin: 0;
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            .email-body {{
                padding: 40px 30px;
                color: #334155;
                line-height: 1.6;
            }}
            .email-body h2 {{
                color: #0f172a;
                font-size: 20px;
                margin-top: 0;
            }}
            .otp-box {{
                background-color: #f8fafc;
                border: 2px dashed #cbd5e1;
                border-radius: 8px;
                padding: 20px;
                text-align: center;
                margin: 30px 0;
            }}
            .otp-code {{
                font-size: 36px;
                font-weight: 800;
                letter-spacing: 6px;
                color: #2563eb;
                margin: 0;
            }}
            .email-footer {{
                background-color: #f8fafc;
                padding: 20px 30px;
                text-align: center;
                color: #94a3b8;
                font-size: 13px;
                border-top: 1px solid #e2e8f0;
            }}
        </style>
    </head>
    <body>
        <div class="email-wrapper">
            <div class="email-content">
                <div class="email-header">
                    <h1>KNUST Mart</h1>
                </div>
                <div class="email-body">
                    <h2>Hello, {user_name}! 👋</h2>
                    <p>Welcome to <strong>KNUST Mart</strong> — your ultimate student marketplace and courier platform on campus. Please use the verification code below:</p>
                    <div class="otp-box">
                        <p style="margin: 0 0 10px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; font-weight: 600;">Your Verification Code</p>
                        <p class="otp-code">{code}</p>
                    </div>
                    <p>This code will expire in <strong>10 minutes</strong>.</p>
                </div>
                <div class="email-footer">
                    <p>&copy; 2026 KNUST Mart. Built for KNUST students.</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    text_body = f"Hello {user_name},\n\nYour KNUST Mart verification code is: {code}\n\nIt expires in 10 minutes."

    if not key:
        print(f"[mail simulation] to={to} subject={subject}\n{text_body}")
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
            "textContent": text_body,
            "htmlContent": html_body
        },
        timeout=10.0,
    )
    
    if response.status_code >= 400:
        print(f"❌ BREVO API ERROR [{response.status_code}]: {response.text}")
        
    response.raise_for_status()