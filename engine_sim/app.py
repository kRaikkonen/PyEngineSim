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

# --- localisation (English / 简体中文) ---------------------------------------
# Keyed by the English string; tr() returns the current language's version.
TR_ZH = {
    # toolbar
    "Demo cars": "示例车", "Load car…": "载入车型…", "Load EQ…": "载入EQ…",
    "Save…": "保存…", "Mixer / EQ": "混音/EQ", "Out:": "输出:",
    "Auto": "自动", "Manual": "手动", "Cabin": "车内", "Gear whine": "直齿啸叫",
    "Touch": "触屏", "Touch OFF": "关闭触屏",
    "Cat": "三元", "Bent": "弯管", "Flutter": "颤振", "Hybrid": "混动",
    "G-pad": "G力",
    "Lang": "语言", "Pops": "放炮", "Slow-mo": "慢动作", "Slow": "慢",
    "off": "关", "firing order:": "点火顺序:",
    # gauges / readouts
    "RPM": "转速", "TORQUE": "扭矩", "POWER": "功率", "THROTTLE": "油门",
    "GEAR": "挡位", "SPEED": "车速", "TELEMETRY": "遥测",
    "TURBO": "涡轮", "ROOTS": "机增", "S/C": "机增",
    # status
    "IGNITION": "点火", "STARTER": "起动机", "REV LIMIT": "断油", "AUDIO": "音频",
    "CLUTCH IN": "离合", "IN GEAR": "在挡",
    # controls
    "CONTROLS": "操作", "ignition": "点火", "starter": "起动机",
    "gas / brake": "油门/刹车", "shift": "换挡", "clutch": "离合",
    "auto box": "自动挡", "voice": "音色", "mixer": "混音",
    "mute / quit": "静音/退出",
    # mixer panel
    "AUDIO MIXER": "混音台",
    "drag the sliders  ·  C or ✕ to close": "拖动滑块  ·  C 或 ✕ 关闭",
    "SPATIAL  (drag)": "空间音频 (拖动)", "far": "远", "near": "近",
    "FIRE TONE  (drag)": "点火音色 (拖动)", "thin": "薄", "fat": "厚",
    "coarse": "粗", "smooth": "顺",
    # forza banner + tach
    "LIVE": "实时", "redline": "红线",
    "waiting for Data Out on UDP": "等待 Data Out 广播 UDP",
    "rpm   x1000": "转速 x1000",
    # firing voice + voices
    "firing voice:": "点火音色:", "cabin": "车内",
    "Balanced": "均衡", "Sharp": "尖锐", "Deep": "低沉", "Raspy": "沙哑",
    "Hollow": "空洞",
    # slider labels
    "MASTER volume": "主音量", "Firing / bang": "点火爆音", "Body (thickness)": "声体(厚)",
    "Drive (solid)": "驱动(扎实)", "Firing pitch (Hz)": "点火音高(Hz)",
    "Attack crack": "起音爆裂", "Attack soft (blunt)": "起音柔化",
    "Fizz / gas noise": "气流嘶声", "Pipe resonance 1": "排气共振1",
    "Pipe resonance 2": "排气共振2", "Intake roar": "进气轰鸣",
    "Explosion reverb": "爆燃混响", "Reverb (space)": "空间混响",
    "Cylinder spread": "缸间差异", "Supercharger whine": "机增啸叫",
    "Turbo spool / BOV": "涡轮/泄压", "Spool reverb": "增压混响",
    "Straight-cut whine": "直齿啸叫", "Gear-whine reverb": "直齿混响",
    "Electric / e-turbo": "电机/电涡轮", "Overrun pops": "收油放炮",
    "Pop muffle": "放炮闷度", "Pop reverb": "放炮混响",
    "Pipe wall (anti-horn)": "管壁厚度(去小号)",
    "Per-cyl character": "逐缸特征", "Road / tyre rumble": "路噪/胎噪",
    "Exhaust whine/scream": "排气啸叫/嘶吼",
    "Active valve open": "主动阀门开度",
    "Muffler reflections": "消音器反射",
    "Tailpipe air-shear": "尾管气流剪切",
    "MANIFOLD": "进气歧管", "AIR": "进气量", "VOL EFF": "容积效率",
    "IN AFR": "进气空燃比", "EX O2": "排气含氧", "FUEL": "油耗",
    "USED": "已耗", "TOTAL EXHAUST FLOW": "总排气流量", "REV LIMIT": "断油保护",
    "Scope": "波形",
    "EXHAUST FLOW — STAGE WAVEFORMS": "排气流 — 各级波形",
    "LISTENER AUDIO — STAGE WAVEFORMS": "听感音频 — 各级波形",
    "each window = that stage's output  ·  E / click to close":
        "每个窗口 = 该级输出  ·  按 E 或点击关闭",
    "show: FLOW": "显示：排气流", "show: AUDIO": "显示：音频",
    "EQ low (dB)": "EQ低频(dB)",
    "EQ mid (dB)": "EQ中频(dB)", "EQ high (dB)": "EQ高频(dB)",
    "Presence (bite)": "临场(咬合)",
}

WIDTH, HEIGHT = 1100, 680
FPS = 60

# Friendly transmission labels for the HUD (sets the auto-shift feel).
_GBX_LABEL = {"dct": "DCT", "single": "single-clutch", "at": "AT", "manual": "manual"}

