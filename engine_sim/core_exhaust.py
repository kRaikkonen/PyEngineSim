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
    # bypass valves (orifices on CH_FLAP) open above this speed or load (0: none)
    flap_rpm: float = 0.0
    flap_load: float = 0.0
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


def murcielago_exhaust():
    """Lamborghini Murcielago LP640 / LP670-4 SV (6.5 V12, 60 degrees,
    firing 1-7-4-10-2-8-6-12-3-9-5-11).

    FROM THE CAR (factory parts catalogue 251.01-251.04, Supersprint notes):
      * four tubular manifolds, front and rear on each bank: two 3-into-1
        per bank (cylinders 1-3 / 4-6, 7-9 / 10-12), outlet joint 51 mm;
        each group of three fires evenly, 240 degrees apart;
      * the two manifolds of a bank join at that bank's catalyst (one per
        bank);
      * each catalyst has two outlets: one, 62 mm, to the ONE shared rear
        silencer; one through a vacuum bypass valve straight on;
        both rejoin, 2-into-1, at a single central tailpipe.
    ESTIMATED (no source gives them; marked so they can be replaced):
      * primaries 41 mm bore, 0.42 m, equal within a manifold;
      * the front manifold reaches the catalyst 0.30 m later than the rear
        one (three bore spacings of about 0.1 m): this, not unequal
        primaries, is what the car has to break its symmetry;
      * catalyst brick 0.12 m long; silencer 30 l, lined; link lengths;
      * the bypass valves open above 3500 rpm or 60 % load (the factory
        logic is not published).
    """
    n = Network(note="Murcielago: layout from the parts catalogue, lengths estimated")
    n.flap_rpm, n.flap_load = 3500.0, 0.6
    a_pri, a_out, a_link = circle(0.041), circle(0.051), circle(0.062)
    box_in, straight = [], []
    for bank in range(2):
        cat_in = n.node()
        for g in range(2):                      # 0: front manifold, 1: rear
            y = n.node()
            for j in range(3):
                k = bank * 6 + g * 3 + j
                v = n.node(VALVE, index=k)
                n.join(v, y, 0.42, a_pri, 1.0, 4.0, name="primary %d" % (k + 1))
            n.join(y, cat_in, 0.50 if g == 0 else 0.20, a_out, 0.95,
                   name="manifold %d%s" % (bank + 1, "fr"[g]))
        x = n.pipe(cat_in, 0.12, 3.0 * a_link, 0.86, 40.0, name="catalyst %d" % (bank + 1))
        n.make(x, ORIFICE, r=2.0e4)
        split = n.pipe(x, 0.10, a_link, 0.80, name="cat out %d" % (bank + 1))
        # to the silencer
        y = n.pipe(split, 0.35, a_link, 0.70, name="to silencer %d" % (bank + 1))
        n.loss(y, (1.0 - a_link / 0.06) ** 2, a_link, 0.6)
        box_in.append(y)
        # the bypass: a valve, then straight to the tail
        y = n.pipe(split, 0.12, a_link, 0.75, name="bypass in %d" % (bank + 1))
        n.loss(y, 0.4, a_link, 0.7)
        n.nodes[y].index = CH_FLAP
        straight.append(n.pipe(y, 0.40, a_link, 0.65, name="bypass %d" % (bank + 1)))
    # the shared silencer: 30 l across the car, lined
    mid = n.node()
    for i, y in enumerate(box_in):
        n.join(y, mid, 0.25, 0.015 / 0.25, 0.55, 12.0, name="silencer %d" % (i + 1))
    y = n.pipe(mid, 0.06, a_link, 0.50, name="silencer out")
    n.loss(y, 0.5, a_link, 0.5)
    tail = n.node()
    n.join(y, tail, 0.25, a_link, 0.48, name="silencer pipe")
    for i, y in enumerate(straight):
        n.join(y, tail, 0.05, a_link, 0.55, name="bypass join %d" % (i + 1))
    a_tail = circle(0.10)
    x = n.pipe(tail, 0.25, a_tail, 0.45, name="tailpipe")
    n.make(x, OPEN, radius=0.05, noise=TAIL_NOISE, distance=_far(0.0))
    return n


# The listener stands 1 m behind the car and half a metre off its centre
# line.  ON the centre line the two banks of a symmetrical exhaust cancel
# each other's odd orders exactly -- a place nobody listens from for long.
LISTENER_OFFSET = 0.5


