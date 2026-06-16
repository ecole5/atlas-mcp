from __future__ import annotations

from typing import Optional

import requests


class AtlasAuthFactory:
    """Build an authenticated requests.Session for Apache Atlas behind Knox.

    Uses HTTP Basic Auth (ATLAS_USER + ATLAS_PASS). Knox proxies credentials through.
    """

    def __init__(
        self,
        user: Optional[str],
        password: Optional[str],
        verify: bool | str = True,
    ):
        self.user = user
        self.password = password
        self.verify = verify

    def build_session(self) -> requests.Session:
        if not self.user or not self.password:
            raise ValueError(
                "ATLAS_USER and ATLAS_PASS must be set.\n"
                "Example: ATLAS_USER=myuser ATLAS_PASS=mypass"
            )

        session = requests.Session()
        session.verify = self.verify
        session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        session.auth = (self.user, self.password)
        return session
