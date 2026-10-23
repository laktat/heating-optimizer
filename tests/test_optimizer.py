"""Optimalizace: vyvážení musí sedět s modelem, křivka nesmí nikoho nechat v zimě."""

import pytest

import building
import economics
import optimizer
import simulate


def test_balancing_hits_targets(plan):
    for bid, ratio in plan.balance.check.items():
        assert ratio == pytest.approx(1.0, abs=0.05), bid
    assert plan.balance.turns[plan.balance.index_branch] == building.BALANCING_TURNS_MAX


def test_optimal_state_keeps_comfort(plan):
    for t in (-12.0, -5.0, 0.0, 5.0, 10.0):
        r = simulate.steady(plan.optimal, t, True)
        for b in r.branches:
            assert b.t_room >= b.setpoint - 0.3, (t, b.id, b.t_room)


def test_optimal_curve_lower_than_today(plan):
    for t in (-10.0, 0.0, 10.0):
        assert plan.optimal.supply_temp(t) < plan.baseline.supply_temp(t)


def test_setback_drop_bounds():
    z = building.design().by_id("adm").zone
    d = economics.setback_drop(z, 0.0, 14.0)
    assert 0 < d <= z.sp_occ - z.sp_setback
    assert economics.setback_drop(z, 0.0, 0.0) == 0.0


def test_economics_saves_money_without_cold(plan):
    e = economics.evaluate(plan)
    assert e["total"]["saving_kc"] > 0
    assert e["total"]["payback_y"] < 3
    for zone in e["optimal"]["comfort"].values():
        assert zone["under_kh"] < 50


def test_npv_and_irr_consistent():
    irr = economics.irr(100_000, 40_000)
    assert economics.npv(100_000, 40_000, rate=irr) == pytest.approx(0, abs=50)
