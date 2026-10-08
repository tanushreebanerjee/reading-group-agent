import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from display.server import Hub, create_app

TUNNEL = {"x-forwarded-for": "203.0.113.5"}   # what a tunnel adds to every request


def client():
    hub = Hub()
    got = []

    async def on_action(msg):
        got.append(msg)

    hub.on_action = on_action
    return TestClient(create_app(hub, share_key="s3cret")), got


def test_pages_through_the_tunnel_need_the_key_and_never_reach_control():
    c, _ = client()
    assert c.get("/").status_code == 200                                  # on the laptop: as before
    assert c.get("/control").status_code == 200
    assert c.get("/", headers=TUNNEL).status_code == 403                  # link without the key
    assert c.get("/?k=wrong", headers=TUNNEL).status_code == 403
    assert c.get("/?k=s3cret", headers=TUNNEL).status_code == 200
    assert c.get("/control?k=s3cret", headers=TUNNEL).status_code == 403  # settings stay local


def test_viewers_can_reveal_but_not_change_settings():
    c, got = client()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers=TUNNEL) as ws:
            ws.receive_text()
    with c.websocket_connect("/ws?k=s3cret", headers=TUNNEL) as ws:
        ws.send_text(json.dumps({"action": "set", "key": "mode", "value": "ask"}))
        ws.send_text(json.dumps({"action": "reveal", "id": "H1"}))
        ws.send_text(json.dumps({"action": "stop_speaking"}))
    assert [m["action"] for m in got] == ["reveal", "stop_speaking"]
    with c.websocket_connect("/ws") as ws:                                # the laptop's control page
        ws.send_text(json.dumps({"action": "set", "key": "mode", "value": "ask"}))
    assert got[-1]["action"] == "set"