# Audio-mixer sliders: (param key, label, min, max).  These bind to
# Synthesizer.params and are dragged live in the in-app console (press C).
SLIDER_DEFS = [
    ("master", "MASTER volume", 0.0, 1.2),
    ("dry", "Firing / bang", 0.0, 4.0),
    ("body", "Body (thickness)", 0.0, 6.0),
    ("drive", "Drive (solid)", 0.0, 3.0),
    ("firing_pitch", "Firing pitch (Hz)", 28.0, 600.0),
    ("crack", "Attack crack", 0.0, 1.6),
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
    ("gearbox_reverb", "Gear-whine reverb", 0.0, 0.6),
    ("hybrid_vol", "Electric / e-turbo", 0.0, 1.2),
    ("pops", "Overrun pops", 0.0, 1.5),
    ("pop_muff", "Pop muffle", 0.0, 1.0),
    ("pops_reverb", "Pop reverb", 0.0, 0.6),
    ("wall_thickness", "Pipe wall (anti-horn)", 0.0, 1.0),
    ("cyl_voice", "Per-cyl character", 0.0, 2.0),
    ("road_noise", "Road / tyre rumble", 0.0, 0.6),
    ("whine", "Exhaust whine/scream", 0.0, 2.0),
    ("valve_open", "Active valve open", 0.0, 1.5),
    ("muffler", "Muffler reflections", 0.0, 1.5),
    ("shear", "Tailpipe air-shear", 0.0, 0.5),
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
        self._draw_scale = 1.0
        self._grad_cache = {}     # cached gradient/gloss surfaces (iOS 6 skin)
        self._tele_smooth = {}    # eased telemetry values (calm gauge needles)
        self._cyl_flow_hist = []  # per-cylinder exhaust-flow scope ring buffers
        self._fuel_total_l = 0.0  # integrated fuel burned (L)
        self._fuel_lph = 0.0      # smoothed instantaneous fuel rate (L/h)
        self._ign_flash = {}      # per-cylinder ignition-light fade
        self._wheel_ang = 0.0     # spinning road-wheel angle (rad)
        # --- touch controls (on-screen pedals/paddles for phones & tablets) ---
        self.touch_mode = False   # show the finger control overlay
        self._ptr = {}            # active pointer id -> control name
        self._ptr_val = {}        # pointer id -> analog value (pedals)
        self._touch_throttle = 0.0
        self._touch_brake = 0.0
        self._touch_starter = False
        self.clock = pygame.time.Clock()
        self.lang = "en"          # "en" | "zh"
        self._init_fonts()

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
        self.slow_mo = 1.0        # 1.0 normal .. 0.001 = 1000x slow motion

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
        self.scope_open = False   # exhaust-path per-stage waveform overlay (press E)
        self.scope_mode = "flow"  # "flow" (exhaust gas) | "audio" (listener chain)
        self._scope_toggle_rect = None
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
        T = self.tr
        arr = "▼" if self.lang == "zh" else "▾"   # YaHei lacks U+25BE
        return [
            # row 0 — car + sound toggles (part 1)
            (f"{T('Demo cars')} {arr}", self._menu_demo, None, 0),
            (T("Auto") if dt.auto else T("Manual"),
             lambda: setattr(dt, "auto", not dt.auto), lambda: dt.auto, 0),
            (T("Cabin"), lambda: setattr(sy, "cabin", not sy.cabin),
             lambda: sy.cabin, 0),
            (T("Gear whine"), lambda: setattr(sy, "straight_cut", not sy.straight_cut),
             lambda: sy.straight_cut, 0),
            ("GPF", lambda: setattr(sy, "gpf", not sy.gpf), lambda: sy.gpf, 0),
            (T("Mixer / EQ"), lambda: setattr(self, "mixer_open", not self.mixer_open),
             lambda: self.mixer_open, 0),
            # row 1 — sound toggles (part 2)
            (T("Cat"), lambda: setattr(sy, "cat", not sy.cat), lambda: sy.cat, 1),
            (T("Bent"), lambda: setattr(sy, "road_pipe", not sy.road_pipe),
             lambda: sy.road_pipe, 1),
            (T("Flutter"), lambda: setattr(sy, "flutter", not sy.flutter),
             lambda: sy.flutter, 1),
            (T("Hybrid"), lambda: setattr(self.sim, "hybrid_on", not self.sim.hybrid_on),
             lambda: self.sim.hybrid_on and self.sim.engine.hybrid_kw > 0, 1),
            (T("Pops"), lambda: setattr(sy, "pops_on", not sy.pops_on),
             lambda: sy.pops_on, 1),
            (f"{'中/EN' if self.lang == 'en' else 'EN/中'}", self.toggle_lang, None, 1),
            # row 2 — output / device / view
            (f"{T('Out:')} {dev} {arr}", self._menu_device, None, 2),
            (f"{rate // 1000}.{(rate % 1000)//100}kHz", self.toggle_rate, None, 2),
            ("Forza", self.toggle_telemetry, lambda: self.telemetry_mode, 2),
            (f"{T('Slow')} {int(round(1/self.slow_mo))}x" if self.slow_mo < 1
             else T("Slow-mo"), self.toggle_slow, lambda: self.slow_mo < 1.0, 2),
            (T("Touch"), lambda: setattr(self, "touch_mode", not self.touch_mode),
             lambda: self.touch_mode, 2),
            (T("Scope"), lambda: setattr(self, "scope_open", not self.scope_open),
             lambda: self.scope_open, 2),
        ]

    def toggle_slow(self):
        steps = [1.0, 0.1, 0.01, 0.001]              # 1x, 10x, 100x, 1000x slow
        i = (steps.index(self.slow_mo) + 1) % len(steps) if self.slow_mo in steps else 0
        self.slow_mo = steps[i]
        if self.synth:
            self.synth.time_scale = self.slow_mo
        self._flash(f"{self.tr('Slow-mo')}: "
                    + (f"{int(round(1/self.slow_mo))}x" if self.slow_mo < 1
                       else self.tr("off")))

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
        # find the device button by its callback (label is translated)
        anchor = next(b["rect"] for b in self._buttons if b["cb"] == self._menu_device)
        self._open_menu_for(items, anchor)

    def _open_menu_for(self, items, anchor_rect):
        # Row-major grid that always fits the 1100x680 canvas: pack as many
        # COLUMNS as the width allows, then scroll vertically for the rest so a
        # big list (128 demo cars) never overflows or crams off-screen.
        n = len(items)
        ih = 24
        natural = max(self.font_small.size(lbl)[0] for lbl, _ in items) + 26
        col_w = min(natural, WIDTH - 32)
        col_w = max(col_w, anchor_rect.width)
        top = anchor_rect.bottom + 3
        cols = max(1, min(n, (WIDTH - 24) // col_w))
        rows_total = (n + cols - 1) // cols
        vis_rows = max(4, min(rows_total, (HEIGHT - top - 12) // ih))
        needs_scroll = rows_total > vis_rows
        sb = 12 if needs_scroll else 0
        w = cols * col_w + 8 + sb
        h = vis_rows * ih + 8
        x = min(max(anchor_rect.x, 8), WIDTH - w - 8)
        y = min(max(top, 8), HEIGHT - h - 8)
        rect = pygame.Rect(x, y, w, h)
        self._open_menu = {
            "items": items, "rect": rect, "ih": ih, "col_w": col_w, "cols": cols,
            "rows_total": rows_total, "vis_rows": vis_rows, "scroll": 0, "n": n,
            "scrollbar": needs_scroll,
        }

    def _menu_max_scroll(self, m):
        return max(0, m["rows_total"] - m["vis_rows"])

    def _menu_item_rect(self, m, i):
        """Screen rect for item i at the current scroll, or None if off-window."""
        row, col = divmod(i, m["cols"])
        vr = row - m["scroll"]
        if vr < 0 or vr >= m["vis_rows"]:
            return None
        r = m["rect"]
        return pygame.Rect(r.x + 4 + col * m["col_w"], r.y + 4 + vr * m["ih"],
                           m["col_w"] - 6, m["ih"] - 2)

    # ----------------------------------------------------------- audio device
    def _make_synth(self, start=True, keep_engine_flags=True):
        """(Re)create the synth on the chosen device + sample rate, preserving
        the current mixer params, cabin and firing voice.

        ``keep_engine_flags`` keeps the GPF/Cat toggles (device/rate changes);
        on a CAR change it is False so GPF/Cat reset to the new engine's own
        has_gpf / has_cat instead of carrying the previous car's exhaust kit."""
        saved = dict(self.synth.params) if self.synth else None
        _flags = ["cabin"]
        if keep_engine_flags:
            _flags += ["gpf", "cat", "straight_cut", "flutter", "road_pipe"]
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
        self._make_synth(start=True, keep_engine_flags=False)
        self._disp_torque = 0.0
        self._tele_smooth.clear()
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
        y = panel.y + 38                 # clear the title/hint row
        self._sliders = []
        for key, label, vmin, vmax in SLIDER_DEFS:
            self._sliders.append({
                "key": key, "label": label, "min": vmin, "max": vmax,
                "track": pygame.Rect(x, y + 4, w, 6), "row_y": y,
            })
            y += 17                       # tight rows so all fit one column
        self._pad_rect = pygame.Rect(panel.x + 462, panel.y + 88, 152, 152)
        # 2-D fire/bang tone IR pad (drag to morph the firing timbre)
        self._fire_pad_rect = pygame.Rect(panel.x + 462, panel.y + 320, 152, 152)

    def _set_pad(self, pos):
        r = self._pad_rect
        px = (pos[0] - r.x) / r.width
        py = (pos[1] - r.y) / r.height          # top = far, bottom = near
        self.synth.params["spatial_x"] = min(max(px, 0.0), 1.0)
        self.synth.params["spatial_y"] = min(max(py, 0.0), 1.0)

    def _set_fire_pad(self, pos):
        r = self._fire_pad_rect
        px = (pos[0] - r.x) / r.width            # X = thin/bright .. thick/fat
        py = 1.0 - (pos[1] - r.y) / r.height     # Y up = more grit/coarse
        self.synth.params["fire_weight"] = min(max(px, 0.0), 1.0)
        self.synth.params["fire_grit"] = min(max(py, 0.0), 1.0)

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
        self._make_synth(start=True, keep_engine_flags=False)   # GPF/Cat per new car
        self._disp_torque = 0.0
        self._tele_smooth.clear()             # don't carry needle state across cars

    # ----------------------------------------------------------------- input
    def _init_fonts(self):
        """(Re)build fonts for the current language — a CJK-capable face for
        Chinese, crisp Consolas for English."""
        if self.lang == "zh":
            fam = "microsoftyahei,microsoftyaheui,simhei,simsun"
            self.font = pygame.font.SysFont(fam, 17)
            self.font_big = pygame.font.SysFont(fam, 40, bold=True)
            self.font_small = pygame.font.SysFont(fam, 14)
        else:
            self.font = pygame.font.SysFont("consolas", 18)
            self.font_big = pygame.font.SysFont("consolas", 44, bold=True)
            self.font_small = pygame.font.SysFont("consolas", 14)

    def tr(self, s):
        """Translate a UI string for the current language."""
        return TR_ZH.get(s, s) if self.lang == "zh" else s

    def toggle_lang(self):
        self.lang = "zh" if self.lang == "en" else "en"
        self._init_fonts()
        self._flash("语言: 中文" if self.lang == "zh" else "Language: English")

    def _map_mouse(self, pos):
        """Map a real-window pixel position back onto the fixed UI canvas — undo
        the centring offset AND the fit-to-window scale, so clicks/taps land
        exactly on what you see at any window or phone-screen size."""
        ox, oy = getattr(self, "_draw_offset", (0, 0))
        s = getattr(self, "_draw_scale", 1.0) or 1.0
        return ((pos[0] - ox) / s, (pos[1] - oy) / s)

    def canvas_mouse(self):
        return self._map_mouse(pygame.mouse.get_pos())

    # ----------------------------------------------------------- touch input
    def _map_finger(self, e):
        """Normalised (0..1) finger event -> canvas coordinates (undo fit scale)."""
        ww, wh = self._win_size
        ox, oy = self._draw_offset
        s = getattr(self, "_draw_scale", 1.0) or 1.0
        return ((e.x * ww - ox) / s, (e.y * wh - oy) / s)

    def _touch_rects(self):
        """On-screen control hit-boxes (finger pedals / paddles / buttons)."""
        return {
            "brake": pygame.Rect(8, 332, 74, 320),
            "gas":   pygame.Rect(WIDTH - 82, 332, 74, 320),
            "up":    pygame.Rect(WIDTH - 182, 332, 90, 98),
            "down":  pygame.Rect(WIDTH - 182, 434, 90, 98),
            "start": pygame.Rect(WIDTH - 182, 536, 90, 52),
            "ign":   pygame.Rect(WIDTH - 182, 592, 90, 52),
            "auto":  pygame.Rect(WIDTH - 182, 286, 90, 42),
            "close": pygame.Rect(WIDTH - 182, 240, 90, 40),   # turn touch mode off
        }

    def _pointer_down(self, pid, pos):
        """A finger/mouse pressed.  Returns True if it hit a touch control."""
        if not self.touch_mode:
            return False
        for name, r in self._touch_rects().items():
            if r.collidepoint(pos):
                self._ptr[pid] = name
                if name in ("gas", "brake"):
                    self._ptr_val[pid] = min(max((pos[1] - r.top) / r.height, 0.0), 1.0)
                elif name == "up" and not self.sim.drivetrain.auto:
                    self.sim.drivetrain.shift_up()
                elif name == "down" and not self.sim.drivetrain.auto:
                    self.sim.drivetrain.shift_down()
                elif name == "ign":
                    self.sim.ignition_on = not self.sim.ignition_on
                elif name == "auto":
                    self.sim.drivetrain.auto = not self.sim.drivetrain.auto
                elif name == "close":
                    self.touch_mode = False
                    self._ptr.clear()
                    self._ptr_val.clear()
                return True
        return False

    def _pointer_move(self, pid, pos):
        name = self._ptr.get(pid)
        if name in ("gas", "brake"):
            r = self._touch_rects()[name]
            self._ptr_val[pid] = min(max((pos[1] - r.top) / r.height, 0.0), 1.0)

    def _pointer_up(self, pid):
        self._ptr.pop(pid, None)
        self._ptr_val.pop(pid, None)

    def _aggregate_touch(self):
        """Fold the active pointers into throttle / brake / starter each frame."""
        self._touch_throttle = 0.0
        self._touch_brake = 0.0
        self._touch_starter = False
        for pid, name in self._ptr.items():
            if name == "gas":
                self._touch_throttle = max(self._touch_throttle,
                                           self._ptr_val.get(pid, 0.8))
            elif name == "brake":
                self._touch_brake = max(self._touch_brake, self._ptr_val.get(pid, 1.0))
            elif name == "start":
                self._touch_starter = True

    def _handle_press(self, mpos):
        """Press on the normal UI (dropdown menu, mixer, sliders, toolbar)."""
        if self.scope_open:                  # modal overlay
            if self._scope_toggle_rect and self._scope_toggle_rect.collidepoint(mpos):
                self.scope_mode = "audio" if self.scope_mode == "flow" else "flow"
            else:                            # any other click dismisses it
                self.scope_open = False
        elif self._open_menu is not None:
            m = self._open_menu
            self._open_menu = None
            for i, (lbl, cb) in enumerate(m["items"]):
                r = self._menu_item_rect(m, i)
                if r is not None and r.collidepoint(mpos):
                    cb()
                    break
        elif self.mixer_open:
            if getattr(self, "_mixer_close_rect", None) and \
                    self._mixer_close_rect.collidepoint(mpos):
                self.mixer_open = False
            elif self._pad_rect.collidepoint(mpos):
                self._drag = "pad"
                self._set_pad(mpos)
            elif self._fire_pad_rect.collidepoint(mpos):
                self._drag = "firepad"
                self._set_fire_pad(mpos)
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

    def _handle_drag(self, mpos):
        if self._drag == "pad":
            self._set_pad(mpos)
        elif self._drag == "firepad":
            self._set_fire_pad(mpos)
        elif self._drag is not None:
            self._set_slider(self._drag, mpos[0])

    def _draw_touch_overlay(self):
        """Translucent on-screen pedals / paddles / buttons for finger control."""
        if not self.touch_mode:
            return
        R = self._touch_rects()
        ov = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)

        def pedal(r, col, frac, label):
            pygame.draw.rect(ov, (col[0], col[1], col[2], 55), r, border_radius=12)
            fh = int(r.height * min(max(frac, 0.0), 1.0))
            if fh > 2:
                pygame.draw.rect(ov, (col[0], col[1], col[2], 150),
                                 pygame.Rect(r.x, r.bottom - fh, r.width, fh),
                                 border_radius=12)
            pygame.draw.rect(ov, (col[0], col[1], col[2], 225), r, 2, border_radius=12)
            t = self.font.render(label, True, (255, 255, 255))
            ov.blit(t, (r.centerx - t.get_width() // 2, r.bottom - 28))

        def btn(name, label, on=False):
            r = R[name]
            c = (70, 150, 80) if on else (54, 60, 74)
            pygame.draw.rect(ov, (c[0], c[1], c[2], 180), r, border_radius=10)
            pygame.draw.rect(ov, (164, 172, 188, 215), r, 2, border_radius=10)
            t = self.font_small.render(label, True, (236, 240, 246))
            ov.blit(t, (r.centerx - t.get_width() // 2, r.centery - 7))

        pedal(R["gas"], (74, 200, 96), self.sim.throttle, "GAS")
        pedal(R["brake"], (224, 84, 64), self.sim.drivetrain.brake, "BRK")
        btn("up", "UP")
        btn("down", "DOWN")
        btn("start", "START", self.sim.starter_engaged)
        btn("ign", "IGN", self.sim.ignition_on)
        btn("auto", "AUTO", self.sim.drivetrain.auto)
        # close button (red) — turn touch mode off without the toolbar
        rc = R["close"]
        pygame.draw.rect(ov, (190, 70, 60, 210), rc, border_radius=10)
        pygame.draw.rect(ov, (230, 150, 140, 230), rc, 2, border_radius=10)
        tc = self.font_small.render(self.tr("Touch OFF"), True, (255, 235, 230))
        ov.blit(tc, (rc.centerx - tc.get_width() // 2, rc.centery - 7))
        self.screen.blit(ov, (0, 0))

    def handle_events(self):
        self._rebuild_toolbar(pygame.Rect(24, 24, 620, 632))
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.VIDEORESIZE:
                # The canvas scales to fit (aspect-preserved), so the window may be
                # any size — bigger OR smaller than native (phones scale it down).
                self._win_size = (max(e.w, 480), max(e.h, 300))
                self.window = pygame.display.set_mode(self._win_size, pygame.RESIZABLE)
            elif e.type == pygame.FINGERDOWN:
                pos = self._map_finger(e)
                # a finger on a touch control (when touch mode is on) drives it;
                # otherwise it falls through to the normal UI (toolbar, sliders…)
                # so the menus/buttons stay tappable on a touchscreen.
                if not (self.touch_mode and self._pointer_down(("f", e.finger_id), pos)):
                    self._handle_press(pos)
            elif e.type == pygame.FINGERMOTION:
                pos = self._map_finger(e)
                if ("f", e.finger_id) in self._ptr:
                    self._pointer_move(("f", e.finger_id), pos)
                elif self._drag is not None:
                    self._handle_drag(pos)
            elif e.type == pygame.FINGERUP:
                self._pointer_up(("f", e.finger_id))
                self._drag = None
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if getattr(e, "touch", False):
                    continue                      # touch-emulated mouse -> use FINGER
                mpos = self._map_mouse(e.pos)
                if not (self.touch_mode and self._pointer_down("mouse", mpos)):
                    self._handle_press(mpos)
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                self._drag = None
                self._pointer_up("mouse")
            elif e.type == pygame.MOUSEWHEEL and self._open_menu is not None:
                m = self._open_menu
                m["scroll"] = max(0, min(self._menu_max_scroll(m),
                                         m["scroll"] - e.y * 2))
            elif e.type == pygame.MOUSEMOTION and "mouse" in self._ptr:
                self._pointer_move("mouse", self._map_mouse(e.pos))
            elif e.type == pygame.MOUSEMOTION and self._drag is not None:
                self._handle_drag(self._map_mouse(e.pos))
            elif e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif e.key == pygame.K_c:
                    self.mixer_open = not self.mixer_open
                elif e.key == pygame.K_e:
                    self.scope_open = not self.scope_open
                elif e.key == pygame.K_v:
                    self.voice_idx = (self.voice_idx + 1) % len(FIRING_VOICES)
                    self._apply_voice()
                elif e.key == pygame.K_i:
                    self.synth.cabin = not self.synth.cabin
                elif e.key == pygame.K_a:
                    self.sim.ignition_on = not self.sim.ignition_on
                elif e.key == pygame.K_m:
                    self.synth.volume = 0.0 if self.synth.volume > 0 else 1.0
                elif e.key == pygame.K_o:                # hidden: turbo V7 + Bdim
                    self.synth.o_chord = not self.synth.o_chord
                    self._flash("♪ V7" if self.synth.o_chord else "")
                elif e.key == pygame.K_p:                # hidden: test the BOV sound
                    self.synth._bov_env = 1.0
                    self.synth._bdim_phase = 0.0
                elif pygame.K_1 <= e.key <= pygame.K_6:   # hidden: firing chord 1-6
                    self.synth.fire_chord = e.key - pygame.K_1
                    self._flash(["power", "major", "root+m2", "m7b5", "dim",
                                 "maj7"][e.key - pygame.K_1])
                elif e.key == pygame.K_x:
                    if not self.sim.drivetrain.auto:
                        self.sim.drivetrain.shift_up()
                elif e.key == pygame.K_z:
                    if not self.sim.drivetrain.auto:
                        self.sim.drivetrain.shift_down()
                elif e.key == pygame.K_t:
                    self.sim.drivetrain.auto = not self.sim.drivetrain.auto

        keys = pygame.key.get_pressed()
        self._aggregate_touch()
        self.sim.starter_engaged = keys[pygame.K_s] or self._touch_starter

        # Clutch (manual only): tapping Z/X auto-blips it (paddle-shift, no need
        # to hold anything), holding Shift fully disengages it for launches.
        dt = self.sim.drivetrain
        shift_held = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
        dt.manual_clutch(shift_held, self.sim.rpm,
                         self.sim.engine.redline_rpm, 1.0 / FPS)
        # Throttle / brake from the keyboard OR the on-screen touch pedals.
        if self.touch_mode and self._ptr:
            self.sim.throttle += (self._touch_throttle - self.sim.throttle) \
                * min(9.0 / FPS, 1.0)
            dt.brake = self._touch_brake
        else:
            if keys[pygame.K_UP]:
                self.sim.throttle = min(1.0, self.sim.throttle + 0.04)
            else:
                self.sim.throttle = max(0.0, self.sim.throttle - 0.04)
            dt.brake = 1.0 if keys[pygame.K_DOWN] else 0.0

    # --------------------------------------------------------------- update
    def update(self, dt):
        if self.synth:
            self.synth.time_scale = 1.0 if self.telemetry_mode else self.slow_mo
        if self.telemetry_mode:
            self._update_telemetry(dt)
        else:
            sdt = dt * self.slow_mo            # slow-motion time dilation
            self.sim.drivetrain.auto_control(
                self.sim.rpm, self.sim.throttle, self.sim.engine.redline_rpm, sdt)
            self.sim.step(sdt)
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
            # Feed the drivetrain a road speed + gear and spool boost off the
            # broadcast rpm/throttle, so the straight-cut gear whine, turbo spool
            # / supercharger whine and the BOV/flutter all work in Forza mode
            # (the physics drivetrain isn't running here, so they'd be silent).
            dtr = self.sim.drivetrain
            if tm.speed_valid:
                dtr.v = tm.speed
            else:                       # FH6 / unknown: pseudo-speed from rpm
                span = max(eng.redline_rpm - eng.idle_rpm, 1.0)
                dtr.v = min(max((tm.rpm - eng.idle_rpm) / span, 0.0), 1.0) * 75.0
            dtr.gear = tm.gear if (tm.throttle_valid and tm.gear > 0) else (
                1 if dtr.v > 0.5 else 0)
            if tm.dash_valid:
                # sync the game's REAL boost / torque / brake / clutch for accuracy
                dtr.brake = tm.brake
                dtr.clutch = 1.0 - tm.clutch        # Forza: 1 = pressed in
                if eng.induction != "na":
                    # Forza broadcasts boost in PSI (negative = vacuum)
                    self.sim.boost = max(0.0, tm.boost_psi) * 0.06895   # -> bar
                self._disp_torque = max(tm.torque, 0.0)
            else:
                self.sim._update_boost(dt)          # FH6/unknown: model the spool
                self._disp_torque = 0.0
        else:
            # not connected yet: idle quietly
            self.sim.throttle = 0.0
            idle = rpm_to_rads(self.sim.engine.idle_rpm)
            self.sim.omega += (idle - self.sim.omega) * min(3.0 * dt, 1.0)
            self._disp_torque = 0.0
        self.sim.crank_angle += self.sim.omega * dt

    # ----------------------------------------------------------- draw: parts
    def draw(self):
        self._update_hud_signals()
        self.screen.blit(self._grad_surf(WIDTH, HEIGHT, BG_TOP, BG_BOT, 0), (0, 0))
        left = pygame.Rect(24, 24, 620, 632)
        if self.mixer_open:
            self._draw_mixer(left)
        else:
            self._draw_engine_panel(left)
        self._draw_gauges(pygame.Rect(664, 24, 412, 632))
        # the exhaust-path stage scopes only sample audio while the overlay is up
        if self.synth is not None:
            self.synth.scope_enabled = self.scope_open
        if self.scope_open:
            self._draw_exhaust_scopes(pygame.Rect(24, 24, 1052, 632))
        if self._open_menu is not None:
            self._draw_menu()
        self._draw_touch_overlay()
        # Fit the native 1100x680 canvas into the window, PRESERVING ASPECT RATIO
        # (letter-boxed) — so it fills a phone screen or a maximised desktop window
        # cleanly instead of sitting tiny in the corner.  scale==1 => crisp 1:1.
        ww, wh = self._win_size
        scale = min(ww / WIDTH, wh / HEIGHT)
        sw, sh = int(WIDTH * scale), int(HEIGHT * scale)
        ox, oy = (ww - sw) // 2, (wh - sh) // 2
        self._draw_scale = scale
        self._draw_offset = (ox, oy)
        self.window.fill(BG)
        if abs(scale - 1.0) < 1e-3:
            self.window.blit(self.screen, (ox, oy))
        else:
            self.window.blit(pygame.transform.smoothscale(self.screen, (sw, sh)),
                             (ox, oy))
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
        for i, (lbl, _cb) in enumerate(m["items"]):
            r = self._menu_item_rect(m, i)
            if r is None:
                continue
            if r.collidepoint(mouse):
                self.screen.blit(self._grad_surf(r.w, r.h, BTN_ON_HI, BTN_ON_LO, 4,
                                                 gloss=True), r.topleft)
                col = (255, 255, 255)
            else:
                col = INK
            self.screen.blit(self.font_small.render(lbl, True, col), (r.x + 8, r.y + 4))
        # scrollbar thumb on the right gutter
        if m.get("scrollbar"):
            maxs = self._menu_max_scroll(m)
            track_x = mr.right - 9
            track_y = mr.y + 4
            track_h = mr.h - 8
            pygame.draw.rect(self.screen, BEVEL_LO, (track_x, track_y, 5, track_h),
                             border_radius=3)
            frac = m["vis_rows"] / max(1, m["rows_total"])
            thumb_h = max(18, int(track_h * frac))
            prog = (m["scroll"] / maxs) if maxs else 0
            thumb_y = track_y + int((track_h - thumb_h) * prog)
            pygame.draw.rect(self.screen, BTN_ON_HI, (track_x, thumb_y, 5, thumb_h),
                             border_radius=3)

    def _draw_mixer(self, rect):
        self._panel(rect)
        self.screen.blit(self.font.render(self.tr("AUDIO MIXER"), True, INK),
                         (rect.x + 18, rect.y + 18))
        self.screen.blit(self.font_small.render(
            self.tr("drag the sliders  ·  C or ✕ to close"),
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
            self.screen.blit(self.font_small.render(self.tr(s["label"]), True, INK),
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
        self.screen.blit(self.font_small.render(self.tr("SPATIAL  (drag)"), True, INK),
                         (pr.x, pr.y - 20))
        pygame.draw.rect(self.screen, (30, 33, 40), pr, border_radius=8)
        pygame.draw.rect(self.screen, (70, 76, 90), pr, width=1, border_radius=8)
        pygame.draw.line(self.screen, (48, 52, 62),
                         (pr.centerx, pr.y + 6), (pr.centerx, pr.bottom - 6))
        pygame.draw.line(self.screen, (48, 52, 62),
                         (pr.x + 6, pr.centery), (pr.right - 6, pr.centery))
        self.screen.blit(self.font_small.render("L", True, DIM), (pr.x + 5, pr.centery - 8))
        self.screen.blit(self.font_small.render("R", True, DIM), (pr.right - 14, pr.centery - 8))
        self.screen.blit(self.font_small.render(self.tr("far"), True, DIM),
                         (pr.centerx - 9, pr.y + 3))
        self.screen.blit(self.font_small.render(self.tr("near"), True, DIM),
                         (pr.centerx - 13, pr.bottom - 16))
        sx = self.synth.params["spatial_x"]
        sy = self.synth.params["spatial_y"]
        dx = pr.x + int(sx * pr.width)
        dy = pr.y + int(sy * pr.height)
        pygame.draw.circle(self.screen, ACCENT, (dx, dy), 9)
        pygame.draw.circle(self.screen, INK, (dx, dy), 9, 1)

        # --- 2-D fire/bang tone pad (drag to morph the firing timbre) --------
        fr = self._fire_pad_rect
        self.screen.blit(self.font_small.render(self.tr("FIRE TONE  (drag)"), True, INK),
                         (fr.x, fr.y - 20))
        pygame.draw.rect(self.screen, (30, 33, 40), fr, border_radius=8)
        pygame.draw.rect(self.screen, (70, 76, 90), fr, width=1, border_radius=8)
        self.screen.blit(self.font_small.render(self.tr("thin"), True, DIM),
                         (fr.x + 4, fr.centery - 8))
        self.screen.blit(self.font_small.render(self.tr("fat"), True, DIM),
                         (fr.right - 24, fr.centery - 8))
        self.screen.blit(self.font_small.render(self.tr("coarse"), True, DIM),
                         (fr.centerx - 18, fr.y + 3))
        self.screen.blit(self.font_small.render(self.tr("smooth"), True, DIM),
                         (fr.centerx - 18, fr.bottom - 16))
        fwx = fr.x + int(self.synth.params["fire_weight"] * fr.width)
        fgy = fr.y + int((1.0 - self.synth.params["fire_grit"]) * fr.height)
        pygame.draw.circle(self.screen, (255, 150, 70), (fwx, fgy), 9)
        pygame.draw.circle(self.screen, INK, (fwx, fgy), 9, 1)

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
        # --- engineering line: displacement · bore×stroke · CR · crank ----------
        cc = eng.total_displacement * 1.0e6
        disp_l = eng.total_displacement * 1000.0
        cyl0 = eng.cylinders[0]
        bore_mm = cyl0.bore * 1000.0
        stroke_mm = cyl0.stroke * 1000.0
        cr = cyl0.compression_ratio
        # Firing interval = 720° four-stroke cycle ÷ cylinders (the meaningful
        # per-engine number — "720° crank" alone is universal to ALL 4-strokes,
        # not a crankshaft type, so we show the pulse spacing instead).
        fire = "" if eng.is_rotary else f"  ·  fires every {720.0 / n:.0f}°"
        geo = (f"{cc:.0f} cc  ·  {disp_l:.1f} L  ·  {bore_mm:.1f} × "
               f"{stroke_mm:.1f} mm  ·  {cr:.1f}:1{fire}")
        gtxt = self.font_small.render(geo, True, DIM)
        self.screen.blit(gtxt, (rect.x + 18, ty + 24))
        # --- configuration line: layout/bank-angle · rotation · exhaust · valves -
        mat = getattr(eng, "wall_material", "steel")
        mat_lbl = {"titanium": "Ti", "stainless": "steel", "aluminium": "Alu",
                   "aluminum": "Alu", "iron": "iron"}.get(mat, "steel")
        if eng.is_rotary:
            vt = "rotary"
        else:
            vt = f"{eng.valvetrain.upper()} {eng.valves_per_cyl}v"
        equal_hdr = ((eng.straight_cut or eng.gearbox_type == "single"
                      or eng.redline_rpm >= 8400)
                     and getattr(eng, "header_unequal_deg", 0.0) < 0.5)
        hdr = "equal-len" if equal_hdr else "uneven-len"
        maxang = max((abs(c.bank_angle_deg) for c in eng.cylinders), default=0.0)
        # Crank plane (V8s): both flat- and cross-plane fire every 90° of CRANK,
        # so the firing interval can't tell them apart.  What differs is whether
        # the firing alternates banks cleanly (flat-plane, even per-bank) or not
        # (cross-plane, the lumpy burble).  Detect it from the bank-firing order.
        plane = ""
        has_banks = any(c.bank_angle_deg < -0.1 for c in eng.cylinders) and \
            any(c.bank_angle_deg > 0.1 for c in eng.cylinders)
        if n == 8 and has_banks:
            order = sorted(range(n),
                           key=lambda i: eng.cylinders[i].cycle_offset_deg % 720.0)
            signs = [1 if eng.cylinders[i].bank_angle_deg > 0 else -1 for i in order]
            alternates = all(signs[j] != signs[(j + 1) % n] for j in range(n))
            plane = " flat-plane" if alternates else " cross-plane"
        if eng.is_rotary:
            cfg = "rotary"
        elif getattr(eng, "is_radial", False):
            cfg = f"radial-{n}"
        elif getattr(eng, "is_w", False):
            mags = sorted({round(abs(c.bank_angle_deg), 1) for c in eng.cylinders
                           if abs(c.bank_angle_deg) > 0.1})
            outer = (mags[0] + mags[-1]) if len(mags) >= 2 else 2 * maxang
            cfg = f"W{n} {outer:.0f}°"
        elif maxang < 0.5:
            cfg = f"inline-{n}"
        elif maxang > 80.0:
            cfg = f"flat-{n}"
        else:
            cfg = f"V{n} {2 * maxang:.0f}°{plane}"
        rot = "CCW" if getattr(eng, "rotation", "CW") == "CCW" else "CW"
        vv = getattr(eng, "variable_valve", "")
        vv_txt = f" · {vv}" if vv else ""
        spec = (f"{cfg} · {rot} · {mat_lbl} exh · {hdr} · "
                f"{vt}{vv_txt}")
        stxt = self.font_small.render(spec, True, (138, 146, 162))
        self.screen.blit(stxt, (rect.x + 18, ty + 44))
        voice = self.tr(FIRING_VOICES[self.voice_idx][0])
        cab = f"   ·   {self.tr('cabin')}" if self.synth.cabin else ""
        self.screen.blit(self.font_small.render(
            f"{self.tr('firing voice:')} {voice}  (V){cab}",
            True, ACCENT), (rect.x + 18, ty + 64))

        self._draw_telemetry(rect, ty + 86)

        # Forza telemetry mode banner + transient save/load status
        by = ty + 178
        if self.telemetry_mode:
            tm = self.telemetry
            if tm is not None and tm.is_live():
                lr = self.tr("redline") if self.lang == "zh" else "redline"
                txt = f"FORZA  {self.tr('LIVE')}   {tm.rpm:5.0f} rpm   ({lr} {tm.max_rpm:.0f})"
                col = GOOD
            else:
                txt = f"FORZA  {self.tr('waiting for Data Out on UDP')} :{FORZA_PORT}"
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
        if getattr(eng, "is_radial", False):
            self._draw_radial(rect, top, bottom)
            return
        # Group cylinders by bank so the drawing matches the real layout: an
        # inline engine is one upright row, a V is two angled banks meeting at a
        # shared crank, a boxer opposes left/right, a W is a wide double-V.
        left = [i for i in range(n) if eng.cylinders[i].bank_angle_deg < -0.1]
        right = [i for i in range(n) if eng.cylinders[i].bank_angle_deg > 0.1]
        if not left and not right:
            stations = [[i] for i in range(n)]           # inline: one per station
        else:
            m = max(len(left), len(right))
            stations = []
            for s in range(m):
                st = []
                if s < len(left):
                    st.append(left[s])
                if s < len(right):
                    st.append(right[s])
                stations.append(st)
        ns = max(1, len(stations))
        maxang = max((abs(c.bank_angle_deg) for c in eng.cylinders), default=0.0)

        # Any two-bank engine (V, W or boxer) is laid out VERTICALLY: the
        # crankshaft runs DOWN the centre and the banks fan out left & right.
        # Spreading a wide V12 / W16 across the top made the tilted banks shoot
        # off the panel edge; going vertical uses the tall axis for the stations
        # and keeps every cylinder inside the frame.  (Inline = upright row below.)
        # A W engine is TWO narrow-angle VR units (VR6/VR8) sharing one crank, so
        # draw it as two side-by-side VR groups — not one strung-out column.
        if left and right and getattr(eng, "is_w", False):
            self._draw_w_banks(rect, sim, eng, left, right)
            fo = "-".join(str(x) for x in eng.firing_order)
            self.screen.blit(self.font_small.render(f"{self.tr('firing order:')} {fo}",
                             True, ACCENT), (rect.x + 18, rect.bottom - 14))
            return
        if left and right:
            cxx = rect.centerx
            mtop, mbot = rect.y + 306, rect.bottom - 48
            dy = (mbot - mtop) / ns
            # V fans each side to one up-angle; a boxer is ~horizontal opposed.
            is_w = getattr(eng, "is_w", False)
            # V tilt: a gentle up-angle from horizontal (W uses real bank angles).
            trad = math.radians(max(4.0, min(22.0, (90.0 - maxang) * 0.30)))
            width = min(dy * (0.46 if is_w else 0.60), 38.0)
            length = min((rect.width * 0.5 - 40.0) / max(math.cos(trad), 0.4),
                         dy * (1.55 if is_w else 2.7), 172.0)
            # vertical metallic crankshaft behind every journal (strip-shaded)
            cy0, cy1 = mtop + dy * 0.5 - 14, mtop + dy * (ns - 0.5) + 14
            chw = max(width * 0.30, 9.0)
            for si in range(7):
                e0 = (si / 7 * 2 - 1) * chw; e1 = ((si + 1) / 7 * 2 - 1) * chw
                f = 0.40 + 0.66 * (1.0 - abs((si + 0.5) / 7 * 2 - 1))
                pygame.draw.polygon(self.screen, (min(255, int(70 * f)),
                                    min(255, int(76 * f)), min(255, int(90 * f))),
                                    [(cxx + e0, cy0), (cxx + e0, cy1),
                                     (cxx + e1, cy1), (cxx + e1, cy0)])
            pygame.draw.line(self.screen, (24, 26, 32), (cxx - chw, cy0), (cxx - chw, cy1))
            pygame.draw.line(self.screen, (24, 26, 32), (cxx + chw, cy0), (cxx + chw, cy1))
            for s, st in enumerate(stations):
                jy = mtop + dy * (s + 0.5)
                for i in st:
                    cyl = eng.cylinders[i]
                    phi = sim.cycle_phase_deg(i)
                    theta = math.radians(phi % 360.0)
                    frac = sim.piston_fraction(i)
                    glow = (min(max(sim.cylinder_pressure[i] - 101325.0, 0.0)
                                / (5.0 * 101325.0), 1.0)
                            if sim.ignition_on and not sim._fuel_cut and 360 <= phi < 445
                            else 0.0)
                    side = -1.0 if cyl.bank_angle_deg < 0 else 1.0
                    # V: every cylinder of a bank tilts up by trad.  W: use the
                    # cylinder's REAL bank angle so the two VR sub-banks per side
                    # (15 deg apart, 90 deg between groups) draw as two tight,
                    # authentic \/ pairs -> the true four-column W.
                    if is_w:
                        a = math.radians(cyl.bank_angle_deg)
                    else:
                        a = side * (math.pi / 2.0 - trad)    # left/right, tilt up
                    self._draw_cyl(cxx, jy, a, length, width, frac, theta, glow)
                    lx = cxx + math.sin(a) * (length + 16)
                    ly = jy - math.cos(a) * (length + 16)
                    lab = self.font_small.render(f"{i + 1}", True, DIM)
                    self.screen.blit(lab, (int(lx) - lab.get_width() // 2, int(ly) - 6))
            fo = "-".join(str(x) for x in eng.firing_order)
            self.screen.blit(self.font_small.render(f"{self.tr('firing order:')} {fo}",
                             True, ACCENT), (rect.x + 18, rect.bottom - 14))
            return

        sw = rect.width / ns
        crank_y = bottom - 30 - (bottom - top - 70) * min(maxang / 90.0, 1.0) * 0.5
        # cap the length so a tilted bank's HORIZONTAL reach never runs into the
        # neighbouring station or off the panel (matters most for wide V / boxer).
        sinmax = math.sin(math.radians(maxang))
        length = min((crank_y - top) * 0.9, sw * 0.95, 0.46 * sw / max(sinmax, 0.34))
        width = min(sw * 0.32, 40)
        # metallic crankshaft running behind every journal (round strip-shaded)
        cx0 = rect.x + sw * 0.5 - 16
        cx1 = rect.x + sw * (ns - 0.5) + 16
        chh = max(width * 0.30, 9.0)
        for si in range(7):
            e0 = (si / 7 * 2 - 1) * chh; e1 = ((si + 1) / 7 * 2 - 1) * chh
            f = 0.40 + 0.66 * (1.0 - abs((si + 0.5) / 7 * 2 - 1))
            pygame.draw.polygon(self.screen, (min(255, int(70 * f)),
                                min(255, int(76 * f)), min(255, int(90 * f))),
                                [(cx0, crank_y + e0), (cx1, crank_y + e0),
                                 (cx1, crank_y + e1), (cx0, crank_y + e1)])
        pygame.draw.line(self.screen, (24, 26, 32), (cx0, crank_y - chh), (cx1, crank_y - chh))
        pygame.draw.line(self.screen, (24, 26, 32), (cx0, crank_y + chh), (cx1, crank_y + chh))
        for s, st in enumerate(stations):
            jx = rect.x + sw * (s + 0.5)
            for i in st:
                cyl = eng.cylinders[i]
                a = math.radians(cyl.bank_angle_deg)
                phi = sim.cycle_phase_deg(i)
                theta = math.radians(phi % 360.0)
                frac = sim.piston_fraction(i)            # 0 TDC .. 1 BDC
                glow = (min(max(sim.cylinder_pressure[i] - 101325.0, 0.0)
                            / (5.0 * 101325.0), 1.0)
                        if sim.ignition_on and not sim._fuel_cut and 360 <= phi < 445
                        else 0.0)
                self._draw_cyl(jx, crank_y, a, length, width, frac, theta, glow)
                lx = jx + math.sin(a) * (length + 14)
                ly = crank_y - math.cos(a) * (length + 14)
                lab = self.font_small.render(f"{i + 1}", True, DIM)
                self.screen.blit(lab, (int(lx) - lab.get_width() // 2, int(ly) - 6))

        # firing order (derived from the cycle offsets, so it's always physical)
        fo = "-".join(str(x) for x in eng.firing_order)
        self.screen.blit(self.font_small.render(f"{self.tr('firing order:')} {fo}",
                                                True, ACCENT), (rect.x + 18, bottom + 26))

    def _flash_surf(self, radius, glow):
        """A layered combustion FLASH (white-hot core -> yellow -> orange -> red
        halo, plus a few spark rays) instead of one flat yellow disc."""
        R = max(int(radius), 4)
        g = min(max(glow, 0.0), 1.0)
        pad = int(R * 1.7) + 4
        gs = pygame.Surface((2 * pad, 2 * pad), pygame.SRCALPHA)
        c = (pad, pad)
        for rad, col, al in ((int(R * 1.5), (255, 64, 20), 30),
                             (int(R * 1.15), (255, 110, 36), 70),
                             (int(R * 0.80), (255, 168, 64), 140),
                             (int(R * 0.48), (255, 224, 130), 205),
                             (int(R * 0.24), (255, 252, 236), 255)):
            pygame.draw.circle(gs, (col[0], col[1], col[2], int(al * g)), c, max(rad, 2))
        for k in range(6):                         # spark rays (rotate with intensity)
            ang = k * (math.pi / 3.0) + g * 1.6
            ex, ey = c[0] + R * 1.45 * math.cos(ang), c[1] + R * 1.45 * math.sin(ang)
            pygame.draw.line(gs, (255, 215, 130, int(110 * g)), c, (int(ex), int(ey)), 1)
        return gs

    def _draw_cyl(self, jx, jy, a, length, width, frac, theta, glow):
        """Draw one cylinder + reciprocating piston + rod + crank journal along a
        bank axis tilted by angle ``a`` (radians) from vertical, hinged at the
        crank centre (jx, jy).  Metal surfaces are strip-shaded (bright centre,
        dark edges) for the round skeuomorphic look, even when tilted."""
        ux, uy = math.sin(a), -math.cos(a)            # 'up' along the cylinder
        qx, qy = math.cos(a), math.sin(a)             # perpendicular (across bore)
        cr = 9.0
        bx, by = jx + ux * cr * 1.4, jy + uy * cr * 1.4   # bore base (off the crank)
        hw = width / 2.0

        def shaded(d0, d1, halfw, base, n=7):         # round-metal strip gradient
            for si in range(n):
                e0 = (si / n * 2 - 1) * halfw; e1 = ((si + 1) / n * 2 - 1) * halfw
                f = 0.42 + 0.62 * (1.0 - abs((si + 0.5) / n * 2 - 1))
                col = (min(255, int(base[0] * f)), min(255, int(base[1] * f)),
                       min(255, int(base[2] * f)))
                pygame.draw.polygon(self.screen, col, [
                    (bx + ux * d0 + qx * e0, by + uy * d0 + qy * e0),
                    (bx + ux * d1 + qx * e0, by + uy * d1 + qy * e0),
                    (bx + ux * d1 + qx * e1, by + uy * d1 + qy * e1),
                    (bx + ux * d0 + qx * e1, by + uy * d0 + qy * e1)])

        def edge(d0, d1, halfw, col, w=1):
            pygame.draw.polygon(self.screen, col, [
                (bx + ux * d0 + qx * halfw, by + uy * d0 + qy * halfw),
                (bx + ux * d1 + qx * halfw, by + uy * d1 + qy * halfw),
                (bx + ux * d1 - qx * halfw, by + uy * d1 - qy * halfw),
                (bx + ux * d0 - qx * halfw, by + uy * d0 - qy * halfw)], w)

        shaded(-2, length + 4, hw + 3, (78, 84, 96))  # finned metal sleeve
        for fz in range(4):                           # cooling fins near the head
            d = length - 2 - fz * 5
            pygame.draw.line(self.screen, (40, 44, 54),
                             (bx + ux * d + qx * (hw + 3), by + uy * d + qy * (hw + 3)),
                             (bx + ux * d - qx * (hw + 3), by + uy * d - qy * (hw + 3)), 2)
        edge(-2, length + 4, hw + 3, (24, 26, 32), 2)
        # dark bore interior
        pygame.draw.polygon(self.screen, (24, 26, 32), [
            (bx + ux * 1 + qx * (hw - 2), by + uy * 1 + qy * (hw - 2)),
            (bx + ux * length + qx * (hw - 2), by + uy * length + qy * (hw - 2)),
            (bx + ux * length - qx * (hw - 2), by + uy * length - qy * (hw - 2)),
            (bx + ux * 1 - qx * (hw - 2), by + uy * 1 - qy * (hw - 2))])
        if glow > 0.02:                               # combustion flash near the top
            gx, gy = bx + ux * (length - 7), by + uy * (length - 7)
            gs = self._flash_surf(width * 0.6, glow)
            self.screen.blit(gs, (int(gx) - gs.get_width() // 2,
                                  int(gy) - gs.get_height() // 2))
        # piston (brighter round metal) with ring lands
        plen = 15.0
        travel = max(length - plen - 14, 6.0)
        ppos = 8.0 + (1.0 - frac) * travel            # TDC high, BDC near crank
        shaded(ppos, ppos + plen, hw - 3, (196, 202, 214))
        edge(ppos, ppos + plen, hw - 3, (96, 102, 114), 1)
        for rg in range(3):
            d = ppos + 4 + rg * 4
            pygame.draw.line(self.screen, (70, 76, 88),
                             (bx + ux * d + qx * (hw - 4), by + uy * d + qy * (hw - 4)),
                             (bx + ux * d - qx * (hw - 4), by + uy * d - qy * (hw - 4)))
        pin = (bx + ux * ppos, by + uy * ppos)        # wrist pin (piston base)
        pygame.draw.circle(self.screen, (54, 58, 70), (int(pin[0]), int(pin[1])), 4)
        pygame.draw.circle(self.screen, (150, 156, 168), (int(pin[0]), int(pin[1])), 4, 1)
        jrn = (jx + cr * math.sin(theta), jy + cr * math.cos(theta))   # crank journal
        self._draw_rod(pin, jrn, max(cr * 0.42, 3))
        # crank web (counterweight) + journal
        cwx, cwy = jx - cr * 0.5 * math.sin(theta), jy - cr * 0.5 * math.cos(theta)
        pygame.draw.circle(self.screen, (46, 50, 60), (int(cwx), int(cwy)), int(cr * 1.25))
        pygame.draw.circle(self.screen, (66, 72, 84), (int(jx), int(jy)), int(cr * 0.7))
        pygame.draw.circle(self.screen, ACCENT, (int(jrn[0]), int(jrn[1])), 4)
        pygame.draw.circle(self.screen, (20, 60, 110), (int(jrn[0]), int(jrn[1])), 4, 1)

    def _reuleaux(self, verts, samples=10):
        """Polygon points for a Reuleaux triangle through three apexes: each side
        is a circular arc centred on the opposite apex (the real rotor shape)."""
        pts = []
        for s in range(3):
            va, vb, vc = verts[s], verts[(s + 1) % 3], verts[(s + 2) % 3]
            rad = math.hypot(va[0] - vc[0], va[1] - vc[1])
            a0 = math.atan2(va[1] - vc[1], va[0] - vc[0])
            a1 = math.atan2(vb[1] - vc[1], vb[0] - vc[0])
            d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi      # shortest sweep
            for k in range(samples):
                a = a0 + d * k / samples
                pts.append((vc[0] + rad * math.cos(a), vc[1] + rad * math.sin(a)))
        return pts

    def _draw_w_banks(self, rect, sim, eng, left, right):
        """A W engine drawn as its TWO real VR units (VR6/VR8) side by side: each
        unit is an upright narrow-angle vee with its own vertical crank and two
        tight sub-banks (the 15-deg VR vee); the two units sit 90 deg apart."""
        mtop, mbot = rect.y + 338, rect.bottom - 46
        unit_name = f"VR{len(left)}"
        for ui, grp in enumerate((left, right)):
            ux = rect.x + int(rect.width * (0.29 if ui == 0 else 0.71))
            # split this VR unit into its two sub-banks by bank angle
            mid = sum(eng.cylinders[i].bank_angle_deg for i in grp) / max(len(grp), 1)
            subA = [i for i in grp if eng.cylinders[i].bank_angle_deg < mid]
            subB = [i for i in grp if eng.cylinders[i].bank_angle_deg >= mid]
            nsu = max(len(subA), len(subB), 1)
            dy = (mbot - mtop) / nsu
            # open the vee wider so cylinders fan OUT (horizontal) rather than UP,
            # keeping their tops clear of the telemetry gauges above.  Cap the
            # size tightly so the taller-dy W12 (3 stations) doesn't overgrow.
            tilt = math.radians(34.0)
            width = min(dy * 0.38, 20.0)
            length = min(dy * 1.1, rect.width * 0.16, 80.0)
            # vertical crankshaft for this unit (strip-shaded metal)
            cy0, cy1 = mtop + dy * 0.5 - 12, mtop + dy * (nsu - 0.5) + 12
            chw = max(width * 0.30, 8.0)
            for si in range(7):
                e0 = (si / 7 * 2 - 1) * chw; e1 = ((si + 1) / 7 * 2 - 1) * chw
                f = 0.40 + 0.66 * (1.0 - abs((si + 0.5) / 7 * 2 - 1))
                pygame.draw.polygon(self.screen, (min(255, int(70 * f)),
                                    min(255, int(76 * f)), min(255, int(90 * f))),
                                    [(ux + e0, cy0), (ux + e0, cy1),
                                     (ux + e1, cy1), (ux + e1, cy0)])
            for s in range(nsu):
                jy = mtop + dy * (s + 0.5)
                for sub, sgn in ((subA, -1.0), (subB, 1.0)):
                    if s >= len(sub):
                        continue
                    i = sub[s]
                    cyl = eng.cylinders[i]
                    phi = sim.cycle_phase_deg(i)
                    theta = math.radians(phi % 360.0)
                    frac = sim.piston_fraction(i)
                    glow = (min(max(sim.cylinder_pressure[i] - 101325.0, 0.0)
                                / (5.0 * 101325.0), 1.0)
                            if sim.ignition_on and not sim._fuel_cut and 360 <= phi < 445
                            else 0.0)
                    a = sgn * tilt
                    self._draw_cyl(ux, jy, a, length, width, frac, theta, glow)
                    lx = ux + math.sin(a) * (length + 14)
                    ly = jy - math.cos(a) * (length + 14)
                    lab = self.font_small.render(f"{i + 1}", True, DIM)
                    self.screen.blit(lab, (int(lx) - lab.get_width() // 2, int(ly) - 6))
            ulab = self.font_small.render(unit_name, True, (150, 158, 172))
            self.screen.blit(ulab, (ux - ulab.get_width() // 2, mbot + 6))

    def _draw_radial(self, rect, top, bottom):
        """Aircraft radial: cylinders arranged in a STAR around a central crank,
        each one reusing the metal cylinder/piston/rod drawing radiating outward."""
        sim, eng = self.sim, self.sim.engine
        n = eng.num_cylinders
        cx = rect.centerx
        cy = int((top + bottom) / 2 + 6)
        Rc = min(rect.width * 0.18, (bottom - top) * 0.40)
        length = Rc * 1.05
        width = min(40.0, Rc * 2.0 * math.pi / n * 0.5)
        # central crankcase
        pygame.draw.circle(self.screen, (30, 32, 38), (cx, cy), int(Rc * 0.46))
        pygame.draw.circle(self.screen, (54, 58, 68), (cx, cy), int(Rc * 0.40))
        pygame.draw.circle(self.screen, (96, 102, 116), (cx, cy), int(Rc * 0.46), 2)
        for i in range(n):
            cyl = eng.cylinders[i]
            a = math.radians(cyl.bank_angle_deg)        # radial position (0=up, CW)
            phi = sim.cycle_phase_deg(i)
            theta = math.radians(phi % 360.0)
            frac = sim.piston_fraction(i)
            glow = (min(max(sim.cylinder_pressure[i] - 101325.0, 0.0)
                        / (5.0 * 101325.0), 1.0)
                    if sim.ignition_on and not sim._fuel_cut and 360 <= phi < 445
                    else 0.0)
            self._draw_cyl(cx, cy, a, length, width, frac, theta, glow)
        # hub on top of all the rod big-ends
        pygame.draw.circle(self.screen, (70, 75, 88), (cx, cy), int(Rc * 0.18))
        pygame.draw.circle(self.screen, ACCENT, (cx, cy), 4)
        fo = "-".join(str(x) for x in eng.firing_order)
        self.screen.blit(self.font_small.render(f"{self.tr('firing order:')} {fo}",
                         True, ACCENT), (rect.x + 18, rect.bottom - 14))

    def _draw_rotary(self, rect, top, bottom):
        """Wankel-rotor visualiser: a 2-lobe epitrochoid housing with a Reuleaux
        triangular rotor orbiting eccentrically (spinning at 1/3 shaft speed),
        face recesses, apex seals, spark plugs and a firing-chamber glow.  One
        housing per ROTOR (the model fires twice per rotor, so rotors = cyl/2)."""
        sim, eng = self.sim, self.sim.engine
        n = max(1, eng.num_cylinders // 2)
        col_w = rect.width / n
        R = min(col_w * 0.34, (bottom - top) * 0.40)
        sx = 1.15                                      # housing is wider than tall
        e = R * 0.15                                   # eccentricity
        cy = (top + bottom) / 2.0
        shaft0 = sim.crank_angle
        TAU3 = 2.0943951                               # 120 deg
        for i in range(n):
            cx = rect.x + col_w * (i + 0.5)
            shaft = shaft0 + i * (2.0 * math.pi / n)
            # --- epitrochoid housing (2-lobe, horizontally stretched) ---
            hull = []
            for k in range(96):
                a = 2.0 * math.pi * k / 96.0
                hx = (R * math.cos(a) + e * math.cos(3 * a)) * sx
                hy = R * math.sin(a) + e * math.sin(3 * a)
                hull.append((cx + hx, cy + hy))
            pygame.draw.polygon(self.screen, (32, 35, 42), hull)       # chamber void
            pygame.draw.polygon(self.screen, (96, 102, 116), hull, 3)  # housing wall
            pygame.draw.polygon(self.screen, (60, 64, 74), hull, 1)
            # spark plugs (leading + trailing) at the top waist
            for sp in (-0.16, 0.16):
                spx, spy = cx + sx * R * sp, cy - R * 0.9
                pygame.draw.circle(self.screen, (150, 156, 168), (int(spx), int(spy)), 3)
            # --- combustion glow in the firing chamber (top-right of housing) ---
            j = min(2 * i + 1, eng.num_cylinders - 1)
            press = max(sim.cylinder_pressure[2 * i], sim.cylinder_pressure[j])
            glow = (min(max(press - 101325.0, 0.0) / (5.0 * 101325.0), 1.0)
                    if sim.ignition_on and not sim._fuel_cut else 0.0)
            if glow > 0.03:
                gx, gy = cx + sx * R * 0.42, cy - R * 0.42
                gs = self._flash_surf(R * 0.5, glow)
                self.screen.blit(gs, (int(gx) - gs.get_width() // 2,
                                      int(gy) - gs.get_height() // 2))
            # --- Reuleaux rotor: centre orbits, body spins at 1/3 shaft speed ---
            rcx = cx + e * sx * math.cos(shaft)
            rcy = cy + e * math.sin(shaft)
            rotang = -shaft / 3.0
            apex = [(rcx + R * 0.66 * sx * math.cos(rotang + k * TAU3),
                     rcy + R * 0.66 * math.sin(rotang + k * TAU3)) for k in range(3)]
            body = self._reuleaux(apex)
            pygame.draw.polygon(self.screen, (120, 126, 140), body)        # rotor base
            # beveled metal sheen: a shrunk, brighter Reuleaux offset up-left
            hi_apex = [(rcx + (ax - rcx) * 0.82 - R * 0.06,
                        rcy + (ay - rcy) * 0.82 - R * 0.06) for ax, ay in apex]
            pygame.draw.polygon(self.screen, (176, 182, 196), self._reuleaux(hi_apex))
            pygame.draw.polygon(self.screen, (90, 96, 110), body, 2)
            # face recesses (the dish in each rotor flank) + side seal lines
            for k in range(3):
                m = ((apex[k][0] + apex[(k + 1) % 3][0]) / 2,
                     (apex[k][1] + apex[(k + 1) % 3][1]) / 2)
                fx, fy = rcx + (m[0] - rcx) * 0.55, rcy + (m[1] - rcy) * 0.55
                pygame.draw.circle(self.screen, (96, 102, 118), (int(fx), int(fy)), int(R * 0.2))
            pygame.draw.line(self.screen, (60, 64, 74),
                             (apex[0][0], apex[0][1]), (apex[1][0], apex[1][1]), 1)
            # internal ring gear hint + apex seals
            pygame.draw.circle(self.screen, (40, 43, 50), (int(rcx), int(rcy)), int(R * 0.34))
            pygame.draw.circle(self.screen, (70, 75, 88), (int(rcx), int(rcy)), int(R * 0.34), 1)
            for v in apex:
                pygame.draw.circle(self.screen, ACCENT, (int(v[0]), int(v[1])), 3)
            # --- eccentric shaft: fixed stationary gear + orbiting journal ---
            pygame.draw.circle(self.screen, (54, 58, 68), (int(cx), int(cy)), int(R * 0.2))
            pygame.draw.circle(self.screen, (90, 96, 110), (int(cx), int(cy)), int(R * 0.2), 1)
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
        lab = self.font_small.render(self.tr("TURBO"), True, DIM)
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
        lab = self.font_small.render(self.tr(txt), True, DIM)
        self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))

    def _exhaust_db(self):
        """A rough exhaust-loudness readout from the synth's last output RMS."""
        lvl = getattr(self.synth, "last_level", 0.0) if self.synth else 0.0
        if lvl < 1e-4:
            return 0.0
        return max(0.0, min(120.0, 108.0 + 20.0 * math.log10(lvl)))

    def _update_hud_signals(self):
        """Sample the exhaust-flow scope and integrate fuel burn — once per frame."""
        sim = self.sim
        # fuel: burn = air-mass / AFR; only when actually combusting.
        if sim.ignition_on and not sim._fuel_cut:
            t = sim.telemetry()
            afr = max(t["afr"], 5.0)
            air_kgs = max(t["scfm"], 0.0) * 0.0283168 / 60.0 * 1.225   # scfm -> kg/s
            fuel_ls = (air_kgs / afr) / 0.745                          # gasoline kg/L
        else:
            fuel_ls = 0.0
        self._fuel_lph += (fuel_ls * 3600.0 - self._fuel_lph) * 0.12
        self._fuel_total_l += fuel_ls / FPS
        # spin the road wheel at the real wheel rate (v / wheel_radius)
        dt = sim.drivetrain
        self._wheel_ang += (dt.v / max(dt.wheel_radius, 0.1)) / FPS

    def _draw_ignition_bank(self, x, y, w):
        """One light per cylinder, flashing as that cylinder fires (power stroke) —
        the original game's IGNITION column.  Returns the y below the bank."""
        sim, eng = self.sim, self.sim.engine
        n = eng.num_cylinders
        self.screen.blit(self.font_small.render(self.tr("IGNITION"), True, DIM), (x, y))
        # Lay the lights out like the real engine, one row per cylinder BANK:
        # inline -> 1 row, V (V6/V12) -> 2 rows, W (W12 3+3+3+3, W16 4+4+4+4) ->
        # 4 rows.  A radial / rotary collapses to a single row.
        if getattr(eng, "is_radial", False) or eng.is_rotary:
            rows_list = [list(range(n))]
        else:
            banks = {}
            for i in range(n):
                banks.setdefault(round(eng.cylinders[i].bank_angle_deg, 1), []).append(i)
            rows_list = ([list(range(n))] if len(banks) <= 1
                         else [banks[k] for k in sorted(banks)])
        nrows = len(rows_list)
        per_row = max((len(rw) for rw in rows_list), default=1)
        dx = min((w - 12) / max(per_row, 1), 30.0)
        # Keep the whole bank within a fixed height (so a 4-bank W doesn't shove
        # the readouts/scope/hints below it off the bottom of the panel): squeeze
        # the row pitch when there are many banks.
        max_h = 60
        pitch = min(20, (max_h - 16) // max(nrows, 1))
        r = 6 if pitch >= 17 else (5 if pitch >= 13 else 4)
        fade = self._ign_flash
        for rr, rw in enumerate(rows_list):
            for cc, i in enumerate(rw):
                cxp = int(x + 8 + dx * (cc + 0.5))
                cyp = int(y + 16 + rr * pitch)
                phi = sim.cycle_phase_deg(i)
                firing = sim.ignition_on and not sim._fuel_cut and 360.0 <= phi < 455.0
                fade[i] = max(1.0 if firing else 0.0, fade.get(i, 0.0) * 0.70)
                f = fade[i]
                col = (int(38 + 214 * f), int(44 + 150 * f), int(52 + 36 * f))
                pygame.draw.circle(self.screen, (22, 24, 30), (cxp, cyp), r + 2)
                pygame.draw.circle(self.screen, col, (cxp, cyp), r)
                pygame.draw.circle(self.screen, (140, 146, 160), (cxp, cyp), r, 1)
        return y + 16 + min(nrows * pitch + 4, 44)   # never taller than a 2-bank V

    def _draw_scope(self, x, y, w, h, label):
        """Per-cylinder exhaust-flow scope: ONE translucent orange trace per
        cylinder.  The whole 720-deg cycle maps across the width with the pulses
        at FIXED positions (refresh-style, not scrolling): each cylinder's blip
        sits where it fires and LIGHTS UP live as that cylinder actually fires,
        its height scaling with load x the cylinder's own voicing amplitude."""
        pygame.draw.rect(self.screen, (12, 13, 16), (x, y, w, h))
        pygame.draw.rect(self.screen, (44, 48, 56), (x, y, w, h), 1)
        self.screen.blit(self.font_small.render(self.tr(label), True, DIM), (x + 6, y + 3))
        sim = self.sim
        n = sim.engine.num_cylinders
        if n < 1 or w < 4:
            return
        offs = sim._offset_deg
        voice = getattr(self.synth, "_cyl_voice", None) if self.synth else None
        # overall flow strength from the real blowdown pressure (grows with load)
        load = min(max((sim.blowdown_pressure() - 101325.0) / (0.9 * 101325.0),
                       0.30), 1.05)
        base, amp = h - 4, h - 12
        ang = np.arange(w) / (w - 1) * 720.0          # FIXED window (refresh, no scroll)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        for i in range(n):
            phi = (ang + offs[i]) % 720.0
            d = phi - 505.0                              # 0 at exhaust-valve open
            env = np.where((d >= 0) & (d < 210.0),
                           np.clip(d / 4.0, 0.0, 1.0) * np.exp(-np.clip(d, 0, None) / 30.0),
                           0.0)
            # exaggerate the (small) per-cylinder voicing x3 so the strong/weak
            # difference is legible on screen (display only — audio is unchanged)
            vdev = (voice.amp[i] - 1.0) * 3.0 if voice and i < len(voice.amp) else 0.0
            # live firing flash: each pulse brightens & swells as its cylinder
            # actually fires now (reuses the ignition-light fade), so a fixed-axis
            # refresh scope still shows the cylinders firing in sequence.
            flash = self._ign_flash.get(i, 0.0)
            amp_i = load * (1.0 + vdev) * (0.45 + 0.55 * flash)
            yv = base - np.clip(env * amp_i, 0.0, 1.1) * amp
            sh = (i % 6) * 8
            a = int(90 + 150 * flash)                    # brighter as it fires
            pts = np.column_stack((np.arange(w), yv)).astype(np.int32).tolist()
            pygame.draw.lines(surf, (236, 150 - sh, 60 + sh, a), False, pts, 1)
        self.screen.blit(surf, (x, y))
        cap = self.font_small.render(f"x{n}", True, (130, 96, 54))
        self.screen.blit(cap, (x + w - cap.get_width() - 6, y + 3))

    def _mini_scope(self, x, y, w, h, label, wave, unipolar=False, col=(90, 230, 130)):
        """One waveform window redrawn across the width every frame.  ``unipolar``
        draws an all-positive AIRFLOW trace rising from a low baseline; otherwise
        a bipolar audio trace centred on the mid-line."""
        pygame.draw.rect(self.screen, (10, 11, 14), (x, y, w, h))
        pygame.draw.rect(self.screen, (52, 58, 70), (x, y, w, h), 1)
        if unipolar:
            base_y = y + h - 8
            pygame.draw.line(self.screen, (32, 36, 44), (x, base_y), (x + w, base_y), 1)
        else:
            base_y = y + h * 0.5
            pygame.draw.line(self.screen, (32, 36, 44), (x, base_y), (x + w, base_y), 1)
        if wave is not None and len(wave) > 1:
            n = len(wave)
            mx = float(np.max(np.abs(wave))) or 1e-6
            xs = x + np.arange(n) / (n - 1) * (w - 2) + 1
            if unipolar:
                yv = base_y - (wave / mx) * (h - 18)
            else:
                yv = base_y - (wave / mx) * (h * 0.40)
            pts = np.column_stack((xs, yv)).astype(np.int32).tolist()
            pygame.draw.lines(self.screen, col, False, pts, 1)
        self.screen.blit(self.font_small.render(self.tr(label), True, (150, 158, 172)),
                         (x + 5, y + 3))

    def _mini_scope_multi(self, x, y, w, h, label, waves, colors):
        """A flow window with ONE coloured trace per cylinder, overlaid (all share
        the same baseline + vertical scale) so each cylinder's pulse is legible by
        colour rather than merged into a single lump."""
        pygame.draw.rect(self.screen, (10, 11, 14), (x, y, w, h))
        pygame.draw.rect(self.screen, (52, 58, 70), (x, y, w, h), 1)
        base_y = y + h - 8
        pygame.draw.line(self.screen, (32, 36, 44), (x, base_y), (x + w, base_y), 1)
        if waves:
            mx = max((float(np.max(np.abs(wv))) for wv in waves), default=1e-6) or 1e-6
            n = len(waves[0])
            xs = x + np.arange(n) / (n - 1) * (w - 2) + 1
            for wv, col in zip(waves, colors):
                yv = base_y - (wv / mx) * (h - 18)
                pts = np.column_stack((xs, yv)).astype(np.int32).tolist()
                pygame.draw.lines(self.screen, col, False, pts, 1)
        self.screen.blit(self.font_small.render(self.tr(label), True, (150, 158, 172)),
                         (x + 5, y + 3))

    def _airflow_cyl(self, w):
        """PER-CYLINDER exhaust airflow, all pulses STACKED at the same position
        (refresh-style): every cylinder's blowdown pulse is drawn at the same spot
        so they overlay, one colour each, and you compare their shapes directly.
        Each swells live as that cylinder fires; tiny per-cylinder decay spread
        keeps the stacked traces distinct.  Returns a list of n positive arrays."""
        sim = self.sim
        n = sim.engine.num_cylinders
        if n < 1 or w < 2:
            return []
        load = min(max((sim.blowdown_pressure() - 101325.0) / (0.9 * 101325.0),
                       0.30), 1.05)
        voice = getattr(self.synth, "_cyl_voice", None) if self.synth else None
        ang = np.arange(w) / (w - 1) * 300.0              # one pulse window
        d = ang - 26.0                                    # small lead-in
        rise = np.clip(d / 4.0, 0.0, 1.0)
        waves = []
        for i in range(n):
            tau = 30.0 * (0.82 + 0.36 * (i / max(n - 1, 1)))   # spread decays a touch
            env = np.where((d >= 0) & (d < 210.0),
                           rise * np.exp(-np.clip(d, 0, None) / tau), 0.0)
            flash = self._ign_flash.get(i, 0.0)           # live per-cylinder firing
            amp = (0.32 + 0.68 * flash) * load
            if voice is not None and i < len(voice.amp):
                amp *= voice.amp[i]
            waves.append(env * amp)
        return waves

    @staticmethod
    def _cyl_colors(n):
        """A distinct hue per cylinder so overlaid traces are easy to tell apart."""
        cols = []
        for i in range(max(n, 1)):
            c = pygame.Color(0, 0, 0)
            c.hsva = (int(360 * i / max(n, 1)), 82, 100, 100)
            cols.append((c.r, c.g, c.b))
        return cols

    @staticmethod
    def _smooth(arr, k):
        """Moving-average smoothing — models a stage damping the flow pulsation."""
        if k <= 1 or len(arr) < 2:
            return arr
        k = min(k, len(arr))
        return np.convolve(arr, np.ones(k) / k, mode="same")

    @staticmethod
    def _stabilize(wave):
        """Peak-trigger an audio tap so the dominant transient sits at a FIXED
        position every frame — you see the steady TIMBRE SHAPE instead of a trace
        sliding with rpm (a poor-man's scope trigger)."""
        if wave is None or len(wave) < 4:
            return wave
        shift = len(wave) // 3 - int(np.argmax(np.abs(wave)))
        return np.roll(wave, shift)

    def _draw_exhaust_scopes(self, rect):
        """Overlay: a grid of per-stage waveform windows.  FLOW mode shows the
        physical EXHAUST AIRFLOW (rpm-pitched, scrolling) progressively damped down
        the pipe; AUDIO mode shows the synth's listener-audio chain taps."""
        self._panel(rect)
        flow = self.scope_mode == "flow"
        title = (self.tr("EXHAUST FLOW — STAGE WAVEFORMS") if flow
                 else self.tr("LISTENER AUDIO — STAGE WAVEFORMS"))
        self.screen.blit(self.font.render(title, True, INK), (rect.x + 18, rect.y + 14))
        hint = self.tr("each window = that stage's output  ·  E / click to close")
        self.screen.blit(self.font_small.render(hint, True, DIM),
                         (rect.x + 18, rect.y + 40))
        # flow / audio toggle button (top-right) — click switches, doesn't close
        bw, bh = 150, 26
        btn = pygame.Rect(rect.right - 18 - bw, rect.y + 12, bw, bh)
        self._scope_toggle_rect = btn
        self.screen.blit(self._grad_surf(btn.w, btn.h, BTN_ON_HI, BTN_ON_LO, 5,
                                         gloss=True), btn.topleft)
        pygame.draw.rect(self.screen, BEVEL_LO, btn, width=1, border_radius=5)
        bl = self.tr("show: FLOW") if flow else self.tr("show: AUDIO")
        bt = self.font_small.render(bl, True, (255, 255, 255))
        self.screen.blit(bt, (btn.centerx - bt.get_width() // 2,
                              btn.centery - bt.get_height() // 2))
        taps = getattr(self.synth, "_stage_taps", {}) if self.synth else {}
        order = ((self.synth._flow_stages if flow else self.synth._audio_stages)
                 if self.synth else [])
        if not order:
            return
        cols, rows = 4, 3
        gx, gy = rect.x + 18, rect.y + 64
        gw, gh = rect.width - 36, rect.height - 82
        cw = (gw - (cols - 1) * 12) / cols
        ch = (gh - (rows - 1) * 12) / rows
        # FLOW: one coloured trace PER CYLINDER, each window damped a bit more down
        # the pipe (header sharp -> tailpipe smoothed).
        base_waves = self._airflow_cyl(int(cw) - 2) if flow else None
        cyl_cols = self._cyl_colors(len(base_waves)) if flow else None
        for idx, name in enumerate(order):
            r, c = divmod(idx, cols)
            cx = gx + c * (cw + 12)
            cy = gy + r * (ch + 12)
            if flow:
                k = max(1, int((int(cw) - 2) * (0.006 + idx * 0.012)))
                waves = [self._smooth(wv, k) for wv in base_waves]
                self._mini_scope_multi(int(cx), int(cy), int(cw), int(ch),
                                       f"{idx + 1} {self.tr(name)}", waves, cyl_cols)
            else:
                self._mini_scope(int(cx), int(cy), int(cw), int(ch),
                                 f"{idx + 1} {self.tr(name)}",
                                 self._stabilize(taps.get(name)))

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
            # Spin the wheel at the REAL shaft speed: the turbine turns
            # (shaft_rpm / engine_rpm)x faster than the crank, so the blades
            # blur as the turbo spools — a live turbo-speed tachometer.
            fi_rpm = ez('fi_rpm', t.get('fi_rpm', 0.0), 0.18)
            ratio = fi_rpm / max(self.sim.rpm, 1.0)
            spin = self.sim.crank_angle * max(ratio, 0.4)
            load = (self.sim.boost / max(eng.boost_bar, 0.05)) if eng.boost_bar else 0.0
            fcx, fcy = x0 + gap * 5.5, cy
            if eng.induction == "turbo":
                self._draw_turbo(fcx, fcy, r - 2, spin, load)
            else:
                self._draw_blower(fcx, fcy, r - 4, spin, load,
                                  centri=(eng.induction == "centrifugal"))
            # shaft-speed readout (the requested turbo-rpm display) + boost
            bt = self.font_small.render(f"{self.sim.boost:.2f} bar", True, ACCENT)
            self.screen.blit(bt, (int(fcx) - bt.get_width() // 2, int(fcy + r * 0.42)))
            rpm_txt = (f"{fi_rpm / 1000.0:.0f}k rpm" if fi_rpm >= 1000.0
                       else "0 rpm")
            rt = self.font_small.render(rpm_txt, True, GOOD)
            self.screen.blit(rt, (int(fcx) - rt.get_width() // 2, int(fcy + r + 18)))

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
        T = self.tr

        # --- tachometer ---
        cx, cy, r = rect.centerx, rect.y + 114, 92
        self._draw_tach(cx, cy, r, sim.rpm, eng.redline_rpm)
        # --- spinning road wheel (in the corner beside the tach) ---
        self._draw_wheel(rect.x + 48, rect.y + 78, 34, self._wheel_ang,
                         sim.drivetrain.speed_kmh)

        # --- per-cylinder ignition bank (original-game IGNITION lights) ---
        yb = self._draw_ignition_bank(rect.x + 24, rect.y + 220, rect.width - 48)

        # --- digital readouts ---
        tq = self._disp_torque
        hp = nm_to_hp_at(max(tq, 0.0), max(sim.rpm, 1.0))
        dt = sim.drivetrain
        y = yb + 8
        mode = T("Auto") if dt.auto else T("Manual")
        rows = [
            ("RPM", f"{sim.rpm:6.0f}", ACCENT),
            ("TORQUE", f"{tq:5.0f} Nm ({nm_to_lbft(tq):.0f} lb-ft)", INK),
            ("POWER", f"{hp:6.0f} hp", INK),
            ("THROTTLE", f"{sim.throttle*100:5.0f} %", INK),
            ("GEAR", f"{dt.gear_name:>3} {mode}"
                     f" [{_GBX_LABEL.get(dt.gearbox_type, dt.gearbox_type).upper()}]", GOOD),
            ("SPEED", f"{dt.speed_kmh:5.0f} km/h", INK),
        ]
        for label, value, col in rows:
            self.screen.blit(self.font.render(T(label), True, DIM), (rect.x + 24, y))
            self.screen.blit(self.font.render(value, True, col), (rect.x + 146, y))
            y += 21

        # --- engine flow / fuel instrument block (the original game's readouts) ---
        t = sim.telemetry()
        y += 5
        pygame.draw.line(self.screen, (44, 48, 56),
                         (rect.x + 24, y - 4), (rect.right - 24, y - 4))
        flow = [
            ("MANIFOLD", f"{t['vacuum_inhg']:+.1f} inHg"),
            ("AIR", f"{t['scfm']:5.0f} scfm"),
            ("VOL EFF", f"{t['ve_pct']:4.0f} %"),
            ("IN AFR", f"{t['afr']:5.1f}"),
            ("EX O2", f"{t['o2_pct']:4.1f} %"),
            ("FUEL", f"{self._fuel_lph:4.1f} L/h"),
        ]
        cols = [rect.x + 24, rect.x + 220]
        for i, (lab, val) in enumerate(flow):
            bx, ry = cols[i % 2], y + (i // 2) * 18
            self.screen.blit(self.font_small.render(T(lab), True, DIM), (bx, ry))
            self.screen.blit(self.font_small.render(val, True, INK), (bx + 76, ry))
        y += 3 * 18 + 2
        used = (f"{T('USED')} {self._fuel_total_l:.3f} L  ·  "
                f"${self._fuel_total_l * 1.5:.2f}")
        self.screen.blit(self.font_small.render(used, True, ACCENT), (rect.x + 24, y))
        y += 19

        # --- total exhaust-flow oscilloscope ---
        self._draw_scope(rect.x + 24, y, rect.width - 48, 56, "TOTAL EXHAUST FLOW")
        y += 56 + 7

        # --- status indicators (compact) ---
        self._status_dot(rect.x + 24, y, T("IGNITION"), sim.ignition_on, GOOD, WARN)
        self._status_dot(rect.x + 150, y, T("STARTER"), sim.starter_engaged, ACCENT, DIM)
        self._status_dot(rect.x + 276, y, T("REV LIMIT"), sim._fuel_cut, WARN, DIM)
        y += 21
        self._status_dot(rect.x + 24, y, T("CLUTCH IN"), dt.clutch < 0.5, ACCENT, DIM)
        self._status_dot(rect.x + 150, y, T("IN GEAR"), dt.gear > 0, GOOD, DIM)
        self._status_dot(rect.x + 276, y, T("AUDIO"),
                         self.synth.enabled and self.synth.volume > 0, GOOD, DIM)
        y += 21
        # two short lines so the hint never runs off the panel's right edge
        self.screen.blit(self.font_small.render(
            "↑↓ gas · ZX shift · A ign · S start", True, (96, 102, 116)),
            (rect.x + 24, y))
        self.screen.blit(self.font_small.render(
            "C mixer · E scope · M mute · Esc quit", True, (96, 102, 116)),
            (rect.x + 24, y + 15))

    def _draw_wheel(self, cx, cy, R, ang, speed_kmh):
        """A spinning road wheel — tyre, alloy rim + spokes, brake disc & caliper —
        turning at the real road rate so you can SEE the car is moving."""
        cx, cy = int(cx), int(cy)
        pygame.draw.circle(self.screen, (16, 17, 20), (cx, cy), R + 2)        # tyre
        pygame.draw.circle(self.screen, (40, 43, 50), (cx, cy), R, 3)         # tread
        pygame.draw.circle(self.screen, (28, 30, 36), (cx, cy), int(R * 0.74))
        # brake disc behind the rim (with a hint of caliper)
        pygame.draw.circle(self.screen, (54, 58, 68), (cx, cy), int(R * 0.66))
        pygame.draw.circle(self.screen, (70, 75, 88), (cx, cy), int(R * 0.66), 1)
        pygame.draw.rect(self.screen, (200, 90, 60),
                         (cx - 3, cy - int(R * 0.7), 6, int(R * 0.22)), border_radius=2)
        rim = int(R * 0.6)
        # blur the spokes at speed: fade them out as the wheel spins fast
        fast = min(speed_kmh / 90.0, 1.0)
        spoke_col = (int(150 - 70 * fast), int(156 - 70 * fast), int(170 - 70 * fast))
        for k in range(5):
            a = ang + k * (2.0 * math.pi / 5.0)
            ex, ey = cx + rim * math.cos(a), cy + rim * math.sin(a)
            pygame.draw.line(self.screen, spoke_col, (cx, cy), (int(ex), int(ey)), 3)
        if fast > 0.25:                                  # motion-blur ring at speed
            br = pygame.Surface((2 * rim + 6, 2 * rim + 6), pygame.SRCALPHA)
            pygame.draw.circle(br, (150, 156, 170, int(70 * fast)),
                               (rim + 3, rim + 3), rim, 3)
            self.screen.blit(br, (cx - rim - 3, cy - rim - 3))
        pygame.draw.circle(self.screen, (180, 186, 200), (cx, cy), int(R * 0.6), 2)  # rim lip
        pygame.draw.circle(self.screen, (120, 126, 140), (cx, cy), int(R * 0.16))    # hub
        pygame.draw.circle(self.screen, (60, 64, 74), (cx, cy), int(R * 0.16), 1)
        lab = self.font_small.render(f"{speed_kmh:.0f} km/h", True, DIM)
        self.screen.blit(lab, (cx - lab.get_width() // 2, cy + R + 4))

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
        cap = self.font_small.render(self.tr("rpm   x1000"), True, DIM)
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
