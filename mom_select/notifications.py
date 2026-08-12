from __future__ import annotations

import base64
import hashlib
import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

import requests


def _check_response(response: requests.Response) -> None:
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        return
    if isinstance(payload, dict) and payload.get("errcode", 0) != 0:
        raise RuntimeError(f"通知接口返回错误：{payload}")


class Notifier(Protocol):
    def send(self, image: Path, subject: str, text: str) -> None: ...


@dataclass
class WeChatNotifier:
    webhook: str

    def send(self, image: Path, subject: str, text: str) -> None:
        content = base64.b64encode(image.read_bytes()).decode("ascii")
        digest = hashlib.md5(image.read_bytes()).hexdigest()
        response = requests.post(self.webhook, json={"msgtype": "text", "text": {"content": f"{subject}\n{text}"}}, timeout=20)
        _check_response(response)
        response = requests.post(self.webhook, json={"msgtype": "image", "image": {"base64": content, "md5": digest}}, timeout=30)
        _check_response(response)


@dataclass
class TelegramNotifier:
    bot_token: str
    chat_id: str
    proxy: str | None = None

    def send(self, image: Path, subject: str, text: str) -> None:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        with image.open("rb") as handle:
            response = requests.post(url, data={"chat_id": self.chat_id, "caption": f"{subject}\n{text}"}, files={"photo": (image.name, handle, "image/png")}, proxies=proxies, timeout=60)
        _check_response(response)


@dataclass
class EmailNotifier:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]
    use_ssl: bool = True

    def send(self, image: Path, subject: str, text: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(text)
        content_type, _ = mimetypes.guess_type(image.name)
        maintype, subtype = (content_type or "image/png").split("/", 1)
        message.add_attachment(image.read_bytes(), maintype=maintype, subtype=subtype, filename=image.name)
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(self.username, self.password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.username, self.password)
                smtp.send_message(message)


def build_notifiers(settings: Any) -> list[Notifier]:
    if not settings.enabled:
        return []
    result: list[Notifier] = []
    for item in settings.channels:
        kind = item.get("type", "").lower()
        if kind == "wechat" and item.get("webhook"):
            result.append(WeChatNotifier(item["webhook"]))
        elif kind == "telegram" and item.get("bot_token") and item.get("chat_id"):
            result.append(TelegramNotifier(item["bot_token"], str(item["chat_id"]), item.get("proxy") or None))
        elif kind == "email" and item.get("host"):
            result.append(EmailNotifier(item["host"], int(item.get("port", 465)), item.get("username", ""), item.get("password", ""), item.get("from", item.get("username", "")), list(item.get("to", [])), bool(item.get("use_ssl", True))))
    return result


def notify_all(notifiers: list[Notifier], image: Path, subject: str, text: str) -> None:
    for notifier in notifiers:
        notifier.send(image, subject, text)
