###############################################################################
# WaterTAP Copyright (c) 2021, The Regents of the University of California,
# through Lawrence Berkeley National Laboratory, Oak Ridge National
# Laboratory, National Renewable Energy Laboratory, and National Energy
# Technology Laboratory (subject to receipt of any required approvals from
# the U.S. Dept. of Energy). All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and license
# information, respectively. These files are also available online at the URL
# "https://github.com/watertap-org/watertap/"
#
###############################################################################
"""
Tests for general zero-order proeprty package
"""
import pytest

from idaes.core import FlowsheetBlock
from idaes.core.util.model_statistics import degrees_of_freedom
from idaes.core.util.testing import initialization_tester
from idaes.core.util import get_solver
import idaes.core.util.scaling as iscale
from pyomo.environ import (ConcreteModel,
                           Constraint,
                           SolverStatus,
                           TerminationCondition,
                           value,
                           Var)
from pyomo.network import Port
from pyomo.util.check_units import assert_units_consistent


from watertap.core.zero_order_unit import SITOBase
from watertap.core.zero_order_properties import \
    WaterParameterBlock, WaterStateBlock

solver = get_solver()


@pytest.fixture(scope="module")
def model():
    m = ConcreteModel()

    m.fs = FlowsheetBlock(default={"dynamic": False})

    m.fs.water_props = WaterParameterBlock(
        default={"solute_list": ["A", "B", "C"]})

    m.fs.unit = SITOBase(default={"property_package": m.fs.water_props})

    m.fs.unit.inlet.flow_vol.fix(42)
    m.fs.unit.inlet.conc_mass_comp[0, "A"].fix(10)
    m.fs.unit.inlet.conc_mass_comp[0, "B"].fix(20)
    m.fs.unit.inlet.conc_mass_comp[0, "C"].fix(30)
    m.fs.unit.inlet.temperature.fix(303.15)
    m.fs.unit.inlet.pressure.fix(1.5e5)

    m.fs.unit.water_recovery.fix(0.8)
    m.fs.unit.removal_fraction[0, "A"].fix(0.1)
    m.fs.unit.removal_fraction[0, "B"].fix(0.2)
    m.fs.unit.removal_fraction[0, "C"].fix(0.3)
    m.fs.unit.deltaP_outlet.fix(1000)
    m.fs.unit.deltaP_waste.fix(2000)

    return m


@pytest.mark.unit
def test_build(model):
    assert isinstance(model.fs.unit.properties_in, WaterStateBlock)
    assert isinstance(model.fs.unit.properties_out, WaterStateBlock)
    assert isinstance(model.fs.unit.properties_waste, WaterStateBlock)

    assert isinstance(model.fs.unit.inlet, Port)
    assert isinstance(model.fs.unit.outlet, Port)
    assert isinstance(model.fs.unit.waste, Port)

    assert isinstance(model.fs.unit.water_recovery, Var)
    assert len(model.fs.unit.water_recovery) == 1
    assert isinstance(model.fs.unit.removal_fraction, Var)
    assert len(model.fs.unit.removal_fraction) == 3
    assert isinstance(model.fs.unit.deltaP_outlet, Var)
    assert len(model.fs.unit.deltaP_outlet) == 1
    assert isinstance(model.fs.unit.deltaP_waste, Var)
    assert len(model.fs.unit.deltaP_waste) == 1

    assert isinstance(model.fs.unit.water_recovery_equation, Constraint)
    assert len(model.fs.unit.water_recovery_equation) == 1
    assert isinstance(model.fs.unit.flow_balance, Constraint)
    assert len(model.fs.unit.flow_balance) == 1
    assert isinstance(model.fs.unit.solute_removal_equation, Constraint)
    assert len(model.fs.unit.solute_removal_equation) == 3
    assert isinstance(model.fs.unit.solute_mass_balances, Constraint)
    assert len(model.fs.unit.solute_mass_balances) == 3
    assert isinstance(model.fs.unit.outlet_pressure_constraint, Constraint)
    assert len(model.fs.unit.outlet_pressure_constraint) == 1
    assert isinstance(model.fs.unit.waste_pressure_constraint, Constraint)
    assert len(model.fs.unit.waste_pressure_constraint) == 1
    assert isinstance(model.fs.unit.outlet_temperature_equality, Constraint)
    assert len(model.fs.unit.outlet_temperature_equality) == 1
    assert isinstance(model.fs.unit.waste_temperature_equality, Constraint)
    assert len(model.fs.unit.waste_temperature_equality) == 1


