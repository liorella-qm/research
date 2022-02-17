from math import exp, sqrt
from typing import Tuple, Callable
import numpy as np

import stim

from qec_generator import CircuitParams
from stim_lib.error_context import StimErrorContext
from stim_lib.run_feedback import measure_instructions

inst_with_duration = {'CX', 'H', 'MR', 'M', 'R', 'RZ'}


def get_pauli_probs(duration: float, t1: float, t2: float) -> Tuple[float, float, float]:
    """
    calculate the Pauli depolarization probabilities
    according to the twirl approximation in eq. 10 in https://arxiv.org/abs/1210.5799
    :param duration: duration of the instruction
    :param t1:
    :param t2:
    :return: (p_x, p_y, p_z)
    """
    return (1 - exp(-duration / t1)) / 4, (1 - exp(-duration / t1)) / 4, (1 - exp(-duration / t2)) / 2 - (
            1 - exp(-duration / t1)) / 4


def get_dephasing_probs(duration: float, t2: float) -> Tuple [float, float, float]:
    # p_I, p_X, p_Y, p_Z
    #0.5*(2 - exp(-duration/(2*t2))), 0.0, 0.0, 0.5*(exp(-duration/(2*t2)))
    return 0, 0, 0.5*exp(-duration/(2*t2))

def get_amplitude_damping_probs(duration: float, t1: float) -> Tuple [float, float, float]:
    """
    Calculates the quasi probabilities of I, Z, RZ channels for amplitude damping
    see Example 2 in https://arxiv.org/pdf/1703.00111.pdf 
    """
    gamma = 1 - exp(-duration/t1)
    p_I = ((1 - gamma) + sqrt(1 - gamma))/2.0
    p_Z = ((1 - gamma) - sqrt(1 - gamma))/2.0
    p_RZ = gamma
    return p_I, p_Z, p_RZ

def instruction_duration(inst: stim.CircuitInstruction, params: CircuitParams) -> float:
    if inst.name not in inst_with_duration:
        ValueError(f'only duration of {inst_with_duration} is known')
    if inst.name == 'H':
        return params.single_qubit_gate_duration
    if inst.name == 'CX':
        return params.two_qubit_gate_duration
    if inst.name == 'MR':
        return params.reset_duration + params.reset_latency + params.meas_duration
    if inst.name == 'M':
        return params.meas_duration
    if inst.name == 'R' or inst.name == 'RZ':
        return params.reset_latency + params.reset_duration


def _updater(circ: stim.Circuit, inst_handler: Callable, weight=1.0):
    w = weight
    new_circ = stim.Circuit()
    for inst in circ:
        inst: stim.CircuitInstruction
        if isinstance(inst, stim.CircuitRepeatBlock):
            for _ in range(inst.repeat_count):
                new_circ += _updater(inst.body_copy(), inst_handler, w)[0]
        else:
            w = inst_handler(inst, new_circ, w)
    return new_circ, w


