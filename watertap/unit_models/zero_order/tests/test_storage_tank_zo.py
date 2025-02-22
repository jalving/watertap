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
Tests for zero-order storage tank model.
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

from watertap.unit_models.zero_order import StorageTankZO
from watertap.core.wt_database import Database
from watertap.core.zero_order_properties import WaterParameterBlock

solver = get_solver()


class TestStorageTankZO:
    @pytest.fixture(scope="class")
    def model(self):
        m = ConcreteModel()
        m.db = Database()

        m.fs = FlowsheetBlock(default={"dynamic": False})
        m.fs.params = WaterParameterBlock(default={"solute_list": ["toc", "tds", "eeq", "nitrate", "tss"]})
                                                                

        m.fs.unit = StorageTankZO(default={ "property_package": m.fs.params,
                                            "database": m.db})

        m.fs.unit.inlet.flow_mass_comp[0, "H2O"].fix(500)
        m.fs.unit.inlet.flow_mass_comp[0, "toc"].fix(3)
        m.fs.unit.inlet.flow_mass_comp[0, "tds"].fix(0.1)
        m.fs.unit.inlet.flow_mass_comp[0, "eeq"].fix(0.03)
        m.fs.unit.inlet.flow_mass_comp[0, "nitrate"].fix(4)
        m.fs.unit.inlet.flow_mass_comp[0, "tss"].fix(17)

        return m

    @pytest.mark.unit
    def test_build(self, model):
        assert model.fs.unit.config.database is model.db
        assert model.fs.unit._tech_type == "storage_tank"
        assert isinstance(model.fs.unit.storage_time, Var)
        assert isinstance(model.fs.unit.surge_capacity, Var)
        assert isinstance(model.fs.unit.tank_volume, Var)
        assert isinstance(model.fs.unit.tank_volume_constraint, Constraint)


    @pytest.mark.component
    def test_load_parameters(self, model):
        model.fs.unit.load_parameters_from_database()

        assert model.fs.unit.storage_time[0].fixed
        assert model.fs.unit.storage_time[0].value == 24

        assert model.fs.unit.surge_capacity[0].fixed
        assert model.fs.unit.surge_capacity[0].value == 0

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

        assert (pytest.approx(45284.831999, rel=1e-5) ==
                value(model.fs.unit.tank_volume[0]))

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

    Key                : Value  : Fixed : Bounds
     Storage Time (hr) : 24.000 :  True : (None, None)
    Surge Capacity (%) : 0.0000 :  True : (None, None)
      Tank Volume (m3) : 45285. : False : (None, None)

------------------------------------------------------------------------------------
    Stream Table
                                 Inlet   Outlet 
    Volumetric Flowrate         0.52413  0.52413
    Mass Concentration H2O       953.96   953.96
    Mass Concentration toc       5.7238   5.7238
    Mass Concentration tds      0.19079  0.19079
    Mass Concentration eeq     0.057238 0.057238
    Mass Concentration nitrate   7.6317   7.6317
    Mass Concentration tss       32.435   32.435
====================================================================================
"""

        assert output in stream.getvalue()
