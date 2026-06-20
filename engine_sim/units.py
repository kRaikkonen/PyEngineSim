"""Small unit-conversion helpers, kept in one place so the physics code reads
in plain SI while the UI can speak rpm / mm / cc / lb-ft like a car person."""

import math

# --- angular speed -----------------------------------------------------------

def rpm_to_rads(rpm: float) -> float:
    """Revolutions per minute -> radians per second."""
    return rpm * 2.0 * math.pi / 60.0


def rads_to_rpm(rads: float) -> float:
    """Radians per second -> revolutions per minute."""
    return rads * 60.0 / (2.0 * math.pi)


# --- length / volume ---------------------------------------------------------

def mm(x: float) -> float:
    """Millimetres -> metres."""
    return x * 1e-3


def cc(x: float) -> float:
    """Cubic centimetres -> cubic metres."""
    return x * 1e-6


def m3_to_cc(x: float) -> float:
    return x * 1e6


def m3_to_litres(x: float) -> float:
    return x * 1e3


# --- torque ------------------------------------------------------------------

def nm_to_lbft(x: float) -> float:
    return x * 0.73756214928


def nm_to_hp_at(torque_nm: float, rpm: float) -> float:
    """Power (in horsepower) from torque (N*m) and engine speed (rpm)."""
    power_watts = torque_nm * rpm_to_rads(rpm)
    return power_watts / 745.699872
