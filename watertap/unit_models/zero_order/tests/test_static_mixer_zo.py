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
Tests for zero-order static mixer model.
"""
import pytest
from io import StringIO

from pyomo.environ import (
    check_optimal_termination, ConcreteModel, Constraint, value, Var)
from pyomo.util.check_units import assert_units_consistent

from idaes.core import FlowsheetBlock
from idaes.core.util import get_solver
from idaes.core.util.model_statistics import degrees_of_freedom
from idaes.core.util.testing import initialization_tester

from watertap.unit_models.zero_order import StaticMixerZO
from watertap.core.wt_database import Database
from watertap.core.zero_order_properties import WaterParameterBlock

solver = get_solver()


class TestStaticMixerZO:
    @pytest.fixture(scope="class")
    def model(self):
        m = ConcreteModel()
        m.db = Database()

        m.fs = FlowsheetBlock(default={"dynamic": False})
        m.fs.params = WaterParameterBlock(default={"solute_list": ["calcium", "magnesium", "foo", "sulfate"]})
                                                                

        m.fs.unit = StaticMixerZO(default={ "property_package": m.fs.params,
                                            "database": m.db})

        m.fs.unit.inlet.flow_mass_comp[0, "H2O"].fix(42)
        m.fs.unit.inlet.flow_mass_comp[0, "calcium"].fix(3)
        m.fs.unit.inlet.flow_mass_comp[0, "magnesium"].fix(0.1)
        m.fs.unit.inlet.flow_mass_comp[0, "foo"].fix(0.003)
        m.fs.unit.inlet.flow_mass_comp[0, "sulfate"].fix(4)

        return m

    @pytest.mark.unit
    def test_build(self, model):
        assert model.fs.unit.config.database is model.db
        assert model.fs.unit._tech_type == "static_mixer"
        assert isinstance(model.fs.unit.electricity, Var)
        assert isinstance(model.fs.unit.energy_electric_flow_vol_inlet, Var)
        assert isinstance(model.fs.unit.electricity_consumption, Constraint)

    @pytest.mark.component
    def test_load_parameters(self, model):
        data = model.db.get_unit_operation_parameters("static_mixer")
        model.fs.unit.load_parameters_from_database()

        assert model.fs.unit.energy_electric_flow_vol_inlet.fixed
        assert model.fs.unit.energy_electric_flow_vol_inlet.value == data[
            "energy_electric_flow_vol_inlet"]["value"]


    @pytest.mark.component
    def test_degrees_of_freedom(self, model):
        assert degrees_of_freedom(model.fs.unit) == 0

    @pytest.mark.component
    def test_unit_consistency(self, model):
        assert_units_consistent(model.fs.unit)

    @pytest.mark.component
    def test_initialize(self, model):
        initialization_tester(model)

    @pytest.mark.solver
    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solve(self, model):
        results = solver.solve(model)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.solver
    @pytest.mark.skipif(solver is None, reason="Solver not available")
    @pytest.mark.component
    def test_solution(self, model):
        for t, j in model.fs.unit.inlet.flow_mass_comp:
            assert (pytest.approx(value(
                model.fs.unit.inlet.flow_mass_comp[t, j]), rel=1e-5) ==
                value(model.fs.unit.outlet.flow_mass_comp[t, j]))

    @pytest.mark.component
    def test_report(self, model):
        stream = StringIO()

        model.fs.unit.report(ostream=stream)

        output = """
====================================================================================
Unit : fs.unit                                                             Time: 0.0
------------------------------------------------------------------------------------
    Unit Performance

    Variables: 

    Key                   : Value  : Fixed : Bounds
       Electricity Demand : 0.0000 : False : (None, None)
    Electricity Intensity : 0.0000 :  True : (None, None)

------------------------------------------------------------------------------------
    Stream Table
                                   Inlet   Outlet 
    Volumetric Flowrate          0.049103 0.049103
    Mass Concentration H2O         855.34   855.34
    Mass Concentration calcium     61.096   61.096
    Mass Concentration magnesium   2.0365   2.0365
    Mass Concentration foo       0.061096 0.061096
    Mass Concentration sulfate     81.461   81.461
====================================================================================
"""

        assert output in stream.getvalue()
