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
        # The whole UI is drawn onto a fixed-size canvas, then scaled to fit a
        # freely resizable OS window — so you can drag the window to any size
        # (or maximise it) and everything scales cleanly, keeping its layout.
        self.window = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        self.screen = pygame.Surface((WIDTH, HEIGHT))
        self._win_size = (WIDTH, HEIGHT)
        self._draw_offset = (0, 0)
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
            ("Forza", self.toggle_telemetry, lambda: self.telemetry_mode, 2),
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
            y += 28
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
                    if self._pad_rect.collidepoint(mpos):
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
            # Show indicated (combustion) torque — the engine's output capacity.
            self._disp_torque += (self.sim.gas_torque - self._disp_torque) * 0.08
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
        else:
            # not connected yet: idle quietly
            self.sim.throttle = 0.0
            idle = rpm_to_rads(self.sim.engine.idle_rpm)
            self.sim.omega += (idle - self.sim.omega) * min(3.0 * dt, 1.0)
        self.sim.crank_angle += self.sim.omega * dt
        self._disp_torque = 0.0

    # ----------------------------------------------------------- draw: parts
    def draw(self):
        self.screen.fill(BG)
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

    def _draw_button(self, b, mouse):
        r = b["rect"]
        active = b["active"]() if b["active"] else False
        hot = r.collidepoint(mouse)
        if active:
            fill, txt = ACCENT, BG
        elif hot:
            fill, txt = (56, 62, 74), INK
        else:
            fill, txt = (40, 44, 52), INK
        pygame.draw.rect(self.screen, fill, r, border_radius=6)
        if not active:
            pygame.draw.rect(self.screen, (70, 76, 90), r, width=1, border_radius=6)
        surf = self.font_small.render(b["label"], True, txt)
        self.screen.blit(surf, (r.x + 10, r.y + 5))

    def _draw_toolbar(self):
        mouse = self.canvas_mouse()
        for b in self._buttons:
            self._draw_button(b, mouse)

    def _draw_menu(self):
        m = self._open_menu
        mouse = self.canvas_mouse()
        pygame.draw.rect(self.screen, (30, 33, 40), m["rect"].inflate(4, 4),
                         border_radius=8)
        pygame.draw.rect(self.screen, (80, 88, 104), m["rect"], width=1,
                         border_radius=8)
        for (lbl, _cb), r in zip(m["items"], m["item_rects"]):
            if r.collidepoint(mouse):
                pygame.draw.rect(self.screen, ACCENT, r, border_radius=4)
                col = BG
            else:
                col = INK
            self.screen.blit(self.font_small.render(lbl, True, col), (r.x + 8, r.y + 4))

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
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
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

    def _exhaust_db(self):
        """A rough exhaust-loudness readout from the synth's last output RMS."""
        lvl = getattr(self.synth, "last_level", 0.0) if self.synth else 0.0
        if lvl < 1e-4:
            return 0.0
        return max(0.0, min(120.0, 108.0 + 20.0 * math.log10(lvl)))

    def _draw_telemetry(self, rect, top_y):
        """Physical engine readouts (manifold pressure, VE, AFR, airflow, O2)."""
        t = self.sim.telemetry()
        x, y = rect.x + 26, top_y
        self.screen.blit(self.font_small.render("TELEMETRY", True, DIM), (x, y))
        y += 21
        rows = [
            ("MANIFOLD", f"{t['map_kpa']:5.0f} kPa  ({t['vacuum_inhg']:+.0f} inHg)"),
            ("VOL. EFF.", f"{t['ve_pct']:5.0f} %"),
            ("AIR / FUEL", f"{t['afr']:5.1f}   (lambda {t['lambda']:.2f})"),
            ("AIRFLOW", f"{t['scfm']:5.0f} SCFM"),
            ("EXHAUST O2", f"{t['o2_pct']:5.1f} %"),
            ("EXHAUST CO2", f"{t['co2_pct']:5.1f} %"),
            ("EXHAUST", f"{self._exhaust_db():5.0f} dB"),
        ]
        eng = self.sim.engine
        if eng.induction != "na":
            kind = {"roots": "S/C", "centrifugal": "C/F", "turbo": "TURBO"}.get(
                eng.induction, eng.induction)
            rows.append(("BOOST", f"{self.sim.boost:4.2f} bar   ({kind})"))
        for label, val in rows:
            self.screen.blit(self.font_small.render(label, True, DIM), (x, y))
            self.screen.blit(self.font_small.render(val, True, INK), (x + 110, y))
            y += 21

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
