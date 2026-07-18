"""Synthetic demo-mode infrastructure.

Everything in this package exists to support the ANONYMOUS synthetic demo
session (see ``creditops.api.demo_sessions``): a locally-signed, short-TTL
RS256 JWT whose public half is served to the API's own verifier.  No real
identity provider, no real customer, and no secret is ever emitted to a
client — all data minted here is synthetic and labelled as such.
"""

from __future__ import annotations

from creditops.infrastructure.demo.signer import DemoJwtSigner, DemoSignerError

__all__ = ["DemoJwtSigner", "DemoSignerError"]
