"""WS /api/nodes/{node_id}/terminal — the browser's live shell into a node.

One WebSocket per session. Binary frames carry raw terminal bytes in both
directions; a JSON text frame carries a resize event
(`{"type": "resize", "cols": n, "rows": n}`) since that is the only other
thing either side needs to say. Nothing else is ever a text frame — see
frontend/src/components/TerminalModal.tsx for the other end of this contract.
"""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from auth import require_admin_ws
from core import terminal_bridge
from core.db_session import session_scope
from database import get_db, log_user_action
import models
from routers.deps import node_or_404

router = APIRouter(prefix="/api/nodes", tags=["Terminal"])

#: No client keystroke for this long closes the session. Output alone (a
#: `tail -f` left running) does not count — see TerminalSession.read's
#: caller below, which only resets the clock on an inbound "bytes" frame.
IDLE_TIMEOUT_SECONDS = 15 * 60


@router.websocket("/{node_id}/terminal")
async def node_terminal(
    websocket: WebSocket,
    node_id: int,
    current_user: models.User = Depends(require_admin_ws),
    db: Session = Depends(get_db),
):
    node = node_or_404(db, node_id)
    settings = db.query(models.Settings).first()
    use_key = current_user.is_superadmin or bool(
        settings and settings.allow_admin_key_terminal_access
    )
    login = node.ssh_login or "root"

    await websocket.accept()

    session = terminal_bridge.open_bridge(
        host=node.ip_address, port=node.ssh_port, user=login, use_key=use_key,
    )
    with session_scope() as log_db:
        log_user_action(log_db, current_user.username, "Open Terminal", f"Opened a terminal to node '{node.hostname}'")

    loop = asyncio.get_event_loop()
    started_at = time.monotonic()
    last_input = time.monotonic()
    close_reason = "closed"
    output_queue: asyncio.Queue = asyncio.Queue()

    def on_pty_readable():
        data = session.read()
        if not data:
            loop.remove_reader(session.master_fd)
            output_queue.put_nowait(None)  # signals "the process is done talking"
            return
        output_queue.put_nowait(data)

    loop.add_reader(session.master_fd, on_pty_readable)

    async def pump_output():
        while True:
            chunk = await output_queue.get()
            if chunk is None:
                return
            await websocket.send_bytes(chunk)

    output_task = asyncio.ensure_future(pump_output())

    try:
        while True:
            remaining = IDLE_TIMEOUT_SECONDS - (time.monotonic() - last_input)
            if remaining <= 0:
                close_reason = "idle"
                break
            if session.poll() is not None:
                close_reason = "remote closed"
                break
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                continue

            if message["type"] == "websocket.disconnect":
                close_reason = "client closed"
                break
            if message.get("bytes") is not None:
                session.write(message["bytes"])
                last_input = time.monotonic()
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except ValueError:
                    continue
                if control.get("type") == "resize":
                    session.resize(int(control.get("cols", 80)), int(control.get("rows", 24)))
    except WebSocketDisconnect:
        close_reason = "client closed"
    finally:
        loop.remove_reader(session.master_fd)
        output_task.cancel()
        session.close()
        duration = int(time.monotonic() - started_at)
        with session_scope() as log_db:
            log_user_action(
                log_db, current_user.username, "Close Terminal",
                f"Closed the terminal to node '{node.hostname}' ({close_reason}, {duration}s)",
            )
        try:
            await websocket.close(reason=close_reason)
        except RuntimeError:
            pass
