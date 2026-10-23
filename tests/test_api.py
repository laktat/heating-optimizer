import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

import web.server as server  # noqa: E402

client = TestClient(server.app)


def test_endpoints():
    for url in ["/api/meta", "/api/overview", "/api/diagnosis", "/api/plan",
                "/api/economics", "/api/timeseries?keys=t_out", "/api/network/optimal",
                "/api/bms", "/"]:
        assert client.get(url).status_code == 200, url


def test_simulate_overrides():
    r = client.post("/api/simulate", json={"base": "baseline", "t_out": -5, "repair": True,
                                            "pump_mode": "dp", "turns": {"sat": 1.0}})
    assert r.status_code == 200
    net = r.json()["network"]
    assert net["pump_mode"] == "dp"
    assert next(b for b in net["branches"] if b["id"] == "sat")["bv_turns"] == 1.0
    assert all(b["valve_stuck"] is None for b in net["branches"])


def test_unknown_state():
    assert client.get("/api/network/neco").status_code == 404
