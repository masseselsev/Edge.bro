"""Lookups shared by the route handlers.

The `fetch a row, 404 if it is not there` preamble appeared twenty-three times
for nodes alone, in four spellings — with and without a trailing full stop, and
with the status written both as `status.HTTP_404_NOT_FOUND` and as a bare
`404`. Which one a client saw depended on which endpoint it happened to call,
which makes the message useless to match on.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

import models


def node_or_404(db: Session, node_id: int) -> models.Node:
    """The node, or a 404 with a message worth reading.

    Naming the id matters more than it looks: the fleet view and the archive
    browser both deep-link by id, so a stale bookmark to a decommissioned node
    is the common way to reach this, and "Node not found." alone leaves the
    operator guessing which node the page was about.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {node_id} not found.",
        )
    return node
