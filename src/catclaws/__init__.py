# SPDX-FileCopyrightText: 2026-present Christopher Soria <chrissoria@berkeley.edu>
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""cat-claws — agent-CLI backend for the CatLLM ecosystem.

Classify text through a Claude subscription (Claude Agent SDK) instead of
per-token API billing. See MASTERPLAN.md for design and roadmap.
"""

from .__about__ import __version__
from ._auth import NotSignedInError, auth_status, ensure_signed_in, login, sign_in_help
from .classify import classify

__all__ = ["classify", "login", "auth_status", "ensure_signed_in", "sign_in_help",
           "NotSignedInError", "__version__"]