def _far(x):
    """Metres from the listener to an outlet ``x`` m left of the centre line."""
    return math.hypot(1.0, x - LISTENER_OFFSET)


def _banks(eng):
    """Cylinder indices by bank (negative bank angle first)."""
    left = [k for k, c in enumerate(eng.cylinders) if c.bank_angle_deg < 0.0]
    right = [k for k, c in enumerate(eng.cylinders) if c.bank_angle_deg >= 0.0]
    return [left, right]


def _manifold(n, cyls, length, a_pri, loss=4.0, pairs=None, a_sec=None, sec_len=0.25):
    """Primaries of ``cyls`` into one collector node (returned): n-into-1,
    or with ``pairs`` (lists of positions) n-into-2-into-1."""
    col = n.node()
    if pairs is None:
        for k in cyls:
            n.join(n.node(VALVE, index=k), col, length, a_pri, 1.0, loss, name="primary %d" % (k + 1))
        return col
    for pr in pairs:
        y = n.node()
        for j in pr:
            k = cyls[j]
            n.join(n.node(VALVE, index=k), y, length, a_pri, 1.0, loss, name="primary %d" % (k + 1))
        n.join(y, col, sec_len, a_sec, 0.95, name="secondary")
    return col


def _flap(n, x, area, heat, name):
    """A bypass valve after node ``x`` (control channel CH_FLAP); returns
    the node behind it."""
    y = n.pipe(x, 0.10, area, heat, name=name)
    n.loss(y, 0.4, area, heat)
    n.nodes[y].index = CH_FLAP
    return y


def ferrari_458_exhaust(eng=None):
    """Ferrari 458 Italia (F136 FB, flat-plane V8; each bank fires every 180
    degrees, the banks alternate).

    FROM THE CAR (Ferrari press release; descriptions by owners and makers):
      * 4-into-1 tubular manifold per bank; stock primary about 1.5 in,
        manifold outlet about 45 mm;
      * three tailpipes: the centre one works at low speed, the outer two
        join in higher up; one on/off vacuum valve per bank routes the gas
        of that bank through or past the main silencer; owners report
        about 3000 rpm at full throttle in Race.
    ESTIMATED: primary length 0.45 m (equal); one catalyst per bank; the
    silencer as one shared 20 l lined box feeding the centre pipe; link
    lengths; outer pipes 0.30 m either side of the centre one.
    """
    from . import presets
    eng = eng or presets.ALL["4"]()
    n = Network(note="458: layout from Ferrari and owners, lengths estimated")
    n.flap_rpm, n.flap_load = 3000.0, 0.5
    a_pri, a_out, a_pipe = circle(0.036), circle(0.045), circle(0.060)
    mid = n.node()
    for i, cyls in enumerate(_banks(eng)):
        col = _manifold(n, cyls, 0.45, a_pri)
        x = n.pipe(col, 0.15, a_out, 0.92, name="collector %d" % (i + 1))
        x = n.pipe(x, 0.12, 3.0 * a_pipe, 0.86, 40.0, name="catalyst %d" % (i + 1))
        n.make(x, ORIFICE, r=2.5e4)
        split = n.pipe(x, 0.20, a_pipe, 0.78, name="cat out %d" % (i + 1))
        y = _flap(n, split, a_pipe, 0.72, "valve in %d" % (i + 1))
        y = n.pipe(y, 0.45, a_pipe, 0.60, name="outer pipe %d" % (i + 1))
        n.make(y, OPEN, radius=0.035, noise=TAIL_NOISE, distance=_far(0.30 if i == 0 else -0.30))
        y = n.pipe(split, 0.25, a_pipe, 0.68, name="to silencer %d" % (i + 1))
        n.loss(y, (1.0 - a_pipe / 0.04) ** 2, a_pipe, 0.6)
        n.join(y, mid, 0.25, 0.010 / 0.25, 0.52, 12.0, name="silencer %d" % (i + 1))
    y = n.pipe(mid, 0.06, a_pipe, 0.48, name="silencer out")
    n.loss(y, 0.5, a_pipe, 0.5)
    y = n.pipe(y, 0.35, a_pipe, 0.44, name="centre pipe")
    n.make(y, OPEN, radius=0.035, noise=TAIL_NOISE, distance=_far(0.0))
    return n


