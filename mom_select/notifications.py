from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

import requests


LOG = logging.getLogger("mom-select.notifications")
WECHAT_IMAGE_LIMIT = 2 * 1024 * 1024


def _check_response(response: requests.Response, channel: str, operation: str) -> None:
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{channel}{operation}返回非JSON响应（HTTP {response.status_code}）"
        ) from exc
    if isinstance(payload, dict) and payload.get("errcode", 0) != 0:
        raise RuntimeError(f"{channel}{operation}返回错误：{payload}")
    LOG.info("%s%s成功（HTTP %s）", channel, operation, response.status_code)


class Notifier(Protocol):
    def send(self, image: Path, subject: str, text: str) -> None: ...


@dataclass
class WeChatNotifier:
    webhook: str

    def send(self, image: Path, subject: str, text: str) -> None:
        image_bytes = image.read_bytes()
        LOG.info("开始发送企业微信通知：图片=%s，大小=%d字节", image.name, len(image_bytes))
        response = requests.post(
            self.webhook,
            json={"msgtype": "text", "text": {"content": f"{subject}\n{text}"}},
            timeout=20,
        )
        _check_response(response, "企业微信", "文本消息")
        if len(image_bytes) > WECHAT_IMAGE_LIMIT:
            raise RuntimeError(
                f"企业微信图片超过2 MiB限制：{len(image_bytes)}字节；文本消息已发送"
            )
        content = base64.b64encode(image_bytes).decode("ascii")
        digest = hashlib.md5(image_bytes).hexdigest()
        response = requests.post(
            self.webhook,
            json={"msgtype": "image", "image": {"base64": content, "md5": digest}},
            timeout=30,
        )
        _check_response(response, "企业微信", "图片消息")


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
        _check_response(response, "Telegram", "图片消息")


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
        LOG.info("消息通知未启用（notifications.enabled=false）")
        return []
    result: list[Notifier] = []
    for item in settings.channels:
        kind = item.get("type", "").lower()
        name = item.get("name") or kind or "未命名渠道"
        if kind == "wechat" and item.get("webhook"):
            result.append(WeChatNotifier(item["webhook"]))
        elif kind == "telegram" and item.get("bot_token") and item.get("chat_id"):
            result.append(TelegramNotifier(item["bot_token"], str(item["chat_id"]), item.get("proxy") or None))
        elif kind == "email" and item.get("host"):
            result.append(EmailNotifier(item["host"], int(item.get("port", 465)), item.get("username", ""), item.get("password", ""), item.get("from", item.get("username", "")), list(item.get("to", [])), bool(item.get("use_ssl", True))))
        else:
            LOG.warning("通知渠道配置不完整或类型未知，已跳过：%s", name)
            continue
        LOG.info("通知渠道已启用：%s（%s）", name, kind)
    LOG.info("消息通知配置完成：启用%d个渠道", len(result))
    if not result:
        LOG.warning("notifications.enabled=true，但没有可用通知渠道")
    return result


def notify_all(notifiers: list[Notifier], image: Path, subject: str, text: str) -> None:
    for notifier in notifiers:
        LOG.info("开始调用通知渠道：%s", type(notifier).__name__)
        notifier.send(image, subject, text)
