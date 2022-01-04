from dataclasses import dataclass
from typing import Iterable

import quantumsim  # https://gitlab.com/quantumsim/quantumsim, branch: stable/v0.2
import quantumsim.circuit as circuit
import numpy as np
import matplotlib.pyplot as plt


@dataclass
class CircuitParams:
    t1: float
    t2: float
    single_qubit_gate_duration: float
    two_qubit_gate_duration: float
    meas_duration: float
    reset_duration: float
    reset_latency: float


class RepCodeGenerator:
    def __init__(self, distance: int, circuit_params: CircuitParams):
        self.distance = distance
        self.params = circuit_params
        self.sampler = circuit.BiasedSampler(readout_error=0, seed=42, alpha=1)

        self.qubits = [
            circuit.Qubit(str(i), t1=self.params.t1, t2=self.params.t2) for i in range(2 * self.distance + 1)
        ]
        self.cbits = [
            circuit.ClassicalBit("m" + str(i)) for i in range(2 * self.distance + 1)
        ]

    @property
    def register_names(self):
        return [q.name for q in self.qubits] + [cb.name for cb in self.cbits]

    @property
    def qubit_names(self):
        return [q.name for q in self.qubits]

    @property
    def cbit_names(self):
        return [cb.name for cb in self.cbits]

    def generate_stabilizer_round(self, injected_errors=None, plot=False) -> circuit.Circuit:
        if injected_errors is not None:
            raise NotImplementedError
        # todo: inject errors inside the stabilizer round
        # stabilizer round with no errors
        ########
        c = self.init_circuit("stabilizer round")

        t_start = 0
        t_moment = self.params.single_qubit_gate_duration

        for q in self.qubit_names[1::2]:
            c.add_gate(circuit.RotateY(q,
                                       t_start + t_moment / 2,
                                       np.pi / 2))

        t_start += t_moment
        t_moment = self.params.two_qubit_gate_duration
        for q1, q2 in zip(self.qubit_names[:-1:2], self.qubit_names[1::2]):
            c.add_gate(circuit.CPhase(q1, q2,
                                      t_start + t_moment / 2))

        t_start += t_moment
        t_moment = self.params.two_qubit_gate_duration
        for q1, q2 in zip(self.qubit_names[2::2], self.qubit_names[1::2]):
            c.add_gate(circuit.CPhase(q1, q2,
                                      t_start + t_moment / 2))

        t_start += t_moment
        t_moment = self.params.single_qubit_gate_duration
        for q in self.qubit_names[1::2]:
            c.add_gate(circuit.RotateY(q,
                                       t_start + t_moment / 2,
                                       -np.pi / 2))

        t_start += t_moment
        t_moment = self.params.meas_duration
        for q, cb in zip(self.qubit_names[1::2], self.cbit_names[1::2]):
            c.add_gate(circuit.Measurement(q,
                                           time=t_start + t_moment / 2,
                                           sampler=self.sampler,
                                           output_bit=cb
                                           ))

        t_start += t_moment
        c.add_waiting_gates(0, t_start)
        c.order()

        if plot:
            fig, ax = c.plot()
            fig.set_figwidth(15)
            plt.show()
        return c

    def init_circuit(self, name=None):
        if name is None:
            name = "unnamed circuit"
        c = circuit.Circuit(name)
        for q in self.qubits:
            c.add_qubit(q)
        for cb in self.cbits:
            c.add_qubit(cb)
        return c

    def generate_bitflip_error(self, qubit_names: Iterable[str], plot=False) -> circuit.Circuit:
        c = self.init_circuit("bitflip error")

        t_start = 0
        t_moment = self.params.single_qubit_gate_duration
        for q in qubit_names:
            c.add_gate(circuit.RotateX(q,
                                       t_start + t_moment / 2,
                                       np.pi))

        t_start += t_moment
        c.add_waiting_gates(0, t_start)
        c.order()

        if plot:
            fig, ax = c.plot()
            fig.set_figwidth(15)
            plt.show()

        return c

    def generate_active_reset(self, qubit_names: Iterable[str], plot=False) -> circuit.Circuit:
        c = self.init_circuit()
        t_start = 0
        t_moment = self.params.reset_latency
        # only wait for the latency period

        t_start += t_start
        t_moment = self.params.single_qubit_gate_duration

        for q in qubit_names:
            c.add_gate(circuit.RotateX(q,
                                       t_start + t_moment / 2,
                                       np.pi))
        t_start += t_moment
        c.add_waiting_gates(0, t_start)
        c.order()

        if plot:
            fig, ax = c.plot()
            fig.set_figwidth(10)
            plt.show()
        return c


if __name__ == "__main__":
    # run simulation
    ########

    cparams = CircuitParams(t1=10e5,
                            t2=10e5,
                            single_qubit_gate_duration=20,
                            two_qubit_gate_duration=40,
                            meas_duration=400,
                            reset_duration=250,
                            reset_latency=200)

    repc = RepCodeGenerator(distance=2,
                            circuit_params=cparams
                            )

    state = quantumsim.sparsedm.SparseDM(repc.register_names)
    results = []

    circ_no_err = repc.generate_stabilizer_round(plot=True)

    for i in range(3):
        circ_no_err.apply_to(state)
        results.append([state.classical[cb] for cb in repc.cbit_names])
        # apply active reset
        to_reset = []
        for q, cb in zip(repc.qubit_names, repc.cbit_names):
            if state.classical[cb] == 1:
                to_reset.append(q)
        repc.generate_active_reset(to_reset).apply_to(state)

    repc.generate_bitflip_error(["0"], plot=True).apply_to(state)

    for i in range(6):
        circ_no_err.apply_to(state)
        results.append([state.classical[cb] for cb in repc.cbit_names])
        # apply active reset
        to_reset = []
        for q, cb in zip(repc.qubit_names, repc.cbit_names):
            if state.classical[cb] == 1:
                to_reset.append(q)
        repc.generate_active_reset(to_reset).apply_to(state)

    repc.generate_bitflip_error(["0"]).apply_to(state)

    for i in range(6):
        circ_no_err.apply_to(state)
        results.append([state.classical[cb] for cb in repc.cbit_names])
        # apply active reset
        to_reset = []
        for q, cb in zip(repc.qubit_names, repc.cbit_names):
            if state.classical[cb] == 1:
                to_reset.append(q)
        repc.generate_active_reset(to_reset).apply_to(state)

    results = np.array(results)
    print(results.T)