def audi_r8_42_exhaust(eng=None):
    """Audi R8 4.2 FSI (BYH; cross-plane V8, firing 1-5-4-8-6-3-7-2).

    FROM THE CAR (Audi SSP 377 for the sister RS4 engine; R8 parts lists
    and an exhaust installation guide):
      * fan-type air-gap manifold per bank, 4-into-2-into-1, equal-length
        pipes (R8 press text), in one unit with a primary catalyst;
      * a main catalyst per bank; one transverse rear silencer assembly;
        in the RS4 the banks stay SEPARATE inside it (absorption type, no
        crossover) -- assumed for the R8 too;
      * two vacuum flaps, shut at idle, low load and low speed.
    Each bank of this engine fires at 90-180-270-180 degrees: with the
    banks kept apart, each tailpipe carries the half orders.
    ESTIMATED: primaries 38 mm x 0.35 m, the pairing (the two front and
    the two rear cylinders of a bank), all lengths behind the manifold,
    silencer 9 l a side, flap thresholds 3500 rpm / 50 % load, outlets
    1.1 m apart.
    """
    from . import presets
    eng = eng or presets.ALL["audiv8"]()
    n = Network(note="R8 4.2: layout from Audi SSP 377 / parts lists, lengths estimated")
    n.flap_rpm, n.flap_load = 3500.0, 0.5
    a_pri, a_sec, a_pipe = circle(0.038), circle(0.045), circle(0.060)
    for i, cyls in enumerate(_banks(eng)):
        col = _manifold(n, cyls, 0.35, a_pri, pairs=[[0, 1], [2, 3]], a_sec=a_sec, sec_len=0.22)
        x = n.pipe(col, 0.08, 2.5 * a_pipe, 0.92, 40.0, name="primary cat %d" % (i + 1))
        n.make(x, ORIFICE, r=1.5e4)
        x = n.pipe(x, 0.45, a_pipe, 0.80, name="pipe %d" % (i + 1))
        x = n.pipe(x, 0.10, 3.0 * a_pipe, 0.72, 40.0, name="main cat %d" % (i + 1))
        n.make(x, ORIFICE, r=2.0e4)
        split = n.pipe(x, 0.10, a_pipe, 0.66, name="cat out %d" % (i + 1))
        end = n.node()
        y = _flap(n, split, a_pipe, 0.62, "flap in %d" % (i + 1))
        n.join(y, end, 0.30, a_pipe, 0.55, name="flap pipe %d" % (i + 1))
        y = n.pipe(split, 0.08, a_pipe, 0.58, name="silencer in %d" % (i + 1))
        y = n.chamber(y, a_pipe, 0.35, 0.009 / 0.35, 0.55, 25.0, name="silencer %d" % (i + 1))
        n.join(y, end, 0.15, a_pipe, 0.50, name="silencer pipe %d" % (i + 1))
        x = n.pipe(end, 0.25, circle(0.075), 0.45, name="tailpipe %d" % (i + 1))
        n.make(x, OPEN, radius=0.0375, noise=TAIL_NOISE,
               distance=_far(0.55 if i == 0 else -0.55))
    return n


