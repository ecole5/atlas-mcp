from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load project .env before dataclass defaults read os.environ (at import time).
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env")


@dataclass
class ServerConfig:
    # Atlas via Knox — full CDP gateway URL including the Atlas API path
    # Example: ATLAS_GATEWAY_URL=https://<host>/<topology>/cdp-proxy-api/atlas/api/atlas/
    atlas_gateway_url: str = os.getenv("ATLAS_GATEWAY_URL", "")

    # Auth — simple Basic Auth (Knox proxies it through)
    atlas_user: Optional[str] = os.getenv("ATLAS_USER")
    atlas_password: Optional[str] = os.getenv("ATLAS_PASS")

    # Knox JWT token as alternative to basic auth
    knox_token: Optional[str] = os.getenv("KNOX_TOKEN")
    # Raw cookie string (highest priority)
    knox_cookie: Optional[str] = os.getenv("KNOX_COOKIE")

    # TLS/HTTP
    verify_ssl_env: str = os.getenv("ATLAS_VERIFY_SSL", "true").lower()
    ca_bundle: Optional[str] = os.getenv("ATLAS_CA_BUNDLE")
    timeout_seconds: int = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
    max_retries: int = int(os.getenv("HTTP_MAX_RETRIES", "3"))

    def build_verify(self) -> bool | str:
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl_env not in {"0", "false", "no"}

    def build_atlas_base(self) -> str:
        if not self.atlas_gateway_url:
            raise ValueError(
                "ATLAS_GATEWAY_URL must be set.\n"
                "Example: ATLAS_GATEWAY_URL=https://<host>/<topology>/cdp-proxy-api/atlas/api/atlas/"
            )
        base = self.atlas_gateway_url.rstrip("/")
        if not base.endswith("/v2"):
            base = f"{base}/v2"
        return base
