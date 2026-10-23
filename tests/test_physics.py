"""Fyzika: hydraulika a tělesa musí dávat to, co dávají rovnice."""

import pytest

import building
import hydraulics
import simulate
import thermal


def test_terminal_nominal_point():
    """Při jmenovitém průtoku a 75 °C dá těleso jmenovitý výkon a zpátečku 55 °C."""
    for b in building.design().branches:
        p, tr = thermal.terminal(b, 75.0, b.zone.sp_occ, b.q_design)
        assert p == pytest.approx(b.p_nom, rel=1e-3)
        assert tr == pytest.approx(55.0, abs=0.05)


def test_double_flow_gives_little_more_heat():
    """Dvojnásobný průtok dá jen o 10–15 % tepla víc — základ diagnostiky nevyváženosti."""
    b = building.design().by_id("sat")
    p1, tr1 = thermal.terminal(b, 75.0, 22.0, b.q_design)
    p2, tr2 = thermal.terminal(b, 75.0, 22.0, 2 * b.q_design)
    assert 1.0 < p2 / p1 < 1.2
    assert tr2 > tr1 + 8


def test_required_flow_inverts_terminal():
    b = building.design().by_id("vzt2")
    q = thermal.required_flow(b, 70.0, 21.0, 90.0)
    p, _ = thermal.terminal(b, 70.0, 21.0, q)
    assert p == pytest.approx(90.0, rel=1e-3)


def test_hydraulics_mass_and_pressure_balance():
    net = building.as_found()
    st = hydraulics.solve(net)
    assert sum(st.flows.values()) == pytest.approx(st.q_total, rel=1e-9)
    # tlak čerpadla = ztráta strojovny + tlak na rozdělovači
    assert st.pump_head == pytest.approx(
        st.node_dp[0] + hydraulics.loss(net.plant_kv, st.q_total), rel=1e-4)
    # po páteři tlak jen klesá
    assert all(a >= b for a, b in zip(st.node_dp, st.node_dp[1:]))


def test_closing_valve_pushes_water_elsewhere():
    net = building.design()
    open_ = hydraulics.solve(net)
    shut = hydraulics.solve(net, {"vzt1": 0.0})
    assert shut.flows["vzt1"] < 0.01
    assert shut.flows["skl"] > open_.flows["skl"]
    assert shut.flows["sat"] > open_.flows["sat"]


def test_obstruction_cuts_flow():
    net = building.design()
    before = hydraulics.solve(net).flows["skl"]
    net.by_id("skl").kv_obstruction = 3.5
    assert hydraulics.solve(net).flows["skl"] < 0.7 * before


def test_sensor_error_heats_more():
    """Čidlo, které ukazuje méně, vyrobí teplejší vodu."""
    net = building.design()
    net.header_sensor_error = -2.5
    assert net.supply_temp(0.0) == pytest.approx(net.curve(0.0) + 2.5)


def test_zone_control_holds_setpoint():
    r = simulate.steady(building.design(), -5.0, True)
    for b in r.branches:
        if b.valve is not None:
            assert b.t_room == pytest.approx(b.setpoint, abs=0.1)
