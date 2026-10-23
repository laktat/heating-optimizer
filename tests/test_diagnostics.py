"""Diagnostika musí najít všechno, co simulátor do budovy schoval — a nic navíc."""

import pytest

import building
import climate
import diagnostics
import optimizer
import simulate


def ids(diag):
    return {f.id for f in diag.findings}


def test_finds_every_injected_fault(diagnosis):
    found = ids(diagnosis)
    assert "sensor-offset-header" in found
    assert "sensor-stuck-sat.t_return" in found
    assert "valve-stuck-adm" in found
    assert "obstruction-skl" in found
    assert "overflow-sat" in found
    assert {"pump-fixed", "curve-high", "setback-off"} <= found


def test_estimates_hidden_values(diagnosis):
    assert diagnosis.sensor_offset == pytest.approx(-2.5, abs=0.2)
    assert diagnosis.estimated_faults["skl"]["kv_obstruction"] == pytest.approx(3.5, rel=0.15)
    assert diagnosis.estimated_faults["adm"]["valve_stuck"] == pytest.approx(0.7, abs=0.12)
    assert diagnosis.config.curve_slope == pytest.approx(3.4, abs=0.05)
    assert diagnosis.config.curve_shift == pytest.approx(6.0, abs=0.3)


def test_virtual_flow_meters_match_truth(records, diagnosis):
    """Odhad průtoku z bilance prostoru sedí s tím, co v simulaci opravdu teklo."""
    import statistics as st
    for row in diagnosis.branches:
        real = st.mean(r[f"_{row['id']}.flow"] for r in records)
        assert row["flow_est"] == pytest.approx(real, rel=0.12, abs=0.15), row["id"]


def test_healthy_building_has_no_critical_findings(plan):
    healthy = plan.optimal
    recs = simulate.week(healthy, climate.measurement_week(), sensor_faults={})
    diag = diagnostics.diagnose(recs, {"bv_turns": {b.id: b.bv_turns for b in healthy.branches}})
    assert not [f for f in diag.findings if f.severity == "critical"], ids(diag)
    assert "overflow-sat" not in ids(diag)
