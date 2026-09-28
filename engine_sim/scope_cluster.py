"""The oscilloscope cluster, laid out and drawn as AngeTheGreat's engine-sim
draws it (src/oscilloscope.cpp, src/oscilloscope_cluster.cpp).

What makes his scopes look alive is not the signals but how they are kept and
drawn: every scope is a RING BUFFER of (x, y) points sampled from the running
engine, plotted in the order they arrived, the old points thin and faint and
the newest thick -- so every cycle lands on top of the last and the trace
carries its own history.  The y range only grows.  Where x wraps (the cycle
starts again) the line either breaks or, for the valve-lift and P-V scopes,
is drawn straight back (the diagonal strokes on his screen).

The layout is his: a 3 x 4 grid, the top half one big FOCUS scope with its
title; below it waveform / total exhaust flow / valve lift and torque-power /
pressure-volume / flow.  Click a small scope to put it in focus.

Signals come from our engine: the gas solver's own per-cylinder exhaust and
intake flows at the running operating point (the synthesizer's bake), the
Wiebe cylinder pressure, the cam the valvetrain animates, the audio output --
sampled every frame over the crank angles the engine actually swept.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

# his palette (engine_sim_application.cpp)
BG = (0x0E, 0x10, 0x12)
FG = (0xFF, 0xFF, 0xFF)
ORANGE = (0xF4, 0x80, 0x2A)
BLUE = (0x77, 0xCE, 0xE0)
PINK = (0xF3, 0x94, 0xBE)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Trace:
    """One oscilloscope: a ring of (x, y), an x window and a growing y range."""

    def __init__(self, size, color, x_min, x_max, y_min=0.0, y_max=0.0,
                 width=2, reverse=False, grow_x=False):
        self.size = int(size)
        self.x = np.zeros(self.size)
        self.y = np.zeros(self.size)
        self.w = 0                        # write index
        self.n = 0                        # points held
        self.color = color
        self.x_min, self.x_max = x_min, x_max
        self.y_min, self.y_max = y_min, y_max
        self.width = width
        self.reverse = reverse
        self.grow_x = grow_x

    def reset(self):
        self.w = self.n = 0

    def add(self, xs, ys):
        xs = np.asarray(xs, dtype=np.float64).ravel()
        ys = np.asarray(ys, dtype=np.float64).ravel()
        m = min(len(xs), len(ys))
        if m == 0:
            return
        if m > self.size:
            xs, ys, m = xs[-self.size:], ys[-self.size:], self.size
        idx = (self.w + np.arange(m)) % self.size
        self.x[idx] = xs[:m]
        self.y[idx] = ys[:m]
        self.w = (self.w + m) % self.size
        self.n = min(self.n + m, self.size)
        # his dynamic resize: the range grows to 110 % of anything it meets
        hi, lo = float(np.max(ys)), float(np.min(ys))
        if hi + abs(0.1 * hi) >= self.y_max:
            self.y_max = hi + abs(0.1 * hi)
        if lo - abs(0.1 * lo) <= self.y_min:
            self.y_min = lo - abs(0.1 * lo)
        if self.grow_x:
            hx, lx = float(np.max(xs)), float(np.min(xs))
            if hx + abs(0.1 * hx) >= self.x_max:
                self.x_max = hx + abs(0.1 * hx)
            if lx - abs(0.1 * lx) <= self.x_min:
                self.x_min = lx - abs(0.1 * lx)

    def ordered(self):
        if self.n == 0:
            return np.zeros(0), np.zeros(0)
        idx = (self.w - self.n + np.arange(self.n)) % self.size
        return self.x[idx], self.y[idx]

    def draw(self, surf, rect, zero=True):
        xs, ys = self.ordered()
        if zero and self.y_max > self.y_min:
            zy = rect.bottom - (0.0 - self.y_min) / (self.y_max - self.y_min) * rect.h
            if rect.top <= zy <= rect.bottom:
                pygame.draw.line(surf, _mix(BG, FG, 0.08), (rect.left, int(zy)),
                                 (rect.right - 1, int(zy)), 1)
        n = len(xs)
        if n < 2 or self.x_max <= self.x_min or self.y_max <= self.y_min:
            return
        px = rect.left + (xs - self.x_min) / (self.x_max - self.x_min) * (rect.w - 1)
        py = rect.bottom - 1 - (ys - self.y_min) / (self.y_max - self.y_min) * (rect.h - 1)
        px = np.clip(px, rect.left, rect.right - 1)
        py = np.clip(py, rect.top, rect.bottom - 1)
        # where the line is detached (x ran backwards, or a big jump)
        dx = np.diff(xs)
        brk = (dx < 0) | (np.abs(np.diff(px)) > 100)
        if self.reverse:
            brk = np.abs(np.diff(px)) > 1e9
        s = np.arange(n) / n                  # age: 0 oldest .. 1 newest
        pts = np.column_stack((px, py)).astype(np.int32)
        # his taper: width ~ max(s, 0.5 px) x lineWidth, the last 5 % thicker;
        # drawn here as bands, older bands also fainter
        bands = ((0.0, 0.25, 1, 0.30), (0.25, 0.5, 1, 0.55), (0.5, 0.75, 1, 0.80),
                 (0.75, 0.95, self.width, 1.0), (0.95, 1.001, self.width + 2, 1.0))
        for a, b, wd, br in bands:
            i0 = int(a * n)
            i1 = min(int(b * n) + 1, n)
            if i1 - i0 < 2:
                continue
            col = _mix(BG, self.color, br)
            start = i0
            for k in range(i0, i1 - 1):
                if brk[k]:
                    if k + 1 - start >= 2:
                        pygame.draw.lines(surf, col, False, pts[start:k + 1].tolist(), wd)
                    start = k + 1
            if i1 - start >= 2:
                pygame.draw.lines(surf, col, False, pts[start:i1].tolist(), wd)


class ScopeCluster:
    """The eight scopes of his cluster and the focus window."""

    FOCUS_TITLES = {"wave": "Waveform", "flow_total": "Total Exhaust Flow",
                    "lift": "Valve Lift", "tp": "Torque/Power",
                    "pv": "pressure-volume", "flow": "Flow"}

    def __init__(self):
        self.focus = "flow_total"          # his default focus
        self._rects = {}
        self.reset()

    def reset(self):
        c4 = 4.0 * math.pi
        self.t = {
            "wave": Trace(882, BLUE, 0.0, 1.0, -1.5, 1.5, width=2),
            "flow_total": Trace(1024, ORANGE, 0.0, c4, -1e-9, 1e-9),
            "lift_in": Trace(1024, BLUE, 0.0, c4, -2e-4, 2e-4),
            "lift_ex": Trace(1024, ORANGE, 0.0, c4, -2e-4, 2e-4),
            "torque": Trace(100, ORANGE, 0.0, 1.0, 0.0, 0.0, grow_x=True),
            "power": Trace(100, PINK, 0.0, 1.0, 0.0, 0.0, grow_x=True),
            "pv": Trace(1024, ORANGE, 0.0, 1e-9, -1.0, 1.0, reverse=True, grow_x=True),
            "flow_in": Trace(1024, BLUE, 0.0, c4, -1e-9, 1e-9),
            "flow_ex": Trace(1024, ORANGE, 0.0, c4, -1e-9, 1e-9),
            "moles": Trace(1024, FG, 0.0, c4, -1e-9, 1e-9, width=3),
        }
        self._phase_prev = None
        self._tp_timer = 0.0
        self._wave_n = 0
        self._moles_n = 0.0

    # ------------------------------------------------------------ sampling
    def sample(self, app, dt):
        sim, synth = app.sim, app.synth
        eng = sim.engine
        rpm = max(float(sim.rpm), 0.0)
        # the crank angles swept this frame (the last cycle of them at most)
        phase = float(sim.cycle_phase_deg(0))
        sweep = min(rpm * 6.0 * dt, 720.0)
        if sweep < 0.5:
            self._phase_prev = phase
        else:
            k = int(min(max(sweep / 4.0, 4.0), 120.0))
            ang = np.mod(phase - sweep + sweep * (np.arange(k) + 1) / k, 720.0)
            self._sample_cycle(app, ang)
        self._phase_prev = phase
        # torque / power against rpm, every 0.25 s (his update period)
        self._tp_timer -= dt
        if self._tp_timer <= 0.0:
            self._tp_timer = 0.25
            tq = float(getattr(app, "_disp_torque", sim.gas_torque))
            self.t["torque"].add([rpm], [tq])
            self.t["power"].add([rpm], [tq * rpm * 2 * math.pi / 60.0 / 1000.0])
            for a, b in (("torque", "power"),):
                ta, tb = self.t[a], self.t[b]
                ta.y_min = tb.y_min = min(ta.y_min, tb.y_min)
                ta.y_max = tb.y_max = max(ta.y_max, tb.y_max)
                ta.x_max = tb.x_max = max(ta.x_max, tb.x_max)
        # the audio: every 4th output sample, x sweeping a 0.1 s window
        ring = getattr(synth, "_scope_ring", None) if synth is not None else None
        if ring is not None:
            n_tot = int(getattr(synth, "_scope_n", 0))
            new = n_tot - self._wave_n
            if new > 0:
                new = min(new, len(ring))
                idx = np.arange(n_tot - new, n_tot)
                win = max(int(0.1 * synth.sample_rate), 64)   # a 0.1 s sweep
                tw = self.t["wave"]
                tw.x_max = float(win)
                tw.add((4 * idx) % win, ring[idx % len(ring)])
            self._wave_n = n_tot

    def _sample_cycle(self, app, ang):
        sim, synth = app.sim, app.synth
        eng = sim.engine
        cyl = eng.cylinders[0]
        x = np.radians(ang)                     # the cycle, 0 .. 4 pi
        # this firing's own strength (the audio's per-firing scatter)
        fs = 1.0
        if synth is not None and getattr(synth, "_fs_a", None) is not None:
            fs = float(synth._fs_a[0])
        # the gas solver's own flows for one cylinder at this operating point
        pt = None
        if synth is not None and getattr(synth, "_inl", None):
            try:
                pt = synth._inl_point(sim.rpm, sim._manifold_pressure() / 101325.0)
            except Exception:
                pt = None
        p_man = sim._manifold_pressure()
        burning = sim.ignition_on and not getattr(sim, "_fuel_cut", False) \
            and not getattr(sim, "_shift_cut", False)
        kb = getattr(sim, "_k_burn", 1.0)
        pc = np.array([sim._cylinder_pressure(cyl, float(a), p_man, burning, kb)
                       for a in ang])
        pc = 101325.0 + (pc - 101325.0) * fs
        flows = pt is not None
        if flows:
            # moles per crank degree -> mol/s at this speed
            deg = synth._inl_deg
            k_s = max(sim.rpm, 1.0) * 6.0
            ex1 = np.interp(ang, deg, pt[0], period=720.0) * fs * k_s
            in1 = np.interp(ang, deg, pt[1], period=720.0) * k_s
            offs = getattr(sim, "_offset_deg", [0.0] * eng.num_cylinders)
            tot = np.zeros(len(ang))
            for o in offs:
                tot += np.interp(np.mod(ang + (o - offs[0]), 720.0), deg, pt[0],
                                 period=720.0)
            tot *= fs * k_s
        # valve lift, the cam the valvetrain animates (metres, as his thou)
        ivo, dur_i, lift_i, evo, dur_e = app._cam_state(eng, sim.rpm)
        l_max = 0.28 * cyl.bore * 0.39
        li = app._valve_lift(ang, ivo, dur_i) * lift_i * l_max
        le = app._valve_lift(ang, evo, dur_e) * l_max
        # the cylinder's volume and the gas it holds
        th = np.radians(np.mod(ang, 360.0))
        r, l = cyl.crank_radius, cyl.rod_length
        vol = cyl.clearance_volume + cyl.piston_area * (
            (r + l) - (r * np.cos(th) + np.sqrt(l * l - (r * np.sin(th)) ** 2)))
        n_mol = pc * vol / (8.314 * 900.0)
        t = self.t
        t["lift_in"].add(x, li)
        t["lift_ex"].add(x, le)
        t["pv"].add(vol, np.sqrt(pc))
        t["moles"].add(x, n_mol)
        if flows:                       # (only the solver's flows, one unit)
            t["flow_total"].add(x, tot)
            t["flow_in"].add(x, in1)
            t["flow_ex"].add(x, ex1)
        for a, b in (("lift_in", "lift_ex"), ("flow_in", "flow_ex")):
            ta, tb = t[a], t[b]
            ta.y_min = tb.y_min = min(ta.y_min, tb.y_min)
            ta.y_max = tb.y_max = max(ta.y_max, tb.y_max)

    # ------------------------------------------------------------- drawing
    def _groups(self):
        t = self.t
        return {"wave": [t["wave"]], "flow_total": [t["flow_total"]],
                "lift": [t["lift_in"], t["lift_ex"]],
                "tp": [t["torque"], t["power"]], "pv": [t["pv"]],
                "flow": [t["flow_in"], t["flow_ex"], t["moles"]]}

    def draw(self, surf, rect, font_title, font_small, tr=lambda s: s):
        pygame.draw.rect(surf, BG, rect)
        cw, rh = rect.w / 3.0, rect.h / 4.0
        frame = _mix(BG, FG, 0.55)
        groups = self._groups()
        # the focus: the top half, a title band then the body
        focus = pygame.Rect(rect.x, rect.y, rect.w, int(2 * rh))
        title_h = 39
        ftitle = pygame.Rect(focus.x, focus.y, focus.w, title_h)
        fbody = pygame.Rect(focus.x, focus.y + title_h, focus.w, focus.h - title_h)
        pygame.draw.rect(surf, frame, ftitle, 1)
        pygame.draw.rect(surf, frame, fbody, 1)
        surf.blit(font_title.render(tr(self.FOCUS_TITLES[self.focus]).upper(), True, FG),
                  (ftitle.x + 20, ftitle.y + 8))
        for tr_ in groups[self.focus]:
            tr_.draw(surf, fbody.inflate(-4, -4))
        # the six small scopes
        cells = {"wave": (0, 2), "flow_total": (1, 2), "lift": (2, 2),
                 "tp": (0, 3), "pv": (1, 3), "flow": (2, 3)}
        self._rects = {}
        for key, (cx, cy) in cells.items():
            r = pygame.Rect(int(rect.x + cx * cw), int(rect.y + cy * rh),
                            int(cw), int(rh))
            self._rects[key] = r
            sel = key == self.focus
            pygame.draw.rect(surf, frame if not sel else FG, r, 1)
            body = r.inflate(-4, -4)
            for tr_ in groups[key]:
                tr_.draw(surf, body)
            lab = font_small.render(tr(self.FOCUS_TITLES[key]), True, _mix(BG, FG, 0.35))
            surf.blit(lab, (r.x + 4, r.y + 2))

    def click(self, pos):
        """A small scope clicked: put it in focus (True).  Else False."""
        for key, r in self._rects.items():
            if r.collidepoint(pos):
                self.focus = key
                return True
        return False
