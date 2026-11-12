"""The exhaust the v2 core is told about: a network of pipes (REDESIGN_V2 s.6.3).

The core (core/sound/src/network.rs) knows four kinds of node and one kind of
pipe; a muffler, a catalyst, a turbine are all drawn with those:

    pipe      length, area, how hot (0 = outside air .. 1 = the gas in the
              port), extra absorption in dB per metre at 1 kHz
    junction  pipe ends at one pressure (one end alone: a closed end)
    valve     where a cylinder discharges
    orifice   a flow resistance dp = r q + k q|q| (turbine, brick, flap);
              ``channel`` names the control that scales it
    open      an end radiating into the air

So an expansion chamber is a fat pipe, a side-branch resonator a pipe with a
closed end, a catalyst a short fat lossy pipe behind a linear resistance.

Two sources of topology:

* ``a3_exhaust()`` -- written out by hand for the one car there will be
  recordings of.  Its dimensions are TYPICAL OF THE LAYOUT, not measured.
* ``exhaust_from_engine()`` -- derived from a preset's old sound parameters
  (primary length, system length, pipe radius, openness, channels...), so any
  car can be tried through the new core.  A first guess, not a fact sheet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

JUNCTION, VALVE, ORIFICE, OPEN = 0, 1, 2, 3

# control channels of the orifices
CH_TURBINE = 0
CH_FLAP = 1

# Flow noise: how much each turbulent loss fluctuates, rms over mean.
# STARTING VALUES, not measurements: chosen so that at the rated point the
# A3's hiss sits below its firing orders rather than over them.  They are
# the first thing the tailpipe recordings will set (s.6.8).
VALVE_NOISE = 0.006
LOSS_NOISE = 0.004
TAIL_NOISE = 0.004
TURBINE_NOISE = 0.006


@dataclass
class Pipe:
    length: float
    area: float
    heat: float
    loss_db: float
    a: int
    b: int
    name: str = ""


@dataclass
class Node:
    kind: int = JUNCTION
    index: int = -1
    r: float = 0.0
    k: float = 0.0
    radius: float = 0.0
    throat: float = 0.0       # orifice: the passage its flow noise is made in, m^2
    noise: float = 0.0        # rms fluctuation of the turbulent loss, as a fraction
    distance: float = 0.0     # open end: metres from the listener (0: 1 m)


@dataclass
class Network:
    pipes: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    t_ambient: float = 293.0
    valve_noise: float = VALVE_NOISE
    # the flow (kg/s) the turbine orifice was sized at, if there is one
    rated_flow: float = 0.0
    turbine_k: float = 0.0
    note: str = ""

    # ---- building
    def node(self, kind=JUNCTION, **kw):
        self.nodes.append(Node(kind, **kw))
        return len(self.nodes) - 1

    def pipe(self, a, length, area, heat, loss_db=0.0, name=""):
        """A pipe from node ``a`` to a new junction, which is returned."""
        b = self.node()
        self.pipes.append(Pipe(length, area, heat, loss_db, a, b, name))
        return b

    def join(self, a, b, length, area, heat, loss_db=0.0, name=""):
        self.pipes.append(Pipe(length, area, heat, loss_db, a, b, name))

    def make(self, n, kind, **kw):
        self.nodes[n] = Node(kind, **kw)
        return n

    def index_of(self, name):
        for i, p in enumerate(self.pipes):
            if p.name == name:
                return i
        raise KeyError(name)

    def describe(self):
        out = []
        for i, p in enumerate(self.pipes):
            d = 2000.0 * math.sqrt(p.area / math.pi)
            out.append("%2d %-14s %5.0f mm  d %5.1f mm  heat %.2f  loss %5.1f dB/m  %d->%d"
                       % (i, p.name, 1000 * p.length, d, p.heat, p.loss_db, p.a, p.b))
        return "\n".join(out)


    def density(self, heat):
        """kg/m^3 of the gas in a pipe at the rated point (port gas 1100 K)."""
        return 101325.0 / (287.0 * (self.t_ambient + heat * (1100.0 - self.t_ambient)))

    def loss(self, x, k_loss, area, heat, noise=None):
        """Node ``x`` becomes a turbulent loss of ``k_loss`` dynamic heads of
        the flow through ``area``: dp = k_loss rho U^2 / 2."""
        k = k_loss / (2.0 * self.density(heat) * area * area)
        return self.make(x, ORIFICE, k=k, throat=area,
                         noise=LOSS_NOISE if noise is None else noise)

    def chamber(self, x, a_in, length, area, heat, loss_db=0.0, a_out=None, name=""):
        """An expansion chamber after node ``x``, entered from a pipe of area
        ``a_in`` and left into one of ``a_out``.  The sudden expansion loses
        (1 - a_in/area)^2 of the dynamic head (Borda-Carnot), the contraction
        about half of (1 - a_out/area): that is what damps a chamber when gas
        is flowing, and where its flow noise is made."""
        a_out = a_in if a_out is None else a_out
        self.loss(x, (1.0 - a_in / area) ** 2, a_in, heat)
        x = self.pipe(x, length, area, heat, loss_db, name=name)
        return self.loss(x, 0.5 * (1.0 - a_out / area), a_out, heat)


def circle(d):
    return 0.25 * math.pi * d * d


def a3_exhaust():
    """Audi A3 35 TFSI (EA211 evo 1.5, MQB evo), turbine to tail.

    Typical of the layout, NOT measured from the car: four short runners
    cast into the head, a variable-vane turbine, a close-coupled catalyst
    and particulate filter, a front pipe, an absorption centre silencer, a
    lined rear silencer, a down-turned tailpipe.
    """
    n = Network(note="A3 35 TFSI, typical dimensions (unverified)")
    collector = n.node()
    for k, length in enumerate([0.19, 0.13, 0.13, 0.19]):
        v = n.node(VALVE, index=k)
        n.join(v, collector, length, circle(0.032), 1.0, name="runner %d" % (k + 1))
    x = n.pipe(collector, 0.06, circle(0.040), 0.97, name="collector")
    # turbine: 1.1 bar across it at 0.155 kg/s
    n.rated_flow, n.turbine_k = 0.155, 4.6e6
    # (below 3 % of that flow its resistance is the linear one of a leak, not
    #  the square law of a nozzle -- else the flow reversing through it at
    #  idle would be a kink, and a kink is a buzz)
    n.make(x, ORIFICE, r=0.03 * n.turbine_k * n.rated_flow, k=n.turbine_k,
           index=CH_TURBINE, throat=circle(0.030), noise=TURBINE_NOISE)
    x = n.pipe(x, 0.10, circle(0.050), 0.85, name="turbine out")
    # catalyst brick (400 cpsi, 75 % open): 4 kPa at rated flow
    x = n.pipe(x, 0.10, 0.75 * circle(0.118), 0.82, 40.0, name="catalyst")
    n.make(x, ORIFICE, r=2.6e4)
    # particulate filter: 7 kPa
    x = n.pipe(x, 0.12, 0.60 * circle(0.118), 0.78, 80.0, name="filter")
    n.make(x, ORIFICE, r=4.5e4)
    a = circle(0.055)
    x = n.pipe(x, 0.90, a, 0.62, name="front pipe")
    x = n.pipe(x, 0.40, a, 0.52, 25.0, name="centre silencer")
    x = n.pipe(x, 1.10, a, 0.45, name="mid pipe")
    # rear silencer: 10 l across the car, wool-lined
    x = n.chamber(x, a, 0.45, 0.022, 0.34, 15.0, a_out=circle(0.050), name="rear silencer")
    x = n.pipe(x, 0.45, circle(0.050), 0.30, name="tailpipe")
    n.make(x, OPEN, radius=0.025, noise=TAIL_NOISE)
    return n


_TURBO = ("turbo", "twin_scroll", "twincharge", "eturbo", "sequential")


def rated_air_flow(eng):
    """kg/s of air at the rated point: displacement x speed x a full charge."""
    boost = max(getattr(eng, "boost_bar", 0.0), 0.0) \
        if getattr(eng, "induction", "na") != "na" else 0.0
    rpm = 0.88 * eng.redline_rpm
    return eng.total_displacement * rpm / 120.0 * 1.18 * 0.92 * (1.0 + boost)


def exhaust_from_engine(eng):
    """A network guessed from a preset's sound parameters.

    Rules (all of them guesses, there to be replaced by facts per car):
      * one runner per cylinder, ``exhaust_primary_m`` long (staggered a few
        per cent, as cast manifolds are), 0.46 x bore across;
      * the runners of a bank meet in one collector; with one exhaust
        channel the banks then merge, with two each has its own line;
      * a turbo engine has a turbine orifice after the collector, sized for
        a pressure drop of 1.1 x boost at the rated flow;
      * catalyst and particulate filter if the preset says so;
      * the line is ``exhaust_total_m`` long from collector to tail and
        ``exhaust_radius_m`` in radius;
      * the silencer shrinks with ``exhaust_openness``: 12 x displacement at
        0 to nothing above 0.9 -- two chambers if reflective, one packed
        chamber if absorptive.
    """
    n = Network(note="derived from preset parameters (a guess)")
    cyls = eng.cylinders
    ncyl = len(cyls)
    bore = cyls[0].bore
    banks = {}
    for k, c in enumerate(cyls):
        a = getattr(c, "bank_angle_deg", 0.0)
        banks.setdefault(-1 if a < -1.0 else (1 if a > 1.0 else 0), []).append(k)
    groups = [banks[b] for b in sorted(banks)]
    channels = 2 if (getattr(eng, "exhaust_channels", 1) >= 2 and len(groups) >= 2) else 1
    lines = len(groups) if channels == 2 else 1

    primary = max(getattr(eng, "exhaust_primary_m", 0.5), 0.08)
    total = max(getattr(eng, "exhaust_total_m", 1.6), primary + 0.4)
    radius = max(getattr(eng, "exhaust_radius_m", 0.022), 0.012)
    openness = min(max(getattr(eng, "exhaust_openness", 0.85), 0.0), 1.0)
    turbo = getattr(eng, "induction", "na") in _TURBO
    a_run = circle(0.46 * bore)
    a_pipe = math.pi * radius * radius
    if lines == 1 and channels == 1 and len(groups) >= 2:
        # one line carries both banks
        a_pipe *= 1.6
    q_line = rated_air_flow(eng) * 1.07 / lines
    boost = max(getattr(eng, "boost_bar", 0.0), 0.3) if turbo else 0.0

    # collectors, one per bank
    collectors = []
    for g in groups:
        col = n.node()
        for j, k in enumerate(g):
            v = n.node(VALVE, index=k)
            stagger = 1.0 + 0.06 * ((j * 2 % 3) - 1)
            n.join(v, col, primary * stagger, a_run, 1.0, name="runner %d" % (k + 1))
        collectors.append(col)
    if lines == 1 and len(collectors) > 1:
        # the banks merge after a short pipe each
        merge = n.node()
        for i, col in enumerate(collectors):
            n.join(col, merge, 0.25, a_pipe / 1.6 * 1.1, 0.95, name="bank %d" % (i + 1))
        collectors = [merge]

    rest = total - primary
    road = bool(getattr(eng, "has_cat", True))
    for i, col in enumerate(collectors):
        tag = "" if len(collectors) == 1 else " %d" % (i + 1)
        heat = 0.95
        x = col
        if turbo:
            x = n.pipe(x, 0.08, a_pipe * 0.8, 0.97, name="turbine in" + tag)
            k = 1.1 * boost * 1.0e5 / (q_line * q_line)
            n.make(x, ORIFICE, r=0.03 * k * q_line, k=k, index=CH_TURBINE,
                   throat=0.35 * a_pipe, noise=TURBINE_NOISE)
            n.rated_flow, n.turbine_k = q_line, k
            heat = 0.85
        r_head = 0.5 * 1.2 * (q_line / (a_pipe * 0.55)) ** 2 / 0.55 / q_line  # one dynamic head
        if road:
            x = n.pipe(x, 0.12, a_pipe, heat, name="downpipe" + tag)
            x = n.pipe(x, 0.10, 3.0 * a_pipe, heat - 0.03, 60.0, name="catalyst" + tag)
            n.make(x, ORIFICE, r=3.0e3 / q_line)
            heat -= 0.06
        if getattr(eng, "has_gpf", False):
            x = n.pipe(x, 0.12, 2.5 * a_pipe, heat - 0.03, 120.0, name="filter" + tag)
            n.make(x, ORIFICE, r=6.0e3 / q_line)
            heat -= 0.06
        del r_head
        # (`exhaust_openness` is a tone control of the old chain, not a
        #  statement that a car has no silencer: a road car -- one with a
        #  catalyst -- always has a box, 1.5 x its displacement at the least)
        vol = eng.total_displacement * ((1.5 if road else 0.0)
                                        + 10.5 * max(1.0 - openness / 0.9, 0.0)) / lines
        tail = min(max(0.15 * rest, 0.25), 0.6)
        front = max(rest - tail - (0.5 if vol > 0.0 else 0.0), 0.3)
        x = n.pipe(x, 0.55 * front, a_pipe, heat * 0.75, name="front pipe" + tag)
        x = n.pipe(x, 0.45 * front, a_pipe, heat * 0.60, name="mid pipe" + tag)
        if vol > 1.0e-3:
            if getattr(eng, "muffler_type", "reflective") == "absorptive":
                length = min(max(vol / (9.0 * a_pipe), 0.25), 0.7)
                x = n.chamber(x, a_pipe, length, max(vol / length, 1.5 * a_pipe),
                              heat * 0.45, 30.0 * (1.0 - openness), name="silencer" + tag)
            else:
                big = max(0.5 * vol / 0.25, 1.5 * a_pipe)
                x = n.chamber(x, a_pipe, 0.25, big, heat * 0.48, a_out=0.7 * a_pipe,
                              name="chamber 1" + tag)
                x = n.pipe(x, 0.15, 0.7 * a_pipe, heat * 0.45, name="tube" + tag)
                x = n.chamber(x, 0.7 * a_pipe, 0.25, big, heat * 0.42, 8.0, a_out=a_pipe,
                              name="chamber 2" + tag)
        x = n.pipe(x, tail, a_pipe, heat * 0.40, name="tailpipe" + tag)
        # two tailpipes are 0.8 m apart and the listener stands 1 m behind
        # the left one: 1 m and 1.28 m.  (On the centre line the two banks
        # would cancel each other's odd orders exactly -- nowhere else.)
        far = 1.0 if (len(collectors) == 1 or i == 0) else math.hypot(1.0, 0.8)
        n.make(x, OPEN, radius=math.sqrt(a_pipe / math.pi), noise=TAIL_NOISE, distance=far)
    return n


def murcielago_exhaust(primaries=(0.30, 0.50, 0.78, 0.42, 0.66, 0.95), box=0.036, lining=14.0,
                        runner_loss=8.0):
    """Lamborghini Murcielago (L539 6.5 V12): per bank two unequal 3-into-1
    groups into one collector, a catalyst per bank, both banks into ONE
    transverse rear box, one central outlet.

    NOT measured from the car.  The layout (two banks into a shared box and
    a single central outlet) is the car's; the primary lengths are CHOSEN:
    unequal, as its compact manifolds are, and set against the order table
    of the ear-approved classic voice (tools/v2_gate.py 6), whose character
    is low orders (0.5-2) under the sixth.  Equal primaries give an even
    V12 only its sixth order and multiples -- the howl without the growl.
    The right bank is the left one mirrored, so the banks do not cancel
    each other's third order exactly.

    Where it stands against the classic (2026-11-13, tools/v2_gate.py):
    band shares within 8-10 dB and spectral centroid 180-600 Hz against
    120-250 Hz; main orders within 13-21 dB.  With the guessed equal-length
    topology it was 22 dB and 34 dB, centroid up to 2200 Hz.  Not yet inside
    the plan's tolerances (6 points, 3 dB).
    """
    n = Network(note="Murcielago: real layout, primary lengths set against the classic voice")
    a_run, a_col, a_pipe = circle(0.042), circle(0.060), circle(0.063)
    box_in = []
    for bank in range(2):
        lens = primaries if bank == 0 else primaries[::-1]
        col = n.node()
        for g in range(2):
            y = n.node()
            for j in range(3):
                k = bank * 6 + g * 3 + j
                v = n.node(VALVE, index=k)
                n.join(v, y, lens[g * 3 + j], a_run, 1.0, runner_loss, name="runner %d" % (k + 1))
            n.join(y, col, 0.18 + 0.10 * g, circle(0.050), 0.95, name="secondary %d%s" % (bank + 1, "ab"[g]))
        x = n.pipe(col, 0.20 + 0.06 * bank, a_col, 0.90, name="downpipe %d" % (bank + 1))
        x = n.pipe(x, 0.12, 3.0 * a_col, 0.86, 40.0, name="catalyst %d" % (bank + 1))
        n.make(x, ORIFICE, r=2.0e4)
        x = n.pipe(x, 0.35 + 0.08 * bank, a_col, 0.75, name="link %d" % (bank + 1))
        n.loss(x, (1.0 - a_col / 0.05) ** 2, a_col, 0.6)
        box_in.append(x)
    # the shared box: 16 l across the car, lightly packed
    mid = n.node()
    for i, x in enumerate(box_in):
        n.join(x, mid, 0.22, 0.5 * box / 0.22, 0.55, lining, name="box %d" % (i + 1))
    a_tail = 1.6 * a_pipe
    x = n.pipe(mid, 0.06, a_tail, 0.50, name="box outlet")
    n.loss(x, 0.5, a_tail, 0.5)
    x = n.pipe(x, 0.30, a_tail, 0.45, name="tailpipe")
    n.make(x, OPEN, radius=math.sqrt(a_tail / math.pi), noise=TAIL_NOISE)
    return n


_BY_HAND = {"a3": a3_exhaust, "6": murcielago_exhaust}


def exhaust_for(key, eng):
    """The network of preset ``key``: by hand where one was written."""
    f = _BY_HAND.get(key)
    return f() if f else exhaust_from_engine(eng)
