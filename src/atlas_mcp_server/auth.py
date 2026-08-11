## Copyright (c) 2025 Cloudera, Inc. All Rights Reserved.
##
## This file is licensed under the Apache License Version 2.0 (the "License").
## You may not use this file except in compliance with the License.
## You may obtain a copy of the License at http:##www.apache.org/licenses/LICENSE-2.0.
##
## This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
## OF ANY KIND, either express or implied. Refer to the License for the specific
## permissions and limitations governing your use of the file.

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
