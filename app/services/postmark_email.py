from __future__ import annotations

import os
import httpx
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PostmarkConfig:
    server_token: str
    from_email: str
    message_stream: str = "outbound"


class PostmarkEmailService:
    """
    Minimal Postmark sender (single config for all tenants).
    Uses env vars:
      - POSTMARK_SERVER_TOKEN
      - POSTMARK_FROM_EMAIL
      - POSTMARK_MESSAGE_STREAM (optional)
    """

    def __init__(self, cfg: PostmarkConfig):
        self.cfg = cfg

    @staticmethod
    def from_env() -> "PostmarkEmailService":
        token = (os.getenv("POSTMARK_SERVER_TOKEN") or "").strip()
        from_email = (os.getenv("POSTMARK_FROM_EMAIL") or "").strip()
        stream = (os.getenv("POSTMARK_MESSAGE_STREAM") or "outbound").strip() or "outbound"

        if not token:
            raise RuntimeError("POSTMARK_SERVER_TOKEN is not set")
        if not from_email:
            raise RuntimeError("POSTMARK_FROM_EMAIL is not set")

        return PostmarkEmailService(PostmarkConfig(server_token=token, from_email=from_email, message_stream=stream))

    async def send(
        self,
        *,
        to_email: str,
        subject: str,
        html_body: str,
        tag: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = "https://api.postmarkapp.com/email"
        payload: dict[str, Any] = {
            "From": self.cfg.from_email,
            "To": to_email,
            "Subject": subject,
            "HtmlBody": html_body,
            "MessageStream": self.cfg.message_stream,
        }
        if tag:
            payload["Tag"] = tag
        if metadata and isinstance(metadata, dict):
            payload["Metadata"] = metadata

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": self.cfg.server_token,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json=payload, headers=headers)

        # Postmark returns non-2xx on errors
        if r.status_code < 200 or r.status_code >= 300:
            raise RuntimeError(f"Postmark error {r.status_code}: {r.text}")

        return r.json()