def lexus_lfa_exhaust(eng=None):
    """Lexus LFA (1LR-GUE, 72 degree V10, even firing).

    FROM THE CAR (Toyota and Lexus releases):
      * equal-length manifolds into an equal-length dual exhaust;
      * a titanium rear silencer in two stages with a valve: below
        3000 rpm the gas goes through several chambers; above, it passes
        them by into ONE resonance chamber; three stacked tailpipes (by a
        secondary account the valve brings in the upper two, and the
        lower one is the long, quiet path).
    ESTIMATED: 5-into-1 per bank (implied by "equal length" and 144 degree
    firing in a bank), primaries 40 mm x 0.50 m, 3.2 m of 60 mm pipe to
    the rear (front-mid engine), one catalyst per bank, chamber volumes.
    NOT HERE: the intake side -- the surge tank built as a sound radiator
    and its ducts into the cabin -- which is half of the voice of this car.
    """
    from . import presets
    eng = eng or presets.ALL["lfax"]()
    n = Network(note="LFA: layout from Toyota/Lexus, dimensions estimated")
    n.flap_rpm, n.flap_load = 3000.0, 2.0          # by speed alone
    a_pri, a_pipe = circle(0.040), circle(0.060)
    loud, quiet = n.node(), n.node()
    for i, cyls in enumerate(_banks(eng)):
        col = _manifold(n, cyls, 0.50, a_pri)
        x = n.pipe(col, 0.20, a_pipe, 0.92, name="collector %d" % (i + 1))
        x = n.pipe(x, 0.12, 3.0 * a_pipe, 0.85, 40.0, name="catalyst %d" % (i + 1))
        n.make(x, ORIFICE, r=2.0e4)
        x = n.pipe(x, 1.60, a_pipe, 0.68, name="tunnel pipe %da" % (i + 1))
        split = n.pipe(x, 1.60, a_pipe, 0.52, name="tunnel pipe %db" % (i + 1))
        y = _flap(n, split, a_pipe, 0.46, "valve in %d" % (i + 1))
        n.join(y, loud, 0.20, 0.006 / 0.20, 0.42, 4.0, name="resonance chamber %d" % (i + 1))
        y = n.pipe(split, 0.08, a_pipe, 0.44, name="chamber in %d" % (i + 1))
        y = n.chamber(y, a_pipe, 0.25, 0.008 / 0.25, 0.42, 10.0, a_out=circle(0.045),
                      name="chamber %da" % (i + 1))
        y = n.pipe(y, 0.15, circle(0.045), 0.40, name="chamber tube %d" % (i + 1))
        n.join(y, quiet, 0.25, 0.008 / 0.25, 0.38, 10.0, name="chamber %db" % (i + 1))
    for j in range(2):                               # the two upper tailpipes
        y = n.pipe(loud, 0.30, a_pipe, 0.40, name="upper tailpipe %d" % (j + 1))
        n.make(y, OPEN, radius=0.03, noise=TAIL_NOISE, distance=_far(0.0))
    y = n.pipe(quiet, 0.55, a_pipe, 0.36, name="lower tailpipe")
    n.make(y, OPEN, radius=0.03, noise=TAIL_NOISE, distance=_far(0.0))
    return n


def ferrari_f2004_exhaust(eng=None):
    """Ferrari F2004 (Tipo 053, 90 degree V10 on shared crankpins: the
    cylinders fire 90 and 54 degrees apart, not evenly).

    FROM THE CAR AND ITS CONTEMPORARIES (Honda and BMW papers on their 2004
    90 degree V10s; nothing dimensional is published for the 053):
      * 5-into-1 per bank: stepped primaries, a collector, a tailpipe, in
        0.7 mm Inconel (Honda); no catalyst, no silencer;
      * the two tailpipes leave through the top of the bodywork, close to
        the centre line.
    ESTIMATED: primaries 0.32 m stepping 38 -> 44 mm, collector 0.10 m,
    tailpipe 0.30 m x 65 mm, outlets 0.5 m apart; the listener 1 m from
    one of them.
    """
    from . import presets
    eng = eng or presets.ALL["7"]()
    n = Network(note="F2004: 5-into-1 per bank as its contemporaries; dimensions estimated")
    for i, cyls in enumerate(_banks(eng)):
        col = n.node()
        for k in cyls:
            v = n.node(VALVE, index=k)
            y = n.pipe(v, 0.16, circle(0.038), 1.0, 2.0, name="primary %da" % (k + 1))
            n.join(y, col, 0.16, circle(0.044), 0.98, 2.0, name="primary %db" % (k + 1))
        x = n.pipe(col, 0.10, circle(0.060), 0.92, name="collector %d" % (i + 1))
        x = n.pipe(x, 0.30, circle(0.065), 0.85, name="tailpipe %d" % (i + 1))
        n.make(x, OPEN, radius=0.0325, noise=TAIL_NOISE,
               distance=_far(0.25 if i == 0 else -0.25))
    return n


_BY_HAND = {"a3": a3_exhaust, "6": murcielago_exhaust, "4": ferrari_458_exhaust,
            "audiv8": audi_r8_42_exhaust, "lfax": lexus_lfa_exhaust, "5": lexus_lfa_exhaust,
            "7": ferrari_f2004_exhaust}


def exhaust_for(key, eng, guess=False):
    """The network of preset ``key`` -- only where one was written from the
    car.  ``guess=True`` falls back to `exhaust_from_engine` (experiments
    only: Leo, 2026-11-13: no guessed fleet; do the named cars from real
    data first)."""
    f = _BY_HAND.get(key)
    if f:
        return f()
    if guess:
        return exhaust_from_engine(eng)
    raise KeyError("no v2 exhaust has been written for %r yet" % (key,))