def generate_scheduled(code_task: str,
                       distance: int,
                       rounds: int,
                       params: CircuitParams,
                       t1_t2_depolarization=True,
                       disable_ancilla_reset=True,
                       separate_gate_errors=True,
                       meas_induced_dephasing_enhancement=True,
                       monte_carlo_trajectory=False) -> Tuple[stim.Circuit, StimErrorContext, float]:
    """
    Generates a stim_lib circuit with a realistic error model based on gate/measure durations and execution lengths,
    and a more detailed error model for the gates. Also allows for ancilla measurement without reset for testing
    alternative reset methods.

    :param code_task: A code task, as given in `stim_lib.Circuit.generated` `code_task` argument

    :param distance: The code distance, supplied to `stim_lib.Circuit.generated`

    :param rounds: Number of correction rounds, supplied to `stim_lib.Circuit.generated`

    :param params: Circuit parameters

    :param separate_gate_errors: If `True`, will use a different error rate for single qubit and 2 qubit gate errors

    :param disable_ancilla_reset: If `True`, will replace all measure and reset instructions with a reset instruction

    :param t1_t2_depolarization: If `True`, will add approximated T1, T2 behavior using the Pauli Twirl approximation
    (see eq. 10 in https://arxiv.org/abs/1210.5799)

    :param meas_induced_dephasing_enhancement: Z error increase to be applied only during measurement on all idle qubits

    :return: The generated circuit and an error detection context for performing the decoding
    """
    circuit = stim.Circuit.generated(code_task,
                                     distance=distance,
                                     rounds=rounds,
                                     after_clifford_depolarization=params.two_qubit_depolarization_rate,
                                     before_measure_flip_probability=0,
                                     after_reset_flip_probability=0
                                     )
    qubit_indices = {inst.targets_copy()[0].value for inst in circuit if
                     isinstance(inst, stim.CircuitInstruction) and inst.name == 'QUBIT_COORDS'}
    weight = 1.0
    def add_t1_t2_depolarization(inst: stim.CircuitInstruction, 
                                 new_circ: stim.Circuit,
                                 weight: float) -> None:
        w = weight
        if inst.name in inst_with_duration:
            idle_qubits = qubit_indices.difference(t.value for t in inst.targets_copy())
            new_circ.append_operation(inst)
            if (monte_carlo_trajectory):
                new_circ.append_operation('PAULI_CHANNEL_1',
                                          list(idle_qubits),
                                          get_dephasing_probs(instruction_duration(inst, params),
                                                              params.t2
                                            )
                                          )
                p_I, p_Z, p_Rz = get_amplitude_damping_probs(instruction_duration(inst, params),
                                                              params.t1)
                channels = ["I", "Z", "RZ"]
                probs = np.array([p_I, abs(p_Z), p_Rz])
                probs /=  sum(probs)

                for qb in list(idle_qubits):
                    num = np.random.choice(3, 1, p=probs)[0]
                    if num!=0:
                        new_circ.append_operation(channels[num], [qb])
                    if num==1:
                        w *= -probs[num]
                    else:
                        w *= probs[num]
                
            else:
                new_circ.append_operation('PAULI_CHANNEL_1',
                                          list(idle_qubits),
                                          get_pauli_probs(instruction_duration(inst, params),
                                                          params.t1,
                                                          params.t2
                                                          )
                                          )
        else:
            new_circ.append_operation(inst)
        return w

    def add_measurement_induced_dephasing(inst: stim.CircuitInstruction, 
                                          new_circ: stim.Circuit,
                                          weight: float) -> None:
        if inst.name in measure_instructions:
            _, _, pz = get_pauli_probs(instruction_duration(inst, params), params.t1, params.t2)
            idle_qubits = qubit_indices.difference(t.value for t in inst.targets_copy())
            new_circ.append_operation(inst.name, inst.targets_copy(), inst.gate_args_copy())
            new_circ.append_operation('PAULI_CHANNEL_1',
                                      list(idle_qubits),
                                      [0, 0,
                                       params.meas_induced_dephasing_enhancement * pz])
        else:
            new_circ.append_operation(inst)
        return weight

    def replace_mr_with_m(inst: stim.CircuitInstruction, 
                          new_circ: stim.Circuit, 
                          weight:float) -> None:
        if inst.name == 'MR':
            new_circ.append_operation('M', inst.targets_copy(), inst.gate_args_copy())
        else:
            new_circ.append_operation(inst)
        return weight

    def separate_depolarization(inst: stim.CircuitInstruction, 
                                new_circ: stim.Circuit,
                                weight: float) -> None:
        if inst.name == 'DEPOLARIZE1':
            new_circ.append_operation('DEPOLARIZE1',
                                      inst.targets_copy(),
                                      params.single_qubit_depolarization_rate)
        elif inst.name == 'DEPOLARIZE2':
            new_circ.append_operation('DEPOLARIZE2',
                                      inst.targets_copy(),
                                      params.two_qubit_depolarization_rate)
        else:
            new_circ.append_operation(inst)
        return weight
    
    if t1_t2_depolarization:
        circuit, weight = _updater(circuit, add_t1_t2_depolarization, weight)
    if separate_gate_errors:
        circuit, weight = _updater(circuit, separate_depolarization, weight)
    if meas_induced_dephasing_enhancement:
        circuit, weight = _updater(circuit, add_measurement_induced_dephasing, weight)
    if disable_ancilla_reset:
        circuit, weight = _updater(circuit, replace_mr_with_m, weight)

    return circuit, StimErrorContext(circuit, code_task, distance, rounds, params), weight


if __name__ == '__main__':
    cparams = CircuitParams(t1=15e3,
                            t2=26e3,
                            single_qubit_gate_duration=20,
                            two_qubit_gate_duration=40,
                            single_qubit_depolarization_rate=1.1e-3,
                            two_qubit_depolarization_rate=6.6e-3,
                            meas_duration=600,
                            reset_duration=0,
                            reset_latency=40,
                            meas_induced_dephasing_enhancement=2)
    print(generate_scheduled('surface_code:rotated_memory_z',
                             distance=3,
                             rounds=4,
                             params=cparams,
                             monte_carlo_trajectory=True))
