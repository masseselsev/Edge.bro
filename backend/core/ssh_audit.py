"""Audit of authorized_keys files against the live database."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Stands in for the orchestrator in SshKeyFinding.host, which is NOT NULL so
#: that the (location, host, fingerprint) unique constraint behaves.
ORCHESTRATOR_HOST = "__orchestrator__"
