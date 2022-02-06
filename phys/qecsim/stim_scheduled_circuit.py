from math import exp
from typing import Tuple, Callable

import stim

from qecsim.qec_generator import CircuitParams


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


def to_scheduled_circuit(circuit: stim.Circuit, params: CircuitParams) -> stim.Circuit:
    qubit_indices = {inst.targets_copy()[0].value for inst in circuit if
                     isinstance(inst, stim.CircuitInstruction) and inst.name == 'QUBIT_COORDS'}

    inst_with_duration = {'CX', 'H', 'MR'}

    def instruction_duration(inst: stim.CircuitInstruction) -> float:
        if inst.name not in inst_with_duration:
            ValueError(f'only duration of {inst_with_duration} is known')
        if inst.name == 'H':
            return params.single_qubit_gate_duration
        if inst.name == 'CX':
            return params.two_qubit_gate_duration
        if inst.name == 'MR':
            return params.reset_duration + params.reset_latency + params.meas_duration

    def add_t1_t2_depolarization(inst: stim.CircuitInstruction, new_circ: stim.Circuit):
        if inst.name in inst_with_duration:
            idle_qubits = qubit_indices.difference(t.value for t in inst.targets_copy())
            new_circ.append_operation(inst)
            new_circ.append_operation('PAULI_CHANNEL_1',
                                      list(idle_qubits),
                                      get_pauli_probs(instruction_duration(inst),
                                                      params.t1,
                                                      params.t2
                                                      )
                                      )
        else:
            new_circ.append_operation(inst)

    def updater(circ: stim.Circuit, inst_handler: Callable):
        new_circ = stim.Circuit()
        for inst in circ:
            inst: stim.CircuitInstruction
            if isinstance(inst, stim.CircuitRepeatBlock):
                new_circ += updater(inst.body_copy(), inst_handler) * inst.repeat_count
            else:
                inst_handler(inst, new_circ)
        return new_circ

    return updater(circuit, add_t1_t2_depolarization)


if __name__ == '__main__':
    cparams = CircuitParams(t1=15e3,
                            t2=19e3,
                            single_qubit_gate_duration=20,
                            two_qubit_gate_duration=100,
                            single_qubit_depolarization_rate=1.1e-3,
                            two_qubit_depolarization_rate=6.6e-3,
                            meas_duration=600,
                            reset_duration=0,
                            reset_latency=40)
    genc = stim.Circuit.generated('surface_code:rotated_memory_z',
                                  distance=3,
                                  rounds=4,
                                  after_clifford_depolarization=cparams.two_qubit_depolarization_rate,
                                  before_measure_flip_probability=0.2,
                                  after_reset_flip_probability=0.3
                                  )

    print(genc)
    print(to_scheduled_circuit(genc, cparams))

#        print(type(inst), '\t', inst)