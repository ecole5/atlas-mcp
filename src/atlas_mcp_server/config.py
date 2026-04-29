from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ServerConfig:
    # Transport: stdio (default), http, sse
    transport: str = os.getenv("MCP_TRANSPORT", "stdio")
    host: str = os.getenv("MCP_HOST", "127.0.0.1")
    port: int = int(os.getenv("MCP_PORT", "3030"))

    # Atlas via Knox — users set either ATLAS_GATEWAY_URL (CDP pattern) or ATLAS_API_BASE (direct URL)
    # CDP pattern:  ATLAS_GATEWAY_URL=https://<host>/<topology>/cdp-proxy-api
    #               → resolved base = {ATLAS_GATEWAY_URL}/atlas/api/atlas/v2
    # Direct:       ATLAS_API_BASE=https://<host>/api/atlas/v2
    atlas_gateway_url: str = os.getenv("ATLAS_GATEWAY_URL", "")
    atlas_api_base: Optional[str] = os.getenv("ATLAS_API_BASE")

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
        if self.atlas_api_base:
            return self.atlas_api_base.rstrip("/")
        if not self.atlas_gateway_url:
            raise ValueError(
                "ATLAS_GATEWAY_URL or ATLAS_API_BASE must be set.\n"
                "Example: ATLAS_GATEWAY_URL=https://<host>/<topology>/cdp-proxy-api"
            )
        return f"{self.atlas_gateway_url.rstrip('/')}/atlas/api/atlas/v2"
