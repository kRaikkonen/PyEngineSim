"""
The playable application: window, rendering, input and the real-time loop.

Controls (mirroring the original Engine Simulator where it makes sense):

    A            toggle ignition on/off
    S  (hold)    engage the starter motor
    Up / Down    throttle (hold to rev; releases back to idle)
    1 / 2 / 3    load a preset engine (single / inline-4 / V8)
    M            mute / unmute audio
    Esc / Q      quit
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from .simulator import Simulator
from .audio import Synthesizer, list_output_devices
from . import presets
from . import config
from .units import nm_to_lbft, nm_to_hp_at

SAMPLE_RATES = [44100, 48000]

# --- palette -----------------------------------------------------------------
BG = (18, 20, 24)
PANEL = (28, 31, 38)
INK = (228, 232, 238)
DIM = (120, 128, 140)
ACCENT = (90, 200, 255)
WARN = (255, 92, 80)
GOOD = (120, 220, 130)
PISTON = (170, 178, 190)
ROD = (140, 146, 158)
FLASH = (255, 168, 60)

WIDTH, HEIGHT = 1100, 680
FPS = 60

# Audio-mixer sliders: (param key, label, min, max).  These bind to
# Synthesizer.params and are dragged live in the in-app console (press C).
SLIDER_DEFS = [
    ("dry", "Firing / bang", 0.0, 1.6),
    ("body", "Body (thickness)", 0.0, 2.0),
    ("drive", "Drive (solid)", 0.0, 1.0),
    ("firing_pitch", "Firing pitch (Hz)", 50.0, 300.0),
    ("crack", "Attack crack", 0.0, 0.8),
    ("attack_deg", "Attack soft (blunt)", 1.0, 25.0),
    ("turbulence", "Fizz / gas noise", 0.0, 1.6),
    ("res1", "Pipe resonance 1", 0.0, 0.8),
    ("res2", "Pipe resonance 2", 0.0, 0.8),
    ("intake", "Intake roar", 0.0, 1.2),
    ("src_reverb", "Explosion reverb", 0.0, 0.6),
    ("reverb", "Reverb (space)", 0.0, 0.5),
    ("cyl_spread", "Cylinder spread", 0.0, 1.0),
    ("eq_low", "EQ low (dB)", -12.0, 12.0),
    ("eq_mid", "EQ mid (dB)", -12.0, 12.0),
    ("eq_high", "EQ high (dB)", -12.0, 12.0),
]

# Firing-pulse timbre presets, cycled with V.  Each sets the single-firing tone.
FIRING_VOICES = [
    ("Balanced", {"pulse_tau": 22.0, "turbulence": 0.80, "body": 0.55,
                  "crack": 0.22, "firing_pitch": 110.0}),
    ("Sharp",    {"pulse_tau": 14.0, "turbulence": 0.60, "body": 0.40,
                  "crack": 0.36, "firing_pitch": 150.0}),
    ("Deep",     {"pulse_tau": 30.0, "turbulence": 0.70, "body": 0.95,
                  "crack": 0.16, "firing_pitch": 75.0}),
    ("Raspy",    {"pulse_tau": 18.0, "turbulence": 1.15, "body": 0.50,
                  "crack": 0.30, "firing_pitch": 130.0}),
    ("Hollow",   {"pulse_tau": 26.0, "turbulence": 0.45, "body": 0.70,
                  "crack": 0.20, "firing_pitch": 95.0}),
]


class App:
    def __init__(self, preset_key="2"):
        pygame.init()
        pygame.display.set_caption("Engine Simulator — Python Edition")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas", 18)
        self.font_big = pygame.font.SysFont("consolas", 44, bold=True)
        self.font_small = pygame.font.SysFont("consolas", 14)

        # load any user engine .json configs as extra presets
        presets.register_user_engines()

        # audio output device + sample-rate selection
        self.devices = list_output_devices()        # [(label, index_or_None)]
        self.device_idx = 0
        self.rate_idx = 0                            # SAMPLE_RATES index
        self.audio_presets = config.list_audio_configs()
        self.audio_idx = -1

        if preset_key not in presets.ALL:
            preset_key = presets.PRESETS[0][0]
        self.current_key = preset_key
        self.sim = Simulator(presets.ALL[preset_key]())
        self.voice_idx = 0        # current firing-timbre voice
        self._status = ""         # transient save/load message
        self._status_t = 0.0
        self.synth = None
        self._make_synth(start=True)

        self._disp_torque = 0.0   # smoothed output torque for the gauges
        self._chip_rects = {}     # preset selector hit-boxes, keyed by preset key
        self.mixer_open = False   # audio console overlay (press C)
        self._drag = None         # slider currently being dragged
        self._build_mixer()
        self.running = True

    # ----------------------------------------------------------- audio device
    def _make_synth(self, start=True):
        """(Re)create the synth on the chosen device + sample rate, preserving
        the current mixer params, cabin and firing voice."""
        saved = dict(self.synth.params) if self.synth else None
        saved_cabin = self.synth.cabin if self.synth else False
        if self.synth:
            self.synth.stop()
        device = self.devices[self.device_idx][1]
        rate = SAMPLE_RATES[self.rate_idx]
        self.synth = Synthesizer(self.sim, sample_rate=rate, device=device)
        if saved is not None:
            self.synth.params.update(saved)
            self.synth.cabin = saved_cabin
        else:
            self._apply_voice()
        if start:
            self.synth.start()

    def cycle_device(self, step=1):
        if len(self.devices) > 1:
            self.device_idx = (self.device_idx + step) % len(self.devices)
            self._make_synth()
            self._flash(f"Output: {self.devices[self.device_idx][0]}")

    def toggle_rate(self):
        self.rate_idx = (self.rate_idx + 1) % len(SAMPLE_RATES)
        self._make_synth()
        self._flash(f"Sample rate: {SAMPLE_RATES[self.rate_idx]} Hz "
                    f"(actual {self.synth.sample_rate})")

    # --------------------------------------------------------- config save/load
    def save_configs(self):
        eng = self.sim.engine
        ep = config.engine_path(eng.name)
        ap = config.audio_path(eng.name)
        config.save_engine(eng, ep)
        config.save_audio(self.synth.params, ap, self.voice_idx, self.synth.cabin)
        presets.register_user_engines()
        self.audio_presets = config.list_audio_configs()
        self._flash(f"Saved engine + audio: {eng.name}")

    def cycle_audio_preset(self):
        self.audio_presets = config.list_audio_configs()
        if not self.audio_presets:
            self._flash("No saved audio presets")
            return
        self.audio_idx = (self.audio_idx + 1) % len(self.audio_presets)
        label, path = self.audio_presets[self.audio_idx]
        data = config.load_audio(path)
        self.synth.params.update(data.get("params", {}))
        self.synth.cabin = bool(data.get("cabin", False))
        self.voice_idx = int(data.get("voice", 0)) % len(FIRING_VOICES)
        self._flash(f"Audio preset: {label}")

    def _flash(self, msg):
        self._status = msg
        self._status_t = 3.0

    def _apply_voice(self):
        self.synth.params.update(FIRING_VOICES[self.voice_idx][1])

    def _build_mixer(self):
        """Lay out the audio-mixer slider tracks over the left panel."""
        panel = pygame.Rect(24, 24, 620, 632)
        x = panel.x + 210
        w = panel.right - 34 - x
        y = panel.y + 38
        self._sliders = []
        for key, label, vmin, vmax in SLIDER_DEFS:
            self._sliders.append({
                "key": key, "label": label, "min": vmin, "max": vmax,
                "track": pygame.Rect(x, y + 6, w, 6), "row_y": y,
            })
            y += 38

    def _set_slider(self, s, mx):
        t = s["track"]
        norm = min(max((mx - t.x) / t.width, 0.0), 1.0)
        self.synth.params[s["key"]] = s["min"] + norm * (s["max"] - s["min"])

    # ----------------------------------------------------------- engine swap
    def load_engine(self, key):
        if key not in presets.ALL or key == self.current_key:
            return
        self.current_key = key
        self.sim = Simulator(presets.ALL[key]())
        self._make_synth(start=True)          # preserves mixer params + device/rate
        self._disp_torque = 0.0

    # ----------------------------------------------------------------- input
    def handle_events(self):
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if self.mixer_open:
                    for s in self._sliders:
                        if s["track"].inflate(12, 26).collidepoint(e.pos):
                            self._drag = s
                            self._set_slider(s, e.pos[0])
                            break
                else:
                    for key, box in self._chip_rects.items():
                        if box.collidepoint(e.pos):
                            self.load_engine(key)
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                self._drag = None
            elif e.type == pygame.MOUSEMOTION and self._drag is not None:
                self._set_slider(self._drag, e.pos[0])
            elif e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif e.key == pygame.K_c:
                    self.mixer_open = not self.mixer_open
                elif e.key == pygame.K_v:
                    self.voice_idx = (self.voice_idx + 1) % len(FIRING_VOICES)
                    self._apply_voice()
                elif e.key == pygame.K_i:
                    self.synth.cabin = not self.synth.cabin
                elif e.key == pygame.K_F2:
                    self.save_configs()
                elif e.key == pygame.K_F3:
                    self.cycle_audio_preset()
                elif e.key == pygame.K_F4:
                    self.cycle_device()
                elif e.key == pygame.K_F5:
                    self.toggle_rate()
                elif e.key == pygame.K_a:
                    self.sim.ignition_on = not self.sim.ignition_on
                elif e.key == pygame.K_m:
                    self.synth.volume = 0.0 if self.synth.volume > 0 else 0.6
                elif e.key == pygame.K_x:
                    if not self.sim.drivetrain.auto:
                        self.sim.drivetrain.shift_up()
                elif e.key == pygame.K_z:
                    if not self.sim.drivetrain.auto:
                        self.sim.drivetrain.shift_down()
                elif e.key == pygame.K_t:
                    self.sim.drivetrain.auto = not self.sim.drivetrain.auto
                elif e.unicode in presets.ALL:
                    self.load_engine(e.unicode)

        keys = pygame.key.get_pressed()
        self.sim.starter_engaged = keys[pygame.K_s]

        # Clutch (manual only): tapping Z/X auto-blips it (paddle-shift, no need
        # to hold anything), holding Shift fully disengages it for launches.
        dt = self.sim.drivetrain
        shift_held = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
        dt.manual_clutch(shift_held, 1.0 / FPS)
        # Up = throttle (ramps up while held, falls back when released).
        # Down = brake pedal.
        if keys[pygame.K_UP]:
            self.sim.throttle = min(1.0, self.sim.throttle + 0.04)
        else:
            self.sim.throttle = max(0.0, self.sim.throttle - 0.04)
        dt.brake = 1.0 if keys[pygame.K_DOWN] else 0.0

    # --------------------------------------------------------------- update
    def update(self, dt):
        self.sim.drivetrain.auto_control(
            self.sim.rpm, self.sim.throttle, self.sim.engine.redline_rpm, dt)
        self.sim.step(dt)
        # Show indicated (combustion) torque — the engine's output capacity.
        # Net brake torque averages to ~0 whenever rpm is steady, so it makes a
        # poor gauge for free revving; gas torque is the lively, meaningful one.
        self._disp_torque += (self.sim.gas_torque - self._disp_torque) * 0.08
        if self._status_t > 0.0:
            self._status_t -= dt

    # ----------------------------------------------------------- draw: parts
    def draw(self):
        self.screen.fill(BG)
        left = pygame.Rect(24, 24, 620, 632)
        if self.mixer_open:
            self._draw_mixer(left)
        else:
            self._draw_engine_panel(left)
        self._draw_gauges(pygame.Rect(664, 24, 412, 632))
        pygame.display.flip()

    def _draw_mixer(self, rect):
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
        self.screen.blit(self.font.render("AUDIO MIXER", True, INK),
                         (rect.x + 18, rect.y + 18))
        self.screen.blit(self.font_small.render("drag the sliders  ·  C to close",
                                                 True, DIM), (rect.x + 150, rect.y + 24))
        P = self.synth.params
        for s in self._sliders:
            key, t = s["key"], s["track"]
            val = P.get(key, 0.0)
            norm = (val - s["min"]) / (s["max"] - s["min"]) if s["max"] > s["min"] else 0
            norm = min(max(norm, 0.0), 1.0)
            # label + value
            self.screen.blit(self.font_small.render(s["label"], True, INK),
                             (rect.x + 22, s["row_y"] + 2))
            vtxt = f"{val:5.0f}" if s["max"] > 20 else f"{val:5.2f}"
            self.screen.blit(self.font_small.render(vtxt, True, ACCENT),
                             (t.right + 6, s["row_y"] + 2))
            # track + fill + handle
            pygame.draw.rect(self.screen, (44, 48, 56), t, border_radius=3)
            fill = t.copy(); fill.width = int(t.width * norm)
            pygame.draw.rect(self.screen, ACCENT, fill, border_radius=3)
            hx = t.x + int(t.width * norm)
            pygame.draw.circle(self.screen, INK, (hx, t.centery), 8)

    def _draw_engine_panel(self, rect):
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
        sim = self.sim
        eng = sim.engine
        n = eng.num_cylinders

        # Modular preset selector — click a chip or press its number key.
        self._draw_preset_bar(rect)

        title = self.font.render(eng.name, True, INK)
        self.screen.blit(title, (rect.x + 18, rect.y + 48))
        voice = FIRING_VOICES[self.voice_idx][0]
        cab = "   ·   CABIN (I)" if self.synth.cabin else ""
        self.screen.blit(self.font_small.render(f"firing voice: {voice}  (V){cab}",
                                                True, ACCENT), (rect.x + 18, rect.y + 72))

        self._draw_telemetry(rect)

        # audio output device + sample rate, and the transient save/load status
        dev = self.devices[self.device_idx][0]
        rate = self.synth.sample_rate if self.synth else 0
        self.screen.blit(self.font_small.render(
            f"audio out: {dev}  ·  {rate} Hz   (F4 device  F5 rate)", True, DIM),
            (rect.x + 26, rect.y + 240))
        if self._status_t > 0.0:
            self.screen.blit(self.font_small.render(self._status, True, GOOD),
                             (rect.x + 26, rect.y + 262))

        # Layout: one column per cylinder.
        top = rect.y + 92
        bottom = rect.bottom - 40
        col_w = rect.width / n
        bore_h = (bottom - top) * 0.5
        bore_w = min(col_w * 0.5, 64)
        crank_r = min(col_w * 0.16, 26)

        for i in range(n):
            cx = rect.x + col_w * (i + 0.5)
            crank_cy = bottom - crank_r - 4
            bore_top = crank_cy - crank_r - bore_h

            cyl = eng.cylinders[i]
            phi = sim.cycle_phase_deg(i)
            theta = math.radians(phi % 360.0)
            frac = sim.piston_fraction(i)              # 0 TDC .. 1 BDC

            # Bore walls
            bx = cx - bore_w / 2
            pygame.draw.rect(self.screen, (40, 44, 52),
                             (bx, bore_top, bore_w, bore_h + crank_r), border_radius=6)
            pygame.draw.rect(self.screen, (64, 70, 82),
                             (bx, bore_top, bore_w, bore_h + crank_r), width=2,
                             border_radius=6)

            # Combustion flash at the top of the bore during a firing power stroke
            if sim.ignition_on and not sim._fuel_cut and 360 <= phi < 430:
                over = max(sim.cylinder_pressure[i] - 101325.0, 0.0)
                glow = min(over / (5.0 * 101325.0), 1.0)
                if glow > 0.02:
                    s = pygame.Surface((bore_w - 6, 30), pygame.SRCALPHA)
                    a = int(220 * glow)
                    s.fill((FLASH[0], FLASH[1], FLASH[2], a))
                    self.screen.blit(s, (bx + 3, bore_top + 3))

            # Piston travel range (top = TDC, lower = BDC)
            piston_h = 26
            travel = bore_h - piston_h
            py = bore_top + 4 + frac * travel
            pin = (cx, py + piston_h / 2)
            pygame.draw.rect(self.screen, PISTON,
                             (bx + 4, py, bore_w - 8, piston_h), border_radius=4)

            # Crank + connecting rod
            crank_pin = (cx + crank_r * math.sin(theta),
                         crank_cy + crank_r * math.cos(theta))
            pygame.draw.circle(self.screen, (52, 56, 66), (cx, crank_cy), crank_r)
            pygame.draw.circle(self.screen, (72, 78, 90), (cx, crank_cy), crank_r, 2)
            pygame.draw.line(self.screen, ROD, pin, crank_pin, 5)
            pygame.draw.circle(self.screen, ACCENT,
                               (int(crank_pin[0]), int(crank_pin[1])), 5)

            label = self.font_small.render(f"{i+1}", True, DIM)
            self.screen.blit(label, (cx - 4, bottom + 8))

    def _draw_telemetry(self, rect):
        """Physical engine readouts (manifold pressure, VE, AFR, airflow, O2),
        drawn in the empty space above the cylinders."""
        t = self.sim.telemetry()
        x, y = rect.x + 26, rect.y + 96
        self.screen.blit(self.font_small.render("TELEMETRY", True, DIM), (x, y))
        y += 22
        rows = [
            ("MANIFOLD", f"{t['map_kpa']:5.0f} kPa  ({t['vacuum_inhg']:+.0f} inHg)"),
            ("VOL. EFF.", f"{t['ve_pct']:5.0f} %"),
            ("AIR / FUEL", f"{t['afr']:5.1f}   (lambda {t['lambda']:.2f})"),
            ("AIRFLOW", f"{t['scfm']:5.0f} SCFM"),
            ("EXHAUST O2", f"{t['o2_pct']:5.1f} %"),
        ]
        for label, val in rows:
            self.screen.blit(self.font_small.render(label, True, DIM), (x, y))
            self.screen.blit(self.font_small.render(val, True, INK), (x + 110, y))
            y += 22

    def _draw_preset_bar(self, rect):
        """Selectable engine chips (wrap to more rows for cfg engines)."""
        self._chip_rects = {}
        x0, h = rect.x + 16, 24
        x, y = x0, rect.y + 10
        for key, label, _factory in presets.PRESETS:
            text = f"{key} {label}"
            surf = self.font_small.render(text, True,
                                          BG if key == self.current_key else INK)
            w = surf.get_width() + 18
            if x + w > rect.right - 12:           # wrap to next row
                x, y = x0, y + h + 4
            box = pygame.Rect(x, y, w, h)
            if key == self.current_key:
                pygame.draw.rect(self.screen, ACCENT, box, border_radius=6)
            else:
                pygame.draw.rect(self.screen, (40, 44, 52), box, border_radius=6)
                pygame.draw.rect(self.screen, (64, 70, 82), box, width=1,
                                 border_radius=6)
            self.screen.blit(surf, (x + 9, y + 4))
            self._chip_rects[key] = box
            x += w + 7
        self._chip_bottom = y + h           # where the chips end (for layout)

    # ----------------------------------------------------------- draw: gauges
    def _draw_gauges(self, rect):
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
        sim = self.sim
        eng = sim.engine

        # --- tachometer ---
        cx, cy, r = rect.centerx, rect.y + 158, 132
        self._draw_tach(cx, cy, r, sim.rpm, eng.redline_rpm)

        # --- digital readouts ---
        tq = self._disp_torque
        hp = nm_to_hp_at(max(tq, 0.0), max(sim.rpm, 1.0))
        dt = sim.drivetrain
        y = rect.y + 296
        rows = [
            ("RPM", f"{sim.rpm:6.0f}", ACCENT),
            ("TORQUE", f"{tq:6.0f} Nm  ({nm_to_lbft(tq):.0f} lb-ft)", INK),
            ("POWER", f"{hp:6.0f} hp", INK),
            ("THROTTLE", f"{sim.throttle*100:5.0f} %", INK),
            ("GEAR", f"{dt.gear_name:>3}  {'AUTO' if dt.auto else 'MANUAL'}", GOOD),
            ("SPEED", f"{dt.speed_kmh:6.0f} km/h", INK),
        ]
        for label, value, col in rows:
            self.screen.blit(self.font.render(label, True, DIM), (rect.x + 28, y))
            self.screen.blit(self.font.render(value, True, col), (rect.x + 150, y))
            y += 28

        # --- status indicators ---
        y += 6
        self._status_dot(rect.x + 28, y, "IGNITION", sim.ignition_on, GOOD, WARN)
        self._status_dot(rect.x + 230, y, "STARTER", sim.starter_engaged, ACCENT, DIM)
        y += 26
        self._status_dot(rect.x + 28, y, "REV LIMIT", sim._fuel_cut, WARN, DIM)
        self._status_dot(rect.x + 230, y, "AUDIO",
                         self.synth.enabled and self.synth.volume > 0, GOOD, DIM)
        y += 26
        self._status_dot(rect.x + 28, y, "CLUTCH IN", dt.clutch < 0.5, ACCENT, DIM)
        self._status_dot(rect.x + 230, y, "IN GEAR", dt.gear > 0, GOOD, DIM)

        # --- throttle bar ---
        y += 26
        bar = pygame.Rect(rect.x + 28, y, rect.width - 56, 12)
        pygame.draw.rect(self.screen, (44, 48, 56), bar, border_radius=6)
        fill = bar.copy()
        fill.width = int(bar.width * sim.throttle)
        pygame.draw.rect(self.screen, ACCENT, fill, border_radius=6)

        # --- help ---
        help_lines = [
            "A ignition  S starter  Up throttle  Down brake  T auto",
            "Z/X shift  Shift clutch  V voice  I cabin  C mixer  M mute",
            "F2 save cfg  F3 audio preset  1-7 engine  Esc quit",
        ]
        hy = rect.bottom - 62
        for line in help_lines:
            self.screen.blit(self.font_small.render(line, True, DIM),
                             (rect.x + 28, hy))
            hy += 18

    def _draw_tach(self, cx, cy, r, rpm, redline):
        # Sweep from 225deg down to -45deg (270deg span).
        start, end = math.radians(225), math.radians(-45)
        span = start - end
        max_rpm = math.ceil((redline + 500) / 1000.0) * 1000

        # Tick marks + redline arc
        for k in range(0, int(max_rpm) + 1, 1000):
            a = start - span * (k / max_rpm)
            over = k >= redline
            r0 = r - (14 if k % 1000 == 0 else 8)
            col = WARN if over else DIM
            p0 = (cx + r0 * math.cos(a), cy - r0 * math.sin(a))
            p1 = (cx + r * math.cos(a), cy - r * math.sin(a))
            pygame.draw.line(self.screen, col, p0, p1, 3)
            num = self.font_small.render(str(k // 1000), True, col)
            lx = cx + (r - 32) * math.cos(a) - 6
            ly = cy - (r - 32) * math.sin(a) - 8
            self.screen.blit(num, (lx, ly))

        pygame.draw.circle(self.screen, (44, 48, 56), (cx, cy), r, 3)

        # Needle
        frac = min(max(rpm / max_rpm, 0.0), 1.0)
        a = start - span * frac
        tip = (cx + (r - 18) * math.cos(a), cy - (r - 18) * math.sin(a))
        needle_col = WARN if rpm >= redline else ACCENT
        pygame.draw.line(self.screen, needle_col, (cx, cy), tip, 4)
        pygame.draw.circle(self.screen, INK, (cx, cy), 8)

        cap = self.font_small.render("x1000 rpm", True, DIM)
        self.screen.blit(cap, (cx - 34, cy + 40))

    def _status_dot(self, x, y, label, on, on_col, off_col):
        col = on_col if on else off_col
        pygame.draw.circle(self.screen, col, (x + 7, y + 9), 7)
        self.screen.blit(self.font_small.render(label, True, INK), (x + 22, y))

    # ------------------------------------------------------------------ loop
    def run(self):
        while self.running:
            dt = self.clock.tick(FPS) / 1000.0
            dt = min(dt, 0.05)              # clamp huge hitches
            self.handle_events()
            self.update(dt)
            self.draw()
        self.synth.stop()
        pygame.quit()


def main():
    App().run()


if __name__ == "__main__":
    main()
