import os
import httpx

def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    # Fallback compatibility wrapper
    send_verification_email(to=to, verify_url=body, user_name="Student")

def send_verification_email(to: str, verify_url: str, user_name: str = "Student") -> None:
    key = os.environ.get("BREVO_API_KEY")
    raw_sender = os.environ.get("MAIL_FROM", "messenger@knustmart.site")
    
    if "<" in raw_sender and ">" in raw_sender:
        sender_email = raw_sender.split("<")[1].split(">")[0].strip()
        sender_name = raw_sender.split("<")[0].strip()
    else:
        sender_email = raw_sender.strip()
        sender_name = "KNUST Mart"

    subject = "Confirm Your KNUST Mart Email 🚀"

    # HTML body featuring a clean, responsive CTA button
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
            }}
            .email-body {{
                padding: 40px 30px;
                color: #334155;
                line-height: 1.6;
            }}
            .btn {{
                display: inline-block;
                background-color: #2563eb;
                color: #ffffff !important;
                text-decoration: none;
                font-weight: 600;
                font-size: 16px;
                padding: 14px 28px;
                border-radius: 8px;
                margin: 30px 0 20px 0;
                text-align: center;
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
                    <p>Welcome to <strong>KNUST Mart</strong>. Click the secure button below to confirm your student email address and activate your account:</p>
                    
                    <div style="text-align: center;">
                        <a href="{verify_url}" class="btn" target="_blank">Verify My Email Address</a>
                    </div>
                    
                    <p style="font-size: 13px; color: #64748b; word-break: break-all;">If the button doesn't work, copy and paste this link into your browser:<br>{verify_url}</p>
                    
                    <p>This link will expire in <strong>24 hours</strong>.</p>
                </div>
                <div class="email-footer">
                    <p>&copy; 2026 KNUST Mart. Built for KNUST students.</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    text_body = f"Hello {user_name},\n\nPlease verify your email by opening this link: {verify_url}\n\nThis link expires in 24 hours."

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