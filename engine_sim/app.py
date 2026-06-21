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
from .telemetry import ForzaTelemetry, FORZA_PORT
from . import presets
from . import config
from .units import nm_to_lbft, nm_to_hp_at, rpm_to_rads

SAMPLE_RATES = [44100, 48000]


def _open_file_dialog(title, initialdir, save=False, default=""):
    """Native OS open/save file dialog via tkinter -> path or None."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        kw = dict(title=title, initialdir=initialdir,
                  filetypes=[("JSON config", "*.json"), ("All files", "*.*")])
        if save:
            path = filedialog.asksaveasfilename(defaultextension=".json",
                                                initialfile=default, **kw)
        else:
            path = filedialog.askopenfilename(**kw)
        root.destroy()
        return path or None
    except Exception as exc:
        print(f"[dialog] {exc}")
        return None

# --- palette (iOS 6 skeuomorphic: glossy, gradients, bevels) ------------------
BG = (28, 30, 34)               # dark "linen" backdrop
BG_TOP = (46, 49, 55)
BG_BOT = (22, 24, 28)
PANEL = (60, 64, 72)            # legacy flat fallback
PANEL_TOP = (78, 83, 92)        # panel gradient (glossy slab)
PANEL_BOT = (46, 49, 56)
BEVEL_HI = (150, 158, 170)      # 1px light top edge
BEVEL_LO = (16, 17, 20)         # dark bottom edge
INK = (236, 240, 245)
DIM = (150, 158, 170)
ACCENT = (74, 164, 255)         # iOS 6 glossy blue
WARN = (255, 92, 80)
GOOD = (120, 220, 130)
PISTON = (196, 202, 212)
ROD = (150, 156, 168)
FLASH = (255, 168, 60)
# glossy button gradients (top -> bottom)
BTN_HI = (104, 110, 122)
BTN_LO = (66, 70, 80)
BTN_HOT_HI = (124, 132, 146)
BTN_HOT_LO = (84, 90, 102)
BTN_ON_HI = (108, 186, 255)     # active = blue glass
BTN_ON_LO = (28, 108, 224)

WIDTH, HEIGHT = 1100, 680
FPS = 60

# Friendly transmission labels for the HUD (sets the auto-shift feel).
_GBX_LABEL = {"dct": "DCT", "single": "single-clutch", "at": "AT", "manual": "manual"}

# Audio-mixer sliders: (param key, label, min, max).  These bind to
# Synthesizer.params and are dragged live in the in-app console (press C).
SLIDER_DEFS = [
    ("master", "MASTER volume", 0.0, 1.2),
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
    ("super_vol", "Supercharger whine", 0.0, 1.2),
    ("turbo_vol", "Turbo spool / BOV", 0.0, 1.2),
    ("spool_reverb", "Spool reverb", 0.0, 0.6),
    ("gearbox_vol", "Straight-cut whine", 0.0, 1.2),
    ("hybrid_vol", "Electric / e-turbo", 0.0, 1.2),
    ("eq_low", "EQ low (dB)", -12.0, 12.0),
    ("eq_mid", "EQ mid (dB)", -12.0, 12.0),
    ("eq_high", "EQ high (dB)", -12.0, 12.0),
    ("presence", "Presence (bite)", -12.0, 12.0),
]

# Firing-pulse timbre presets, cycled with V.  Each sets the single-firing tone.
FIRING_VOICES = [
    ("Balanced", {"pulse_tau": 22.0, "turbulence": 0.72, "body": 1.05,
                  "crack": 0.16, "firing_pitch": 105.0}),
    ("Sharp",    {"pulse_tau": 14.0, "turbulence": 0.60, "body": 0.62,
                  "crack": 0.30, "firing_pitch": 145.0}),
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
        # The whole UI is drawn onto a fixed-size canvas, then scaled to fit a
        # freely resizable OS window — so you can drag the window to any size
        # (or maximise it) and everything scales cleanly, keeping its layout.
        self.window = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        self.screen = pygame.Surface((WIDTH, HEIGHT))
        self._win_size = (WIDTH, HEIGHT)
        self._draw_offset = (0, 0)
        self._grad_cache = {}     # cached gradient/gloss surfaces (iOS 6 skin)
        self._tele_smooth = {}    # eased telemetry values (calm gauge needles)
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

        # Forza telemetry mode: drive the sound from a real game's broadcast rpm
        self.telemetry = None
        self.telemetry_mode = False
        self.g_spatial = False    # drift the spatial dot from Forza G-force

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
        self._buttons = []        # toolbar buttons (rebuilt each frame)
        self._open_menu = None    # active dropdown {items, rect, item_rects}
        self._build_mixer()
        self.running = True

    # ------------------------------------------------------------- toolbar
    def _toolbar_defs(self):
        """Button definitions: (label, callback, active_fn_or_None, row)."""
        dt = self.sim.drivetrain
        dev = self.devices[self.device_idx][0]
        rate = SAMPLE_RATES[self.rate_idx]
        sy = self.synth
        return [
            ("Demo cars ▾", self._menu_demo, None, 0),
            ("Load car…", self.load_car_dialog, None, 0),
            ("Load EQ…", self.load_eq_dialog, None, 0),
            ("Save…", self.save_dialog, None, 0),
            ("Mixer / EQ", lambda: setattr(self, "mixer_open", not self.mixer_open),
             lambda: self.mixer_open, 0),
            (f"Out: {dev} ▾", self._menu_device, None, 1),
            (f"{rate // 1000}.{(rate % 1000)//100}kHz", self.toggle_rate, None, 1),
            ("Auto" if dt.auto else "Manual", lambda: setattr(dt, "auto", not dt.auto),
             lambda: dt.auto, 1),
            ("Cabin", lambda: setattr(sy, "cabin", not sy.cabin),
             lambda: sy.cabin, 1),
            ("Gear whine", lambda: setattr(sy, "straight_cut", not sy.straight_cut),
             lambda: sy.straight_cut, 1),
            ("GPF", lambda: setattr(sy, "gpf", not sy.gpf), lambda: sy.gpf, 2),
            ("Cat", lambda: setattr(sy, "cat", not sy.cat), lambda: sy.cat, 2),
            ("Flutter", lambda: setattr(sy, "flutter", not sy.flutter),
             lambda: sy.flutter, 2),
            ("Hybrid", lambda: setattr(self.sim, "hybrid_on", not self.sim.hybrid_on),
             lambda: self.sim.hybrid_on and self.sim.engine.hybrid_kw > 0, 2),
            ("Forza", self.toggle_telemetry, lambda: self.telemetry_mode, 2),
            ("G-pad", lambda: setattr(self, "g_spatial", not self.g_spatial),
             lambda: self.g_spatial, 2),
        ]

    def _rebuild_toolbar(self, panel):
        defs = self._toolbar_defs()
        rows = {}
        for label, cb, active, row in defs:
            rows.setdefault(row, []).append((label, cb, active))
        self._buttons = []
        y = panel.y + 12
        for ri in sorted(rows):
            x = panel.x + 14
            for label, cb, active in rows[ri]:
                w = self.font_small.size(label)[0] + 20
                self._buttons.append({"label": label, "cb": cb, "active": active,
                                      "rect": pygame.Rect(x, y, w, 26)})
                x += w + 6
            y += 32
        self._toolbar_bottom = y

    def _menu_demo(self):
        items = [(label, (lambda k=key: self.load_engine(k)))
                 for key, label, _f in presets.PRESETS]
        self._open_menu_for(items, self._buttons[0]["rect"])

    def _menu_device(self):
        items = [(lbl, (lambda i=i: self.set_device(i)))
                 for i, (lbl, _idx) in enumerate(self.devices)]
        # the device button is the first on row 1
        anchor = next(b["rect"] for b in self._buttons if b["label"].startswith("Out:"))
        self._open_menu_for(items, anchor)

    def _open_menu_for(self, items, anchor_rect):
        # Wrap a long list (e.g. all the demo cars) into as many COLUMNS as it
        # takes to fit the window height, so nothing runs off the bottom.
        n = len(items)
        ih = 26
        col_w = max(self.font_small.size(lbl)[0] for lbl, _ in items) + 28
        col_w = max(col_w, anchor_rect.width)
        top = anchor_rect.bottom + 3
        max_rows = max(1, (HEIGHT - top - 10) // ih)
        rows = min(n, max_rows)
        cols = (n + rows - 1) // max(rows, 1)
        w = cols * col_w + 8
        h = rows * ih + 8
        x = min(max(anchor_rect.x, 8), WIDTH - w - 8)
        y = min(max(top, 8), HEIGHT - h - 8)
        rect = pygame.Rect(x, y, w, h)
        item_rects = []
        for i in range(n):
            c, r = divmod(i, rows)
            item_rects.append(pygame.Rect(rect.x + 4 + c * col_w,
                                          rect.y + 4 + r * ih, col_w - 6, ih - 2))
        self._open_menu = {"items": items, "rect": rect, "item_rects": item_rects}

    # ----------------------------------------------------------- audio device
    def _make_synth(self, start=True):
        """(Re)create the synth on the chosen device + sample rate, preserving
        the current mixer params, cabin and firing voice."""
        saved = dict(self.synth.params) if self.synth else None
        _flags = ("cabin", "straight_cut", "gpf", "cat", "flutter")
        saved_flags = ({f: getattr(self.synth, f) for f in _flags}
                       if self.synth else None)
        if self.synth:
            self.synth.stop()
        device = self.devices[self.device_idx][1]
        rate = SAMPLE_RATES[self.rate_idx]
        self.synth = Synthesizer(self.sim, sample_rate=rate, device=device)
        if saved is not None:
            self.synth.params.update(saved)
            for f, v in saved_flags.items():
                setattr(self.synth, f, v)
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

    def save_dialog(self):
        eng = self.sim.engine
        path = _open_file_dialog("Save engine + audio config", config.ENGINE_DIR,
                                 save=True, default=config._safe_name(eng.name))
        if not path:
            return
        config.save_engine(eng, path)
        # save the audio alongside, with an _audio suffix
        import os
        base, _ = os.path.splitext(path)
        config.save_audio(self.synth.params, base + "_audio.json",
                          self.voice_idx, self.synth.cabin)
        self._flash(f"Saved: {os.path.basename(path)}")

    def load_car_dialog(self):
        path = _open_file_dialog("Load engine config (.json)", config.ENGINE_DIR)
        if not path:
            return
        try:
            eng = config.load_engine(path)
        except Exception as exc:
            self._flash(f"Load failed: {exc}")
            return
        self.current_key = None
        self.sim = Simulator(eng)
        self._make_synth(start=True)
        self._disp_torque = 0.0
        import os
        self._flash(f"Loaded engine: {os.path.basename(path)}")

    def load_eq_dialog(self):
        path = _open_file_dialog("Load audio / EQ config (.json)", config.AUDIO_DIR)
        if not path:
            return
        try:
            data = config.load_audio(path)
        except Exception as exc:
            self._flash(f"Load failed: {exc}")
            return
        self.synth.params.update(data.get("params", {}))
        self.synth.cabin = bool(data.get("cabin", False))
        self.voice_idx = int(data.get("voice", 0)) % len(FIRING_VOICES)
        import os
        self._flash(f"Loaded audio: {os.path.basename(path)}")

    def set_device(self, idx):
        if 0 <= idx < len(self.devices):
            self.device_idx = idx
            self._make_synth()
            self._flash(f"Output: {self.devices[idx][0]}")

    def toggle_telemetry(self):
        """Forza telemetry mode: play the selected engine sound at the rpm a
        running Forza Horizon / Motorsport broadcasts over UDP (no gears)."""
        if self.telemetry_mode:
            if self.telemetry:
                self.telemetry.stop()
            self.telemetry = None
            self.telemetry_mode = False
            self.sim.throttle = 0.0
            self._flash("Telemetry mode OFF")
            return
        self.telemetry = ForzaTelemetry(FORZA_PORT)
        if not self.telemetry.start():
            self._flash(f"Telemetry: cannot open UDP :{FORZA_PORT} "
                        f"({self.telemetry.error})")
            self.telemetry = None
            return
        self.telemetry_mode = True
        self.sim.ignition_on = True
        self._flash(f"Telemetry ON — Forza Data Out -> this PC :{FORZA_PORT}")

    def _flash(self, msg):
        self._status = msg
        self._status_t = 3.0

    def _apply_voice(self):
        self.synth.params.update(FIRING_VOICES[self.voice_idx][1])

    def _build_mixer(self):
        """Lay out the audio-mixer slider tracks + the spatial XY pad."""
        panel = pygame.Rect(24, 24, 620, 632)
        x = panel.x + 196
        w = (panel.x + 400) - x          # shorter tracks -> room for the pad
        y = panel.y + 28
        self._sliders = []
        for key, label, vmin, vmax in SLIDER_DEFS:
            self._sliders.append({
                "key": key, "label": label, "min": vmin, "max": vmax,
                "track": pygame.Rect(x, y + 5, w, 6), "row_y": y,
            })
            y += 26
        self._pad_rect = pygame.Rect(panel.x + 462, panel.y + 88, 152, 152)

    def _set_pad(self, pos):
        r = self._pad_rect
        px = (pos[0] - r.x) / r.width
        py = (pos[1] - r.y) / r.height          # top = far, bottom = near
        self.synth.params["spatial_x"] = min(max(px, 0.0), 1.0)
        self.synth.params["spatial_y"] = min(max(py, 0.0), 1.0)

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
    def _map_mouse(self, pos):
        """Map a real-window pixel position back onto the fixed UI canvas.  The
        canvas is drawn 1:1 (never scaled), just centred, so this only undoes
        the centring offset — clicks land exactly on what you see."""
        ox, oy = getattr(self, "_draw_offset", (0, 0))
        return (pos[0] - ox, pos[1] - oy)

    def canvas_mouse(self):
        return self._map_mouse(pygame.mouse.get_pos())

    def handle_events(self):
        self._rebuild_toolbar(pygame.Rect(24, 24, 620, 632))
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.VIDEORESIZE:
                # Never go below the native UI size, so nothing is ever clipped;
                # the window can only grow (extra room becomes background).
                self._win_size = (max(e.w, WIDTH), max(e.h, HEIGHT))
                self.window = pygame.display.set_mode(self._win_size, pygame.RESIZABLE)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                mpos = self._map_mouse(e.pos)
                if self._open_menu is not None:
                    m = self._open_menu
                    self._open_menu = None
                    for (lbl, cb), r in zip(m["items"], m["item_rects"]):
                        if r.collidepoint(mpos):
                            cb()
                            break
                elif self.mixer_open:
                    if getattr(self, "_mixer_close_rect", None) and \
                            self._mixer_close_rect.collidepoint(mpos):
                        self.mixer_open = False
                    elif self._pad_rect.collidepoint(mpos):
                        self._drag = "pad"
                        self._set_pad(mpos)
                    else:
                        for s in self._sliders:
                            if s["track"].inflate(12, 26).collidepoint(mpos):
                                self._drag = s
                                self._set_slider(s, mpos[0])
                                break
                else:
                    for b in self._buttons:
                        if b["rect"].collidepoint(mpos):
                            b["cb"]()
                            break
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                self._drag = None
            elif e.type == pygame.MOUSEMOTION and self._drag is not None:
                mpos = self._map_mouse(e.pos)
                if self._drag == "pad":
                    self._set_pad(mpos)
                else:
                    self._set_slider(self._drag, mpos[0])
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
                elif e.key == pygame.K_a:
                    self.sim.ignition_on = not self.sim.ignition_on
                elif e.key == pygame.K_m:
                    self.synth.volume = 0.0 if self.synth.volume > 0 else 1.0
                elif e.key == pygame.K_x:
                    if not self.sim.drivetrain.auto:
                        self.sim.drivetrain.shift_up()
                elif e.key == pygame.K_z:
                    if not self.sim.drivetrain.auto:
                        self.sim.drivetrain.shift_down()
                elif e.key == pygame.K_t:
                    self.sim.drivetrain.auto = not self.sim.drivetrain.auto

        keys = pygame.key.get_pressed()
        self.sim.starter_engaged = keys[pygame.K_s]

        # Clutch (manual only): tapping Z/X auto-blips it (paddle-shift, no need
        # to hold anything), holding Shift fully disengages it for launches.
        dt = self.sim.drivetrain
        shift_held = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
        dt.manual_clutch(shift_held, self.sim.rpm,
                         self.sim.engine.redline_rpm, 1.0 / FPS)
        # Up = throttle (ramps up while held, falls back when released).
        # Down = brake pedal.
        if keys[pygame.K_UP]:
            self.sim.throttle = min(1.0, self.sim.throttle + 0.04)
        else:
            self.sim.throttle = max(0.0, self.sim.throttle - 0.04)
        dt.brake = 1.0 if keys[pygame.K_DOWN] else 0.0

    # --------------------------------------------------------------- update
    def update(self, dt):
        if self.telemetry_mode:
            self._update_telemetry(dt)
        else:
            self.sim.drivetrain.auto_control(
                self.sim.rpm, self.sim.throttle, self.sim.engine.redline_rpm, dt)
            self.sim.step(dt)
            # Show indicated torque — combustion plus any electric-motor assist.
            drive_tq = self.sim.gas_torque + self.sim.motor_torque
            self._disp_torque += (drive_tq - self._disp_torque) * 0.08
        if self._status_t > 0.0:
            self._status_t -= dt

    def _update_telemetry(self, dt):
        """Drive the engine speed straight from the Forza broadcast (no physics,
        no gears) — the audio synth then plays the selected engine at that rpm."""
        tm = self.telemetry
        if tm is not None and tm.is_live():
            # scale the sound's redline/idle to the real car so the variable
            # exhaust valve and tach map across the right rpm range
            eng = self.sim.engine
            if tm.max_rpm > 1000.0:
                eng.redline_rpm = tm.max_rpm
            if 200.0 < tm.idle_rpm < eng.redline_rpm:
                eng.idle_rpm = tm.idle_rpm
            if tm.throttle_valid:
                self.sim.throttle = tm.throttle
            else:
                # FH6 / unknown packet: derive 'throttle' from rpm so the
                # variable exhaust valve still opens as the car revs
                span = max(tm.max_rpm - tm.idle_rpm, 1.0)
                self.sim.throttle = min(max((tm.rpm - tm.idle_rpm) / span, 0.0), 1.0)
            target = rpm_to_rads(max(tm.rpm, 0.0))
            # light smoothing between 60 Hz packets so the pitch glides cleanly
            self.sim.omega += (target - self.sim.omega) * min(22.0 * dt, 1.0)
            # G-force spatial: drift the spatial-audio dot with the car's real
            # lateral / longitudinal acceleration (cornering pans L/R, accel &
            # braking move it near/far), eased so it floats rather than snaps.
            if self.g_spatial:
                gx = max(-1.0, min(1.0, tm.accel_x / 12.0))   # ~1.2 g -> full
                gz = max(-1.0, min(1.0, tm.accel_z / 12.0))
                tx = 0.5 + 0.5 * gx
                ty = 0.5 - 0.5 * gz                           # accel -> nearer
                p = self.synth.params
                p["spatial_x"] += (tx - p["spatial_x"]) * min(6.0 * dt, 1.0)
                p["spatial_y"] += (ty - p["spatial_y"]) * min(6.0 * dt, 1.0)
        else:
            # not connected yet: idle quietly
            self.sim.throttle = 0.0
            idle = rpm_to_rads(self.sim.engine.idle_rpm)
            self.sim.omega += (idle - self.sim.omega) * min(3.0 * dt, 1.0)
        self.sim.crank_angle += self.sim.omega * dt
        self._disp_torque = 0.0

    # ----------------------------------------------------------- draw: parts
    def draw(self):
        self.screen.blit(self._grad_surf(WIDTH, HEIGHT, BG_TOP, BG_BOT, 0), (0, 0))
        left = pygame.Rect(24, 24, 620, 632)
        if self.mixer_open:
            self._draw_mixer(left)
        else:
            self._draw_engine_panel(left)
        self._draw_gauges(pygame.Rect(664, 24, 412, 632))
        if self._open_menu is not None:
            self._draw_menu()
        # Blit the native-size UI into the window WITHOUT scaling (no stretch /
        # squish) — when the window is bigger, the UI is centred and the extra
        # space is just background.  Layout and element sizes never change.
        ww, wh = self._win_size
        ox, oy = max(0, (ww - WIDTH) // 2), max(0, (wh - HEIGHT) // 2)
        self._draw_offset = (ox, oy)
        if (ww, wh) != (WIDTH, HEIGHT):
            self.window.fill(BG)
        self.window.blit(self.screen, (ox, oy))
        pygame.display.flip()

    # ----------------------------------------------------- iOS 6 skeuomorphism
    def _grad_surf(self, w, h, c1, c2, radius, gloss=False):
        """A cached vertical-gradient rounded-rect surface; with `gloss`, bake in
        the top-half glassy sheen + top highlight (the iOS 6 button look)."""
        w, h = int(w), int(h)
        key = (w, h, c1, c2, radius, gloss)
        surf = self._grad_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            for y in range(h):
                t = y / max(h - 1, 1)
                surf.fill((round(c1[0] + (c2[0] - c1[0]) * t),
                           round(c1[1] + (c2[1] - c1[1]) * t),
                           round(c1[2] + (c2[2] - c1[2]) * t)),
                          (0, y, w, 1))
            if gloss:
                sheen = pygame.Surface((w, h), pygame.SRCALPHA)
                gh = max(1, int(h * 0.5))
                for y in range(gh):
                    a = int(60 * (1.0 - y / gh))
                    sheen.fill((255, 255, 255, a), (0, y, w, 1))
                surf.blit(sheen, (0, 0))
            if radius > 0:
                mask = pygame.Surface((w, h), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                                 border_radius=radius)
                surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self._grad_cache[key] = surf
        return surf

    def _cyl_shade(self, w, h, base, radius=4):
        """A cached HORIZONTAL light-centre gradient — bright down the middle,
        dark at the edges — so a rect reads as a round metal cylinder/piston."""
        w, h = int(w), int(h)
        key = ('cyl', w, h, base, radius)
        surf = self._grad_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            for x in range(w):
                shade = 1.0 - abs(x / max(w - 1, 1) - 0.5) * 2.0   # 0 edge..1 centre
                f = 0.5 + 0.7 * shade
                surf.fill((min(255, int(base[0] * f)), min(255, int(base[1] * f)),
                           min(255, int(base[2] * f))), (x, 0, 1, h))
            if radius > 0:
                mask = pygame.Surface((w, h), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                                 border_radius=radius)
                surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self._grad_cache[key] = surf
        return surf

    def _draw_rod(self, small, big, w):
        """An I-beam connecting rod between the wrist pin and the crank journal."""
        sx, sy = small
        bx, by = big
        dx, dy = bx - sx, by - sy
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * w, dx / ln * w            # perpendicular offset
        beam = [(sx + nx * 0.5, sy + ny * 0.5), (bx + nx, by + ny),
                (bx - nx, by - ny), (sx - nx * 0.5, sy - ny * 0.5)]
        pygame.draw.polygon(self.screen, ROD, beam)
        pygame.draw.polygon(self.screen, (110, 116, 128), beam, 1)
        pygame.draw.line(self.screen, (180, 186, 198), small, big, 1)   # web highlight

    def _panel(self, rect, radius=14):
        """A glossy beveled panel slab (gradient + light top edge + dark base)."""
        self.screen.blit(self._grad_surf(rect.w, rect.h, PANEL_TOP, PANEL_BOT,
                                          radius), rect.topleft)
        pygame.draw.rect(self.screen, BEVEL_LO, rect, width=1, border_radius=radius)
        pygame.draw.line(self.screen, BEVEL_HI, (rect.x + radius, rect.y + 1),
                         (rect.right - radius, rect.y + 1))

    def _draw_button(self, b, mouse):
        r = b["rect"]
        active = b["active"]() if b["active"] else False
        hot = r.collidepoint(mouse)
        if active:
            c1, c2, txt = BTN_ON_HI, BTN_ON_LO, (255, 255, 255)
        elif hot:
            c1, c2, txt = BTN_HOT_HI, BTN_HOT_LO, INK
        else:
            c1, c2, txt = BTN_HI, BTN_LO, INK
        self.screen.blit(self._grad_surf(r.w, r.h, c1, c2, 6, gloss=True), r.topleft)
        pygame.draw.rect(self.screen, BEVEL_LO, r, width=1, border_radius=6)
        lbl = self.font_small.render(b["label"], True, txt)
        self.screen.blit(lbl, (r.centerx - lbl.get_width() // 2,
                               r.centery - lbl.get_height() // 2))

    def _draw_toolbar(self):
        mouse = self.canvas_mouse()
        for b in self._buttons:
            self._draw_button(b, mouse)

    def _draw_menu(self):
        m = self._open_menu
        mouse = self.canvas_mouse()
        mr = m["rect"]
        self.screen.blit(self._grad_surf(mr.w + 8, mr.h + 8, PANEL_TOP, PANEL_BOT, 8),
                         (mr.x - 4, mr.y - 4))
        pygame.draw.rect(self.screen, BEVEL_LO, mr.inflate(8, 8), width=1, border_radius=8)
        for (lbl, _cb), r in zip(m["items"], m["item_rects"]):
            if r.collidepoint(mouse):
                self.screen.blit(self._grad_surf(r.w, r.h, BTN_ON_HI, BTN_ON_LO, 4,
                                                 gloss=True), r.topleft)
                col = (255, 255, 255)
            else:
                col = INK
            self.screen.blit(self.font_small.render(lbl, True, col), (r.x + 8, r.y + 4))

    def _draw_mixer(self, rect):
        self._panel(rect)
        self.screen.blit(self.font.render("AUDIO MIXER", True, INK),
                         (rect.x + 18, rect.y + 18))
        self.screen.blit(self.font_small.render("drag the sliders  ·  C or ✕ to close",
                                                 True, DIM), (rect.x + 150, rect.y + 24))
        # a mouse-clickable close box (so you never need the keyboard)
        self._mixer_close_rect = pygame.Rect(rect.right - 42, rect.y + 14, 28, 24)
        pygame.draw.rect(self.screen, (56, 62, 74), self._mixer_close_rect,
                         border_radius=5)
        self.screen.blit(self.font_small.render("X", True, INK),
                         (self._mixer_close_rect.x + 9, self._mixer_close_rect.y + 4))
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
            # inset glossy track + blue-glass fill + chrome knob (iOS 6)
            pygame.draw.rect(self.screen, (24, 26, 31), t, border_radius=3)
            pygame.draw.line(self.screen, (12, 13, 16), (t.x + 2, t.y),
                             (t.right - 2, t.y))               # inset shadow
            fill = t.copy(); fill.width = max(0, int(t.width * norm))
            if fill.width > 2:
                self.screen.blit(self._grad_surf(fill.width, t.height, BTN_ON_HI,
                                                 BTN_ON_LO, 3, gloss=True), fill.topleft)
            hx = t.x + int(t.width * norm)
            self.screen.blit(self._grad_surf(16, 16, (236, 240, 245), (150, 158, 170),
                                             8, gloss=True), (hx - 8, t.centery - 8))
            pygame.draw.circle(self.screen, (90, 96, 108), (hx, t.centery), 8, 1)

        # --- spatial audio XY pad (drag the dot = position in the room) ------
        pr = self._pad_rect
        self.screen.blit(self.font_small.render("SPATIAL  (drag)", True, INK),
                         (pr.x, pr.y - 20))
        pygame.draw.rect(self.screen, (30, 33, 40), pr, border_radius=8)
        pygame.draw.rect(self.screen, (70, 76, 90), pr, width=1, border_radius=8)
        pygame.draw.line(self.screen, (48, 52, 62),
                         (pr.centerx, pr.y + 6), (pr.centerx, pr.bottom - 6))
        pygame.draw.line(self.screen, (48, 52, 62),
                         (pr.x + 6, pr.centery), (pr.right - 6, pr.centery))
        self.screen.blit(self.font_small.render("L", True, DIM), (pr.x + 5, pr.centery - 8))
        self.screen.blit(self.font_small.render("R", True, DIM), (pr.right - 14, pr.centery - 8))
        self.screen.blit(self.font_small.render("far", True, DIM), (pr.centerx - 9, pr.y + 3))
        self.screen.blit(self.font_small.render("near", True, DIM),
                         (pr.centerx - 13, pr.bottom - 16))
        sx = self.synth.params["spatial_x"]
        sy = self.synth.params["spatial_y"]
        dx = pr.x + int(sx * pr.width)
        dy = pr.y + int(sy * pr.height)
        pygame.draw.circle(self.screen, ACCENT, (dx, dy), 9)
        pygame.draw.circle(self.screen, INK, (dx, dy), 9, 1)

    def _draw_engine_panel(self, rect):
        self._panel(rect)
        sim = self.sim
        eng = sim.engine
        n = eng.num_cylinders

        # toolbar of buttons (engine/EQ load, device, modes) at the top
        self._rebuild_toolbar(rect)
        self._draw_toolbar()
        ty = self._toolbar_bottom + 4

        title = self.font.render(eng.name, True, INK)
        self.screen.blit(title, (rect.x + 18, ty))
        voice = FIRING_VOICES[self.voice_idx][0]
        cab = "   ·   cabin" if self.synth.cabin else ""
        self.screen.blit(self.font_small.render(f"firing voice: {voice}  (V){cab}",
                                                True, ACCENT), (rect.x + 18, ty + 24))

        self._draw_telemetry(rect, ty + 48)

        # Forza telemetry mode banner + transient save/load status
        by = ty + 178
        if self.telemetry_mode:
            tm = self.telemetry
            if tm is not None and tm.is_live():
                txt = f"FORZA  LIVE   {tm.rpm:5.0f} rpm   (redline {tm.max_rpm:.0f})"
                col = GOOD
            else:
                txt = f"FORZA  waiting for Data Out on UDP :{FORZA_PORT}"
                col = WARN
            self.screen.blit(self.font_small.render(txt, True, col), (rect.x + 26, by))
        if self._status_t > 0.0:
            self.screen.blit(self.font_small.render(self._status, True, ACCENT),
                             (rect.x + 26, by + 18))

        # Layout: one column per cylinder (or per rotor for a Wankel).
        top = rect.y + 92
        bottom = rect.bottom - 40
        if eng.is_rotary:
            self._draw_rotary(rect, top, bottom)
            return
        col_w = rect.width / n
        bore_h = (bottom - top) * 0.5
        bore_w = min(col_w * 0.52, 62)
        crank_r = min(col_w * 0.16, 24)
        piston_h = min(max(bore_w * 0.7, 18), 34)

        for i in range(n):
            cx = rect.x + col_w * (i + 0.5)
            crank_cy = bottom - crank_r - 6
            bore_top = crank_cy - crank_r * 2.2 - bore_h
            bw = bore_w
            bx = cx - bw / 2
            travel = bore_h - piston_h
            phi = sim.cycle_phase_deg(i)
            theta = math.radians(phi % 360.0)
            frac = sim.piston_fraction(i)              # 0 TDC .. 1 BDC
            py = bore_top + 6 + frac * travel
            pin = (cx, py + piston_h - 7)
            crank_pin = (cx + crank_r * math.sin(theta),
                         crank_cy + crank_r * math.cos(theta))

            # --- cylinder block: finned metal sleeve with a head cap ---
            head_h = 16
            sleeve = pygame.Rect(bx - 4, bore_top - head_h, bw + 8,
                                 bore_h + head_h + 8)
            self.screen.blit(self._cyl_shade(sleeve.w, sleeve.h, (74, 80, 92), 7),
                             sleeve.topleft)
            pygame.draw.rect(self.screen, (26, 28, 34), sleeve, width=2,
                             border_radius=7)
            for fy in range(3):                        # cooling fins on the head
                yy = int(bore_top - head_h + 4 + fy * 4)
                pygame.draw.line(self.screen, (44, 48, 56),
                                 (sleeve.x + 4, yy), (sleeve.right - 4, yy))
            # bore interior (where the piston runs)
            pygame.draw.rect(self.screen, (30, 33, 40),
                             (bx, bore_top, bw, bore_h + 4), border_radius=4)

            # --- combustion glow above the crown during the firing stroke ---
            if sim.ignition_on and not sim._fuel_cut and 360 <= phi < 445:
                glow = min(max(sim.cylinder_pressure[i] - 101325.0, 0.0)
                           / (5.0 * 101325.0), 1.0)
                if glow > 0.02:
                    gh = max(6, int(py - bore_top))
                    gs = pygame.Surface((bw - 4, gh), pygame.SRCALPHA)
                    gs.fill((FLASH[0], FLASH[1], FLASH[2], int(210 * glow)))
                    self.screen.blit(gs, (bx + 2, bore_top + 2))

            # --- piston: crown + ring grooves + skirt (round metal) ---
            self.screen.blit(self._cyl_shade(bw - 6, piston_h, (190, 196, 208), 3),
                             (cx - (bw - 6) / 2, py))
            pygame.draw.rect(self.screen, (96, 102, 114),
                             (cx - (bw - 6) / 2, py, bw - 6, piston_h), width=1,
                             border_radius=3)
            for rg in range(3):                        # ring lands
                ry = int(py + 5 + rg * 4)
                pygame.draw.line(self.screen, (74, 80, 92),
                                 (cx - (bw - 8) / 2, ry), (cx + (bw - 8) / 2, ry))
            pygame.draw.circle(self.screen, (54, 58, 68),
                               (int(pin[0]), int(pin[1])), 4)   # wrist pin
            pygame.draw.circle(self.screen, (150, 156, 168),
                               (int(pin[0]), int(pin[1])), 4, 1)

            # --- connecting rod (I-beam) ---
            self._draw_rod(pin, crank_pin, max(crank_r * 0.34, 4))

            # --- crankshaft: counterweight web + main + crank journal ---
            cwx = cx - crank_r * 0.6 * math.sin(theta)
            cwy = crank_cy - crank_r * 0.6 * math.cos(theta)
            pygame.draw.circle(self.screen, (44, 47, 56),
                               (int(cwx), int(cwy)), int(crank_r * 1.35))
            pygame.draw.circle(self.screen, (62, 66, 78), (int(cx), int(crank_cy)),
                               int(crank_r * 0.7))
            pygame.draw.circle(self.screen, (84, 90, 104), (int(cx), int(crank_cy)),
                               int(crank_r * 0.7), 1)
            pygame.draw.circle(self.screen, ACCENT,
                               (int(crank_pin[0]), int(crank_pin[1])), 5)
            pygame.draw.circle(self.screen, (20, 60, 110),
                               (int(crank_pin[0]), int(crank_pin[1])), 5, 1)

            label = self.font_small.render(f"{i + 1}", True, DIM)
            self.screen.blit(label, (int(cx) - 4, bottom + 8))

    def _draw_rotary(self, rect, top, bottom):
        """Wankel-rotor visualiser: an epitrochoid housing with a triangular
        rotor orbiting eccentrically (spinning at 1/3 shaft speed), for rotary
        engines instead of the piston bores.  One housing per ROTOR (the model
        runs two firing pulses per rotor, so rotors = cylinders / 2)."""
        sim, eng = self.sim, self.sim.engine
        n = max(1, eng.num_cylinders // 2)
        col_w = rect.width / n
        R = min(col_w * 0.30, (bottom - top) * 0.34)
        e = R * 0.16                                   # eccentricity
        cy = (top + bottom) / 2.0
        shaft0 = sim.crank_angle                       # eccentric-shaft angle (rad)
        for i in range(n):
            cx = rect.x + col_w * (i + 0.5)
            shaft = shaft0 + i * (2.0 * math.pi / n)
            # epitrochoid housing (2-lobe) outline
            hull = []
            for k in range(72):
                a = 2.0 * math.pi * k / 72.0
                hull.append((cx + R * math.cos(a) + e * math.cos(3 * a),
                             cy + R * math.sin(a) + e * math.sin(3 * a)))
            pygame.draw.polygon(self.screen, (40, 44, 52), hull)
            pygame.draw.polygon(self.screen, (84, 90, 104), hull, 2)
            # triangular rotor: centre orbits, body spins at 1/3 shaft speed
            rcx, rcy = cx + e * math.cos(shaft), cy + e * math.sin(shaft)
            rot = shaft / 3.0
            verts = [(rcx + R * 0.84 * math.cos(rot + k * 2.094395),
                      rcy + R * 0.84 * math.sin(rot + k * 2.094395)) for k in range(3)]
            pygame.draw.polygon(self.screen, PISTON, verts)
            pygame.draw.polygon(self.screen, (96, 102, 116), verts, 2)
            for v in verts:                            # apex seals
                pygame.draw.circle(self.screen, ACCENT, (int(v[0]), int(v[1])), 4)
            # combustion glow in the firing chamber (one of the rotor's faces)
            j = min(2 * i + 1, eng.num_cylinders - 1)
            press = max(sim.cylinder_pressure[2 * i], sim.cylinder_pressure[j])
            glow = (min(max(press - 101325.0, 0.0) / (5.0 * 101325.0), 1.0)
                    if sim.ignition_on and not sim._fuel_cut else 0.0)
            if glow > 0.03:
                mx, my = (verts[0][0] + verts[1][0]) / 2, (verts[0][1] + verts[1][1]) / 2
                gx, gy = cx + (mx - cx) * 1.18, cy + (my - cy) * 1.18
                gs = pygame.Surface((44, 44), pygame.SRCALPHA)
                pygame.draw.circle(gs, (FLASH[0], FLASH[1], FLASH[2], int(210 * glow)),
                                   (22, 22), 20)
                self.screen.blit(gs, (gx - 22, gy - 22))
            # eccentric shaft centre + orbiting journal
            pygame.draw.circle(self.screen, (60, 64, 74), (int(cx), int(cy)), 6)
            pygame.draw.circle(self.screen, ACCENT, (int(rcx), int(rcy)), 4)
            lbl = self.font_small.render(f"R{i + 1}", True, DIM)
            self.screen.blit(lbl, (int(cx) - 8, bottom + 8))

    def _air_gauge(self, cx, cy, r, frac, label, value, danger=False):
        """An old-school round aircraft instrument: metal bezel, black face, tick
        marks, a swept needle (270 deg) and a digital readout."""
        cx, cy = int(cx), int(cy)
        frac = min(max(frac, 0.0), 1.0)
        pygame.draw.circle(self.screen, (58, 62, 72), (cx, cy), r + 4)     # bezel
        pygame.draw.circle(self.screen, (150, 156, 168), (cx, cy), r + 4, 2)
        pygame.draw.circle(self.screen, (18, 19, 23), (cx, cy), r)         # face
        for k in range(11):
            a = math.radians(135 + k * 27)
            major = (k % 5 == 0)
            r2 = r - (8 if major else 5)
            col = (210, 214, 222) if major else (110, 116, 128)
            pygame.draw.line(self.screen, col,
                             (cx + (r - 2) * math.cos(a), cy + (r - 2) * math.sin(a)),
                             (cx + r2 * math.cos(a), cy + r2 * math.sin(a)),
                             2 if major else 1)
        # red danger arc over the last fifth
        for k in range(9, 11):
            a = math.radians(135 + k * 27)
            pygame.draw.line(self.screen, (210, 70, 60),
                             (cx + (r - 2) * math.cos(a), cy + (r - 2) * math.sin(a)),
                             (cx + (r - 4) * math.cos(a), cy + (r - 4) * math.sin(a)), 3)
        a = math.radians(135 + frac * 270)
        tip = (cx + (r - 6) * math.cos(a), cy + (r - 6) * math.sin(a))
        tail = (cx - 7 * math.cos(a), cy - 7 * math.sin(a))
        pygame.draw.line(self.screen, (235, 92, 80) if danger else (240, 206, 96),
                         tail, tip, 2)
        pygame.draw.circle(self.screen, (180, 186, 198), (cx, cy), 4)
        pygame.draw.circle(self.screen, (30, 32, 38), (cx, cy), 4, 1)
        gl = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)               # glass gloss
        pygame.draw.ellipse(gl, (255, 255, 255, 22),
                            (int(r * 0.28), int(r * 0.14), int(r * 1.45), int(r * 0.85)))
        self.screen.blit(gl, (cx - r, cy - r))
        lab = self.font_small.render(label, True, DIM)
        self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))
        val = self.font_small.render(value, True, INK)
        self.screen.blit(val, (cx - val.get_width() // 2, cy + r * 0.4))

    def _draw_turbo(self, cx, cy, r, spin, load):
        """A turbocharger: scroll/volute housing with a spinning turbine wheel,
        glowing hotter as it makes boost."""
        cx, cy = int(cx), int(cy)
        pygame.draw.circle(self.screen, (52, 56, 66), (cx, cy), r)         # volute
        pygame.draw.circle(self.screen, (150, 156, 168), (cx, cy), r, 2)
        pygame.draw.circle(self.screen, (28, 30, 36), (cx, cy), int(r * 0.78))
        # outlet snout
        pygame.draw.rect(self.screen, (60, 64, 74),
                         (cx + r - 3, cy - 6, 12, 12), border_radius=3)
        hub = max(3, int(r * 0.18))
        for k in range(10):                                               # blades
            a = spin + k * (2 * math.pi / 10)
            x1 = cx + hub * math.cos(a); y1 = cy + hub * math.sin(a)
            x2 = cx + r * 0.72 * math.cos(a + 0.5); y2 = cy + r * 0.72 * math.sin(a + 0.5)
            pygame.draw.line(self.screen, (176, 182, 194), (x1, y1), (x2, y2), 2)
        if load > 0.02:                                                   # hot glow
            gs = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
            pygame.draw.circle(gs, (255, 130, 50, int(150 * min(load, 1.0))),
                               (r, r), int(r * 0.5))
            self.screen.blit(gs, (cx - r, cy - r))
        pygame.draw.circle(self.screen, (90, 96, 108), (cx, cy), hub)
        lab = self.font_small.render("TURBO", True, DIM)
        self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))

    def _draw_blower(self, cx, cy, r, spin, load, centri=False):
        """A supercharger: a centrifugal impeller, or two counter-rotating Roots
        lobes, spinning with the engine."""
        cx, cy = int(cx), int(cy)
        if centri:
            pygame.draw.circle(self.screen, (52, 56, 66), (cx, cy), r)
            pygame.draw.circle(self.screen, (150, 156, 168), (cx, cy), r, 2)
            for k in range(12):
                a = spin + k * (2 * math.pi / 12)
                pygame.draw.line(self.screen, (176, 182, 194), (cx, cy),
                                 (cx + r * 0.82 * math.cos(a), cy + r * 0.82 * math.sin(a)), 2)
            pygame.draw.circle(self.screen, (90, 96, 108), (cx, cy), max(3, int(r*0.2)))
            txt = "S/C"
        else:
            for j, (ox, sgn) in enumerate(((-r * 0.55, 1), (r * 0.55, -1))):
                rcx = cx + ox
                pygame.draw.circle(self.screen, (54, 58, 68), (int(rcx), cy), int(r * 0.62))
                pygame.draw.circle(self.screen, (150, 156, 168), (int(rcx), cy), int(r * 0.62), 1)
                for k in range(3):                       # 3-lobe roots rotor
                    a = sgn * spin + k * (2 * math.pi / 3)
                    pygame.draw.line(self.screen, (184, 190, 202), (rcx, cy),
                                     (rcx + r * 0.55 * math.cos(a), cy + r * 0.55 * math.sin(a)), 3)
            txt = "ROOTS"
        if load > 0.02:
            gs = pygame.Surface((3 * r, 2 * r), pygame.SRCALPHA)
            pygame.draw.circle(gs, (90, 170, 255, int(120 * min(load, 1.0))),
                               (int(1.5 * r), r), int(r * 0.7))
            self.screen.blit(gs, (cx - int(1.5 * r), cy - r))
        lab = self.font_small.render(txt, True, DIM)
        self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))

    def _exhaust_db(self):
        """A rough exhaust-loudness readout from the synth's last output RMS."""
        lvl = getattr(self.synth, "last_level", 0.0) if self.synth else 0.0
        if lvl < 1e-4:
            return 0.0
        return max(0.0, min(120.0, 108.0 + 20.0 * math.log10(lvl)))

    def _draw_telemetry(self, rect, top_y):
        """Telemetry as a cluster of round aircraft instruments, plus a turbo /
        supercharger visualiser when the engine is forced-induction."""
        t = self.sim.telemetry()
        eng = self.sim.engine
        # Ease every reading so the needles glide instead of snapping — O2/CO2 in
        # particular jump hard on the brief fuel-cuts of each gearshift, which
        # made the needles spasm.  Slower ease on those two.
        sm = self._tele_smooth

        def ez(k, v, rate=0.12):
            sm[k] = sm.get(k, v) + (v - sm.get(k, v)) * rate
            return sm[k]
        mapk = ez('map', t['map_kpa'])
        ve = ez('ve', t['ve_pct'])
        afr = ez('afr', t['afr'])
        o2 = ez('o2', t['o2_pct'], 0.05)
        co2 = ez('co2', t['co2_pct'], 0.05)
        db = ez('db', self._exhaust_db())
        # (label, value-text, fraction 0..1, danger?)
        gauges = [
            ("MAP", f"{mapk:.0f}", mapk / 250.0, mapk > 200),
            ("VE", f"{ve:.0f}%", ve / 120.0, False),
            ("AFR", f"{afr:.1f}", (afr - 10.0) / 6.0, afr < 11.5),
            ("O2", f"{o2:.1f}", o2 / 21.0, False),
            ("CO2", f"{co2:.0f}%", co2 / 16.0, False),
            ("dB", f"{db:.0f}", (db - 60.0) / 60.0, db > 108),
        ]
        r = 30
        cy = top_y + r + 6
        x0 = rect.x + 16
        gap = (rect.width - 32) / 6.0
        fi = eng.induction != "na"
        n_gauges = 5 if fi else 6              # last slot becomes the FI visualiser
        for k in range(n_gauges):
            lab, val, frac, danger = gauges[k]
            self._air_gauge(x0 + gap * (k + 0.5), cy, r, frac, lab, val, danger)
        if fi:
            spin = self.sim.crank_angle * (6.0 if eng.induction == "turbo" else 3.5)
            load = (self.sim.boost / max(eng.boost_bar, 0.05)) if eng.boost_bar else 0.0
            fcx, fcy = x0 + gap * 5.5, cy
            if eng.induction == "turbo":
                self._draw_turbo(fcx, fcy, r - 2, spin, load)
            else:
                self._draw_blower(fcx, fcy, r - 4, spin, load,
                                  centri=(eng.induction == "centrifugal"))
            bt = self.font_small.render(f"{self.sim.boost:.2f}b", True, ACCENT)
            self.screen.blit(bt, (int(fcx) - bt.get_width() // 2, int(fcy + r * 0.4)))

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
        self._panel(rect)
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
            ("GEAR", f"{dt.gear_name:>3}  {'AUTO' if dt.auto else 'MANUAL'}"
                     f"  [{_GBX_LABEL.get(dt.gearbox_type, dt.gearbox_type).upper()}]", GOOD),
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

        # --- controls reference (two tidy columns) ---
        y += 26
        pygame.draw.line(self.screen, (44, 48, 56),
                         (rect.x + 28, y - 6), (rect.right - 28, y - 6))
        self.screen.blit(self.font_small.render("CONTROLS", True, DIM),
                         (rect.x + 28, y))
        pairs = [
            ("A", "ignition"), ("S", "starter"),
            ("Up/Dn", "gas / brake"), ("Z X", "shift"),
            ("Shift", "clutch"), ("T", "auto box"),
            ("V", "voice"), ("I", "cabin"),
            ("C", "mixer"), ("M / Esc", "mute / quit"),
        ]
        cols = [rect.x + 28, rect.x + 212]
        for i, (k, act) in enumerate(pairs):
            cx = cols[i % 2]
            ry = y + 22 + (i // 2) * 15
            self.screen.blit(self.font_small.render(k, True, ACCENT), (cx, ry))
            self.screen.blit(self.font_small.render(act, True, DIM), (cx + 64, ry))

    def _draw_tach(self, cx, cy, r, rpm, redline):
        """A glossy iOS 6 / aircraft-style tachometer dial."""
        cx, cy = int(cx), int(cy)
        start = math.radians(225)
        span = math.radians(270)
        max_rpm = math.ceil((redline + 500) / 1000.0) * 1000

        def pt(rad, ang):
            return (cx + rad * math.cos(ang), cy - rad * math.sin(ang))

        # metal bezel (beveled ring) + dark glossy face
        pygame.draw.circle(self.screen, (38, 41, 48), (cx, cy), r + 12)
        pygame.draw.circle(self.screen, (120, 127, 142), (cx, cy), r + 12, 3)
        pygame.draw.circle(self.screen, (80, 86, 98), (cx, cy), r + 6)
        pygame.draw.circle(self.screen, (158, 166, 180), (cx, cy), r + 6, 1)
        pygame.draw.circle(self.screen, (20, 22, 27), (cx, cy), r)
        pygame.draw.circle(self.screen, (8, 9, 12), (cx, cy), r, 2)

        # redline band
        seg = 26
        for i in range(seg + 1):
            k = redline + (max_rpm - redline) * i / seg
            a = start - span * (k / max_rpm)
            pygame.draw.line(self.screen, (212, 60, 52), pt(r - 3, a), pt(r - 8, a), 4)

        # ticks (every 500) + numbers (every 1000)
        for k in range(0, int(max_rpm) + 1, 500):
            a = start - span * (k / max_rpm)
            major = (k % 1000 == 0)
            over = k >= redline
            r0 = r - (16 if major else 8)
            col = WARN if over else (214, 219, 228) if major else (110, 116, 128)
            pygame.draw.line(self.screen, col, pt(r0, a), pt(r - 3, a),
                             3 if major else 1)
            if major:
                num = self.font.render(str(k // 1000), True, col)
                nx, ny = pt(r - 36, a)
                self.screen.blit(num, (nx - num.get_width() // 2,
                                       ny - num.get_height() // 2))

        # glass gloss highlight (upper arc)
        gl = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
        pygame.draw.ellipse(gl, (255, 255, 255, 20),
                            (int(r * 0.22), int(r * 0.08), int(r * 1.56), int(r * 0.92)))
        self.screen.blit(gl, (cx - r, cy - r))

        # tapered needle + counterweight tail
        frac = min(max(rpm / max_rpm, 0.0), 1.0)
        a = start - span * frac
        ct, st = math.cos(a), math.sin(a)
        col = WARN if rpm >= redline else ACCENT
        tip = (cx + (r - 16) * ct, cy - (r - 16) * st)
        b1 = (cx + st * 5, cy + ct * 5)
        b2 = (cx - st * 5, cy - ct * 5)
        pygame.draw.polygon(self.screen, col, [tip, b1, b2])
        pygame.draw.line(self.screen, col, (cx, cy), (cx - 20 * ct, cy + 20 * st), 4)

        # chrome hub
        self.screen.blit(self._grad_surf(22, 22, (224, 230, 238), (118, 126, 140),
                                         11, gloss=True), (cx - 11, cy - 11))
        pygame.draw.circle(self.screen, (54, 58, 68), (cx, cy), 11, 1)

        # digital rpm window
        win = pygame.Rect(cx - 46, cy + int(r * 0.44), 92, 26)
        self.screen.blit(self._grad_surf(win.w, win.h, (14, 16, 20), (32, 35, 42), 6),
                         win.topleft)
        pygame.draw.rect(self.screen, (6, 7, 10), win, 1, border_radius=6)
        rt = self.font.render(f"{int(rpm):>5d}", True, col)
        self.screen.blit(rt, (win.centerx - rt.get_width() // 2, win.y + 4))
        cap = self.font_small.render("rpm   x1000", True, DIM)
        self.screen.blit(cap, (cx - cap.get_width() // 2, win.bottom + 4))

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
