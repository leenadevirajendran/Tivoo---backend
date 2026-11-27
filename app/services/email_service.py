from __future__ import annotations
from email.message import EmailMessage
import json
import smtplib
import ssl
import urllib.error
import urllib.request

from app.core.config import get_settings


class EmailService:
    """Robust email sender supporting Resend/Brevo HTTPS API (cloud-safe) and Gmail SMTP with graceful fallback."""

    def _build_html_content(self, subject: str, subtitle: str, otp_code: str, expire_minutes: int) -> str:
        return f"""
        <html>
          <body style="margin:0;padding:24px;background:#0b0e17;font-family:Segoe UI,Arial,sans-serif;color:#ffffff;">
            <div style="max-width:480px;margin:0 auto;background:#15182b;border:1px solid #2b2f55;border-radius:18px;padding:28px;">
              <div style="font-size:24px;font-weight:800;letter-spacing:0.6px;color:#ff2dc0;margin-bottom:8px;">
                Tivoo
              </div>
              <h2 style="margin:0 0 10px 0;font-size:20px;color:#ffffff;">{subject}</h2>
              <p style="margin:0 0 20px 0;font-size:14px;line-height:1.5;color:#cbd5e1;">
                {subtitle}
              </p>
              <div style="background:#0e1020;border:1px dashed #9d4edd;border-radius:12px;padding:16px;text-align:center;margin-bottom:20px;">
                <span style="font-size:32px;font-weight:800;letter-spacing:8px;color:#fffc00;">{otp_code}</span>
              </div>
              <p style="margin:0;font-size:12px;color:#94a3b8;">
                This OTP expires in <strong>{expire_minutes} minutes</strong>. Never share this code with anyone.
              </p>
            </div>
          </body>
        </html>
        """

    def _send_via_resend(self, api_key: str, recipient: str, subject: str, html_content: str, text_content: str) -> bool:
        settings = get_settings()
        sender_name = settings.mail_sender_name or "Tivoo"
        # Resend default testing address or configured domain
        from_email = f"{sender_name} <onboarding@resend.dev>"
        if "@" in settings.mail_sender and not settings.mail_sender.lower().endswith("@gmail.com"):
            from_email = f"{sender_name} <{settings.mail_sender.strip()}>"

        payload = {
            "from": from_email,
            "to": [recipient],
            "subject": subject,
            "html": html_content,
            "text": text_content,
        }
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "User-Agent": "TivooBackend/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    print(f"[Tivoo Mail] OTP successfully delivered to {recipient} via Resend HTTPS API", flush=True)
                    return True
        except Exception as exc:
            print(f"[Warning] Resend HTTPS API delivery failed: {exc}", flush=True)
        return False

    def _send_via_brevo(self, api_key: str, recipient: str, subject: str, html_content: str, text_content: str) -> bool:
        settings = get_settings()
        sender_email = settings.mail_sender.strip() or "no-reply@tivoo.com"
        sender_name = settings.mail_sender_name or "Tivoo"

        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": recipient}],
            "subject": subject,
            "htmlContent": html_content,
            "textContent": text_content,
        }
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "api-key": api_key.strip(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "TivooBackend/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    print(f"[Tivoo Mail] OTP successfully delivered to {recipient} via Brevo HTTPS API", flush=True)
                    return True
        except Exception as exc:
            print(f"[Warning] Brevo HTTPS API delivery failed: {exc}", flush=True)
        return False

    def send_otp_email(self, recipient: str, otp_code: str, purpose: str) -> bool:
        settings = get_settings()
        sender = settings.mail_sender.strip()
        app_password = settings.mail_app_password.replace(" ", "").strip()

        purpose_labels = {
            "login": ("Sign in to Tivoo", "Use this one-time code to sign in to your Tivoo account:"),
            "verify_email": ("Verify your Tivoo email", "Use this code to verify your email address on Tivoo:"),
            "reset_password": ("Reset your Tivoo password", "Use this code to reset your Tivoo password:"),
        }
        subject_title, subtitle = purpose_labels.get(
            purpose,
            ("Your Tivoo verification code", "Use this verification code:"),
        )
        full_subject = f"{subject_title} ({otp_code})"
        text_content = (
            f"{subtitle}\n\n"
            f"Verification Code: {otp_code}\n\n"
            f"This code expires in {settings.otp_expire_minutes} minutes.\n"
            "If you did not request this code, please ignore this email.\n"
        )
        html_content = self._build_html_content(
            subject=subject_title,
            subtitle=subtitle,
            otp_code=otp_code,
            expire_minutes=settings.otp_expire_minutes,
        )

        # 1. Try Resend HTTPS API (port 443 — works everywhere on cloud free tiers)
        if settings.resend_api_key.strip():
            if self._send_via_resend(settings.resend_api_key, recipient, full_subject, html_content, text_content):
                return True

        # 2. Try Brevo HTTPS API (port 443 — works everywhere on cloud free tiers)
        if settings.brevo_api_key.strip():
            if self._send_via_brevo(settings.brevo_api_key, recipient, full_subject, html_content, text_content):
                return True

        # 3. Try standard Gmail SMTP (port 587 / 465)
        if sender and app_password:
            msg = EmailMessage()
            msg["Subject"] = full_subject
            msg["From"] = f"{settings.mail_sender_name} <{sender}>"
            msg["To"] = recipient
            msg.set_content(text_content)
            msg.add_alternative(html_content, subtype="html")

            try:
                context = ssl.create_default_context()
                if settings.mail_use_tls:
                    with smtplib.SMTP(settings.mail_smtp_host, settings.mail_smtp_port, timeout=10) as server:
                        server.ehlo()
                        server.starttls(context=context)
                        server.ehlo()
                        server.login(sender, app_password)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP_SSL(settings.mail_smtp_host, settings.mail_smtp_port, context=context, timeout=10) as server:
                        server.login(sender, app_password)
                        server.send_message(msg)
                print(f"[Tivoo Mail] OTP successfully delivered to {recipient} via SMTP ({sender})", flush=True)
                return True
            except smtplib.SMTPAuthenticationError as exc:
                print(f"[Warning] Gmail SMTP authentication failed: {exc}", flush=True)
            except (smtplib.SMTPException, OSError) as exc:
                # Render free tier blocks outbound SMTP ports 25, 465, 587
                print(f"[Warning] Outbound SMTP port blocked or unreachable ({exc}). Cloud host requires HTTPS API.", flush=True)

        # 4. Fallback: Log OTP to server logs so verification is NEVER blocked
        print(
            f"\n{'='*64}\n"
            f"[TIVOO OTP CODE - CLOUD FALLBACK]\n"
            f"Recipient: {recipient}\n"
            f"Purpose:   {purpose}\n"
            f"OTP CODE:  >>> {otp_code} <<<\n"
            f"Expires:   {settings.otp_expire_minutes} minutes\n"
            f"Note: To deliver directly to inboxes on cloud free tier without SMTP blocks,\n"
            f"add RESEND_API_KEY or BREVO_API_KEY in Render Environment Variables.\n"
            f"{'='*64}\n",
            flush=True,
        )
        return False


email_service = EmailService()