@pytest.mark.unit
def test_degrees_of_freedom(model):
    assert degrees_of_freedom(model) == 0


@pytest.mark.component
def test_unit_consistency(model):
    assert_units_consistent(model)


@pytest.mark.component
def test_scaling(model):
    iscale.calculate_scaling_factors(model)

    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.water_recovery_equation[0]) == 1e3
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.flow_balance[0]) == 1e3
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.solute_removal_equation[0, "A"]) == 1e5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.solute_removal_equation[0, "B"]) == 1e5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.solute_removal_equation[0, "C"]) == 1e5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.solute_mass_balances[0, "A"]) == 1e5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.solute_mass_balances[0, "B"]) == 1e5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.solute_mass_balances[0, "C"]) == 1e5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.outlet_pressure_constraint[0]) == 1e-5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.waste_pressure_constraint[0]) == 1e-5
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.outlet_temperature_equality[0]) == 1e-2
    assert iscale.get_constraint_transform_applied_scaling_factor(
        model.fs.unit.outlet_temperature_equality[0]) == 1e-2


@pytest.mark.component
def test_initialization(model):
    initialization_tester(model)


@pytest.mark.component
def test_solve(model):
    results = solver.solve(model)

    # Check for optimal solution
    assert results.solver.termination_condition == \
        TerminationCondition.optimal
    assert results.solver.status == SolverStatus.ok


@pytest.mark.component
def test_solution(model):
    assert (pytest.approx(33.6, rel=1e-5) ==
            value(model.fs.unit.outlet.flow_vol[0]))
    assert (pytest.approx(8.4, rel=1e-5) ==
            value(model.fs.unit.waste.flow_vol[0]))

    assert (pytest.approx(11.25, rel=1e-5) ==
            value(model.fs.unit.outlet.conc_mass_comp[0, "A"]))
    assert (pytest.approx(5, rel=1e-5) ==
            value(model.fs.unit.waste.conc_mass_comp[0, "A"]))
    assert (pytest.approx(20, rel=1e-5) ==
            value(model.fs.unit.outlet.conc_mass_comp[0, "B"]))
    assert (pytest.approx(20, rel=1e-5) ==
            value(model.fs.unit.waste.conc_mass_comp[0, "B"]))
    assert (pytest.approx(26.25, rel=1e-5) ==
            value(model.fs.unit.outlet.conc_mass_comp[0, "C"]))
    assert (pytest.approx(45, rel=1e-5) ==
            value(model.fs.unit.waste.conc_mass_comp[0, "C"]))

    assert (pytest.approx(151000, rel=1e-5) ==
            value(model.fs.unit.outlet.pressure[0]))
    assert (pytest.approx(152000, rel=1e-5) ==
            value(model.fs.unit.waste.pressure[0]))

    assert (pytest.approx(303.15, rel=1e-5) ==
            value(model.fs.unit.outlet.temperature[0]))
    assert (pytest.approx(303.15, rel=1e-5) ==
            value(model.fs.unit.waste.temperature[0]))


@pytest.mark.component
def test_conservation(model):
    assert abs(value(model.fs.unit.inlet.flow_vol[0] -
                     model.fs.unit.outlet.flow_vol[0] -
                     model.fs.unit.waste.flow_vol[0])) <= 1e-6

    for j in model.fs.water_props.solute_set:
        assert (abs(value(model.fs.unit.inlet.flow_vol[0] *
                          model.fs.unit.inlet.conc_mass_comp[0, j] -
                          model.fs.unit.outlet.flow_vol[0] *
                          model.fs.unit.outlet.conc_mass_comp[0, j] -
                          model.fs.unit.waste.flow_vol[0] *
                          model.fs.unit.waste.conc_mass_comp[0, j]))
                <= 1e-6)
