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
Initial property package for multi-ionic system for use in the
Donnan Steric Pore Model with Dielectric Exclusion (DSPM-DE)
"""

# Import Python libraries
import idaes.logger as idaeslog

from enum import Enum, auto
# Import Pyomo libraries
from pyomo.environ import Constraint, Expression, Reals, NonNegativeReals, \
    Var, Param, Suffix, value, check_optimal_termination, units as pyunits, assert_optimal_termination
from pyomo.common.config import ConfigValue, In

# Import IDAES cores
from idaes.core import (declare_process_block_class,
                        MaterialFlowBasis,
                        PhysicalParameterBlock,
                        StateBlockData,
                        StateBlock,
                        MaterialBalanceType,
                        EnergyBalanceType)
from idaes.core.components import Component, Solute, Solvent
from idaes.core.phases import LiquidPhase
from idaes.core.util.constants import Constants
from idaes.core.util.initialization import (fix_state_vars,
                                            revert_state_vars,
                                            solve_indexed_blocks)
from idaes.core.util.misc import add_object_reference, extract_data
from idaes.core.util import get_solver
from idaes.core.util.model_statistics import degrees_of_freedom, \
    number_unfixed_variables
from idaes.core.util.exceptions import ConfigurationError, InitializationError
import idaes.core.util.scaling as iscale

# Set up logger
_log = idaeslog.getLogger(__name__)


class ActivityCoefficientModel(Enum):
    ideal = auto()                    # Ideal
    davies = auto()                   # Davies


@declare_process_block_class("DSPMDEParameterBlock")
class DSPMDEParameterData(PhysicalParameterBlock):
    CONFIG = PhysicalParameterBlock.CONFIG()

    CONFIG.declare("solute_list", ConfigValue(
        domain=list,
        description="List of solute species names"))
    CONFIG.declare("stokes_radius_data", ConfigValue(
        default={},
        domain=dict,
        description="Dict of solute species names and Stokes radius data"))
    CONFIG.declare("diffusivity_data", ConfigValue(
        default={},
        domain=dict,
        description="Dict of solute species names and bulk ion diffusivity data"))
    CONFIG.declare("mw_data", ConfigValue(
        default={},
        domain=dict,
        description="Dict of component names and molecular weight data"))
    CONFIG.declare("charge", ConfigValue(
        default={},
        domain=dict,
        description="Ion charge"))
    CONFIG.declare("activity_coefficient_model", ConfigValue(
        default=ActivityCoefficientModel.ideal,
        domain=In(ActivityCoefficientModel),
        description="Activity coefficient model construction flag",
        doc="""
           Options to account for activity coefficient model.
    
           **default** - ``ActivityCoefficientModel.ideal``
    
       .. csv-table::
           :header: "Configuration Options", "Description"
    
           "``ActivityCoefficientModel.ideal``", "Activity coefficients equal to 1 assuming ideal solution"
           "``ActivityCoefficientModel.davies``", "Activity coefficients estimated via Davies model"
       """))

    def build(self):
        '''
        Callable method for Block construction.
        '''
        super(DSPMDEParameterData, self).build()

        self._state_block_class = DSPMDEStateBlock

        # components
        self.H2O = Solvent()

        for j in self.config.solute_list:
            self.add_component(str(j), Solute())

        # phases
        self.Liq = LiquidPhase()

        # reference
        # Todo: enter any relevant references

        # TODO: consider turning parameters into variables for future param estimation
        # molecular weight
        self.mw_comp = Param(
            self.component_list,
            mutable=True,
            default=18e-3,
            initialize=self.config.mw_data,
            units=pyunits.kg/pyunits.mol,
            doc="Molecular weight")
        # Stokes radius
        self.radius_stokes_comp = Param(
            self.solute_set,
            mutable=True,
            default=1e-10,
            initialize=self.config.stokes_radius_data,
            units=pyunits.m,
            doc="Stokes radius of solute")
        self.diffus_phase_comp = Param(
            self.phase_list,
            self.solute_set,
            mutable=True,
            default=1e-9,
            initialize=self.config.diffusivity_data,
            units=pyunits.m ** 2 * pyunits.s ** -1,
            doc="Bulk diffusivity of ion")
        self.visc_d_phase = Param(
            self.phase_list,
            mutable=True,
            default=1e-3,
            initialize=1e-3, #TODO:revisit- assuming ~ 1e-3 Pa*s for pure water
            units=pyunits.Pa * pyunits.s,
            doc="Fluid viscosity")
        # Ion charge
        self.charge_comp = Param(
            self.solute_set,
            mutable=True,
            default=1,
            initialize=self.config.charge,
            units=pyunits.dimensionless,
            doc="Ion charge")
        # Dielectric constant of water
        self.dielectric_constant = Param(
            mutable=True,
            default=80.4,
            initialize=80.4, #todo: make a variable with parameter values for coefficients in the function of temperature
            units=pyunits.dimensionless,
            doc="Dielectric constant of water")


        # ---default scaling---
        self.set_default_scaling('temperature', 1e-2)
        self.set_default_scaling('pressure', 1e-6)
        self.set_default_scaling('dens_mass_phase', 1e-3, index='Liq')
        self.set_default_scaling('visc_d_phase', 1e3, index='Liq')


    @classmethod
    def define_metadata(cls, obj):
        """Define properties supported and units."""
        obj.add_properties(
            {'flow_mol_phase_comp': {'method': None},
             'temperature': {'method': None},
             'pressure': {'method': None},
             'flow_mass_phase_comp': {'method': '_flow_mass_phase_comp'},
             'mass_frac_phase_comp': {'method': '_mass_frac_phase_comp'},
             'dens_mass_phase': {'method': '_dens_mass_phase'},
             'flow_vol_phase': {'method': '_flow_vol_phase'},
             'flow_vol': {'method': '_flow_vol'},
             'conc_mol_phase_comp': {'method': '_conc_mol_phase_comp'},
             'conc_mass_phase_comp': {'method': '_conc_mass_phase_comp'},
             'mole_frac_phase_comp': {'method': '_mole_frac_phase_comp'},
             'molality_comp': {'method': '_molality_comp'},
             'diffus_phase_comp': {'method': '_diffus_phase_comp'},
             'visc_d_phase': {'method': '_visc_d_phase'},
             'pressure_osm': {'method': '_pressure_osm'},
             'radius_stokes_comp': {'method': '_radius_stokes_comp'},
             'mw_comp': {'method': '_mw_comp'},
             'charge_comp': {'method': '_charge_comp'},
             'act_coeff_phase_comp': {'method': '_act_coeff_phase_comp'},
             'dielectric_constant': {'method': '_dielectric_constant'}
             })

        obj.add_default_units({'time': pyunits.s,
                               'length': pyunits.m,
                               'mass': pyunits.kg,
                               'amount': pyunits.mol,
                               'temperature': pyunits.K})


class _DSPMDEStateBlock(StateBlock):
    """
    This Class contains methods which should be applied to Property Blocks as a
    whole, rather than individual elements of indexed Property Blocks.
    """

    def initialize(self, state_args=None, state_vars_fixed=False,
                   hold_state=False, outlvl=idaeslog.NOTSET,
                   solver=None, optarg=None):
        """
        Initialization routine for property package.
        Keyword Arguments:
            state_args : Dictionary with initial guesses for the state vars
                         chosen. Note that if this method is triggered
                         through the control volume, and if initial guesses
                         were not provided at the unit model level, the
                         control volume passes the inlet values as initial
                         guess.The keys for the state_args dictionary are:

                         flow_mol_phase_comp : value at which to initialize
                                               phase component flows
                         pressure : value at which to initialize pressure
                         temperature : value at which to initialize temperature
            outlvl : sets output level of initialization routine (default=idaeslog.NOTSET)
            optarg : solver options dictionary object (default=None)
            state_vars_fixed: Flag to denote if state vars have already been
                              fixed.
                              - True - states have already been fixed by the
                                       control volume 1D. Control volume 0D
                                       does not fix the state vars, so will
                                       be False if this state block is used
                                       with 0D blocks.
                             - False - states have not been fixed. The state
                                       block will deal with fixing/unfixing.
            solver : Solver object to use during initialization if None is provided
                     it will use the default solver for IDAES (default = None)
            hold_state : flag indicating whether the initialization routine
                         should unfix any state variables fixed during
                         initialization (default=False).
                         - True - states variables are not unfixed, and
                                 a dict of returned containing flags for
                                 which states were fixed during
                                 initialization.
                        - False - state variables are unfixed after
                                 initialization by calling the
                                 release_state method
        Returns:
            If hold_states is True, returns a dict containing flags for
            which states were fixed during initialization.
        """
        # Get loggers
        init_log = idaeslog.getInitLogger(self.name, outlvl, tag="properties")
        solve_log = idaeslog.getSolveLogger(self.name, outlvl, tag="properties")

        # Set solver and options
        opt = get_solver(solver, optarg)

        # Fix state variables
        flags = fix_state_vars(self, state_args)
        # Check when the state vars are fixed already result in dof 0
        for k in self.keys():
            dof = degrees_of_freedom(self[k])
            if dof != 0:
                raise InitializationError("\nWhile initializing {sb_name}, the degrees of freedom "
                                           "are {dof}, when zero is required. \nInitialization assumes "
                                           "that the state variables should be fixed and that no other "
                                           "variables are fixed. \nIf other properties have a "
                                           "predetermined value, use the calculate_state method "
                                           "before using initialize to determine the values for "
                                           "the state variables and avoid fixing the property variables."
                                           "".format(sb_name=self.name, dof=dof))

        # ---------------------------------------------------------------------
        skip_solve = True  # skip solve if only state variables are present
        for k in self.keys():
            if number_unfixed_variables(self[k]) != 0:
                skip_solve = False

        if not skip_solve:
            # Initialize properties
            with idaeslog.solver_log(solve_log, idaeslog.DEBUG) as slc:
                results = solve_indexed_blocks(opt, [self], tee=slc.tee)
                if not check_optimal_termination(results):
                    raise InitializationError('The property package failed to solve during initialization.')
            init_log.info_high("Property initialization: {}.".format(idaeslog.condition(results)))

        # ---------------------------------------------------------------------
        # If input block, return flags, else release state
        if state_vars_fixed is False:
            if hold_state is True:
                return flags
            else:
                self.release_state(flags)

    def release_state(self, flags, outlvl=idaeslog.NOTSET):
        '''
        Method to release state variables fixed during initialisation.

        Keyword Arguments:
            flags : dict containing information of which state variables
                    were fixed during initialization, and should now be
                    unfixed. This dict is returned by initialize if
                    hold_state=True.
            outlvl : sets output level of of logging
        '''
        # Unfix state variables
        init_log = idaeslog.getInitLogger(self.name, outlvl, tag="properties")
        revert_state_vars(self, flags)
        init_log.info_high('{} State Released.'.format(self.name))

    def calculate_state(self, var_args=None, hold_state=False, outlvl=idaeslog.NOTSET,
                        solver=None, optarg=None):
        """
        Solves state blocks given a set of variables and their values. These variables can
        be state variables or properties. This method is typically used before
        initialization to solve for state variables because non-state variables (i.e. properties)
        cannot be fixed in initialization routines.

        Keyword Arguments:
            var_args : dictionary with variables and their values, they can be state variables or properties
                       {(VAR_NAME, INDEX): VALUE}
            hold_state : flag indicating whether all of the state variables should be fixed after calculate state.
                         True - State variables will be fixed.
                         False - State variables will remain unfixed, unless already fixed.
            outlvl : idaes logger object that sets output level of solve call (default=idaeslog.NOTSET)
            solver : solver name string if None is provided the default solver
                     for IDAES will be used (default = None)
            optarg : solver options dictionary object (default={})

        Returns:
            results object from state block solve
        """
        # Get logger
        solve_log = idaeslog.getSolveLogger(self.name, level=outlvl, tag="properties")

        # Initialize at current state values (not user provided)
        self.initialize(solver=solver, optarg=optarg, outlvl=outlvl)

        # Set solver and options
        opt = get_solver(solver, optarg)

        # Fix variables and check degrees of freedom
        flags = {}  # dictionary noting which variables were fixed and their previous state
        for k in self.keys():
            sb = self[k]
            for (v_name, ind), val in var_args.items():
                var = getattr(sb, v_name)
                if iscale.get_scaling_factor(var[ind]) is None:
                    _log.warning(
                            "While using the calculate_state method on {sb_name}, variable {v_name} "
                            "was provided as an argument in var_args, but it does not have a scaling "
                            "factor. This suggests that the calculate_scaling_factor method has not been "
                            "used or the variable was created on demand after the scaling factors were "
                            "calculated. It is recommended to touch all relevant variables (i.e. call "
                            "them or set an initial value) before using the calculate_scaling_factor "
                            "method.".format(v_name=v_name, sb_name=sb.name))
                if var[ind].is_fixed():
                    flags[(k, v_name, ind)] = True
                    if value(var[ind]) != val:
                        raise ConfigurationError(
                            "While using the calculate_state method on {sb_name}, {v_name} was "
                            "fixed to a value {val}, but it was already fixed to value {val_2}. "
                            "Unfix the variable before calling the calculate_state "
                            "method or update var_args."
                            "".format(sb_name=sb.name, v_name=var.name, val=val, val_2=value(var[ind])))
                else:
                    flags[(k, v_name, ind)] = False
                    var[ind].fix(val)

            if degrees_of_freedom(sb) != 0:
                raise RuntimeError("While using the calculate_state method on {sb_name}, the degrees "
                                   "of freedom were {dof}, but 0 is required. Check var_args and ensure "
                                   "the correct fixed variables are provided."
                                   "".format(sb_name=sb.name, dof=degrees_of_freedom(sb)))

        # Solve
        with idaeslog.solver_log(solve_log, idaeslog.DEBUG) as slc:
            results = solve_indexed_blocks(opt, [self], tee=slc.tee)
            solve_log.info_high("Calculate state: {}.".format(idaeslog.condition(results)))

        if not check_optimal_termination(results):
            _log.warning("While using the calculate_state method on {sb_name}, the solver failed "
                         "to converge to an optimal solution. This suggests that the user provided "
                         "infeasible inputs, or that the model is poorly scaled, poorly initialized, "
                         "or degenerate.")

        # unfix all variables fixed with var_args
        for (k, v_name, ind), previously_fixed in flags.items():
            if not previously_fixed:
                var = getattr(self[k], v_name)
                var[ind].unfix()

        # fix state variables if hold_state
        if hold_state:
            fix_state_vars(self)

        return results

@declare_process_block_class("DSPMDEStateBlock",
                             block_class=_DSPMDEStateBlock)
class DSPMDEStateBlockData(StateBlockData):
    def build(self):
        """Callable method for Block construction."""
        super().build()

        self.scaling_factor = Suffix(direction=Suffix.EXPORT)

        # Add state variables
        self.flow_mol_phase_comp = Var(
            self.params.phase_list,
            self.params.component_list,
            initialize=100, #todo: revisit
            bounds=(1e-8, None),
            domain=NonNegativeReals,
            units=pyunits.mol/pyunits.s,
            doc='Mole flow rate')

        self.temperature = Var(
            initialize=298.15,
            bounds=(273.15, 373.15),
            domain=NonNegativeReals,
            units=pyunits.K,
            doc='State temperature')

        self.pressure = Var(
            initialize=101325,
            bounds=(1e5, 5e7),
            domain=NonNegativeReals,
            units=pyunits.Pa,
            doc='State pressure')

    # -----------------------------------------------------------------------------
    # Property Methods
    def _mass_frac_phase_comp(self):
        self.mass_frac_phase_comp = Var(
            self.params.phase_list,
            self.params.component_list,
            initialize=lambda b,p,j : 0.4037 if j == "H2O" else 0.0033, #todo: revisit
            bounds=(1e-6, 1.001),
            units=pyunits.kg/pyunits.kg,
            doc='Mass fraction')

        def rule_mass_frac_phase_comp(b, j):
            return (b.mass_frac_phase_comp['Liq', j] == b.flow_mass_phase_comp['Liq', j] /
                    sum(b.flow_mass_phase_comp['Liq', j]
                        for j in self.params.component_list))
        self.eq_mass_frac_phase_comp = Constraint(self.params.component_list, rule=rule_mass_frac_phase_comp)

    def _dens_mass_phase(self):
        self.dens_mass_phase = Var(
            ['Liq'],
            initialize=1e3,
            bounds=(5e2, 2e3),
            units=pyunits.kg * pyunits.m ** -3,
            doc="Mass density")
        #TODO: reconsider this approach for solution density based on arbitrary solute_list
        def rule_dens_mass_phase(b):
            return (b.dens_mass_phase['Liq'] == 1000 * pyunits.kg * pyunits.m**-3)
        self.eq_dens_mass_phase = Constraint(rule=rule_dens_mass_phase)

    def _flow_vol_phase(self):
        self.flow_vol_phase = Var(
            self.params.phase_list,
            initialize=1,
            bounds=(1e-8, None),
            units=pyunits.m ** 3 / pyunits.s,
            doc="Volumetric flow rate")

        def rule_flow_vol_phase(b):
            return (b.flow_vol_phase['Liq']
                    == sum(b.flow_mol_phase_comp['Liq', j]*b.mw_comp[j] for j in self.params.component_list)
                    / b.dens_mass_phase['Liq'])
        self.eq_flow_vol_phase = Constraint(rule=rule_flow_vol_phase)

    def _flow_vol(self):

        def rule_flow_vol(b):
            return sum(b.flow_vol_phase[p] for p in self.params.phase_list)
        self.flow_vol = Expression(rule=rule_flow_vol)

    def _conc_mol_phase_comp(self):
        self.conc_mol_phase_comp = Var(
            self.params.phase_list,
            self.params.component_list,
            initialize=10,
            bounds=(1e-6, None),
            units=pyunits.mol * pyunits.m ** -3,
            doc="Molar concentration")

        def rule_conc_mol_phase_comp(b, j):
            return (b.conc_mol_phase_comp['Liq', j] * b.params.mw_comp[j] ==
                    b.conc_mass_phase_comp['Liq', j])
        self.eq_conc_mol_phase_comp = Constraint(self.params.component_list, rule=rule_conc_mol_phase_comp)

    def _conc_mass_phase_comp(self):
        self.conc_mass_phase_comp = Var(
            self.params.phase_list,
            self.params.component_list,
            initialize=10,
            bounds=(1e-3, 2e3),
            units=pyunits.kg * pyunits.m ** -3,
            doc="Mass concentration")

        def rule_conc_mass_phase_comp(b, j):
            return (b.conc_mass_phase_comp['Liq', j] ==
                    b.dens_mass_phase['Liq'] * b.mass_frac_phase_comp['Liq', j])
        self.eq_conc_mass_phase_comp = Constraint(self.params.component_list, rule=rule_conc_mass_phase_comp)

    def _flow_mass_phase_comp(self):
        self.flow_mass_phase_comp = Var(
            self.params.phase_list,
            self.params.component_list,
            initialize=100,
            bounds=(1e-8, None),
            units=pyunits.kg / pyunits.s,
            doc="Component Mass flowrate")

        def rule_flow_mass_phase_comp(b, j):
            return (b.flow_mass_phase_comp['Liq', j] ==
                    b.flow_mol_phase_comp['Liq', j] * b.params.mw_comp[j])
        self.eq_flow_mass_phase_comp = Constraint(self.params.component_list, rule=rule_flow_mass_phase_comp)

    def _mole_frac_phase_comp(self):
        self.mole_frac_phase_comp = Var(
            self.params.phase_list,
            self.params.component_list,
            initialize=0.1,
            bounds=(1e-6, 1.001),
            units=pyunits.dimensionless,
            doc="Mole fraction")

        def rule_mole_frac_phase_comp(b, j):
            return (b.mole_frac_phase_comp['Liq', j] == b.flow_mol_phase_comp['Liq', j] /
                    sum(b.flow_mol_phase_comp['Liq', j] for j in b.params.component_list))
        self.eq_mole_frac_phase_comp = Constraint(self.params.component_list, rule=rule_mole_frac_phase_comp)

    def _molality_comp(self):
        self.molality_comp = Var(
            self.params.solute_set,
            initialize=1,
            bounds=(1e-4, 10),
            units=pyunits.mole / pyunits.kg,
            doc="Molality")

        def rule_molality_comp(b, j):
            return (b.molality_comp[j] ==
                    b.flow_mol_phase_comp['Liq', j]
                    / b.flow_mol_phase_comp['Liq', 'H2O']
                    / b.params.mw_comp['H2O'])

        self.eq_molality_comp = Constraint(self.params.solute_set, rule=rule_molality_comp)

    def _radius_stokes_comp(self):
        add_object_reference(self, "radius_stokes_comp", self.params.radius_stokes_comp)

    def _diffus_phase_comp(self):
        add_object_reference(self, "diffus_phase_comp", self.params.diffus_phase_comp)

    def _visc_d_phase(self):
        add_object_reference(self, "visc_d_phase", self.params.visc_d_phase)

    def _mw_comp(self):
        add_object_reference(self, "mw_comp", self.params.mw_comp)

    def _charge_comp(self):
        add_object_reference(self, "charge_comp", self.params.charge_comp)

    def _dielectric_constant(self):
        add_object_reference(self, "dielectric_constant", self.params.dielectric_constant)

    def _act_coeff_phase_comp(self):
        self.act_coeff_phase_comp = Var(
            self.phase_list,
            self.params.solute_set,
            initialize=1,
            bounds=(1e-4, 1.001),
            units=pyunits.dimensionless,
            doc="activity coefficient of component")

        def rule_act_coeff_phase_comp(b, j):
            if b.params.config.activity_coefficient_model == ActivityCoefficientModel.ideal:
                return b.act_coeff_phase_comp['Liq', j] == 1
            elif b.params.config.activity_coefficient_model == ActivityCoefficientModel.davies:
                raise NotImplementedError(f"Davies model has not been implemented yet.")
        self.eq_act_coeff_phase_comp = Constraint(self.params.solute_set,
                                                  rule=rule_act_coeff_phase_comp)

    #TODO: change osmotic pressure calc
    def _pressure_osm(self):
        self.pressure_osm = Var(
            initialize=1e6,
            bounds=(5e2, 5e7),
            units=pyunits.Pa,
            doc="van't Hoff Osmotic pressure")

        def rule_pressure_osm(b):
            return (b.pressure_osm ==
                    sum(b.conc_mol_phase_comp['Liq', j] for j in self.params.solute_set)
                    * Constants.gas_constant * b.temperature)
        self.eq_pressure_osm = Constraint(rule=rule_pressure_osm)

    # -----------------------------------------------------------------------------
    # General Methods
    # NOTE: For scaling in the control volume to work properly, these methods must
    # return a pyomo Var or Expression

    def get_material_flow_terms(self, p, j):
        """Create material flow terms for control volume."""
        return self.flow_mol_phase_comp[p, j]

    # TODO: add enthalpy terms later
    # def get_enthalpy_flow_terms(self, p):
    #     """Create enthalpy flow terms."""
    #     return self.enth_flow

    # TODO: make property package compatible with dynamics
    # def get_material_density_terms(self, p, j):
    #     """Create material density terms."""

    # def get_enthalpy_density_terms(self, p):
    #     """Create enthalpy density terms."""

    def default_material_balance_type(self):
        return MaterialBalanceType.componentTotal

    # TODO: augment model with energybalance later
    # def default_energy_balance_type(self):
    #     return EnergyBalanceType.enthalpyTotal

    def get_material_flow_basis(self):
        return MaterialFlowBasis.molar

    def define_state_vars(self):
        """Define state vars."""
        return {"flow_mol_phase_comp": self.flow_mol_phase_comp,
                "temperature": self.temperature,
                "pressure": self.pressure}

    def assert_electroneutrality(self, tol=None, tee=False):
        if tol is None:
            tol = 1e-6
            for j in self.params.solute_set:
                if not self.flow_mol_phase_comp['Liq', j].is_fixed():
                    raise AssertionError(
                        f"{self.flow_mol_phase_comp['Liq', j]} was not fixed. Fix flow_mol_phase_comp for each solute"
                        f" to check that electroneutrality is satisfied.")
        val = value(sum(self.charge_comp[j] * self.flow_mol_phase_comp['Liq', j]
                    for j in self.params.solute_set))
        if abs(val) <= tol:
            if tee:
                return print('Electroneutrality satisfied')
        else:
            raise AssertionError(f"Electroneutrality condition violated. Ion concentrations should be adjusted to bring "
                                 f"the result of {val} closer towards 0.")

    # -----------------------------------------------------------------------------
    # Scaling methods
    def calculate_scaling_factors(self):
        super().calculate_scaling_factors()

        # setting scaling factors for variables

        # default scaling factors have already been set with
        # idaes.core.property_base.calculate_scaling_factors()
        # for the following variables: pressure,
        # temperature, dens_mass, visc_d_phase, diffus_phase_comp

        # these variables should have user input
        if iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', 'H2O']) is None:
            sf = iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', 'H2O'], default=1, warning=True)
            iscale.set_scaling_factor(self.flow_mol_phase_comp['Liq', 'H2O'], sf)

        for j in self.params.solute_set:
            if iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', j]) is None:
                sf = iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', j], default=1, warning=True)
                iscale.set_scaling_factor(self.flow_mol_phase_comp['Liq', j], sf)

        # scaling factors for parameters
        for j, v in self.mw_comp.items():
            if iscale.get_scaling_factor(v) is None:
                iscale.set_scaling_factor(self.mw_comp[j], 1e1)
        for ind, v in self.diffus_phase_comp.items():
            if iscale.get_scaling_factor(v) is None:
                iscale.set_scaling_factor(self.diffus_phase_comp[ind], 1e10)

        for p, v in self.dens_mass_phase.items():
            if iscale.get_scaling_factor(v) is None:
                iscale.set_scaling_factor(self.dens_mass_phase[p], 1e-2)
        for p, v in self.visc_d_phase.items():
            if iscale.get_scaling_factor(v) is None:
                iscale.set_scaling_factor(self.visc_d_phase[p], 1e3)

        if self.is_property_constructed('mole_frac_phase_comp'):
            for j in self.params.component_list:
                if iscale.get_scaling_factor(self.mole_frac_phase_comp['Liq', j]) is None:
                    if j == 'H2O':
                        iscale.set_scaling_factor(self.mole_frac_phase_comp['Liq', j], 1)
                    else:
                        sf = (iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', j])
                              / iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', 'H2O']))
                        iscale.set_scaling_factor(self.mole_frac_phase_comp['Liq', j], sf)

        if self.is_property_constructed('conc_mol_phase_comp'):
            for j in self.params.component_list:
                if iscale.get_scaling_factor(self.conc_mol_phase_comp['Liq', j]) is None:
                    sf_dens = iscale.get_scaling_factor(self.dens_mass_phase['Liq'])
                    sf = (sf_dens * iscale.get_scaling_factor(self.mole_frac_phase_comp['Liq', j], default=1)
                          / iscale.get_scaling_factor(self.mw_comp[j]))
                    iscale.set_scaling_factor(self.conc_mol_phase_comp['Liq', j], sf)

        if self.is_property_constructed('flow_mass_phase_comp'):
            for j in self.params.component_list:
                if iscale.get_scaling_factor(self.flow_mass_phase_comp['Liq', j]) is None:
                    sf = iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', j], default=1)
                    sf *= iscale.get_scaling_factor(self.mw_comp[j])
                    iscale.set_scaling_factor(self.flow_mass_phase_comp['Liq', j], sf)

        # these variables do not typically require user input,
        # will not override if the user does provide the scaling factor
        if self.is_property_constructed('pressure_osm'):
            if iscale.get_scaling_factor(self.pressure_osm) is None:
                sf = iscale.get_scaling_factor(self.pressure)
                iscale.set_scaling_factor(self.pressure_osm, sf)

        if self.is_property_constructed('mass_frac_phase_comp'):
            for j in self.params.component_list:
                comp = self.params.get_component(j)
                if iscale.get_scaling_factor(self.mass_frac_phase_comp['Liq', j]) is None:
                    if comp.is_solute():
                        sf = (iscale.get_scaling_factor(self.flow_mass_phase_comp['Liq', j], default=1)
                              / iscale.get_scaling_factor(self.flow_mass_phase_comp['Liq', 'H2O'], default=1))
                        iscale.set_scaling_factor(self.mass_frac_phase_comp['Liq', j], sf)
                    elif comp.is_solvent():
                        iscale.set_scaling_factor(self.mass_frac_phase_comp['Liq', j], 100)
                    else:
                        raise TypeError(f'comp={comp}, j = {j}')

        if self.is_property_constructed('flow_vol_phase'):
            sf = (iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', 'H2O'], default=1)
                  * iscale.get_scaling_factor(self.mw_comp[j])
                  / iscale.get_scaling_factor(self.dens_mass_phase['Liq']))
            iscale.set_scaling_factor(self.flow_vol_phase, sf)

        if self.is_property_constructed('flow_vol'):
            sf = iscale.get_scaling_factor(self.flow_vol_phase)
            iscale.set_scaling_factor(self.flow_vol, sf)

        if self.is_property_constructed('conc_mass_phase_comp'):
            for j in self.params.component_list:
                sf_dens = iscale.get_scaling_factor(self.dens_mass_phase['Liq'])
                if iscale.get_scaling_factor(self.conc_mass_phase_comp['Liq', j]) is None:
                    if j == 'H2O':
                        # solvents typically have a mass fraction between 0.5-1
                        iscale.set_scaling_factor(self.conc_mass_phase_comp['Liq', j], sf_dens)
                    else:
                        iscale.set_scaling_factor(
                            self.conc_mass_phase_comp['Liq', j],
                            sf_dens * iscale.get_scaling_factor(self.mass_frac_phase_comp['Liq', j],default=1,warning=True))


        if self.is_property_constructed('molality_comp'):
            for j in self.params.solute_set:
                if iscale.get_scaling_factor(self.molality_comp[j]) is None:
                    sf = (iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', j])
                          / iscale.get_scaling_factor(self.flow_mol_phase_comp['Liq', 'H2O'])
                          / iscale.get_scaling_factor(self.mw_comp[j]))
                    iscale.set_scaling_factor(self.molality_comp[j], sf)

        if self.is_property_constructed('act_coeff_phase_comp'):
            for j in self.params.solute_set:
                if iscale.get_scaling_factor(self.act_coeff_phase_comp['Liq', j]) is None:
                    iscale.set_scaling_factor(self.act_coeff_phase_comp['Liq', j], 1)

        # transforming constraints
        # property relationships with no index, simple constraint
        if self.is_property_constructed('pressure_osm'):
            sf = iscale.get_scaling_factor(self.pressure_osm, default=1, warning=True)
            iscale.constraint_scaling_transform(self.eq_pressure_osm, sf)

        # # property relationships with phase index, but simple constraint
        for v_str in ('flow_vol_phase', 'dens_mass_phase'):
            if self.is_property_constructed(v_str):
                v = getattr(self, v_str)
                sf = iscale.get_scaling_factor(v['Liq'], default=1, warning=True)
                c = getattr(self, 'eq_' + v_str)
                iscale.constraint_scaling_transform(c, sf)

        # property relationship indexed by component
        v_str_lst_comp = ['molality_comp']
        for v_str in v_str_lst_comp:
            if self.is_property_constructed(v_str):
                v_comp = getattr(self, v_str)
                c_comp = getattr(self, 'eq_' + v_str)
                for j, c in c_comp.items():
                    sf = iscale.get_scaling_factor(v_comp[j], default=1, warning=True)
                    iscale.constraint_scaling_transform(c, sf)

        # property relationships indexed by component and phase
        v_str_lst_phase_comp = ['mass_frac_phase_comp', 'conc_mass_phase_comp', 'flow_mass_phase_comp',
                                'mole_frac_phase_comp', 'conc_mol_phase_comp', 'act_coeff_phase_comp']
        for v_str in v_str_lst_phase_comp:
            if self.is_property_constructed(v_str):
                v_comp = getattr(self, v_str)
                c_comp = getattr(self, 'eq_' + v_str)
                for j, c in c_comp.items():
                    sf = iscale.get_scaling_factor(v_comp['Liq', j], default=1, warning=True)
                    iscale.constraint_scaling_transform(c, sf)
