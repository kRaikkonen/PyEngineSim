"""Python's door into the v2 core (core/, Rust) -- its C ABI through ctypes.

Build the library first:

    cd core
    cargo build --release

The studio, the bake's checks and the tests all drive the SAME compiled core
the phone runs (docs/REDESIGN_V2.md s.4).  Nothing here is a second
implementation: this file only moves numbers across the boundary.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, Structure, c_double, c_float, c_int, c_uint32, c_uint64, c_void_p

import numpy as np

ABI_VERSION = 3
P_ATM = 101325.0


class EsValve(Structure):
    _fields_ = [("open_deg", c_double), ("span_deg", c_double),
                ("area", POINTER(c_double)), ("n", c_int)]


class EsSpec(Structure):
    _fields_ = [("bore", c_double), ("stroke", c_double), ("rod", c_double),
                ("compression", c_double),
                ("firing_deg", POINTER(c_double)), ("cylinders", c_int),
                ("exhaust", EsValve), ("intake", EsValve),
                ("wall_temp", c_double), ("heat_scale", c_double),
                ("gamma_const", c_double), ("wall_refresh_deg", c_double)]


class EsControl(Structure):
    """One operating point (core/sound/src/control.rs)."""
    _fields_ = [("rpm", c_double),
                ("p_intake", c_double), ("t_intake", c_double),
                ("p_exhaust", c_double), ("t_exhaust", c_double),
                ("charge", c_double), ("lambda_", c_double),
                ("ca50_deg", c_double), ("burn_deg", c_double),
                ("intake_phase_deg", c_double), ("exhaust_phase_deg", c_double),
                ("orifice", c_double * 4), ("ccv", c_double),
                ("level_ref", c_double), ("dynamics", c_double), ("volume", c_double),
                ("fuel_cut", c_uint64)]


class EsPipe(Structure):
    _fields_ = [("length", c_double), ("area", c_double), ("heat", c_double),
                ("loss_db", c_double), ("a", c_uint32), ("b", c_uint32)]


class EsNode(Structure):
    _fields_ = [("kind", c_int), ("index", c_int), ("r", c_double), ("k", c_double),
                ("radius", c_double), ("throat", c_double), ("noise", c_double),
                ("distance", c_double)]


class EsCylinder(Structure):
    _fields_ = [(n, c_double) for n in (
        "p", "t", "m", "m_burned", "v", "q_exhaust", "q_intake",
        "work", "burn", "wall", "m_exhaust", "m_intake")]


def control(rpm, p_intake, t_intake=310.0, p_exhaust=P_ATM, t_exhaust=900.0,
            charge=0.0, lam=1.0, ca50_deg=10.0, burn_deg=25.0,
            intake_phase_deg=0.0, exhaust_phase_deg=0.0, fuel_cut=0,
            orifice=(1.0, 1.0, 1.0, 1.0), ccv=0.0, level_ref=0.0, dynamics=1.0,
            volume=1.0):
    return EsControl(rpm, p_intake, t_intake, p_exhaust, t_exhaust, charge, lam,
                     ca50_deg, burn_deg, intake_phase_deg, exhaust_phase_deg,
                     (c_double * 4)(*orifice), ccv, level_ref, dynamics, volume,
                     fuel_cut)


def library_path():
    """Where the built core is (ENGINESIM_CORE overrides)."""
    env = os.environ.get("ENGINESIM_CORE")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    name = {"win32": "enginesim_core.dll", "darwin": "libenginesim_core.dylib"}.get(
        sys.platform, "libenginesim_core.so")
    return os.path.normpath(os.path.join(here, "..", "core", "target", "release", name))


_lib = None


def lib():
    global _lib
    if _lib is not None:
        return _lib
    path = library_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            "the v2 core is not built: %s\n  cd core && cargo build --release" % path)
    L = ctypes.CDLL(path)
    L.es_abi_version.restype = c_uint32
    L.es_sizeof_control.restype = c_uint32
    if L.es_abi_version() != ABI_VERSION:
        raise RuntimeError("core ABI %d, this binding speaks %d -- rebuild the core"
                           % (L.es_abi_version(), ABI_VERSION))
    if L.es_sizeof_control() != ctypes.sizeof(EsControl):
        raise RuntimeError("es_control_t is %d bytes in the core, %d here"
                           % (L.es_sizeof_control(), ctypes.sizeof(EsControl)))
    L.es_open.restype = c_void_p
    L.es_open.argtypes = [POINTER(EsSpec), c_double]
    L.es_close.argtypes = [c_void_p]
    L.es_control.restype = c_int
    L.es_control.argtypes = [c_void_p, POINTER(EsControl)]
    L.es_render.argtypes = [c_void_p, POINTER(c_float), POINTER(c_float), c_int]
    L.es_prime.argtypes = [c_void_p, POINTER(EsControl), c_double]
    L.es_run.argtypes = [c_void_p, c_int, c_int] + [POINTER(c_double)] * 5
    L.es_cylinder.restype = c_int
    L.es_cylinder.argtypes = [c_void_p, c_int, POINTER(EsCylinder)]
    L.es_clear_ledgers.argtypes = [c_void_p]
    L.es_crank_deg.restype = c_double
    L.es_crank_deg.argtypes = [c_void_p]
    L.es_set_port_impedance.argtypes = [c_void_p, c_double, c_double]
    L.es_attach_exhaust.restype = c_int
    L.es_attach_exhaust.argtypes = [c_void_p, POINTER(EsPipe), c_int,
                                    POINTER(EsNode), c_int, c_double, c_double]
    L.es_listen.argtypes = [c_void_p, c_int, c_int] + [POINTER(c_double)] * 4
    L.es_level.restype = c_double
    L.es_level.argtypes = [c_void_p]
    _lib = L
    return L


def _doubles(values):
    return (c_double * len(values))(*values)


class Core:
    """One engine in the core."""

    def __init__(self, spec, rate=48000.0):
        """``spec``: an engine_sim.core_spec.CoreSpec."""
        L = lib()
        firing = _doubles(spec.firing_deg)
        ex = _doubles(spec.exhaust.area)
        inl = _doubles(spec.intake.area)
        s = EsSpec(spec.bore, spec.stroke, spec.rod, spec.compression,
                   firing, len(spec.firing_deg),
                   EsValve(spec.exhaust.open_deg, spec.exhaust.span_deg, ex, len(ex)),
                   EsValve(spec.intake.open_deg, spec.intake.span_deg, inl, len(inl)),
                   spec.wall_temp, spec.heat_scale, spec.gamma_const, spec.wall_refresh_deg)
        self._h = L.es_open(ctypes.byref(s), float(rate))
        if not self._h:
            raise ValueError("the core refused this engine spec")
        self.rate = float(rate)
        self.spec = spec

    def close(self):
        if self._h:
            lib().es_close(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def control(self, c):
        """Publish an operating point (ramped in over the next 32 samples)."""
        return bool(lib().es_control(self._h, ctypes.byref(c)))

    def prime(self, c, cycles=8.0):
        """Jump to an operating point and run it in, silently."""
        lib().es_prime(self._h, ctypes.byref(c), float(cycles))

    def run(self, n, cyl=0):
        """Advance n samples; the valves' flows (kg/s, all cylinders) and
        cylinder ``cyl``'s pressure, temperature and mass after each."""
        out = {k: np.zeros(n) for k in ("q_exhaust", "q_intake", "p", "t", "m")}
        ptr = [out[k].ctypes.data_as(POINTER(c_double))
               for k in ("q_exhaust", "q_intake", "p", "t", "m")]
        lib().es_run(self._h, int(n), int(cyl), *ptr)
        return out

    def render(self, n):
        left = np.zeros(n, dtype=np.float32)
        right = np.zeros(n, dtype=np.float32)
        lib().es_render(self._h, left.ctypes.data_as(POINTER(c_float)),
                        right.ctypes.data_as(POINTER(c_float)), int(n))
        return left, right

    def render_into(self, left, right, n):
        """Render n frames into two float32 arrays that already exist (the
        audio thread: no allocation here)."""
        lib().es_render(self._h, left.ctypes.data_as(POINTER(c_float)),
                        right.ctypes.data_as(POINTER(c_float)), int(n))

    def cylinder(self, k=0):
        c = EsCylinder()
        if not lib().es_cylinder(self._h, int(k), ctypes.byref(c)):
            raise IndexError(k)
        return {n: getattr(c, n) for n, _ in EsCylinder._fields_}

    def clear_ledgers(self):
        lib().es_clear_ledgers(self._h)

    @property
    def crank_deg(self):
        return lib().es_crank_deg(self._h)

    def set_port_impedance(self, exhaust_z, intake_z=0.0):
        lib().es_set_port_impedance(self._h, float(exhaust_z), float(intake_z))

    def attach_exhaust(self, net):
        """``net``: an engine_sim.core_exhaust.Network -- the pipes the exhaust
        valves discharge into.  From then on ``p_exhaust`` of an operating
        point is the pressure at the open ends (the air outside)."""
        pipes = (EsPipe * len(net.pipes))(*[
            EsPipe(p.length, p.area, p.heat, p.loss_db, p.a, p.b) for p in net.pipes])
        nodes = (EsNode * len(net.nodes))(*[
            EsNode(n.kind, n.index, n.r, n.k, n.radius, n.throat, n.noise, n.distance)
            for n in net.nodes])
        if not lib().es_attach_exhaust(self._h, pipes, len(pipes), nodes, len(nodes),
                                       float(net.t_ambient), float(net.valve_noise)):
            raise ValueError("the core refused this exhaust network")
        self.exhaust = net

    def listen(self, n, pipe=0):
        """Advance n samples; the flow through the exhaust valves and out of
        the open ends (kg/s), the sound pressure 1 m from the open ends (Pa)
        and the pressure at the upstream end of pipe ``pipe`` (Pa, gauge)."""
        keys = ("q_exhaust", "q_tail", "p_far", "p_pipe")
        out = {k: np.zeros(n) for k in keys}
        lib().es_listen(self._h, int(n), int(pipe),
                        *[out[k].ctypes.data_as(POINTER(c_double)) for k in keys])
        return out

    @property
    def level(self):
        """Pa rms at 1 m, as the loudness curve reads it now."""
        return lib().es_level(self._h)

    def samples_per_cycle(self, rpm):
        return 120.0 / rpm * self.rate
