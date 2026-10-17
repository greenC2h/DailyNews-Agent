"""基于已保存 HTML 的 SMTP 投递服务。"""
import smtplib
from email.message import EmailMessage
from app.config import get_settings

def send_html_email(recipient: str, subject: str, html: str) -> None:
    """发送一封 HTML 邮件；失败由调用方记录为明确失败或待确认。"""
    settings = get_settings()
    if not all((settings.smtp_host, settings.smtp_username, settings.smtp_password, settings.smtp_from)):
        raise RuntimeError("SMTP_HOST、SMTP_USERNAME、SMTP_PASSWORD、SMTP_FROM 必须配置")
    message = EmailMessage(); message["From"] = settings.smtp_from; message["To"] = recipient; message["Subject"] = subject
    message.set_content("DailyNews HTML 简报请使用支持 HTML 的邮件客户端查看。"); message.add_alternative(html, subtype="html")
    if settings.smtp_tls_mode == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.starttls(); server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
