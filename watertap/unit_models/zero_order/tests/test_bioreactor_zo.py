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
Tests for zero-order bioreactor model
"""
import pytest
from io import StringIO

from pyomo.environ import (
    check_optimal_termination, ConcreteModel, Constraint, value, Var, Param)
from pyomo.util.check_units import assert_units_consistent

from idaes.core import FlowsheetBlock
from idaes.core.util import get_solver
from idaes.core.util.model_statistics import degrees_of_freedom
from idaes.core.util.testing import initialization_tester

from watertap.unit_models.zero_order import BioreactorZO
from watertap.core.wt_database import Database
from watertap.core.zero_order_properties import WaterParameterBlock

solver = get_solver()


class TestBioreactorZO:
    @pytest.fixture(scope="class")
    def model(self):
        m = ConcreteModel()
        m.db = Database()

        m.fs = FlowsheetBlock(default={"dynamic": False})
        m.fs.params = WaterParameterBlock(
            default={"solute_list": ["boron", "selenium", "foo"]})

        m.fs.unit = BioreactorZO(default={
            "property_package": m.fs.params,
            "database": m.db})

        m.fs.unit.inlet.flow_mass_comp[0, "H2O"].fix(10000)
        m.fs.unit.inlet.flow_mass_comp[0, "boron"].fix(1)
        m.fs.unit.inlet.flow_mass_comp[0, "selenium"].fix(1)
        m.fs.unit.inlet.flow_mass_comp[0, "foo"].fix(1)

        return m

    @pytest.mark.unit
    def test_build(self, model):
        assert model.fs.unit.config.database == model.db

        assert isinstance(model.fs.unit.electricity, Var)
        assert isinstance(model.fs.unit.energy_electric_flow_vol_inlet, Var)

        assert isinstance(model.fs.unit.electricity_consumption, Constraint)

    @pytest.mark.component
    def test_load_parameters(self, model):
        data = model.db.get_unit_operation_parameters("bioreactor")

        model.fs.unit.load_parameters_from_database(use_default_removal=True)

        assert model.fs.unit.recovery_frac_mass_H2O[0].fixed
        assert model.fs.unit.recovery_frac_mass_H2O[0].value == \
            data["recovery_frac_mass_H2O"]["value"]

        for (t, j), v in model.fs.unit.removal_frac_mass_solute.items():
            assert v.fixed
            if j == "foo":
                assert v.value == data["default_removal_frac_mass_solute"]["value"]
            else:
                assert v.value == data["removal_frac_mass_solute"][j]["value"]

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
        assert (pytest.approx(9.7002, rel=1e-3) ==
                value(model.fs.unit.properties_treated[0].flow_vol))
        assert (pytest.approx(2.0618e-2, rel=1e-3) == value(
            model.fs.unit.properties_treated[0].conc_mass_comp["boron"]))
        assert (pytest.approx(3.608e-3, rel=1e-3) == value(
            model.fs.unit.properties_treated[0].conc_mass_comp["selenium"]))
        assert (pytest.approx(1.0308e-1, rel=1e-3) == value(
            model.fs.unit.properties_treated[0].conc_mass_comp["foo"]))
        assert (pytest.approx(0, abs=1e-8) == value(model.fs.unit.electricity[0]))

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

    Key                       : Value   : Fixed : Bounds
           Electricity Demand :  0.0000 : False : (None, None)
        Electricity Intensity :  0.0000 :  True : (None, None)
       Solute Removal [boron] : 0.80000 :  True : (0, None)
         Solute Removal [foo] :  0.0000 :  True : (0, None)
    Solute Removal [selenium] : 0.96500 :  True : (0, None)

------------------------------------------------------------------------------------
    Stream Table
                                  Inlet    Treated
    Volumetric Flowrate           10.003    9.7012
    Mass Concentration H2O        999.70    999.87
    Mass Concentration boron    0.099970  0.020616
    Mass Concentration selenium 0.099970 0.0036078
    Mass Concentration foo      0.099970   0.10308
====================================================================================
"""
        assert output in stream.getvalue()

