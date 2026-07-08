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
import os
import sys

# Let the audio callback thread grab the GIL more often (default 5ms) so a long
# pure-Python draw can't starve it for a whole audio block -> no high-rpm breakup.
sys.setswitchinterval(0.001)

import numpy as np
import pygame

from .simulator import Simulator
from .audio import Synthesizer, list_output_devices
from .telemetry import ForzaTelemetry, FORZA_PORT
from . import presets
from . import config
from .units import nm_to_lbft, nm_to_hp_at, rpm_to_rads

# python-for-android sets these in the app's environment; use them to detect that
# we're running on a phone so the UI can default to the finger-control overlay.
# Unified Android detection (compatible with both env vars and sys.platform)
IS_ANDROID = bool(os.environ.get("ANDROID_ARGUMENT")
                  or os.environ.get("ANDROID_APP_PATH")
                  or os.environ.get("ANDROID_PRIVATE")
                  or (hasattr(sys, "platform") and sys.platform == "android"))

# Backward compatibility: keep ON_ANDROID for existing code
ON_ANDROID = IS_ANDROID

# 32 kHz is the DEFAULT: the whole synth is sample-rate-normalised (every filter
# cutoff is f/(sr/2) and clamped to sr*0.45, delays/decays are in seconds), so
# dropping from 44.1k costs almost nothing audible but cuts the per-block DSP by
# ~27% — the cheapest real win on a phone.  44.1/48k stay available on the toggle.
SAMPLE_RATES = [32000, 44100, 48000]


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
    # control-key hint functions (【key】function)
    "Gas": "油门", "Clutch": "离合", "Upshift": "升挡", "Downshift": "降挡",
    "Auto/Manual": "自动/手动", "Ign": "点火", "Start": "起动",
    "Mixer": "混音", "Mute": "静音", "Exit": "退出",
    # engine-off prompt
    "Engine off — start it:": "发动机未启动，请起动：", "Ignition": "点火",
    # trip readouts
    "Oil": "机油", "RESET": "清零",
    # engine-bay components (shown under the bay drawing)
    "Air Filter": "空气滤芯", "Radiator": "散热器", "Throttle Body": "节气门体",
    "Catalytic": "三元催化", "Resonator": "中段消音", "Muffler": "尾段消音",
    "Tailpipe": "排气尾管", "Intercooler": "中冷器", "Wastegate": "废气阀",
    "Blow-off": "泄压阀", "Megaphone": "喇叭口", "Supercharger": "机械增压",
    "Centrifugal SC": "离心增压", "Single Turbo": "单涡轮", "Quad-turbo": "四涡轮",
    "Prop Reduction": "桨减速器", "HV Battery": "高压电池",
    "Twin-scroll Single Turbo": "双涡管单涡轮", "Parallel Twin-turbo": "并列双涡轮",
    "Twincharge": "双增压", "DOC": "氧化催化", "DPF": "颗粒捕集", "DEF": "尿素",
    # touch toggle / states
    "ON": "开", "OFF": "关", "Odo Reset": "里程清零", "Low Q": "低画质",
    "Forza Ultra": "极速模式", "FORZA ULTRA — display off (audio only)": "极速模式 — 关闭画面（仅音频）",
    # gearbox type tag in the GEAR readout
    "single-clutch": "单离合", "AT": "自动变速", "manual": "手动挡", "DCT": "双离合",
    # toolbar
    "Demo cars": "示例车", "Load car…": "载入车型…", "Load EQ…": "载入EQ…",
    "Save…": "保存…", "Mixer / EQ": "混音/EQ", "Out:": "输出:",
    "Auto": "自动", "Manual": "手动", "Cabin": "车内", "Gear whine": "直齿啸叫",
    "Touch": "触屏", "Touch OFF": "关闭触屏",
    "Cat": "三元", "Bent": "弯管", "Flutter": "颤振", "Hybrid": "混动",
    "G-pad": "G力",
    "Lang": "语言", "Pops": "放炮", "Slip": "打滑", "Slow-mo": "慢动作", "Slow": "慢",
    "off": "关", "Firing order:": "点火顺序:",
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
    "Firing voice:": "点火音色:", "cabin": "车内",
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
    "Engine Bay": "发动机舱",
    "Flat-plane Crank": "平面曲轴", "Cross-plane Crank": "十字曲轴",
    "ENGINE ANALYZER": "发动机分析仪",
    "Live engine signals  ·  E / click to close": "实时发动机信号  ·  按 E 或点击关闭",
    "WAVEFORM · master audio output": "WAVEFORM · 主音频输出波形",
    "FIRING PULSES · cylinder combustion": "FIRING PULSES · 气缸燃烧脉冲",
    "EXHAUST FLOW · system pressure": "EXHAUST FLOW · 排气系统压力",
    "VALVE LIFT · intake / exhaust": "VALVE LIFT · 进 / 排气门升程",
    "TORQUE / HP · output curves": "TORQUE / HP · 扭矩 / 马力曲线",
    "CYLINDER PRESSURE · 4-stroke": "CYLINDER PRESSURE · 单缸四冲程缸压",
    "SPARK ADVANCE · ignition timing": "SPARK ADVANCE · 点火提前角时序",
    "EQ low (dB)": "EQ低频(dB)",
    "EQ mid (dB)": "EQ中频(dB)", "EQ high (dB)": "EQ高频(dB)",
    "Presence (bite)": "临场(咬合)",
}

# The UI is authored in a fixed 1100x680 logical coordinate space (panels reach
# ~1076x656, so a smaller canvas CLIPS the layout — it does not scale it).  On
# Android the GPU (SDL2 SCALED, below) downsamples this logical canvas to the
# phone's native screen, so the cheap GPU shrink is what cuts the on-screen
# pixel cost — NOT a smaller logical canvas, which would just chop off the
# gauges/bay.  Per-frame CPU cost is reduced losslessly by the static-layer
# cache instead (see _bay_cache / _draw_engine_panel).
WIDTH, HEIGHT = 1100, 680
FPS = 60

# Friendly transmission labels for the HUD (sets the auto-shift feel).
_GBX_LABEL = {"dct": "DCT", "single": "single-clutch", "at": "AT", "manual": "manual",
              "aircraft": "Prop Reduction"}

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
# turbulence RESTORED (2026-07): the ungated hiss floor is now tiny (0.008 in
# audio.py), so these gated-fizz values give per-firing grit back WITHOUT the
# dyno-cell steady hiss the earlier blanket cut was chasing.
FIRING_VOICES = [
    ("Balanced", {"pulse_tau": 22.0, "turbulence": 0.66, "body": 1.05,
                  "crack": 0.18, "firing_pitch": 105.0}),
    ("Sharp",    {"pulse_tau": 14.0, "turbulence": 0.56, "body": 0.62,
                  "crack": 0.32, "firing_pitch": 145.0}),
    ("Deep",     {"pulse_tau": 30.0, "turbulence": 0.62, "body": 0.95,
                  "crack": 0.18, "firing_pitch": 75.0}),
    ("Raspy",    {"pulse_tau": 18.0, "turbulence": 1.05, "body": 0.50,
                  "crack": 0.32, "firing_pitch": 130.0}),
    ("Hollow",   {"pulse_tau": 26.0, "turbulence": 0.42, "body": 0.70,
                  "crack": 0.22, "firing_pitch": 95.0}),
]


class App:
    def __init__(self, preset_key="aven"):
        pygame.init()
        from . import __version__
        pygame.display.set_caption(f"PyEngineSim  v{__version__}  —  by Leo")
        try:                                  # window/taskbar icon: the hot turbo
            icon = pygame.image.load(os.path.join(
                os.path.dirname(__file__), "assets", "logo.png"))
            pygame.display.set_icon(icon)
        except Exception:
            pass
        # The whole UI is drawn onto a fixed-size canvas, then scaled to fit a
        # freely resizable OS window — so you can drag the window to any size
        # (or maximise it) and everything scales cleanly, keeping its layout.
        self._scaled = False
        if IS_ANDROID:
            # NOTE: do NOT set SDL_VIDEO_GL_DRIVER here.  It expects a LIBRARY
            # PATH (e.g. "libGLESv2.so"); a bogus value like "gles2" makes SDL's
            # GL library load fail, EVERY window-creation attempt then fails, and
            # the app dies on launch with no traceback.  SDL already picks the
            # right GLES driver on Android by itself.
            os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
            # SDL2 logical-size scaling: render the fixed canvas, then let the
            # GPU scale it to FILL the phone screen with aspect ratio preserved
            # (fixes the "tiny in the corner" bug and drops the costly per-frame
            # CPU smoothscale).  Fall back progressively — a device that rejects
            # SCALED still gets a plain fullscreen, then a bare window; never die
            # in display init.
            self.window = None
            for flags, scaled in (
                    (pygame.SCALED | pygame.FULLSCREEN | pygame.DOUBLEBUF, True),
                    (pygame.FULLSCREEN, False),
                    (0, False)):
                try:
                    self.window = pygame.display.set_mode((WIDTH, HEIGHT), flags)
                    self._scaled = scaled
                    break
                except Exception:
                    continue
            if self.window is None:                    # absolute last resort
                self.window = pygame.display.set_mode((0, 0))
        else:
            self.window = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        self.screen = pygame.Surface((WIDTH, HEIGHT))
        self._win_size = (WIDTH, HEIGHT)
        self._draw_offset = (0, 0)
        self._draw_scale = 1.0
        self._grad_cache = {}     # cached gradient/gloss surfaces (iOS 6 skin)
        # Low-Q per-element refresh dividers: secondary readouts (exhaust scope
        # waveform, ignition lamps, fuel/economy text) don't need the full render
        # rate — their rendered Surface is cached and rebuilt only every N draws,
        # then re-blitted each draw so they stay visible.  Normal mode draws every
        # element live.  See _slow_surf / _draw_n.
        self._draw_n = 0
        self._elem_cache = {}
        # FOCUS OSCILLOSCOPE (original engine-sim layout): one big scope window +
        # a row of clickable channel tiles below it (waveform / exhaust flow /
        # valve lift / cylinder pressure) — click a tile to focus that channel.
        self._scope_chan = "flow"
        self._scope_tile_rects = {}
        # Static cylinder-SLEEVE cache (lossless render optimisation): the metal
        # barrel/head/fins never move, so each cylinder's shell is rendered ONCE to
        # a small opaque sprite and re-blitted every frame; only the live
        # valvetrain/piston/crank are redrawn.  Sprites are blitted per-cylinder, in
        # the same order as the inline draw, so cross-cylinder OVERLAP (where one
        # bank's shell covers a neighbour's parts) stays byte-for-byte identical.
        # Invalidated on engine swap (load_engine) and Low-Q toggle — the only
        # things that change sleeve geometry/appearance.
        self._sleeve_sprites = None     # list[(Surface, (x, y)) | None] per cylinder
        self._sleeve_scratch = None     # reusable full-canvas SRCALPHA render target
        self._bay_sig = None
        self._bay_dirty = False
        self._cyl_idx = 0               # per-frame cylinder counter (sprite index)
        self._tele_smooth = {}    # eased telemetry values (calm gauge needles)
        self._cyl_flow_hist = []  # per-cylinder exhaust-flow scope ring buffers
        self._fuel_total_l = 0.0  # integrated fuel burned (L)
        self._odo_km = 0.0        # total distance travelled (km)
        self._oil_total_l = 0.0   # integrated oil consumed (L)
        self._fuel_lph = 0.0      # smoothed instantaneous fuel rate (L/h)
        self._trip_reset_rect = None   # clickable RESET button for the trip stats
        self._touch_toggle_rect = None # big top-right Touch on/off toggle
        self._ign_flash = {}      # per-cylinder ignition-light fade
        self._wheel_ang = 0.0     # spinning road-wheel angle (rad)
        # --- touch controls (on-screen pedals/paddles for phones & tablets) ---
        self.touch_mode = ON_ANDROID   # finger-control overlay on by default on phones
        self._ptr = {}            # active pointer id -> control name
        self._ptr_val = {}        # pointer id -> analog value (pedals)
        self._touch_throttle = 0.0
        self._touch_brake = 0.0
        self._touch_starter = False
        self.clock = pygame.time.Clock()
        self.lang = "en"          # "en" | "zh"
        self._bundled_fonts = self._find_bundled_fonts()
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
        self.low_quality = ON_ANDROID  # phones default to the lighter render
        self.traction_on = False       # tyre-slip dynamics: OPT-IN (Slip toggle)
        self.forza_ultra = False  # draw NOTHING (just audio) — max perf for Forza play
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
        self.speed_mph = False    # show speed in mph (else km/h)
        self.scope_open = False   # exhaust-path per-stage waveform overlay (press E)
        self.scope_mode = "flow"  # "flow" (exhaust gas) | "audio" (listener chain)
        self._scope_toggle_rect = None
        self._drag = None         # slider currently being dragged
        self._menu_drag = None    # touch tap/scroll state for the open dropdown
        self._buttons = []        # toolbar buttons (rebuilt each frame)
        self._open_menu = None    # active dropdown {items, rect, item_rects}
        self._build_mixer()
        self.running = True

        # --- Android-specific performance optimizations ---
        if IS_ANDROID:
            # Force low-quality mode: disable gradients, anti-aliasing, fine shading
            self.low_quality = True
            # Disable waveform/scope display (high CPU cost)
            self.scope_open = False
            # Disable ignition indicator lights (if exists)
            if hasattr(self, "_ign_flash"):
                self._ign_flash = {}
            # Disable turbo/belt rotation animations (if exists)
            if hasattr(self, "_wheel_ang"):
                self._wheel_ang = 0.0
            # Disable combustion flash effects (if exists)
            if hasattr(self, "_flash"):
                # Keep the method but it will check low_quality flag
                pass

    # ------------------------------------------------------------- toolbar
    def _toolbar_defs(self):
        """Button definitions: (label, callback, active_fn_or_None, row)."""
        dt = self.sim.drivetrain
        dev = self.devices[self.device_idx][0]
        rate = SAMPLE_RATES[self.rate_idx]
        sy = self.synth
        T = self.tr
        arr = "▼" if self.lang == "zh" else "▾"   # YaHei lacks U+25BE
        # Forza / Forza Ultra button colour reflects the UDP link: GREEN once Forza
        # packets are arriving, RED while connected-but-no-data, default when off.
        if self.telemetry_mode and self.telemetry is not None:
            tele_col = (((46, 168, 80), (22, 116, 52)) if self.telemetry.is_live()
                        else ((200, 66, 54), (150, 40, 34)))
        else:
            tele_col = None
        ultra_col = tele_col or ((110, 60, 40), (210, 110, 50))
        if self.forza_ultra:                      # display-off mode: just these
            return [
                (f"{T('Demo cars')} {arr}", self._menu_demo, None, 0),
                (T("Mixer / EQ"),
                 lambda: setattr(self, "mixer_open", not self.mixer_open),
                 lambda: self.mixer_open, 0),
                (T("Forza Ultra"),
                 lambda: setattr(self, "forza_ultra", False), lambda: True, 0,
                 ultra_col),
            ]
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
            # HKS SSQV atmospheric dump valve — loud sharp 'TSSSH' on lift-off
            (T("SSQV"), lambda: setattr(sy, "ssqv", not sy.ssqv),
             lambda: sy.ssqv, 1),
            # gas_truth solver exhaust-signature colour (off = pure tuned synth)
            (T("Solver"), lambda: setattr(sy, "solver_tone", not sy.solver_tone),
             lambda: sy.solver_tone, 1),
            (T("Hybrid"), lambda: setattr(self.sim, "hybrid_on", not self.sim.hybrid_on),
             lambda: self.sim.hybrid_on and self.sim.engine.hybrid_kw > 0, 1),
            (T("Pops"), lambda: setattr(sy, "pops_on", not sy.pops_on),
             lambda: sy.pops_on, 1),
            # tyre-slip dynamics (friction circle + wheelspin): OPT-IN — the grip
            # cap changes launch feel a lot, so it stays off unless asked for
            (T("Slip"), lambda: setattr(self, "traction_on", not self.traction_on),
             lambda: self.traction_on, 1),
            ("mph" if self.speed_mph else "km/h",
             lambda: setattr(self, "speed_mph", not self.speed_mph),
             lambda: self.speed_mph, 1),
            ("Language", self.toggle_lang, None, 1, ((255, 120, 180), (224, 78, 146))),
            # row 2 — output / device / view
            (f"{T('Out:')} {dev} {arr}", self._menu_device, None, 2),
            (f"{rate // 1000}.{(rate % 1000)//100}kHz", self.toggle_rate, None, 2),
            ("Forza", self.toggle_telemetry, lambda: self.telemetry_mode, 2, tele_col),
            # Forza Ultra sits right next to Forza
            (T("Forza Ultra"), self._enter_forza_ultra, lambda: self.forza_ultra, 2,
             ultra_col),
            (f"{T('Slow')} {int(round(1/self.slow_mo))}x" if self.slow_mo < 1
             else T("Slow-mo"), self.toggle_slow, lambda: self.slow_mo < 1.0, 2),
            # Touch moved out to the big top-right toggle (_draw_touch_toggle)
            (T("Low Q"), lambda: setattr(self, "low_quality", not self.low_quality),
             lambda: self.low_quality, 2),
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
        for entry in defs:
            label, cb, active, row = entry[0], entry[1], entry[2], entry[3]
            color = entry[4] if len(entry) > 4 else None
            rows.setdefault(row, []).append((label, cb, active, color))
        self._buttons = []
        y = panel.y + 12
        for ri in sorted(rows):
            x = panel.x + 26                  # clear the top-left corner screw
            for label, cb, active, color in rows[ri]:
                w = self.font_small.size(label)[0] + 20
                self._buttons.append({"label": label, "cb": cb, "active": active,
                                      "rect": pygame.Rect(x, y, w, 26), "color": color})
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
            # restore the low-Q state from before Forza was entered: it was forced
            # on for Forza, so only keep it on if the user had it on beforehand
            self.low_quality = getattr(self, "_low_q_pre_forza", False)
            self.sim.throttle = 0.0
            self._flash("Telemetry mode OFF")
            return
        self.telemetry = ForzaTelemetry(FORZA_PORT)
        if not self.telemetry.start():
            self._flash(f"Telemetry: cannot open UDP :{FORZA_PORT} "
                        f"({self.telemetry.error})")
            self.telemetry = None
            return
        self._low_q_pre_forza = self.low_quality   # remember to restore on exit
        self.telemetry_mode = True
        self.low_quality = True            # Forza defaults to the low-quality render
        self.sim.ignition_on = True
        self._flash(f"Telemetry ON · Forza Data Out -> this PC :{FORZA_PORT}")

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
        self._sleeve_sprites = None           # new layout -> rebuild the sleeve cache
        self._elem_cache.clear()              # drop slow-refresh surfaces of the old car
        self._reset_trip()                    # fresh odometer / fuel / oil per car

    def _reset_trip(self):
        """Zero the trip readouts (odometer, fuel & oil used) — the RESET button,
        and run automatically whenever the engine is swapped."""
        self._fuel_total_l = 0.0
        self._odo_km = 0.0
        self._oil_total_l = 0.0
        self._fuel_lph = 0.0

    # ----------------------------------------------------------------- input
    @staticmethod
    def _find_bundled_fonts():
        """Any .ttf/.otf shipped in engine_sim/assets/fonts (so a BankGothic-style
        face can be EMBEDDED and used even when the user hasn't installed it —
        PyInstaller bundles the folder)."""
        d = os.path.join(os.path.dirname(__file__), "assets", "fonts")
        out = []
        try:
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith((".ttf", ".otf")):
                    out.append(os.path.join(d, fn))
        except Exception:
            pass
        return out

    _CJK_HINTS = ("noto", "sourcehan", "cjk", "pingfang", "yahei",
                  "simhei", "simsun", "-sc", " zh")

    def _bundled_by_kind(self, cjk):
        """Bundled font paths matching (or NOT matching) CJK name hints."""
        out = []
        for p in getattr(self, "_bundled_fonts", []):
            low = os.path.basename(p).lower()
            is_cjk = any(h in low for h in self._CJK_HINTS)
            if is_cjk == cjk:
                out.append(p)
        return out

    def _eng_font(self, size, bold=False):
        """English face: prefer a bundled BankGothic-style .ttf, then a matching
        installed industrial/gothic face, finally Consolas."""
        for path in self._bundled_by_kind(cjk=False):
            try:
                f = pygame.font.Font(path, size); f.set_bold(bold); return f
            except Exception:
                pass
        name = pygame.font.match_font(
            "bankgothicmdbt,bankgothic,bahnschrift,eurostile,microgramma,oswald")
        if name:
            try:
                f = pygame.font.Font(name, size); f.set_bold(bold); return f
            except Exception:
                pass
        return pygame.font.SysFont("consolas", size, bold=bold)

    def _cjk_font(self, size, bold=False):
        """Chinese face: prefer a bundled CJK .otf (Noto Sans SC — works without
        being installed), then PingFang / YaHei, then any CJK system face."""
        for path in self._bundled_by_kind(cjk=True):
            try:
                f = pygame.font.Font(path, size); f.set_bold(bold); return f
            except Exception:
                pass
        name = pygame.font.match_font("pingfangsc,pingfang,microsoftyaheui,"
                                      "microsoftyahei,simhei,simsun")
        if name:
            try:
                f = pygame.font.Font(name, size); f.set_bold(bold); return f
            except Exception:
                pass
        return pygame.font.SysFont("microsoftyahei,simhei,simsun", size, bold=bold)

    def _init_fonts(self):
        """(Re)build fonts for the current language — a bundled Noto Sans SC for
        Chinese, a bundled BankGothic-style face for English (both embedded)."""
        if self.lang == "zh":
            self.font = self._cjk_font(17)
            self.font_big = self._cjk_font(38, bold=True)
            self.font_small = self._cjk_font(14)
            self.font_hint = self._cjk_font(9)    # shrunk so 【键】释义 never overlaps
        else:
            self.font = self._eng_font(18)
            self.font_big = self._eng_font(42, bold=True)
            self.font_small = self._eng_font(14)
            self.font_hint = self._eng_font(13)

    def _speed_disp(self, kmh):
        """(value, unit-label) for the current speed unit (km/h or mph)."""
        if self.speed_mph:
            return kmh * 0.621371, "mph"
        return kmh, "km/h"

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
            "odoreset": pygame.Rect(WIDTH - 182, 194, 90, 40),  # zero the trip stats
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
                elif name == "odoreset":
                    self._reset_trip()
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
        if self.scope_open:                  # modal overlay: any click dismisses it
            self.scope_open = False
            return
        # the trip RESET button lives in the always-visible right panel, so check
        # it before the left-panel overlays (menu / mixer) get a shot
        rr = self._trip_reset_rect
        if rr is not None and rr.collidepoint(mpos):
            self._reset_trip()
            return
        # focus-oscilloscope channel tiles (right panel, below the scope window)
        if not self.mixer_open and self._open_menu is None:
            for chan, tr in self._scope_tile_rects.items():
                if tr.collidepoint(mpos):
                    self._scope_chan = chan
                    self._elem_cache.pop("scope", None)   # refresh the Low-Q cache
                    return
        tt = self._touch_toggle_rect
        if (tt is not None and not self.mixer_open and self._open_menu is None
                and tt.collidepoint(mpos)):
            self.touch_mode = not self.touch_mode
            return
        if self._open_menu is not None:
            # Defer the choice to release so a touchscreen can DRAG to scroll the
            # list (a tap still selects).  Mouse-drag scrolls too, as a bonus.
            m = self._open_menu
            self._menu_drag = {"y0": mpos[1], "scroll0": m["scroll"],
                               "moved": False}
            self._drag = "menu"
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
        if self._drag == "menu":
            m, md = self._open_menu, self._menu_drag
            if m is None or md is None:
                return
            if abs(mpos[1] - md["y0"]) > 6:
                md["moved"] = True
            rows = (md["y0"] - mpos[1]) / float(m["ih"])   # finger up -> reveal lower
            m["scroll"] = max(0, min(self._menu_max_scroll(m),
                                     int(round(md["scroll0"] + rows))))
        elif self._drag == "pad":
            self._set_pad(mpos)
        elif self._drag == "firepad":
            self._set_fire_pad(mpos)
        elif self._drag is not None:
            self._set_slider(self._drag, mpos[0])

    def _handle_menu_release(self, pos):
        """Finish a tap/drag on the open dropdown: a tap (no real movement) picks
        the item under the finger and closes the menu; a tap on empty space just
        dismisses it; a scroll-drag leaves the menu open."""
        m, md = self._open_menu, self._menu_drag
        self._menu_drag = None
        if m is None or md is None or md["moved"]:
            return
        self._open_menu = None                     # a tap always dismisses
        if pos is not None:
            for i, (lbl, cb) in enumerate(m["items"]):
                r = self._menu_item_rect(m, i)
                if r is not None and r.collidepoint(pos):
                    cb()
                    break

    def _draw_touch_overlay(self):
        """Porsche-dash finger controls: carbon-fibre plastic buttons with backlit
        labels, and brushed-aluminium sport pedals with metal trim + travel fill."""
        if not self.touch_mode:
            return
        R = self._touch_rects()
        sc = self.screen

        def carbon(r, radius=12):                          # carbon plastic body
            sc.blit(self._carbon(r.w, r.h, radius), r.topleft)
            sh = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
            pygame.draw.rect(sh, (255, 255, 255, 28), (2, 2, r.w - 4, int(r.h * 0.42)),
                             border_radius=radius - 2)
            sc.blit(sh, r.topleft)
            pygame.draw.rect(sc, (6, 7, 10), r, 1, border_radius=radius)
            pygame.draw.rect(sc, (78, 84, 98), r.inflate(-2, -2), 1, border_radius=radius - 1)

        def btn(name, label, on=False, accent=(96, 176, 255)):
            r = R[name]
            carbon(r, 11)
            col = accent if on else (150, 156, 172)
            t = self.font_small.render(self.tr(label), True, col)
            sc.blit(t, (r.centerx - t.get_width() // 2, r.centery - t.get_height() // 2))
            if on:                                         # backlit indicator bar
                pygame.draw.rect(sc, accent, (r.x + 12, r.bottom - 7, r.w - 24, 3),
                                 border_radius=2)

        def paddle(name, up):
            r = R[name]
            carbon(r, 11)
            cxp, cyp = r.centerx, r.centery
            tri = ([(cxp, cyp - 10), (cxp - 10, cyp + 7), (cxp + 10, cyp + 7)] if up
                   else [(cxp, cyp + 10), (cxp - 10, cyp - 7), (cxp + 10, cyp - 7)])
            pygame.draw.polygon(sc, (214, 220, 232), tri)   # chrome triangle
            pygame.draw.polygon(sc, (110, 116, 132), tri, 1)

        def metal_pedal(name, frac, label, glow):
            r = R[name]
            sc.blit(self._grad_surf(r.w, r.h, (176, 182, 196), (74, 80, 94), 10), r.topleft)
            # strong vertical brushed grain (fine light/dark streaks down the plate)
            grain = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
            for gx in range(4, r.w - 4, 2):
                v = (gx * 73 + 17) % 11
                col = (236, 240, 248, 26) if v < 5 else (24, 26, 32, 34)
                pygame.draw.line(grain, col, (gx, 4), (gx, r.h - 4), 1)
            pygame.draw.rect(grain, (0, 0, 0, 0), (0, 0, r.w, r.h), border_radius=10)
            sc.blit(grain, r.topleft)
            # glossy diagonal sheen across the upper-left
            gl = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
            pygame.draw.polygon(gl, (255, 255, 255, 46),
                                [(0, 0), (int(r.w * 0.62), 0), (int(r.w * 0.26), r.h), (0, r.h)])
            sc.blit(gl, r.topleft)
            for ry in range(r.y + 24, r.bottom - 30, 26):     # drilled sport holes
                for rx in (r.centerx - 13, r.centerx + 13):
                    pygame.draw.circle(sc, (44, 48, 58), (rx, ry), 5)
                    pygame.draw.circle(sc, (188, 194, 206), (rx, ry), 5, 1)
            fh = int((r.height - 14) * min(max(frac, 0.0), 1.0))   # travel fill
            if fh > 3:
                fr = pygame.Rect(r.x + 6, r.bottom - 7 - fh, r.width - 12, fh)
                sh = pygame.Surface((fr.w, fr.h), pygame.SRCALPHA)
                pygame.draw.rect(sh, (glow[0], glow[1], glow[2], 165),
                                 (0, 0, fr.w, fr.h), border_radius=7)
                sc.blit(sh, fr.topleft)
            pygame.draw.rect(sc, (216, 222, 234), r, 2, border_radius=10)  # chrome trim
            pygame.draw.rect(sc, (40, 44, 54), r.inflate(-4, -4), 1, border_radius=8)
            t = self.font.render(label, True, (26, 28, 34))
            sc.blit(t, (r.centerx - t.get_width() // 2, r.bottom - 30))

        metal_pedal("brake", self.sim.drivetrain.brake, "BRK", (232, 84, 72))
        metal_pedal("gas", self.sim.throttle, "GAS", (92, 212, 124))
        paddle("up", True)
        paddle("down", False)
        btn("start", "START", self.sim.starter_engaged, (255, 184, 84))
        btn("ign", "IGN", self.sim.ignition_on, (96, 204, 255))
        btn("auto", "AUTO", self.sim.drivetrain.auto, (120, 224, 144))
        btn("odoreset", "Odo Reset", accent=(120, 200, 255))
        btn("close", "Touch OFF", accent=(240, 112, 100))

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
                if self._drag == "menu":
                    self._handle_menu_release(self._map_finger(e))
                self._drag = None
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if getattr(e, "touch", False):
                    continue                      # touch-emulated mouse -> use FINGER
                mpos = self._map_mouse(e.pos)
                if not (self.touch_mode and self._pointer_down("mouse", mpos)):
                    self._handle_press(mpos)
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                if self._drag == "menu":
                    self._handle_menu_release(self._map_mouse(e.pos))
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
        # Low-Q (phones) caps the high-rpm physics sub-steps lower: ~3x cheaper
        # integration where it matters (the redline), with a negligible rpm-
        # trajectory shift (the flywheel smooths it; audio is independent).  Normal
        # mode keeps the fine 80 for full fidelity.
        self.sim.substep_cap = 24 if self.low_quality else 80
        # tyre-slip dynamics follow the toggle (re-pushed every frame so it
        # survives engine swaps, which rebuild the drivetrain)
        self.sim.drivetrain.traction_model = self.traction_on
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

    def _draw_engine_off_prompt(self):
        """Engine not running: a red prompt in the (otherwise blank) top-centre of
        the engine bay telling the player to switch on the ignition and crank it."""
        bay = getattr(self, "_bay_rect", None)
        if bay is None or self.sim.rpm >= 200.0:
            return
        bay = bay.inflate(-40, -40)                 # back to the real bay rect
        zh = self.lang == "zh"
        lbk, rbk = ("【", "】") if zh else ("[", "]")
        s1 = self.font.render(self.tr("Engine off — start it:"), True, (255, 90, 80))
        s2 = self.font.render(
            f"{lbk}A{rbk}{self.tr('Ignition')}     {lbk}S{rbk}{self.tr('Start')}",
            True, (255, 210, 70))
        pw = max(s1.get_width(), s2.get_width()) + 28
        ph = s1.get_height() + s2.get_height() + 18
        bx = bay.centerx - pw // 2
        by = bay.y + 18
        panel = pygame.Surface((pw, ph), pygame.SRCALPHA)
        panel.fill((10, 10, 12, 185))
        pygame.draw.rect(panel, (255, 90, 80), panel.get_rect(), 2, border_radius=8)
        self.screen.blit(panel, (bx, by))
        self.screen.blit(s1, (bay.centerx - s1.get_width() // 2, by + 7))
        self.screen.blit(s2, (bay.centerx - s2.get_width() // 2,
                              by + 9 + s1.get_height()))

    # ----------------------------------------------------------- draw: parts
    def _enter_forza_ultra(self):
        """Display-off mode for playing Forza: connect the UDP telemetry feed (so
        the engine follows the game) and stop drawing everything but two buttons."""
        if not self.telemetry_mode:
            self.toggle_telemetry()            # hook up the Forza UDP listener
        self.forza_ultra = True

    def draw(self):
        self._draw_n += 1
        self._update_hud_signals()
        if self.forza_ultra:
            # DISPLAY OFF: plain background + just the (minimal) toolbar + any menu.
            self._touch_toggle_rect = None        # owning panels aren't drawn here
            self._trip_reset_rect = None
            self.screen.fill((12, 13, 16))
            if self.mixer_open:                   # the EQ window IS allowed up here
                self._draw_mixer(pygame.Rect(24, 24, 620, 632))
            else:
                udp = (self.telemetry_mode and self.telemetry is not None)
                msg = self.tr("FORZA ULTRA — display off (audio only)")
                if not udp:
                    msg += "   [UDP: --]"
                m = self.font.render(msg, True, (96, 102, 116))
                self.screen.blit(m, (WIDTH // 2 - m.get_width() // 2, HEIGHT // 2 - 10))
            self._draw_toolbar()
            if self._open_menu is not None:
                self._draw_menu()
            self._present()
            return
        self.screen.blit(self._grad_surf(WIDTH, HEIGHT, BG_TOP, BG_BOT, 0), (0, 0))
        self.screen.blit(self._brushed(WIDTH, HEIGHT, 0), (0, 0))   # brushed backplate
        for sx, sy in ((13, 13), (WIDTH - 13, 13), (13, HEIGHT - 13),
                       (WIDTH - 13, HEIGHT - 13)):                  # backplate screws
            self._screw(sx, sy, 6)
        left = pygame.Rect(24, 24, 620, 632)
        if self.mixer_open:
            self._touch_toggle_rect = None   # toggle is hidden behind the mixer
            self._draw_mixer(left)
        else:
            self._draw_engine_panel(left)
            self._draw_engine_off_prompt()
        self._draw_gauges(pygame.Rect(664, 24, 412, 632))
        # the exhaust-path stage scopes only sample audio while the overlay is up;
        # in low-quality mode they're suppressed entirely (only the TOTAL EXHAUST
        # FLOW scope stays) to keep the frame cheap for the audio thread.
        stage_scopes = self.scope_open and not self.low_quality
        if self.synth is not None:
            self.synth.scope_enabled = stage_scopes
        if stage_scopes:
            self._draw_exhaust_scopes(pygame.Rect(24, 24, 1052, 632))
        if self._open_menu is not None:
            self._draw_menu()
        self._draw_touch_overlay()
        self._present()

    def _present(self):
        # Fit the native 1100x680 canvas into the window, PRESERVING ASPECT RATIO
        # (letter-boxed) — so it fills a phone screen or a maximised desktop window
        # cleanly instead of sitting tiny in the corner.  scale==1 => crisp 1:1.
        if self._scaled:                      # SDL2 SCALED: the GPU scales for us
            self._draw_scale = 1.0
            self._draw_offset = (0, 0)
            self.window.blit(self.screen, (0, 0))
            pygame.display.flip()
            return
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

    def _brushed(self, w, h, radius):
        """A cached translucent BRUSHED-METAL overlay: fine horizontal grain (long
        light/dark streaks) for a real machined-aluminium feel over the slab."""
        w, h = int(w), int(h)
        key = ('brush', w, h, radius)
        s = self._grad_cache.get(key)
        if s is None:
            rng = np.random.default_rng(7)
            streak = np.cumsum(rng.standard_normal((h, w)).astype(np.float32), axis=1)
            streak -= streak.mean(axis=1, keepdims=True)
            streak = np.clip(streak * 0.10, -16.0, 16.0)
            rgb = np.repeat(np.where(streak[:, :, None] >= 0, 255, 0).astype(np.uint8),
                            3, axis=2)
            alpha = np.clip(np.abs(streak) * 2.2, 0, 24).astype(np.uint8)[:, :, None]
            arr = np.ascontiguousarray(np.dstack([rgb, alpha]))
            s = pygame.image.frombuffer(arr.tobytes(), (w, h), "RGBA").convert_alpha()
            if radius > 0:
                mask = pygame.Surface((w, h), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                                 border_radius=radius)
                s.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self._grad_cache[key] = s
        return s

    def _carbon(self, w, h, radius):
        """A cached carbon-fibre weave texture (dark woven twill) for premium
        plastic dash buttons."""
        w, h = int(w), int(h)
        key = ('carbon', w, h, radius)
        s = self._grad_cache.get(key)
        if s is None:
            cell = 5
            yy, xx = np.mgrid[0:h, 0:w]
            cxy = ((xx // cell) % 2 + (yy // cell) % 2) % 2
            fx = (xx % cell) / cell
            fy = (yy % cell) / cell
            diag = np.where(cxy == 0, fx + fy, (1.0 - fx) + fy)        # 0..2 twill
            lum = 16.0 + 16.0 * np.clip(diag / 2.0, 0.0, 1.0)
            rgb = np.stack([lum * 0.92, lum, lum * 1.12], axis=-1)
            # a soft top-down sheen
            rgb *= (1.0 + 0.5 * (1.0 - yy / max(h - 1, 1)))[:, :, None]
            arr = np.dstack([np.clip(rgb, 0, 255),
                             np.full((h, w), 255, np.float32)]).astype(np.uint8)
            arr = np.ascontiguousarray(arr)
            s = pygame.image.frombuffer(arr.tobytes(), (w, h), "RGBA").convert_alpha()
            if radius > 0:
                mask = pygame.Surface((w, h), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                                 border_radius=radius)
                s.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self._grad_cache[key] = s
        return s

    def _screw(self, cx, cy, r=5):
        """A skeuomorphic countersunk screw head (iOS 6): recess shadow, brushed
        head lit from the top-left, and a slotted notch with shadow + highlight."""
        cx, cy = int(cx), int(cy)
        pygame.draw.circle(self.screen, (18, 20, 24), (cx, cy + 1), r + 1)   # recess
        pygame.draw.circle(self.screen, (96, 102, 114), (cx, cy), r)        # head
        pygame.draw.circle(self.screen, (150, 158, 172), (cx - 1, cy - 1), max(1, r - 2))
        pygame.draw.circle(self.screen, (58, 62, 72), (cx, cy), r, 1)
        a = 0.7
        dx, dy = math.cos(a) * (r - 1), math.sin(a) * (r - 1)
        pygame.draw.line(self.screen, (34, 37, 44),
                         (cx - dx, cy - dy + 1), (cx + dx, cy + dy + 1), 2)
        pygame.draw.line(self.screen, (188, 194, 206),
                         (cx - dx, cy - dy), (cx + dx, cy + dy), 1)

    def _recess(self, rect, radius=4, fill=(13, 14, 17)):
        """A SUNKEN inset window — dark face with a top/left inner shadow and a
        bottom/right catch-light, so it reads as machined INTO the metal slab
        (the look of an embedded LCD / instrument cut-out)."""
        pygame.draw.rect(self.screen, fill, rect, border_radius=radius)
        sh = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        pygame.draw.line(sh, (0, 0, 0, 160), (radius, 1), (rect.w - radius, 1), 2)
        pygame.draw.line(sh, (0, 0, 0, 130), (1, radius), (1, rect.h - radius), 2)
        pygame.draw.line(sh, (122, 130, 146, 95),
                         (radius, rect.h - 1), (rect.w - radius, rect.h - 1))
        pygame.draw.line(sh, (122, 130, 146, 95),
                         (rect.w - 1, radius), (rect.w - 1, rect.h - radius))
        self.screen.blit(sh, rect.topleft)
        pygame.draw.rect(self.screen, (6, 7, 9), rect, width=1, border_radius=radius)

    def _panel(self, rect, radius=14, screws=True):
        """A glossy beveled brushed-metal panel slab with iOS-6 corner screws."""
        self.screen.blit(self._grad_surf(rect.w, rect.h, PANEL_TOP, PANEL_BOT,
                                          radius), rect.topleft)
        self.screen.blit(self._brushed(rect.w, rect.h, radius), rect.topleft)
        pygame.draw.rect(self.screen, BEVEL_LO, rect, width=1, border_radius=radius)
        pygame.draw.line(self.screen, BEVEL_HI, (rect.x + radius, rect.y + 1),
                         (rect.right - radius, rect.y + 1))
        if screws:
            d = radius
            for sx, sy in ((rect.x + d, rect.y + d), (rect.right - d, rect.y + d),
                           (rect.x + d, rect.bottom - d), (rect.right - d, rect.bottom - d)):
                self._screw(sx, sy)

    def _draw_button(self, b, mouse):
        r = b["rect"]
        active = b["active"]() if b["active"] else False
        hot = r.collidepoint(mouse)
        accent = b.get("color")
        if accent:                              # fixed-colour accent button (e.g. pink)
            c1, c2, txt = accent[0], accent[1], (255, 255, 255)
        elif active:
            c1, c2, txt = BTN_ON_HI, BTN_ON_LO, (255, 255, 255)
        elif hot:
            c1, c2, txt = BTN_HOT_HI, BTN_HOT_LO, INK
        else:
            c1, c2, txt = BTN_HI, BTN_LO, INK
        self.screen.blit(self._grad_surf(r.w, r.h, c1, c2, 6, gloss=True), r.topleft)
        pygame.draw.rect(self.screen, BEVEL_LO, r, width=1, border_radius=6)
        # a dropdown caret is DRAWN (the ▾ glyph is absent from the gothic font)
        text = b["label"]
        caret = text.endswith("▾") or text.endswith("▼")
        if caret:
            text = text[:-1].rstrip()
        lbl = self.font_small.render(text, True, txt)
        lw = lbl.get_width()
        block = lw + (12 if caret else 0)
        lx = r.centerx - block // 2
        self.screen.blit(lbl, (lx, r.centery - lbl.get_height() // 2))
        if caret:
            tx, ty = lx + lw + 6, r.centery
            pygame.draw.polygon(self.screen, txt,
                                [(tx - 4, ty - 2), (tx + 4, ty - 2), (tx, ty + 3)])

    def _draw_toolbar(self):
        mouse = self.canvas_mouse()
        for b in self._buttons:
            self._draw_button(b, mouse)

    def _ios_button(self, r, c1, c2, radius=6):
        """Draw the project's iOS-glass button face (glossy gradient + bevel) so
        ad-hoc buttons match the toolbar."""
        self.screen.blit(self._grad_surf(r.w, r.h, c1, c2, radius, gloss=True),
                         r.topleft)
        pygame.draw.rect(self.screen, BEVEL_LO, r, width=1, border_radius=radius)

    def _draw_touch_toggle(self, rect):
        """The big Touch on/off toggle in the empty top-right of the engine panel,
        in the same iOS-glass style as the toolbar — GREEN glass when on, ORANGE
        glass when off."""
        on = self.touch_mode
        # only span the TOP TWO toolbar rows, so the (wide) bottom row — Forza /
        # Low Q / Scope — sits clear BELOW the toggle instead of under it
        h = (self._toolbar_bottom - 32) - (rect.y + 14) - 4
        r = pygame.Rect(rect.right - 102, rect.y + 14, 86, max(46, h))
        self._touch_toggle_rect = r
        c1, c2 = ((104, 200, 126), (30, 138, 66)) if on \
            else ((244, 186, 86), (196, 120, 26))
        self._ios_button(r, c1, c2, radius=8)
        t1 = self.font.render(self.tr("Touch"), True, (255, 255, 255))
        self.screen.blit(t1, (r.centerx - t1.get_width() // 2, r.y + 8))
        s2 = self.font.render(self.tr("ON") if on else self.tr("OFF"),
                              True, (255, 255, 255))
        self.screen.blit(s2, (r.centerx - s2.get_width() // 2,
                              r.bottom - s2.get_height() - 7))

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

    def _slider_active(self, key):
        """Whether a mixer slider has any audible effect on the CURRENT engine.
        Induction-/gearbox-specific channels are silent on a car that lacks the
        hardware (e.g. the turbo/supercharger/hybrid/straight-cut sliders on a
        plain NA car) — those are shown greyed-out + 'n/a' so they read as
        not-applicable, not broken."""
        eng = self.sim.engine
        ind = getattr(eng, "induction", "na")
        if key == "turbo_vol":
            return ind == "turbo" or getattr(eng, "electric_turbo", False)
        if key == "super_vol":
            return ind in ("roots", "centrifugal")
        if key == "spool_reverb":
            return ind != "na" or getattr(eng, "electric_turbo", False)
        if key in ("gearbox_vol", "gearbox_reverb"):
            return getattr(eng, "straight_cut", False)
        if key == "hybrid_vol":
            return (getattr(eng, "hybrid_kw", 0.0) > 0.0
                    or getattr(eng, "electric_turbo", False))
        return True

    def _draw_mixer(self, rect):
        self._panel(rect, screws=False)
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
        DIM2 = (92, 98, 110)                  # greyed-out (not-applicable) colour
        for s in self._sliders:
            key, t = s["key"], s["track"]
            active = self._slider_active(key)
            val = P.get(key, 0.0)
            norm = (val - s["min"]) / (s["max"] - s["min"]) if s["max"] > s["min"] else 0
            norm = min(max(norm, 0.0), 1.0)
            # label + value (greyed + 'n/a' when the channel doesn't apply here)
            self.screen.blit(self.font_small.render(self.tr(s["label"]), True,
                             INK if active else DIM2), (rect.x + 22, s["row_y"] + 2))
            if active:
                vtxt = f"{val:5.0f}" if s["max"] > 20 else f"{val:5.2f}"
                self.screen.blit(self.font_small.render(vtxt, True, ACCENT),
                                 (t.right + 6, s["row_y"] + 2))
            else:
                self.screen.blit(self.font_small.render(self.tr("n/a"), True, DIM2),
                                 (t.right + 6, s["row_y"] + 2))
            # inset glossy track + blue-glass fill + chrome knob (iOS 6)
            pygame.draw.rect(self.screen, (24, 26, 31), t, border_radius=3)
            pygame.draw.line(self.screen, (12, 13, 16), (t.x + 2, t.y),
                             (t.right - 2, t.y))               # inset shadow
            fill = t.copy(); fill.width = max(0, int(t.width * norm))
            if fill.width > 2:
                if active:
                    self.screen.blit(self._grad_surf(fill.width, t.height, BTN_ON_HI,
                                                     BTN_ON_LO, 3, gloss=True), fill.topleft)
                else:
                    pygame.draw.rect(self.screen, (52, 56, 66), fill, border_radius=3)
            hx = t.x + int(t.width * norm)
            knob_hi, knob_lo = ((236, 240, 245), (150, 158, 170)) if active \
                else ((150, 154, 162), (96, 100, 110))
            self.screen.blit(self._grad_surf(16, 16, knob_hi, knob_lo, 8, gloss=True),
                             (hx - 8, t.centery - 8))
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
        self._draw_touch_toggle(rect)
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
        if eng.is_rotary:
            fire = ""
        elif self.lang == "zh":
            fire = f"  ·  每 {720.0 / n:.0f}° 点火"
        else:
            fire = f"  ·  fires every {720.0 / n:.0f}°"
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
            plane = " flat-plane" if alternates else " cross-plane"   # qualifier
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
            if getattr(eng, "hot_v", False):
                cfg += " hot-V"
        rot = "CCW" if getattr(eng, "rotation", "CW") == "CCW" else "CW"
        vv = getattr(eng, "variable_valve", "")
        vv_txt = f" · {vv}" if vv else ""
        # capitalise the leading letter of each descriptor for a consistent look
        # (acronyms like CW / DOHC keep their caps)
        def _cap(s):
            return s[:1].upper() + s[1:]
        spec = (f"{_cap(cfg)} · {rot} · {_cap(mat_lbl)} exh · {_cap(hdr)} · "
                f"{_cap(vt)}{vv_txt}")
        stxt = self.font_small.render(spec, True, (138, 146, 162))
        self.screen.blit(stxt, (rect.x + 18, ty + 44))
        voice = self.tr(FIRING_VOICES[self.voice_idx][0])
        cab = f"   ·   {self.tr('cabin')}" if self.synth.cabin else ""
        self.screen.blit(self.font_small.render(
            f"{self.tr('Firing voice:')} {voice}  (V){cab}",
            True, ACCENT), (rect.x + 18, ty + 64))
        # firing order — in the open area to the RIGHT of the firing-voice line
        self._blit_firing(eng, rect.x + 286, ty + 64, rect.right - 18 - (rect.x + 286))
        # control-key hints — tucked into the empty top-right of the engine panel,
        # right-aligned so they clear the (left-aligned) title / spec lines
        # Control-key hints in【key】function form, yellow, upshift/downshift on
        # the first line.  A dedicated smaller font (smaller still in Chinese) keeps
        # the three lines clear of the title / spec / firing-order text on the left,
        # and short of the firing-order row at ty+64.
        zh = self.lang == "zh"
        lb, rb = ("【", "】") if zh else ("[", "]")
        hint_rows = [
            [("X", "Upshift"), ("Z", "Downshift"), ("T", "Auto/Manual")],
            [("Up/Dn", "Gas"), ("Shift", "Clutch"), ("A", "Ign"), ("S", "Start")],
            [("C", "Mixer"), ("E", "Scope"), ("M", "Mute"), ("Esc", "Exit")],
        ]
        hf = self.font_hint
        lh = hf.get_height() + 3
        for li, row in enumerate(hint_rows):
            line = "  ".join(f"{lb}{k}{rb}{self.tr(fn)}" for k, fn in row)
            ht = hf.render(line, True, (255, 200, 60))
            self.screen.blit(ht, (rect.right - 14 - ht.get_width(), ty + li * lh))

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

        # --- ENGINE BAY: a recessed rounded-rect that frames the whole engine,
        # whatever its layout (inline / V / W / boxer / rotary / radial) ---------
        bay = pygame.Rect(rect.x + 14, ty + 198, rect.width - 28,
                          rect.bottom - 14 - (ty + 198))
        self._recess(bay, 12, fill=(19, 21, 26))
        # crankshaft centre + half-height — set by each layout, used to hang the
        # oil pan etc.; default to the bay centre for layouts that don't set it.
        self._crank_xy = (bay.centerx, (bay.y + bay.bottom) // 2)
        self._crank_h = 30.0
        self._bay_rect = bay.inflate(40, 40)             # alpha-layer composite region
        self._cur_vt = getattr(eng, "valvetrain", "dohc")   # for per-cylinder cams
        self._exh_exit = None                            # NA engine's exhaust outlet
        vv = getattr(eng, "variable_valve", "")
        # VTEC and all its cousins (MIVEC / VVTL-i / Valvematic / AVS / Valvetronic /
        # VarioCam Plus / Ti-VCT / CVVT / D-VVT ...) switch to the aggressive cam
        # profile high up — shown as the high-lift cam engaging.
        self._vtec_on = bool(vv) and sim.rpm > 0.74 * eng.redline_rpm
        self.screen.blit(self.font_small.render(self.tr("Engine Bay"), True,
                                                (84, 90, 104)), (bay.x + 12, bay.y + 6))
        cp = getattr(eng, "crank_plane", "")
        if cp in ("flat", "cross"):                   # end-on crankshaft phase diagram
            self._draw_crank_diagram(bay.x + 38, bay.y + 50, 17, cp, sim.crank_angle)
        vv = getattr(eng, "variable_valve", "")
        if vv:                                        # variable-valve status badge
            on = getattr(self, "_vtec_on", False)
            col = (90, 220, 120) if on else (150, 158, 174)
            bd = self.font_small.render(f"{vv}{'  ON' if on else ''}", True, col)
            bx0 = bay.right - bd.get_width() - 16
            pygame.draw.circle(self.screen, col, (bx0 - 7, bay.y + 13), 4)
            pygame.draw.circle(self.screen, (30, 33, 42), (bx0 - 7, bay.y + 13), 4, 1)
            self.screen.blit(bd, (bx0, bay.y + 7))
        # --- static cylinder-sleeve cache (lossless) ----------------------------
        # The metal barrels/heads don't move; cache each cylinder's shell as a
        # sprite so _draw_cyl only redraws its live valvetrain/piston/crank.
        # Rebuild when the engine or Low-Q state (the only things affecting sleeve
        # geometry/look) changes; on the rebuild frame _draw_cyl paints the shell to
        # both the sprite and the screen, so output is pixel-identical to drawing
        # the cylinder inline.  Sprites are blitted per-cylinder (in draw order) so
        # cross-cylinder overlap is preserved exactly.
        # Only worthwhile in HIGH-Q: Low-Q sleeves are already cheap flat quads, so
        # the per-cylinder sprite blit costs about as much as redrawing them — there
        # the direct path (sprites=None) is used instead.
        if self.low_quality:
            self._sleeve_sprites = None        # -> _draw_cyl draws shells directly
            self._bay_dirty = False
        else:
            sig = (self.current_key, WIDTH, HEIGHT)
            if self._sleeve_sprites is None or sig != self._bay_sig:
                self._sleeve_sprites = []
                self._bay_sig = sig
                self._bay_dirty = True
                if self._sleeve_scratch is None:
                    self._sleeve_scratch = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            else:
                self._bay_dirty = False
        self._cyl_idx = 0
        top = bay.y + 26
        bottom = bay.bottom - 24
        # Per-cylinder size scaled by DISPLACEMENT, with the Bugatti W16's
        # ~500 cc/cylinder as the reference 1.0 (linear scale = cube-root of the
        # volume ratio so an 8x-volume cylinder is ~2x bigger each way).
        per_cc = eng.total_displacement * 1.0e6 / max(n, 1)
        cyl_scale = max(0.55, min(1.30, (per_cc / 500.0) ** (1.0 / 3.0)))
        if eng.is_rotary:
            self._draw_rotary(bay, top, bottom)
            self._draw_bay_induction(bay, eng, sim)
            return
        if getattr(eng, "is_radial", False):
            self._draw_radial(bay, top, bottom)
            self._draw_bay_induction(bay, eng, sim)
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
            self._draw_w_banks(bay, sim, eng, left, right, top, bottom)
            self._draw_bay_induction(bay, eng, sim)
            return
        if left and right:
            cxx = bay.centerx
            # use the REAL bank angle from vertical (60-deg V -> +/-30 deg,
            # 90-deg V -> +/-45 deg, boxer -> nearly horizontal).
            bank = math.radians(min(maxang, 82.0))
            width = 18.0 * cyl_scale                  # HALF size
            length = 62.0 * cyl_scale
            reach = length * math.sin(bank) + width   # fan-out within the bay
            maxreach = bay.width * 0.5 - 16.0
            if reach > maxreach:
                f = maxreach / reach; width *= f; length *= f
            # vertical pitch: enough that stacked V-pairs don't overlap badly
            dy = max(width * 1.5, length * math.cos(bank) * 0.78)
            avail = bottom - top
            # the top cylinders point UP past the top journal by reach_up, so the
            # full visual block is taller than the journal span — fit & centre THAT
            reach_up = length * math.cos(bank)
            block_h = dy * (ns - 1) + reach_up + 18
            if block_h > avail:
                f = avail / block_h
                dy *= f; width *= f; length *= f; reach_up *= f
                block_h = dy * (ns - 1) + reach_up + 18
            mtop = (top + bottom) * 0.5 - block_h * 0.5 - dy * 0.5 + reach_up
            # vertical metallic crankshaft behind every journal (strip-shaded)
            cy0, cy1 = mtop + dy * 0.5 - 14, mtop + dy * (ns - 0.5) + 14
            self._crank_xy = (cxx, (cy0 + cy1) * 0.5); self._crank_h = (cy1 - cy0) * 0.5
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
                    # tilt each bank out by the REAL bank angle from vertical
                    a = side * bank
                    self._draw_cyl(cxx, jy, a, length, width, frac, theta, glow, phi)
                    lx = cxx + math.sin(a) * (length + 16)
                    ly = jy - math.cos(a) * (length + 16)
                    lab = self.font_small.render(f"{i + 1}", True, DIM)
                    self.screen.blit(lab, (int(lx) - lab.get_width() // 2, int(ly) - 6))
            # manifolds ON TOP of the banks so the red/green pipes are never hidden
            self._draw_v_timing(cxx, mtop, dy, (cy0 + cy1) * 0.5, sim, eng)
            self._begin_pipe_layers()
            self._draw_v_manifolds(eng, stations, cxx, mtop, dy, bank, length, width, bay)
            self._end_pipe_layers()
            self._draw_bay_induction(bay, eng, sim)
            return

        # size the cylinder by displacement, then space the stations TIGHTLY
        # around it (no huge gaps for small cylinders) and centre the row.
        width = 18.0 * cyl_scale                      # HALF size
        length = 62.0 * cyl_scale
        sw = max(width * 1.5, 16.0)
        if ns * sw > bay.width - 24:                  # shrink to fit the bay width
            f = (bay.width - 24) / (ns * sw); sw *= f; width *= f; length *= f
        crank_y = (top + bottom) * 0.5 + length * 0.42   # centre the row vertically
        if length > (crank_y - top) * 0.92:           # fit the height too
            f = (crank_y - top) * 0.92 / length; length *= f; width *= f
        x_start = bay.centerx - ns * sw * 0.5
        # metallic crankshaft running behind every journal (round strip-shaded)
        cx0 = x_start + sw * 0.5 - 16
        cx1 = x_start + sw * (ns - 0.5) + 16
        self._crank_xy = ((cx0 + cx1) * 0.5, crank_y); self._crank_h = max(width * 0.30, 9.0)
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
        # balance shaft — a slim CYAN-BLACK counter-rotating shaft below the crank,
        # spun the opposite way so it reads as a distinct part
        bsy = int(crank_y + chh + 7)
        ba = -sim.crank_angle
        pygame.draw.line(self.screen, (10, 26, 30), (cx0 + 8, bsy), (cx1 - 8, bsy), 5)
        pygame.draw.line(self.screen, (38, 120, 130), (cx0 + 8, bsy), (cx1 - 8, bsy), 3)
        pygame.draw.line(self.screen, (90, 200, 210), (cx0 + 8, bsy - 1), (cx1 - 8, bsy - 1), 1)
        for bi in range(ns):                              # eccentric balance weights
            bx = cx0 + 8 + (cx1 - cx0 - 16) * (bi + 0.5) / ns
            pygame.draw.circle(self.screen, (16, 60, 66),
                               (int(bx), int(bsy + math.sin(ba + bi) * 3)), 3)
        # --- colour-coded manifolds (per the I4 reference): a GREEN plenum LOG
        # across the top with runners down to the intake ports, and the exhaust
        # plumbed to the turbo — TWIN-SCROLL engines split into two scrolls (the
        # firing pairs) in red + orange feeding the divided housing ---
        self._begin_pipe_layers()
        hrad = max(2, int(width * 0.16))
        irad = max(3, int(width * 0.2))                 # thick intake PLENUM
        brad = max(2, irad - 2)                          # thin individual RUNNERS
        cyv = (bay.y + bay.bottom) // 2
        heads = [(x_start + sw * (s + 0.5), crank_y - length * 0.82) for s in range(ns)]
        turbo = (bay.right - 50, cyv)
        sub = getattr(eng, "induction_subtype", "")
        # INTAKE (green): a thick plenum log over the heads feeding FOUR separate
        # THIN runners — one per cylinder — so the even 4-way distribution reads
        # clearly; each runner bolts to its head through a short joint.
        port_dx = width * 0.42
        ports_i = [(jx - port_dx, hy) for jx, hy in heads]
        plen_y = min(h[1] for h in heads) - 26
        px0, px1 = ports_i[0][0] - 6, ports_i[-1][0] + 6
        self._draw_header_tube((px0, plen_y), (px1, plen_y),
                               ((px0 + px1) * 0.5, plen_y - 2), irad + 2,
                               cols=self._INT_COLS)                   # the plenum
        for ix, hy in ports_i:                                       # 4 thin runners
            self._draw_header_tube((ix, hy), (ix, plen_y + irad),
                                   (ix - 6, (plen_y + hy) * 0.5), brad,
                                   cols=self._INT_COLS, joint=True)
            pygame.draw.circle(self.screen, self._INT_COLS[1],
                               (int(ix), int(plen_y + 2)), brad)      # runner-to-plenum boss
        self._draw_header_tube((px0, plen_y), (bay.x + 34, bay.bottom - 58),
                               (bay.x + 18, plen_y + 30), irad + 1, cols=self._INT_COLS)
        self._collector_slug((px0, plen_y), irad + 1)
        # EXHAUST: a right-angle FORK — risers UP from the cylinder tops to a
        # horizontal spine, then a thick handle to the turbo. Twin-scroll splits
        # into two stacked spines (the firing pairs) in red + orange.
        ex_ports = [(jx + width * 0.3, hy) for jx, hy in heads]
        spine_y = min(h[1] for h in heads) - 14
        if sub == "twin_scroll" and ns >= 4:
            fo = eng.firing_order
            sA, sB = set(fo[0::2]), set(fo[1::2])
            pA = [p for s, p in enumerate(ex_ports) if (s + 1) in sA]
            pB = [p for s, p in enumerate(ex_ports) if (s + 1) in sB]
            self._draw_exhaust_fork(pA, spine_y - 9, (turbo[0] - 2, turbo[1] - 9),
                                    hrad, self._EXH_COLS, axis="h")
            self._draw_exhaust_fork(pB, spine_y + 5, (turbo[0] - 2, turbo[1] + 9),
                                    hrad, self._EXH2_COLS, axis="h")
        elif eng.induction == "na" and ns == 4:
            # 4-2-1 UNEQUAL-LENGTH header ('tubular extractor'): companion cylinders
            # pair first (1&4, 2&3), the two primaries merge to one secondary, then
            # one collector — staggered so the runs read as unequal-length.
            fo = eng.firing_order
            pA = [ex_ports[fo[0] - 1], ex_ports[fo[2] - 1]]
            pB = [ex_ports[fo[1] - 1], ex_ports[fo[3] - 1]]
            cA = (sum(p[0] for p in pA) / 2 + 8, spine_y - 10)
            cB = (sum(p[0] for p in pB) / 2 - 8, spine_y + 2)
            self._draw_exhaust_fork(pA, cA[1], cA, hrad, self._EXH_COLS, axis="h")
            self._draw_exhaust_fork(pB, cB[1], cB, hrad, self._EXH_COLS, axis="h")
            exit_pt = (bay.right - 60, cyv)
            self._draw_exhaust_fork([cA, cB], int(bay.centerx + 30),
                                    exit_pt, hrad + 1, self._EXH_COLS, axis="v")
            if eng.induction == "na":
                self._exh_exit = exit_pt
        else:
            self._draw_exhaust_fork(ex_ports, spine_y, (turbo[0], turbo[1]),
                                    hrad, self._EXH_COLS, axis="h")
            if eng.induction == "na":
                self._exh_exit = turbo
        if eng.cylinders[0].compression_ratio >= 14.5:    # diesel EGR cooler loop
            scr = self.screen
            egr_y = int(plen_y - 16)
            tap_x = int(turbo[0] - 44)
            cool = pygame.Rect(int(bay.centerx) - 16, egr_y - 5, 32, 11)
            self._draw_ortho_pipe([(tap_x, int(spine_y)), (tap_x, egr_y),
                                   (cool.right, egr_y)], 2, self._EXH_COLS)   # hot tap
            scr.blit(self._grad_surf(cool.w, cool.h, (108, 116, 128), (52, 58, 70), 3),
                     cool.topleft)
            for fx in range(cool.x + 3, cool.right - 2, 3):
                pygame.draw.line(scr, (40, 44, 54), (fx, cool.y + 2), (fx, cool.bottom - 2), 1)
            pygame.draw.rect(scr, (40, 44, 54), cool, 1, border_radius=2)
            self._draw_ortho_pipe([(cool.left, egr_y), (int(px0) + 12, egr_y),
                                   (int(px0) + 12, int(plen_y))], 2, self._INT_COLS)
            tag = self.font_small.render("EGR", True, (150, 158, 174))
            scr.blit(tag, (cool.centerx - tag.get_width() // 2, cool.y - 12))
        self._end_pipe_layers()
        self._draw_fuel_rail(ports_i)                # port fuel injection
        for s, st in enumerate(stations):
            jx = x_start + sw * (s + 0.5)
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
                self._draw_cyl(jx, crank_y, a, length, width, frac, theta, glow, phi)
                lx = jx + math.sin(a) * (length + 14)
                ly = crank_y - math.cos(a) * (length + 14)
                lab = self.font_small.render(f"{i + 1}", True, DIM)
                self.screen.blit(lab, (int(lx) - lab.get_width() // 2, int(ly) - 6))
        self._draw_bay_induction(bay, eng, sim)

    def _flash_surf(self, radius, glow):
        """A SOFT combustion bloom — a smooth radial gradient (white-hot core ->
        yellow -> orange -> red) with a gentle squared falloff, cached per size &
        intensity.  No hard rings: a clean, glowing flash."""
        R = max(int(radius), 4)
        gq = min(max(int(glow * 8), 0), 8)            # quantise intensity for caching
        key = ('flash', R, gq)
        s = self._grad_cache.get(key)
        if s is None:
            pad = int(R * 1.9) + 6
            size = 2 * pad
            yy, xx = np.mgrid[0:size, 0:size]
            d = np.clip(np.sqrt((xx - pad) ** 2 + (yy - pad) ** 2) / (R * 1.75), 0.0, 1.0)
            # vivid, MAX-saturation fire ramp (white-hot core -> deep red edge)
            stops = [(0.0, (255, 255, 240)), (0.18, (255, 238, 70)),
                     (0.42, (255, 150, 12)), (0.68, (255, 56, 6)), (1.0, (230, 12, 4))]
            rgb = np.zeros((size, size, 3), dtype=np.float32)
            for i in range(len(stops) - 1):
                t0, c0 = stops[i]; t1, c1 = stops[i + 1]
                m = (d >= t0) & (d <= t1)
                fr = (d[m] - t0) / max(t1 - t0, 1e-6)
                for ch in range(3):
                    rgb[..., ch][m] = c0[ch] + (c1[ch] - c0[ch]) * fr
            # punchier + always visible: gentler falloff and a lifted intensity
            # curve so even a weak combustion flash still shows clearly
            alpha = np.clip((1.0 - d) ** 1.2, 0.0, 1.0) * (gq / 8.0) ** 0.6 * 255.0
            arr = np.ascontiguousarray(
                np.dstack([rgb, alpha]).astype(np.uint8))
            s = pygame.image.frombuffer(arr.tobytes(), (size, size), "RGBA").convert_alpha()
            self._grad_cache[key] = s
        return s

    def _draw_cyl(self, jx, jy, a, length, width, frac, theta, glow, phi=0.0):
        """Draw one cylinder + reciprocating piston + rod + crank journal along a
        bank axis tilted by angle ``a`` (radians) from vertical, hinged at the
        crank centre (jx, jy).  Metal surfaces are strip-shaded (bright centre,
        dark edges) for the round skeuomorphic look, even when tilted.

        The static metal SLEEVE/head is cached once per engine (see
        _draw_engine_panel) as a per-cylinder sprite and re-blitted each frame just
        before that cylinder's live valvetrain/piston/crank — preserving the inline
        draw order, so the result is byte-for-byte identical."""
        idx = self._cyl_idx
        self._cyl_idx += 1
        if self._sleeve_sprites is None:               # defensive: no cache -> draw all
            self._cyl_static(self.screen, jx, jy, a, length, width)
        elif self._bay_dirty:                          # building the sprite this frame
            scr = self._sleeve_scratch
            scr.fill((0, 0, 0, 0))
            self._cyl_static(scr, jx, jy, a, length, width)
            bb = scr.get_bounding_rect()               # tight box of the opaque shell
            if bb.width and bb.height:
                spr = scr.subsurface(bb).copy()
                self._sleeve_sprites.append((spr, (bb.x, bb.y)))
                self.screen.blit(spr, (bb.x, bb.y))    # rebuild frame: show the shell
            else:
                self._sleeve_sprites.append(None)
        else:                                          # restore this cylinder's shell
            item = self._sleeve_sprites[idx] if idx < len(self._sleeve_sprites) else None
            if item is not None:
                self.screen.blit(item[0], item[1])
        self._cyl_dynamic(jx, jy, a, length, width, frac, theta, glow, phi)

    def _cyl_static(self, tgt, jx, jy, a, length, width):
        """The non-moving cylinder shell — base cap, strip-shaded sleeve, brushed
        grain, specular band, cooling fins, edge outline and the domed head cap
        (in Low-Q: the flat sleeve/head-band/outline/bore).  Painted onto ``tgt``
        (the sleeve cache, or directly the screen on the rebuild frame)."""
        ux, uy = math.sin(a), -math.cos(a)            # 'up' along the cylinder
        qx, qy = math.cos(a), math.sin(a)             # perpendicular (across bore)
        cr = 9.0
        bx, by = jx + ux * cr * 1.4, jy + uy * cr * 1.4   # bore base (off the crank)
        hw = width / 2.0

        if self.low_quality:
            def quad(d0, d1, halfw, col, w=0):
                pygame.draw.polygon(tgt, col, [
                    (bx + ux * d0 + qx * halfw, by + uy * d0 + qy * halfw),
                    (bx + ux * d1 + qx * halfw, by + uy * d1 + qy * halfw),
                    (bx + ux * d1 - qx * halfw, by + uy * d1 - qy * halfw),
                    (bx + ux * d0 - qx * halfw, by + uy * d0 - qy * halfw)], w)
            quad(-2, length + 4, hw + 3, (98, 104, 118))      # cylinder sleeve
            quad(length - 1, length + 5, hw + 3, (70, 76, 90))  # head band
            quad(-2, length + 4, hw + 3, (28, 30, 38), 2)     # outline
            quad(1, length, hw - 2, (24, 26, 32))             # bore interior
            return

        def shaded(d0, d1, halfw, base, n=9):         # round-metal strip gradient
            # light from the upper-left: brightest just left of centre, dark edges
            for si in range(n):
                e0 = (si / n * 2 - 1) * halfw; e1 = ((si + 1) / n * 2 - 1) * halfw
                t = (si + 0.5) / n * 2 - 1             # -1 edge .. +1 edge
                f = 0.30 + 0.85 * max(0.0, 1.0 - abs(t - 0.18)) ** 1.3   # off-centre hi
                col = (min(255, int(base[0] * f)), min(255, int(base[1] * f)),
                       min(255, int(base[2] * f)))
                pygame.draw.polygon(tgt, col, [
                    (bx + ux * d0 + qx * e0, by + uy * d0 + qy * e0),
                    (bx + ux * d1 + qx * e0, by + uy * d1 + qy * e0),
                    (bx + ux * d1 + qx * e1, by + uy * d1 + qy * e1),
                    (bx + ux * d0 + qx * e1, by + uy * d0 + qy * e1)])

        def cap(d, halfw, base, spec=False):          # DOMED rounded end-cap
            cxp = int(bx + ux * d); cyp = int(by + uy * d); rr = int(halfw)
            for sr, f, off in ((rr, 0.62, 0.0), (int(rr * 0.80), 0.86, 0.16),
                               (int(rr * 0.60), 1.12, 0.30), (int(rr * 0.40), 1.4, 0.44),
                               (int(rr * 0.22), 1.7, 0.56)):
                ox = int(qx * halfw * off); oy = int(qy * halfw * off)   # toward light
                col = (min(255, int(base[0] * f)), min(255, int(base[1] * f)),
                       min(255, int(base[2] * f)))
                pygame.draw.circle(tgt, col, (cxp + ox, cyp + oy), max(1, sr))
            pygame.draw.circle(tgt, (22, 24, 30), (cxp, cyp), rr, 1)
            if spec and rr >= 5:                       # sharp polished catch-light
                pygame.draw.circle(tgt, (240, 244, 252),
                                   (cxp + int(qx * halfw * 0.5), cyp + int(qy * halfw * 0.5)),
                                   max(1, rr // 5))

        def along(d0, d1, e, col, w=1):               # a line running ALONG the bore
            pygame.draw.line(tgt, col,
                             (bx + ux * d0 + qx * e, by + uy * d0 + qy * e),
                             (bx + ux * d1 + qx * e, by + uy * d1 + qy * e), w)

        def edge(d0, d1, halfw, col, w=1):
            pygame.draw.polygon(tgt, col, [
                (bx + ux * d0 + qx * halfw, by + uy * d0 + qy * halfw),
                (bx + ux * d1 + qx * halfw, by + uy * d1 + qy * halfw),
                (bx + ux * d1 - qx * halfw, by + uy * d1 - qy * halfw),
                (bx + ux * d0 - qx * halfw, by + uy * d0 - qy * halfw)], w)

        cap(-2, hw + 3, (60, 66, 78))                 # base cap (behind)
        shaded(-2, length + 4, hw + 3, (78, 84, 96), n=16)  # smooth metal sleeve
        # brushed grain (faint lines running along the bore)
        for be in (-0.55, -0.25, 0.55):
            along(0, length + 2, be * (hw + 3), (94, 100, 114), 1)
        # a SOFT specular band (bright core fading out) instead of a hard stripe
        for eoff, c in ((0.18, 206), (0.05, 168), (0.31, 150), (-0.08, 132)):
            along(1, length + 2, eoff * (hw + 3),
                  (c, min(255, c + 6), min(255, c + 16)), 2 if c > 190 else 1)
        for fz in range(4):                           # cooling fins near the head
            d = length - 2 - fz * 5
            pygame.draw.line(tgt, (40, 44, 54),
                             (bx + ux * d + qx * (hw + 3), by + uy * d + qy * (hw + 3)),
                             (bx + ux * d - qx * (hw + 3), by + uy * d - qy * (hw + 3)), 2)
        edge(-2, length + 4, hw + 3, (22, 24, 30), 2)
        cap(length + 4, hw + 3, (104, 110, 124), spec=True)   # DOMED head cap on top

    def _cyl_dynamic(self, jx, jy, a, length, width, frac, theta, glow, phi=0.0):
        """The moving parts drawn on TOP of the cached sleeve each frame: the
        valvetrain on the head, the dark bore, combustion glow, the piston + rod
        and the crank throw/journals."""
        ux, uy = math.sin(a), -math.cos(a)            # 'up' along the cylinder
        qx, qy = math.cos(a), math.sin(a)             # perpendicular (across bore)
        cr = 9.0
        bx, by = jx + ux * cr * 1.4, jy + uy * cr * 1.4   # bore base (off the crank)
        hw = width / 2.0

        if self.low_quality:
            def quad(d0, d1, halfw, col, w=0):
                pygame.draw.polygon(self.screen, col, [
                    (bx + ux * d0 + qx * halfw, by + uy * d0 + qy * halfw),
                    (bx + ux * d1 + qx * halfw, by + uy * d1 + qy * halfw),
                    (bx + ux * d1 - qx * halfw, by + uy * d1 - qy * halfw),
                    (bx + ux * d0 - qx * halfw, by + uy * d0 - qy * halfw)], w)
            plen = 15.0
            travel = max(length - plen - 14, 6.0)
            ppos = 8.0 + (1.0 - frac) * travel                # TDC high, BDC low
            quad(ppos, ppos + plen, hw - 3, (190, 196, 208))  # piston
            quad(ppos, ppos + plen, hw - 3, (96, 102, 114), 1)
            pin = (int(bx + ux * ppos), int(by + uy * ppos))
            jrn = (int(jx + cr * math.sin(theta)), int(jy + cr * math.cos(theta)))
            pygame.draw.line(self.screen, (126, 132, 146), pin, jrn, 4)  # con-rod
            pygame.draw.circle(self.screen, (54, 58, 70), pin, 4)
            pygame.draw.circle(self.screen, (150, 156, 168), jrn, 4)
            return

        def shaded(d0, d1, halfw, base, n=9):         # round-metal strip gradient
            for si in range(n):
                e0 = (si / n * 2 - 1) * halfw; e1 = ((si + 1) / n * 2 - 1) * halfw
                t = (si + 0.5) / n * 2 - 1             # -1 edge .. +1 edge
                f = 0.30 + 0.85 * max(0.0, 1.0 - abs(t - 0.18)) ** 1.3   # off-centre hi
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

        # --- valvetrain on the head: poppet valves, springs, lifters + camshaft -
        if length > 34:
            sc = self.screen

            def lift_at(open_deg, dur):                # raised-cosine valve lift
                for shf in (-720.0, 0.0, 720.0):
                    t = (phi + shf - open_deg) / dur
                    if 0.0 <= t <= 1.0:
                        return 0.5 * (1.0 - math.cos(2 * math.pi * t))
                return 0.0
            il, el = lift_at(700.0, 240.0), lift_at(500.0, 230.0)
            cam_d = length + 19
            vt = getattr(self, "_cur_vt", "dohc")
            vtec = getattr(self, "_vtec_on", False)       # VTEC high-cam engaged
            lobe_col = (240, 180, 80) if vtec else (170, 176, 192)

            def camshaft(qo, lift):                       # an end-on cam: journal + lobe
                ccx = bx + ux * cam_d + qx * qo
                ccy = by + uy * cam_d + qy * qo
                pygame.draw.circle(sc, (96, 102, 118), (int(ccx), int(ccy)), 4)
                pygame.draw.circle(sc, (150, 156, 172), (int(ccx), int(ccy)), 4, 1)
                # the lobe presses toward the valve; VTEC swaps to a taller HIGH cam
                lobe = (3.0 + lift * (6.5 if vtec else 3.0))
                lx2 = ccx - ux * lobe; ly2 = ccy - uy * lobe
                pygame.draw.line(sc, lobe_col, (int(ccx), int(ccy)),
                                 (int(lx2), int(ly2)), 3)
                pygame.draw.circle(sc, (200, 206, 222), (int(ccx), int(ccy)), 1)
            if vt == "ohv":
                # OHV: NO overhead cam — a single low gear + PUSHRODS up to rockers
                for qo in (-hw * 0.5, hw * 0.5):
                    r0x = bx + ux * (length + 2) + qx * qo
                    r0y = by + uy * (length + 2) + qy * qo
                    r1x = bx + ux * (cam_d + 3) + qx * qo * 0.6
                    r1y = by + uy * (cam_d + 3) + qy * qo * 0.6
                    pygame.draw.line(sc, (150, 120, 60), (int(r0x), int(r0y)),
                                     (int(r1x), int(r1y)), 2)   # pushrod (brassy)
                pygame.draw.line(sc, (118, 124, 138),          # rocker shaft
                                 (bx + ux * cam_d + qx * (hw + 1), by + uy * cam_d + qy * (hw + 1)),
                                 (bx + ux * cam_d - qx * (hw + 1), by + uy * cam_d - qy * (hw + 1)), 3)
            elif vt == "sohc":
                camshaft(0.0, max(il, el))                # one central overhead cam
            else:                                         # DOHC: two overhead cams
                camshaft(-hw * 0.5, il)
                camshaft(hw * 0.5, el)
            for qo, lift, vcol, scol in ((-hw * 0.5, il, (110, 196, 255), (70, 225, 215)),
                                         (hw * 0.5, el, (255, 146, 110), (255, 196, 60))):
                # valve head dips INTO the bore as it opens — a tinted metal disc
                vd = length - 1 - lift * 6.0
                vxp = int(bx + ux * vd + qx * qo); vyp = int(by + uy * vd + qy * qo)
                vr = max(2, int(hw * 0.32))
                lx, ly = -0.52, -0.62                       # screen upper-left light
                # ambient occlusion: dark contact shadow where the valve seats
                ao = pygame.Surface((vr * 4, vr * 4), pygame.SRCALPHA)
                pygame.draw.circle(ao, (0, 0, 0, 150), (vr * 2, vr * 2 + 1), vr + 2)
                sc.blit(pygame.transform.smoothscale(ao, (vr * 4, vr * 4)),
                        (vxp - vr * 2, vyp - vr * 2))
                # metallic radial gradient: dark rim -> lit face -> white-tinted crown
                for sr, f, off, wt in ((vr, 0.5, 0.0, 0.0), (int(vr * 0.74), 0.95, 0.28, 0.0),
                                       (int(vr * 0.46), 1.35, 0.5, 0.35),
                                       (int(vr * 0.24), 1.7, 0.66, 0.62)):
                    ox, oy = int(lx * vr * off), int(ly * vr * off)
                    col = tuple(min(255, int(c * f + (255 - c * f) * wt)) for c in vcol)
                    pygame.draw.circle(sc, col, (vxp + ox, vyp + oy), max(1, sr))
                # sharp specular pip + dark seating rim
                pygame.draw.circle(sc, (255, 255, 255),
                                   (vxp + int(lx * vr * 0.6), vyp + int(ly * vr * 0.6)),
                                   max(1, vr // 4))
                pygame.draw.circle(sc, (18, 20, 26), (vxp, vyp), vr, 1)
                # valve spring (compresses as the valve opens) — 4 vivid anodised
                # coils with a bright top edge so they read as colour, not grey
                hi = tuple(min(255, c + 55) for c in scol)
                for ci in range(4):
                    d = length + 4 + ci * (3.0 - lift * 1.2)
                    p0 = (bx + ux * d + qx * (qo - hw * 0.24),
                          by + uy * d + qy * (qo - hw * 0.24))
                    p1 = (bx + ux * d + qx * (qo + hw * 0.24),
                          by + uy * d + qy * (qo + hw * 0.24))
                    pygame.draw.line(sc, scol, p0, p1, 2)
                    pygame.draw.line(sc, hi, p0, (p0[0] + (p1[0] - p0[0]) * 0.45,
                                                  p0[1] + (p1[1] - p0[1]) * 0.45), 1)
                # simplified rocker-arm bracket (anodised gold) over the eccentric
                # cam lobe + a bucket lifter riding it
                lobe = (1.0 + lift) * hw * 0.18
                ld = cam_d - lobe
                lxp = int(bx + ux * ld + qx * qo); lyp = int(by + uy * ld + qy * qo)
                pygame.draw.line(sc, (224, 182, 86),
                                 (int(bx + ux * (length + 4) + qx * qo),
                                  int(by + uy * (length + 4) + qy * qo)),
                                 (lxp, lyp), 3)
                pygame.draw.circle(sc, (150, 156, 170), (lxp, lyp), max(2, int(hw * 0.2)))
                pygame.draw.circle(sc, (245, 248, 255), (lxp - 1, lyp - 1), 1)
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

        def metal_disc(px, py, rad, base):            # domed steel disc, lit top-left
            for sr, f, off in ((rad, 0.6, 0), (rad * 0.72, 1.0, 0.22),
                               (rad * 0.42, 1.4, 0.42)):
                o = int(rad * off)
                col = (min(255, int(base[0] * f)), min(255, int(base[1] * f)),
                       min(255, int(base[2] * f)))
                pygame.draw.circle(self.screen, col, (int(px) - o, int(py) - o),
                                   max(1, int(sr)))
            pygame.draw.circle(self.screen, (22, 24, 30), (int(px), int(py)), int(rad), 1)
        # --- crank throw: a heavy counterweight FAN opposite the rod journal,
        # the web that carries it, then the main + rod journals (forged steel) ---
        jdx, jdy = math.sin(theta), math.cos(theta)        # unit toward rod journal
        opp = math.atan2(-jdy, -jdx)                       # counterweight points away
        cwr = cr * 1.42                                    # slimmer: less bottom clutter
        fan = [(jx, jy)]
        for tt in range(-6, 7):
            aa = opp + math.radians(tt * 11.5)
            fan.append((jx + cwr * math.cos(aa), jy + cwr * math.sin(aa)))
        pygame.draw.polygon(self.screen, (96, 68, 26), fan)            # brass counterweight
        inner = [(jx - 1.5, jy - 2)]
        for tt in range(-6, 7):
            aa = opp + math.radians(tt * 11.5)
            inner.append((jx + cwr * 0.78 * math.cos(aa) - 1.5,
                          jy + cwr * 0.78 * math.sin(aa) - 2))
        pygame.draw.polygon(self.screen, (164, 126, 56), inner)       # lit brass sheen
        pygame.draw.polygon(self.screen, (40, 28, 10), fan, 1)
        # web carrying the rod journal
        webw = cr * 0.5
        ox, oy = -jdy, jdx
        pygame.draw.polygon(self.screen, (60, 65, 78), [
            (jx + ox * webw, jy + oy * webw), (jrn[0] + ox * webw * 0.8, jrn[1] + oy * webw * 0.8),
            (jrn[0] - ox * webw * 0.8, jrn[1] - oy * webw * 0.8), (jx - ox * webw, jy - oy * webw)])
        metal_disc(jx, jy, cr * 0.7, (98, 104, 120))                  # main journal
        # rod big-end pin (steel, no marker dot)
        pygame.draw.circle(self.screen, (44, 48, 58), (int(jrn[0]), int(jrn[1])), 4)
        pygame.draw.circle(self.screen, (150, 158, 174), (int(jrn[0]), int(jrn[1])), 4, 1)
        pygame.draw.circle(self.screen, (96, 102, 116), (int(jrn[0]), int(jrn[1])), 2)

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

    def _blit_firing(self, eng, x, y, maxw):
        """Draw the firing-order line, shrinking it to fit maxw (a long W16 order
        would otherwise overrun the panel)."""
        fo = "-".join(str(v) for v in eng.firing_order)
        surf = self.font_small.render(f"{self.tr('Firing order:')} {fo}", True, ACCENT)
        if surf.get_width() > maxw and maxw > 20:
            h = max(9, int(surf.get_height() * maxw / surf.get_width()))
            surf = pygame.transform.smoothscale(surf, (int(maxw), h))
        self.screen.blit(surf, (x, y))

    def _draw_w_banks(self, rect, sim, eng, left, right, top=None, bottom=None):
        """A W engine drawn as its TWO real VR units (VR6/VR8) side by side: each
        unit is an upright narrow-angle vee with its own vertical crank and two
        tight sub-banks (the 15-deg VR vee); the two units sit 90 deg apart."""
        mtop = top if top is not None else rect.y + 32
        mbot = bottom if bottom is not None else rect.bottom - 12
        unit_name = f"VR{len(left)}"
        headsL, headsR = [], []                       # head tops per VR unit
        wwidth = 13.0
        for ui, grp in enumerate((left, right)):
            ux = rect.x + int(rect.width * (0.40 if ui == 0 else 0.60))  # closer columns
            # split this VR unit into its two sub-banks by bank angle
            mid = sum(eng.cylinders[i].bank_angle_deg for i in grp) / max(len(grp), 1)
            subA = [i for i in grp if eng.cylinders[i].bank_angle_deg < mid]
            subB = [i for i in grp if eng.cylinders[i].bank_angle_deg >= mid]
            nsu = max(len(subA), len(subB), 1)
            tilt = math.radians(34.0)
            width = 13.0
            length = 52.0
            # TIGHT vertical pitch so the V-pairs sit close (no big gaps), then
            # fit & centre the VR unit in the bay.
            dy = max(width * 1.7, length * math.cos(tilt) * 0.58)
            reach_up = length * math.cos(tilt)
            block_h = dy * (nsu - 1) + reach_up + 14
            avail = mbot - mtop
            if block_h > avail:
                f = avail / block_h
                dy *= f; width *= f; length *= f; reach_up *= f
                block_h = dy * (nsu - 1) + reach_up + 14
            u_mtop = (mtop + mbot) * 0.5 - block_h * 0.5 - dy * 0.5 + reach_up
            # vertical crankshaft for this unit (strip-shaded metal)
            cy0, cy1 = u_mtop + dy * 0.5 - 10, u_mtop + dy * (nsu - 0.5) + 10
            chw = max(width * 0.30, 8.0)
            for si in range(7):
                e0 = (si / 7 * 2 - 1) * chw; e1 = ((si + 1) / 7 * 2 - 1) * chw
                f = 0.40 + 0.66 * (1.0 - abs((si + 0.5) / 7 * 2 - 1))
                pygame.draw.polygon(self.screen, (min(255, int(70 * f)),
                                    min(255, int(76 * f)), min(255, int(90 * f))),
                                    [(ux + e0, cy0), (ux + e0, cy1),
                                     (ux + e1, cy1), (ux + e1, cy0)])
            for s in range(nsu):
                jy = u_mtop + dy * (s + 0.5)
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
                    self._draw_cyl(ux, jy, a, length, width, frac, theta, glow, phi)
                    hx = ux + math.sin(a) * length     # head top
                    hy = jy - math.cos(a) * length
                    (headsL if ui == 0 else headsR).append((hx, hy))
                    lx = ux + math.sin(a) * (length + 14)
                    ly = jy - math.cos(a) * (length + 14)
                    lab = self.font_small.render(f"{i + 1}", True, DIM)
                    self.screen.blit(lab, (int(lx) - lab.get_width() // 2, int(ly) - 6))
            ulab = self.font_small.render(unit_name, True, (150, 158, 172))
            self.screen.blit(ulab, (ux - ulab.get_width() // 2, mbot + 6))
            wwidth = width
        # manifolds ON TOP, routed only through EMPTY space (above heads / outside
        # the units) so nothing crosses the pistons: a green intake plenum bar over
        # each VR unit with short down-runners, and the exhaust taken up-and-over to
        # the two turbos on that unit's OUTER side (left VR8 -> left turbos, etc.).
        self._begin_pipe_layers()
        cyv = int((mtop + mbot) * 0.5)
        hrad = max(2, int(wwidth * 0.16)); irad = max(2, int(wwidth * 0.13))
        ctop = int(min((min(h[1] for h in headsL) if headsL else mtop),
                       (min(h[1] for h in headsR) if headsR else mtop)) - 22)
        for heads, outer in ((headsL, -1), (headsR, 1)):
            if not heads:
                continue
            xs = [h[0] for h in heads]
            topy = int(min(h[1] for h in heads))
            bar_y, band_y = topy - 12, topy - 26
            # INTAKE: plenum bar over the unit + short down-runners to each head top
            self._draw_ortho_pipe([(int(min(xs) - 6), bar_y), (int(max(xs) + 6), bar_y)],
                                  irad + 1, self._INT_COLS)
            for hx, hy in heads:
                self._draw_ortho_pipe([(int(hx), bar_y), (int(hx), int(hy))], irad,
                                      self._INT_COLS, joint=True)
            inner_x = int(max(xs) + 6) if outer < 0 else int(min(xs) - 6)
            self._draw_ortho_pipe([(inner_x, bar_y), (rect.centerx, ctop)], irad,
                                  self._INT_COLS)
            # EXHAUST: each head up to a band above, out to a side rail, down to the
            # two turbos on this side
            rail_x = rect.x + 64 if outer < 0 else rect.right - 64
            for hx, hy in heads:
                ex = int(hx + outer * wwidth * 0.5)
                self._draw_ortho_pipe([(ex, int(hy)), (ex, band_y), (rail_x, band_y)],
                                      hrad, self._EXH_COLS, joint=True)
            turbo_x = rect.x + 46 if outer < 0 else rect.right - 46
            self._draw_ortho_pipe([(rail_x, band_y), (rail_x, cyv + 34),
                                   (turbo_x, cyv + 34)], hrad + 1, self._EXH_COLS)
            self._draw_ortho_pipe([(rail_x, cyv - 34), (turbo_x, cyv - 34)], hrad,
                                  self._EXH_COLS)
        self._collector_slug((rect.centerx, ctop), irad + 1)
        self._end_pipe_layers()
        # W engines were missing the front timing GEARS + accessory BELT the V
        # layout draws in its valley — add them at the front-centre of the block.
        self._draw_w_front_drive(rect, sim, eng, mtop, mbot)

    def _draw_w_front_drive(self, rect, sim, eng, mtop, mbot):
        """Front-of-engine accessory drive for a W: a meshing timing-GEAR cluster
        (crank gear driving two cam gears) plus a serpentine BELT around the crank
        pulley and two accessory pulleys.  Drawn on top, at the bottom centre."""
        sc = self.screen
        ang = 0.0 if self.telemetry_mode else sim.crank_angle   # Forza: frozen drive
        cx = rect.centerx
        by = int(mbot - 16)                              # front/bottom baseline

        def gear(c, r, teeth, spin, ring=None):
            c = (int(c[0]), int(c[1]))
            pygame.draw.circle(sc, (44, 48, 60), c, r + 2)
            for k in range(teeth):                       # radial gear teeth
                t = spin + k * 2 * math.pi / teeth
                pygame.draw.line(sc, (158, 164, 180),
                                 (int(c[0] + math.cos(t) * (r - 1)),
                                  int(c[1] + math.sin(t) * (r - 1))),
                                 (int(c[0] + math.cos(t) * (r + 2)),
                                  int(c[1] + math.sin(t) * (r + 2))), 2)
            if self.low_quality:
                pygame.draw.circle(sc, (134, 140, 154), c, r)   # flat gear/sprocket
            else:
                sc.blit(self._grad_surf(2 * r, 2 * r, (140, 146, 162), (60, 66, 80), r,
                                        gloss=True), (c[0] - r, c[1] - r))
            if ring:
                pygame.draw.circle(sc, ring, c, int(r * 0.7), 2)
            pygame.draw.circle(sc, (40, 44, 54), c, max(2, int(r * 0.28)))

        # --- timing GEAR train: a central crank gear meshing two cam gears -------
        gy = by - 34
        camL, camR = (cx - 17, gy - 14), (cx + 17, gy - 14)
        gear(camL, 12, 16, -ang * 0.5, ring=(236, 176, 72))
        gear(camR, 12, 16, ang * 0.5, ring=(236, 176, 72))
        gear((cx, gy), 10, 12, ang)                      # crank gear (drives them)

        # --- serpentine BELT around the crank pulley + two accessory pulleys -----
        pulls = [(cx, by, 9), (cx - 34, by + 3, 6), (cx + 34, by + 1, 6)]
        loop = pygame.Rect(cx - 46, by - 12, 92, 30)
        pygame.draw.ellipse(sc, (26, 26, 30), loop, 6)   # rubber belt
        pygame.draw.ellipse(sc, (62, 62, 70), loop, 1)
        for px, py, pr in pulls:                         # pulleys with hubs
            spin = ang if pr > 7 else -ang * 1.7
            if self.low_quality:
                pygame.draw.circle(sc, (140, 146, 160), (px, py), pr)   # flat pulley
            else:
                sc.blit(self._grad_surf(2 * pr, 2 * pr, (150, 156, 170), (64, 70, 84),
                                        pr, gloss=True), (px - pr, py - pr))
            pygame.draw.circle(sc, (90, 96, 110), (px, py), pr, 1)
            pygame.draw.circle(sc, (40, 44, 54), (px, py),
                               max(2, int(pr * 0.4)))
            # a spinning index nick so the pulleys visibly turn
            pygame.draw.line(sc, (210, 214, 224), (px, py),
                             (int(px + math.cos(spin) * (pr - 1)),
                              int(py + math.sin(spin) * (pr - 1))), 1)

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
            self._draw_cyl(cx, cy, a, length, width, frac, theta, glow, phi)
        sc = self.screen
        # --- aircraft RING exhaust: a short stub off each head into a collector
        # ring hugging the cylinder bank (no civilian after-treatment) ---
        head_r = 9 * 1.4 + length + width * 0.45
        ring_r = int(min(head_r + 5, (bottom - top) * 0.49, rect.width * 0.47))
        pygame.draw.circle(sc, (54, 18, 14), (cx, cy), ring_r, 5)
        pygame.draw.circle(sc, (176, 62, 38), (cx, cy), ring_r, 3)
        for i in range(n):
            a = math.radians(eng.cylinders[i].bank_angle_deg)
            hx, hy = cx + math.sin(a) * head_r, cy - math.cos(a) * head_r
            rx, ry = cx + math.sin(a) * ring_r, cy - math.cos(a) * ring_r
            pygame.draw.line(sc, (54, 18, 14), (hx, hy), (rx, ry), 5)
            pygame.draw.line(sc, (176, 62, 38), (hx, hy), (rx, ry), 3)
        # ring oil sump (a brass arc cradling the bottom of the crankcase)
        sump = pygame.Rect(cx - int(Rc * 0.6), cy - int(Rc * 0.6), int(Rc * 1.2), int(Rc * 1.2))
        pygame.draw.arc(sc, (96, 68, 26), sump, math.radians(200), math.radians(340), 4)
        # --- central PROP-REDUCTION gearbox: a toothed reduction gear + prop boss -
        hub_r = int(Rc * 0.2)
        gear_a = 0.0 if self.telemetry_mode else sim.crank_angle * 0.5   # Forza: frozen
        for k in range(16):                              # reduction-gear teeth
            t = gear_a + k * math.pi / 8
            pygame.draw.line(sc, (150, 156, 172),
                             (int(cx + math.cos(t) * (hub_r - 1)), int(cy + math.sin(t) * (hub_r - 1))),
                             (int(cx + math.cos(t) * (hub_r + 2)), int(cy + math.sin(t) * (hub_r + 2))), 2)
        if self.low_quality:                             # flat hub (no gloss shading)
            pygame.draw.circle(sc, (122, 128, 144), (cx, cy), hub_r)
        else:
            sc.blit(self._grad_surf(2 * hub_r, 2 * hub_r, (130, 136, 152), (56, 62, 76),
                                    hub_r, gloss=True), (cx - hub_r, cy - hub_r))
        pygame.draw.circle(sc, (40, 44, 54), (cx, cy), hub_r, 1)
        pygame.draw.circle(sc, (190, 196, 210), (cx, cy), 4)   # prop shaft boss
        pygame.draw.circle(sc, (40, 44, 54), (cx, cy), 4, 1)
        pl = self.font_small.render(self.tr("Prop Reduction"), True, (150, 158, 174))
        sc.blit(pl, (cx - pl.get_width() // 2, cy + hub_r + 3))

    # manifold pipe colour sets — (dark casing, lit body, top sheen)
    _EXH_COLS = ((46, 20, 16), (168, 72, 46), (238, 152, 104))  # hot exhaust = red
    _EXH2_COLS = ((50, 32, 12), (196, 120, 40), (242, 184, 96))  # 2nd scroll = orange
    _INT_COLS = ((16, 42, 28), (62, 146, 94), (150, 214, 170))  # cool intake = green

    def _draw_fuel_rail(self, ports):
        """A high-pressure fuel rail (amber) running along the intake ports with a
        small injector nozzle dropped into each one."""
        if not ports:
            return
        sc = self.screen
        xs = [int(p[0]) for p in ports]
        ry = int(max(p[1] for p in ports)) + 6
        pygame.draw.line(sc, (96, 66, 18), (min(xs) - 3, ry), (max(xs) + 3, ry), 4)
        pygame.draw.line(sc, (208, 158, 52), (min(xs) - 3, ry), (max(xs) + 3, ry), 2)
        for px, py in ports:
            ix = int(px)
            pygame.draw.line(sc, (96, 66, 18), (ix, ry), (ix, int(py) + 2), 3)
            pygame.draw.line(sc, (220, 170, 64), (ix, ry), (ix, int(py) + 2), 1)
            pygame.draw.circle(sc, (244, 196, 90), (ix, int(py) + 2), 2)

    def _pipe_target(self, cols):
        """Route green/red manifold pipes onto translucent layers (set up by
        _begin_pipe_layers) so they can be composited semi-transparently."""
        pl = getattr(self, "_pipe_layers", None)
        if pl is not None:
            if cols is self._INT_COLS:
                return pl[0]
            if cols is self._EXH_COLS or cols is self._EXH2_COLS:
                return pl[1]
        return self.screen

    def _get_layer(self, idx):
        """A CACHED full-window SRCALPHA scratch layer (reused per frame, only the
        bay region cleared) — avoids re-allocating big surfaces every frame, which
        was holding the GIL long enough to glitch the audio at high rpm."""
        cache = getattr(self, "_alpha_cache", None)
        if cache is None:
            cache = self._alpha_cache = {}
        sz = self.screen.get_size()
        s = cache.get(idx)
        if s is None or s.get_size() != sz:
            s = cache[idx] = pygame.Surface(sz, pygame.SRCALPHA)
        br = getattr(self, "_bay_rect", None)
        s.fill((0, 0, 0, 0), br)
        return s

    def _begin_pipe_layers(self):
        self._pl_real = self.screen
        # Low-quality mode: draw pipes SOLID straight onto the screen (skip the
        # per-frame full-window alpha alloc/clear/composite) to free CPU for audio.
        if self.low_quality:
            self._pipe_layers = None
        else:
            self._pipe_layers = (self._get_layer(0), self._get_layer(1))

    def _end_pipe_layers(self):
        if self._pipe_layers is None:
            return
        g, r = self._pipe_layers
        self._pipe_layers = None
        br = getattr(self, "_bay_rect", None)
        dst = br.topleft if br else (0, 0)
        r.set_alpha(204); self._pl_real.blit(r, dst, br)   # red 80% opacity
        g.set_alpha(204); self._pl_real.blit(g, dst, br)   # green 80% opacity

    def _draw_header_tube(self, p0, p1, ctrl, rad, cols=None, joint=False):
        """A manifold runner from a head port (p0) bending through ctrl into the
        collector (p1): dark casing, lit body and a top sheen.  Sampled at only a
        few points so the bend is FACETED (bent-metal polygon), not an organic
        curve.  ``joint`` caps the port end with a flange fitting."""
        cols = cols or self._EXH_COLS
        sc = self._pipe_target(cols)
        pts = []
        for k in range(5):
            t = k / 4.0
            u = 1.0 - t
            pts.append((u * u * p0[0] + 2 * u * t * ctrl[0] + t * t * p1[0],
                        u * u * p0[1] + 2 * u * t * ctrl[1] + t * t * p1[1]))
        if self.low_quality:                               # flat single-colour runner
            pygame.draw.lines(sc, cols[1], False, pts, max(2, rad * 2))
            if joint:
                pygame.draw.circle(sc, cols[1], (int(p0[0]), int(p0[1])), rad + 1)
            return
        self._tube_run(sc, [(int(x), int(y)) for x, y in pts], rad, cols)
        if joint:                                          # flange on a head boss
            jx, jy = int(p0[0]), int(p0[1])
            pygame.draw.circle(sc, (18, 20, 26), (jx, jy), rad + 4)   # dark mounting boss
            pygame.draw.circle(sc, (44, 48, 58), (jx, jy), rad + 4, 1)
            pygame.draw.circle(sc, cols[0], (jx, jy), rad + 1)        # pipe flange
            pygame.draw.circle(sc, cols[1], (jx, jy), rad)
            pygame.draw.circle(sc, cols[2], (jx - 1, jy - 1), max(1, rad // 2))

    def _draw_ortho_pipe(self, pts, rad, cols=None, joint=False):
        """A RIGHT-ANGLED manifold run (riser, then a 90-deg elbow out to a rail).
        Corners are softened with a small CHAMFER so the bends read as gently
        rounded rather than robotically square (a slight 488-style curve)."""
        cols = cols or self._EXH_COLS
        sc = self._pipe_target(cols)
        if self.low_quality:                           # flat single-colour pipe
            ipts = [(int(x), int(y)) for x, y in pts]  # no chamfer, no shading
            if len(ipts) >= 2:
                pygame.draw.lines(sc, cols[1], False, ipts, max(2, rad * 2))
            if joint:
                pygame.draw.circle(sc, cols[1], ipts[0], rad + 1)
            return
        raw = [(float(x), float(y)) for x, y in pts]
        if len(raw) >= 3:                              # chamfer the interior corners
            soft = [raw[0]]
            cut = min(7.0, rad * 2.2)
            for i in range(1, len(raw) - 1):
                a, b, c = raw[i - 1], raw[i], raw[i + 1]
                v1 = (b[0] - a[0], b[1] - a[1]); l1 = math.hypot(*v1) or 1.0
                v2 = (c[0] - b[0], c[1] - b[1]); l2 = math.hypot(*v2) or 1.0
                c1, c2 = min(cut, l1 * 0.45), min(cut, l2 * 0.45)
                soft.append((b[0] - v1[0] / l1 * c1, b[1] - v1[1] / l1 * c1))
                soft.append((b[0] + v2[0] / l2 * c2, b[1] + v2[1] / l2 * c2))
            soft.append(raw[-1])
            raw = soft
        ipts = [(int(x), int(y)) for x, y in raw]
        self._tube_run(sc, ipts, rad, cols)
        if joint:                                      # flange on a head boss
            x, y = ipts[0]
            pygame.draw.circle(sc, (18, 20, 26), (x, y), rad + 4)
            pygame.draw.circle(sc, (44, 48, 58), (x, y), rad + 4, 1)
            pygame.draw.circle(sc, cols[0], (x, y), rad + 1)
            pygame.draw.circle(sc, cols[1], (x, y), rad)
            pygame.draw.circle(sc, cols[2], (x - 1, y - 1), max(1, rad // 2))

    @staticmethod
    def _mix(c1, c2, t):
        return (int(c1[0] + (c2[0] - c1[0]) * t), int(c1[1] + (c2[1] - c1[1]) * t),
                int(c1[2] + (c2[2] - c1[2]) * t))

    def _tube_run(self, sc, ipts, rad, cols):
        """Draw a polyline as a glossy ROUND tube: dark casing, a body core that's
        shaded darker on the lower flank, ROUNDED elbow joints (no mitre gaps),
        and a thin bright specular ridge along the top — reads as a bent metal
        pipe instead of a flat coloured ribbon."""
        if len(ipts) < 2:
            if ipts:
                pygame.draw.circle(sc, cols[1], ipts[0], rad)
            return
        dark = self._mix(cols[0], (0, 0, 0), 0.15)
        low = self._mix(cols[1], cols[0], 0.55)          # shaded underside flank
        core = cols[1]
        spec = self._mix(cols[2], (255, 255, 255), 0.15)  # crisp specular ridge
        wid = max(2, rad * 2)
        # casing + rounded joints (close every bend)
        pygame.draw.lines(sc, dark, False, ipts, wid + 2)
        for p in ipts:
            pygame.draw.circle(sc, dark, p, rad + 1)
        # lower flank (offset DOWN a touch) then the lit core over it
        pygame.draw.lines(sc, low, False, [(x, y + 1) for x, y in ipts], wid)
        pygame.draw.lines(sc, core, False, ipts, max(2, wid - 1))
        for p in ipts:
            pygame.draw.circle(sc, core, p, rad)
        # specular ridge: thin, offset up toward the light (upper-left)
        off = max(1, rad // 2)
        pygame.draw.lines(sc, spec, False,
                          [(x - off // 2, y - off) for x, y in ipts], max(1, rad // 2))

    def _collector_slug(self, pt, rad):
        """A brushed merge slug where runners join the collector."""
        cxc, cyc = int(pt[0]), int(pt[1])
        pygame.draw.circle(self.screen, (30, 33, 42), (cxc, cyc), rad * 2 + 2)
        self.screen.blit(self._grad_surf(rad * 4, rad * 4, (150, 156, 170),
                                         (66, 72, 86), rad * 2, gloss=True),
                         (cxc - rad * 2, cyc - rad * 2))
        pygame.draw.circle(self.screen, (34, 38, 48), (cxc, cyc), rad * 2, 2)

    def _draw_headers(self, eng, ports, collector, rad, cols=None):
        """Route every cylinder's exhaust runner (ports: list of (x, y, axis_ang,
        side)) into a shared collector, then cap it with a merged collector pipe —
        short equal-length headers for a hot-V, longer outboard runs otherwise."""
        cxc, cyc = collector
        for px, py, a, _side in ports:
            mx, my = (px + cxc) * 0.5, (py + cyc) * 0.5
            ctrl = (mx + math.cos(a) * rad * 1.6, my)      # bow the runner outward
            self._draw_header_tube((px, py), (cxc, cyc), ctrl, rad, cols=cols, joint=True)
        self._collector_slug(collector, rad)

    def _draw_exhaust_fork(self, ports, spine, handle_end, rad, cols=None, axis="v"):
        """A 4-into-1 exhaust as a right-angle FORK.  axis='v': a vertical collector
        spine at x=spine, tines run horizontally out to it (V banks).  axis='h': a
        horizontal spine at y=spine, tines run vertically UP from the cylinder tops
        (inline).  The merged tines feed a single THICK handle that Z-routes (right
        angles only) to the turbo."""
        cols = cols or self._EXH_COLS
        if not ports:
            return
        spine = int(spine)
        hx, hy = int(handle_end[0]), int(handle_end[1])
        if axis == "v":
            cs = [int(p[1]) for p in ports]
            je = int(min(max(hy, min(cs)), max(cs)))
            for px, py in ports:
                self._draw_ortho_pipe([(int(px), int(py)), (spine, int(py))], rad,
                                      cols, joint=True)
            self._draw_ortho_pipe([(spine, min(min(cs), je)), (spine, max(max(cs), je))],
                                  rad, cols)
            junc = (spine, je)
            pts = [junc]
            if abs(hx - junc[0]) > 2:
                pts.append((hx, junc[1]))
            if abs(hy - pts[-1][1]) > 2:
                pts.append((hx, hy))
        else:
            cs = [int(p[0]) for p in ports]
            je = int(min(max(hx, min(cs)), max(cs)))
            for px, py in ports:
                self._draw_ortho_pipe([(int(px), int(py)), (int(px), spine)], rad,
                                      cols, joint=True)
            self._draw_ortho_pipe([(min(min(cs), je), spine), (max(max(cs), je), spine)],
                                  rad, cols)
            junc = (je, spine)
            pts = [junc]
            if abs(hy - junc[1]) > 2:
                pts.append((junc[0], hy))
            if abs(hx - pts[-1][0]) > 2:
                pts.append((hx, hy))
        if len(pts) > 1:
            self._draw_ortho_pipe(pts, rad + 2, cols)    # the THICK merged handle
        self._collector_slug(junc, rad + 1)

    def _draw_v_timing(self, cxx, mtop, dy, crank_cy, sim, eng):
        """The DOHC valvetrain DRIVE up the front of the V valley: a crank sprocket
        at the bottom, two overhead-cam sprockets at the top (one per bank for a
        DOHC head), a timing chain wrapping them, and — if the engine has VVT — a
        blue cam phaser whose vane advances with rpm."""
        sc = self.screen
        cxx = int(cxx)
        cs = (cxx, int(crank_cy))                      # crank sprocket
        cam_y = int(mtop + dy * 0.2)                   # cam line, high in the valley
        vt = getattr(eng, "valvetrain", "dohc")
        if vt == "ohv":                                # pushrod: cam-in-block, no OHC
            cams = [(cxx, int(crank_cy - dy * 0.7))]
        elif vt == "sohc":
            cams = [(cxx, cam_y)]
        else:                                          # DOHC: one cam each bank
            cams = [(cxx - 20, cam_y), (cxx + 20, cam_y)]
        ang = 0.0 if self.telemetry_mode else sim.crank_angle   # Forza: frozen drive
        cam_ang = -ang * 0.5                            # cams turn at half crank speed
        cr_r, cam_r = 8, 11
        vvt = bool(getattr(eng, "variable_valve", ""))
        adv = vvt * (min(sim.rpm / max(eng.redline_rpm, 1.0), 1.0)) * 0.5  # phaser swing

        def chain(a, b, ra, rb):                       # a BRONZE linked timing chain
            dx, dy2 = b[0] - a[0], b[1] - a[1]
            d = math.hypot(dx, dy2) or 1.0
            ux, uy = dx / d, dy2 / d
            nx, ny = -uy, ux                           # normal, for the two strands
            for s in (1, -1):
                p0 = (a[0] + nx * ra * s, a[1] + ny * ra * s)
                p1 = (b[0] + nx * rb * s, b[1] + ny * rb * s)
                if self.low_quality:                           # flat single-colour belt
                    pygame.draw.line(sc, (198, 158, 86), p0, p1, 3)
                    continue
                pygame.draw.line(sc, (74, 54, 22), p0, p1, 4)      # dark bronze casing
                pygame.draw.line(sc, (198, 158, 86), p0, p1, 2)    # bronze body
                k = 0                                              # link rollers
                while k < d:
                    pygame.draw.circle(sc, (120, 92, 40),
                                       (int(p0[0] + ux * k), int(p0[1] + uy * k)), 1)
                    k += 4

        for c in cams:                                 # chain: crank -> each cam
            chain(cs, c, cr_r, cam_r)
        if len(cams) == 2:
            chain(cams[0], cams[1], cam_r, cam_r)

        def sprocket(c, r, teeth, spin, ring=None):
            pygame.draw.circle(sc, (44, 48, 60), c, r + 2)
            for k in range(teeth):                     # real radial GEAR TEETH
                t = spin + k * 2 * math.pi / teeth
                pygame.draw.line(sc, (158, 164, 180),
                                 (int(c[0] + math.cos(t) * (r - 1)), int(c[1] + math.sin(t) * (r - 1))),
                                 (int(c[0] + math.cos(t) * (r + 2)), int(c[1] + math.sin(t) * (r + 2))), 2)
            if self.low_quality:
                pygame.draw.circle(sc, (134, 140, 154), c, r)   # flat gear/sprocket
            else:
                sc.blit(self._grad_surf(2 * r, 2 * r, (140, 146, 162), (60, 66, 80), r,
                                        gloss=True), (c[0] - r, c[1] - r))
            if ring:
                pygame.draw.circle(sc, ring, c, int(r * 0.7), 2)
            pygame.draw.circle(sc, (40, 44, 54), c, max(2, int(r * 0.28)))

        sprocket(cs, cr_r, 12, ang)
        for c in cams:                                 # cam sprockets: amber-ringed
            sprocket(c, cam_r, 16, cam_ang, ring=(236, 176, 72))
            if vvt:                                    # VVT cam phaser: blue vane that swings
                pygame.draw.circle(sc, (44, 96, 150), c, int(cam_r * 0.55))
                pygame.draw.circle(sc, (120, 196, 255), c, int(cam_r * 0.55), 1)
                va = cam_ang + adv * math.pi
                pygame.draw.line(sc, (150, 210, 255), c,
                                 (int(c[0] + math.cos(va) * cam_r * 0.5),
                                  int(c[1] + math.sin(va) * cam_r * 0.5)), 2)
        camlab = "VVT" if vvt else ("CAM" if cams else "")
        if camlab and cams:
            tag = self.font_small.render(camlab, True,
                                         (120, 196, 255) if vvt else (236, 176, 72))
            sc.blit(tag, (cams[-1][0] + cam_r + 3, cams[-1][1] - 6))

    def _draw_v_manifolds(self, eng, stations, cxx, mtop, dy, bank, length, width, bay):
        """V-engine manifolds on top of the banks, per the cold-V reference: smooth
        curved RED exhaust headers (a 4-into-1 per bank) sweeping into the turbos,
        and GREEN intake — a central plenum 'tree' in the valley for a cold-V
        (intake inboard), or outboard plenums for a hot-V (intake outboard)."""
        cyv = (bay.y + bay.bottom) // 2
        hrad = max(2, int(width * 0.18))
        irad = max(2, int(width * 0.15))
        hot = getattr(eng, "hot_v", False)
        Lout, Rout, Lin, Rin = [], [], [], []
        for s, st in enumerate(stations):
            jy = mtop + dy * (s + 0.5)
            for i in st:
                side = -1.0 if eng.cylinders[i].bank_angle_deg < 0 else 1.0
                a = side * bank
                hxc = cxx + math.sin(a) * length
                hyc = jy - math.cos(a) * length
                ox = math.cos(a) * width * 0.5 * side
                oy = math.sin(a) * width * 0.5 * side
                (Lout if side < 0 else Rout).append((hxc + ox, hyc + oy, a, side))
                (Lin if side < 0 else Rin).append((hxc - ox, hyc - oy, a, side))
        if not hot:
            # COLD-V: exhaust from the OUTER heads to a collector just OUTSIDE each
            # bank (never crossing the cylinders), then on to the outboard turbo if
            # there is one; intake is two valley arms (below).
            turbo = (eng.induction == "turbo")
            aircraft = getattr(eng, "gearbox_type", "") == "aircraft"
            for ports_t, sgn in ((Lout, -1), (Rout, 1)):
                if not ports_t:
                    continue
                pts = [(p[0], p[1]) for p in ports_t]
                ex_x = (min if sgn < 0 else max)(p[0] for p in pts)
                if aircraft:
                    # literal STUB-TO-RAIL (like the radial ring): a straight short
                    # collector rail hugging the bank + one short stub per head, the
                    # gas dumped overboard at the rail's aft end.
                    rail_x = int(ex_x + sgn * 9)
                    ys = [int(p[1]) for p in pts]
                    self._draw_ortho_pipe([(rail_x, min(ys) - 4), (rail_x, max(ys) + 6)],
                                          hrad + 1, self._EXH_COLS)
                    for px, py in pts:
                        self._draw_ortho_pipe([(int(px), int(py)), (rail_x, int(py))],
                                              hrad, self._EXH_COLS, joint=True)
                    self._collector_slug((rail_x, max(ys) + 6), hrad)
                else:
                    spine_x = int(ex_x + sgn * 16)
                    handle = ((bay.x + 48 if sgn < 0 else bay.right - 48), cyv) \
                        if turbo else (spine_x, cyv)
                    self._draw_exhaust_fork(pts, spine_x, handle, hrad, self._EXH_COLS)
            # INTAKE: a valley plenum log at the top feeding N SEPARATE thin runners
            # — one per cylinder — so the per-cylinder distribution reads clearly
            # (the plenum stays high in the open part of the vee; runners are thin).
            inner = Lin + Rin
            brad = max(2, irad - 1)
            ys = [int(p[1]) for p in inner]
            ptop = (min(ys) if ys else mtop) - 12
            xs_in = [int(p[0]) for p in inner]
            plx0, plx1 = (min(xs_in) - 4 if xs_in else cxx), (max(xs_in) + 4 if xs_in else cxx)
            self._draw_header_tube((plx0, ptop), (plx1, ptop),     # the plenum log
                                   ((plx0 + plx1) * 0.5, ptop - 2), irad + 1,
                                   cols=self._INT_COLS)
            for px, py, a, side in inner:                          # one runner per cyl
                self._draw_header_tube((px, py), (int(px), ptop),
                                       (int(px) - 4 * side, (py + ptop) * 0.5), brad,
                                       cols=self._INT_COLS, joint=True)
            self._collector_slug((int(cxx), ptop), irad + 1)
            self._draw_header_tube((int(cxx), ptop), (int(cxx), int(ptop - dy * 0.45)),
                                   (int(cxx), int(ptop - dy * 0.22)), irad + 1,
                                   cols=self._INT_COLS)
        else:
            # HOT-V: exhaust from the INNER heads to the central valley turbos;
            # intake is the OUTER inverted-U with the top intercooler.
            intL, intR = Lout, Rout
            for ports, tgt in ((Lin, (cxx - 12, cyv + 6)), (Rin, (cxx + 12, cyv - 5))):
                if ports:
                    self._draw_headers(eng, ports, tgt, hrad, cols=self._EXH_COLS)
            sc = self.screen
            railL = int(min(p[0] for p in intL) - 14) if intL else bay.x + 40
            railR = int(max(p[0] for p in intR) + 14) if intR else bay.right - 40
            railL = max(railL, bay.x + 16); railR = min(railR, bay.right - 16)
            top_y = mtop - dy * 0.15
            for px, py, a, side in intL:
                self._draw_header_tube((px, py), (railL, py), ((px + railL) * 0.5, py - 6),
                                       irad, cols=self._INT_COLS, joint=True)
            for px, py, a, side in intR:
                self._draw_header_tube((px, py), (railR, py), ((px + railR) * 0.5, py - 6),
                                       irad, cols=self._INT_COLS, joint=True)
            yL = [p[1] for p in intL]
            yR = [p[1] for p in intR]
            if yL:
                self._draw_header_tube((railL, max(yL)), (railL, top_y),
                                       (railL - 5, (max(yL) + top_y) * 0.5), irad,
                                       cols=self._INT_COLS)
            if yR:
                self._draw_header_tube((railR, max(yR)), (railR, top_y),
                                       (railR + 5, (max(yR) + top_y) * 0.5), irad,
                                       cols=self._INT_COLS)
            ic_w = int(width * 2.4)
            ic = pygame.Rect(int(cxx - ic_w // 2), int(top_y) - 7, ic_w, 14)
            self._draw_header_tube((railL, top_y), (ic.left, top_y),
                                   ((railL + ic.left) * 0.5, top_y - 4), irad,
                                   cols=self._INT_COLS)
            self._draw_header_tube((ic.right, top_y), (railR, top_y),
                                   ((ic.right + railR) * 0.5, top_y - 4), irad,
                                   cols=self._INT_COLS)
            sc.blit(self._grad_surf(ic.w, ic.h, (152, 158, 172), (70, 76, 90), 3),
                    ic.topleft)
            for fx in range(ic.x + 3, ic.right - 2, 4):       # cooler fins
                pygame.draw.line(sc, (44, 48, 58), (fx, ic.y + 2), (fx, ic.bottom - 2), 1)
            pygame.draw.rect(sc, (40, 44, 54), ic, 1, border_radius=3)
            # the hot-V intake feeds from its outboard plenum; no central trunk

    def _draw_crank_diagram(self, cx, cy, r, plane, angle):
        """A small END-ON crankshaft view showing the crankpin phase: a FLAT-plane
        crank's throws sit at 0/180 (a straight bar) while a CROSS-plane crank's
        sit at 0/90/180/270 (an X) — the instantly-recognisable difference behind
        the flat-plane 'scream' vs the cross-plane 'burble'.  Spins with the crank."""
        sc = self.screen
        cx, cy, r = int(cx), int(cy), int(r)
        pins = [0.0, math.pi] if plane == "flat" else [0.0, math.pi / 2, math.pi,
                                                       3 * math.pi / 2]
        # recessed bearing housing
        pygame.draw.circle(sc, (13, 14, 18), (cx, cy), r + 4)
        pygame.draw.circle(sc, (44, 48, 58), (cx, cy), r + 4, 2)
        pr = r * 0.55
        for pa in pins:
            a = angle + pa
            px, py = cx + math.cos(a) * pr, cy + math.sin(a) * pr
            ox, oy = cx - math.cos(a) * pr * 0.92, cy - math.sin(a) * pr * 0.92
            # counterweight blob opposite the pin
            pygame.draw.circle(sc, (58, 63, 76), (int(ox), int(oy)), max(3, int(r * 0.36)))
            pygame.draw.circle(sc, (88, 94, 110), (int(ox), int(oy)), max(3, int(r * 0.36)), 1)
            # web + rod journal (lit metal pin)
            pygame.draw.line(sc, (104, 110, 126), (cx, cy), (int(px), int(py)),
                             max(2, int(r * 0.2)))
            pygame.draw.circle(sc, (150, 158, 174), (int(px), int(py)), max(2, int(r * 0.22)))
            pygame.draw.circle(sc, (30, 33, 42), (int(px), int(py)), max(2, int(r * 0.22)), 1)
            pygame.draw.circle(sc, (240, 244, 252), (int(px - 1), int(py - 1)), 1)
        # main journal hub
        pygame.draw.circle(sc, (172, 180, 196), (cx, cy), max(3, int(r * 0.28)))
        pygame.draw.circle(sc, (40, 44, 54), (cx, cy), max(3, int(r * 0.28)), 1)
        pygame.draw.circle(sc, (245, 248, 255), (cx - 1, cy - 1), 1)
        lab = self.tr("Flat-plane Crank" if plane == "flat" else "Cross-plane Crank")
        t = self.font_small.render(lab, True, (150, 158, 174))
        sc.blit(t, (int(cx - r), cy + r + 7))      # left-aligned: stays inside the bay

    def _draw_rotary(self, rect, top, bottom):
        """Wankel-rotor visualiser: a 2-lobe epitrochoid housing with a Reuleaux
        triangular rotor orbiting eccentrically (spinning at 1/3 shaft speed),
        face recesses, apex seals, spark plugs and a firing-chamber glow.  One
        housing per ROTOR (the model fires twice per rotor, so rotors = cyl/2)."""
        sim, eng = self.sim, self.sim.engine
        n = max(1, eng.num_cylinders // 2)
        col_w = rect.width / n
        R = min(col_w * 0.18, (bottom - top) * 0.21)   # rotor housing (kept compact)
        sx = 1.15                                      # housing is wider than tall
        e = R * 0.15                                   # eccentricity
        cy = (top + bottom) / 2.0
        shaft0 = sim.crank_angle
        TAU3 = 2.0943951                               # 120 deg
        def troch(cx, cy, a):
            return (cx + (R * math.cos(a) + e * math.cos(3 * a)) * sx,
                    cy + R * math.sin(a) + e * math.sin(3 * a))

        xs = []                                        # rotor-housing centres
        for i in range(n):
            cx = rect.x + col_w * (i + 0.5)
            xs.append(cx)
            shaft = shaft0 + i * (2.0 * math.pi / n)
            # --- epitrochoid housing (2-lobe peanut), recessed chamber ---
            hull = [troch(cx, cy, 2.0 * math.pi * k / 120.0) for k in range(120)]
            pygame.draw.polygon(self.screen, (24, 26, 32), hull)        # chamber void
            pygame.draw.polygon(self.screen, (164, 170, 184), hull, 3)  # polished wall
            pygame.draw.polygon(self.screen, (70, 75, 88), hull, 1)
            # intake/exhaust ports (side notches) + two spark plugs at the waist
            for sp in (-0.5, 0.5):
                spx, spy = cx + sx * R * sp * 0.12, cy - R * 0.86
                pygame.draw.circle(self.screen, (40, 43, 50), (int(spx), int(spy)), 4)
                pygame.draw.circle(self.screen, (210, 180, 90), (int(spx), int(spy)), 2)
            # --- rotor: 3 apexes ALWAYS riding the housing wall (epitrochoid) ---
            phase = shaft * 0.5
            apex = [troch(cx, cy, phase + k * TAU3) for k in range(3)]
            rcx = sum(p[0] for p in apex) / 3.0
            rcy = sum(p[1] for p in apex) / 3.0
            # combustion glow in the working chamber straddling the spark plugs
            j = min(2 * i + 1, eng.num_cylinders - 1)
            press = max(sim.cylinder_pressure[2 * i], sim.cylinder_pressure[j])
            glow = (min(max(press - 101325.0, 0.0) / (5.0 * 101325.0), 1.0)
                    if sim.ignition_on and not sim._fuel_cut else 0.0)
            if glow > 0.03 and not self.low_quality:      # low-Q: no combustion flash
                gs = self._flash_surf(R * 0.55, glow)
                self.screen.blit(gs, (int(cx) - gs.get_width() // 2,
                                      int(cy - R * 0.5) - gs.get_height() // 2))
            body = self._reuleaux(apex)
            sc = self.screen
            lx, ly = -0.5, -0.62                          # screen upper-left light
            rb = max(8, int(R * 1.3))                      # rotor bounding radius
            # 1) soft drop shadow cast onto the housing floor (down-right)
            shp = pygame.Surface((rb * 2, rb * 2), pygame.SRCALPHA)
            pygame.draw.polygon(shp, (0, 0, 0, 120),
                                [(ax - rcx + rb, ay - rcy + rb) for ax, ay in body])
            shp = pygame.transform.smoothscale(shp, (rb * 2, rb * 2))
            sc.blit(shp, (int(rcx - rb + 3), int(rcy - rb + 4)))
            # 2) smooth rounded-metal gradient (dark rim -> lit crown): many
            #    interpolated stops, each shrunk toward the centre and nudged
            #    toward the light so the banding melts into a continuous sheen
            c0, c1 = (70, 76, 90), (228, 234, 246)
            STOPS = 11
            for s in range(STOPS):
                t = s / (STOPS - 1)
                scl = 1.0 - 0.82 * t
                off = 0.46 * t
                col = tuple(int(c0[j] + (c1[j] - c0[j]) * (t ** 0.85)) for j in range(3))
                ox, oy = lx * R * off, ly * R * off
                pts = [(rcx + (ax - rcx) * scl + ox, rcy + (ay - rcy) * scl + oy)
                       for ax, ay in apex]
                pygame.draw.polygon(sc, col, self._reuleaux(pts))
            # 3) brushed-metal grain masked to the rotor face
            br = self._brushed(rb * 2, rb * 2, 0).copy()
            msk = pygame.Surface((rb * 2, rb * 2), pygame.SRCALPHA)
            pygame.draw.polygon(msk, (255, 255, 255, 255),
                                [(ax - rcx + rb, ay - rcy + rb) for ax, ay in body])
            br.blit(msk, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            sc.blit(br, (int(rcx - rb), int(rcy - rb)))
            # 4) ambient-occlusion dark rim + a sharp specular pip near the crown
            pygame.draw.polygon(sc, (34, 38, 48), body, 2)
            pygame.draw.circle(sc, (245, 248, 255),
                               (int(rcx + lx * R * 0.5), int(rcy + ly * R * 0.5)),
                               max(2, int(R * 0.1)))
            # combustion-dish recess on each rotor flank
            for k in range(3):
                m = ((apex[k][0] + apex[(k + 1) % 3][0]) / 2,
                     (apex[k][1] + apex[(k + 1) % 3][1]) / 2)
                fx, fy = rcx + (m[0] - rcx) * 0.5, rcy + (m[1] - rcy) * 0.5
                pygame.draw.circle(self.screen, (86, 92, 108),
                                   (int(fx), int(fy)), int(R * 0.18))
            # internal ring gear (the rotor's phasing gear)
            pygame.draw.circle(self.screen, (46, 50, 60), (int(rcx), int(rcy)), int(R * 0.30))
            pygame.draw.circle(self.screen, (84, 90, 104), (int(rcx), int(rcy)), int(R * 0.30), 1)
            for k in range(12):
                ga = shaft + k * (2 * math.pi / 12)
                pygame.draw.circle(self.screen, (60, 64, 76),
                                   (int(rcx + R * 0.30 * math.cos(ga)),
                                    int(rcy + R * 0.30 * math.sin(ga))), 1)
            # apex seals: a bright bar riding each apex on the wall
            for v in apex:
                ux, uy = v[0] - rcx, v[1] - rcy
                ln = math.hypot(ux, uy) or 1.0
                px, py = -uy / ln * 4, ux / ln * 4
                pygame.draw.line(self.screen, (220, 226, 238),
                                 (v[0] + px, v[1] + py), (v[0] - px, v[1] - py), 3)
                pygame.draw.circle(self.screen, (216, 222, 234), (int(v[0]), int(v[1])), 2)
            # --- eccentric shaft: fixed centre gear + orbiting journal ---
            pygame.draw.circle(self.screen, (54, 58, 68), (int(cx), int(cy)), int(R * 0.16))
            pygame.draw.circle(self.screen, (90, 96, 110), (int(cx), int(cy)), int(R * 0.16), 1)
            pygame.draw.line(self.screen, (60, 64, 74), (int(cx), int(cy)),
                             (int(rcx), int(rcy)), 2)
            pygame.draw.circle(self.screen, (200, 206, 220), (int(rcx), int(rcy)), 4)
            pygame.draw.circle(self.screen, (40, 43, 50), (int(rcx), int(rcy)), 4, 1)
            lbl = self.font_small.render(f"R{i + 1}", True, DIM)
            self.screen.blit(lbl, (int(cx) - lbl.get_width() // 2, int(cy + R * 1.1)))

        # Per-rotor INTAKE trumpets + EXHAUST headers so the rotary reads as a
        # complete induction/exhaust system — the NA 4-rotor 787B used to show bare
        # housings; now it gets four trumpets + a 4-into-1 collector like the RX-7.
        if xs:
            self._begin_pipe_layers()
            irad = max(2, int(R * 0.20)); hrad = max(2, int(R * 0.22))
            plen_y = int(cy - R * 1.95)
            rail_y = int(cy + R * 1.85)
            x0, x1 = int(min(xs) - 8), int(max(xs) + 8)
            # INTAKE: a plenum log over the rotors with a trumpet down to each
            self._draw_ortho_pipe([(x0, plen_y), (x1, plen_y)], irad + 1, self._INT_COLS)
            for cxi in xs:
                self._draw_ortho_pipe([(int(cxi), plen_y), (int(cxi), int(cy - R))],
                                      irad, self._INT_COLS, joint=True)
            # EXHAUST: a peripheral header (offset off centre) down to a 4-1 rail
            for cxi in xs:
                ex = int(cxi + R * 0.5)
                self._draw_ortho_pipe([(ex, int(cy + R * 0.9)), (ex, rail_y)],
                                      hrad, self._EXH_COLS, joint=True)
            self._draw_ortho_pipe([(x0, rail_y), (x1, rail_y)], hrad + 1, self._EXH_COLS)
            self._collector_slug((x1, rail_y), hrad + 1)
            if eng.induction == "na":     # NA: hand the outlet to the cat/muffler chain
                self._exh_exit = (x1, rail_y)
            self._end_pipe_layers()

    def _air_gauge(self, cx, cy, r, frac, label, value, danger=False):
        """An old-school round aircraft instrument: metal bezel, black face, tick
        marks, a swept needle (270 deg) and a digital readout."""
        cx, cy = int(cx), int(cy)
        frac = min(max(frac, 0.0), 1.0)
        if self.low_quality:                          # flat dial: face + needle only
            pygame.draw.circle(self.screen, (40, 44, 52), (cx, cy), r + 3)
            pygame.draw.circle(self.screen, (18, 19, 23), (cx, cy), r)
            a = math.radians(135 + frac * 270)
            pygame.draw.line(self.screen, (235, 92, 80) if danger else (240, 206, 96),
                             (cx, cy), (cx + (r - 6) * math.cos(a),
                                        cy + (r - 6) * math.sin(a)), 2)
            pygame.draw.circle(self.screen, (120, 126, 140), (cx, cy), 3)
            lab = self.font_small.render(label, True, DIM)
            self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))
            val = self.font_small.render(value, True, INK)
            self.screen.blit(val, (cx - val.get_width() // 2, int(cy + r * 0.4)))
            return
        # recessed seat: a dark shadow ring (sunk into the brushed slab) with a
        # bottom-right catch-light, so the instrument reads as INSET, not stuck on
        pygame.draw.circle(self.screen, (22, 24, 28), (cx, cy), r + 7)
        pygame.draw.circle(self.screen, (78, 84, 96), (cx + 1, cy + 2), r + 7, 1)
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

    def _draw_turbo(self, cx, cy, r, spin, load, label=True):
        """A turbocharger: scroll/volute housing with a spinning turbine wheel,
        glowing hotter as it makes boost."""
        cx, cy = int(cx), int(cy)
        if self.low_quality:
            # flat single-colour turbo; spin kept (cheap); heat glow is BINARY
            pygame.draw.circle(self.screen, (54, 58, 68), (cx, cy), r)
            pygame.draw.circle(self.screen, (120, 126, 138), (cx, cy), r, 1)
            hub = max(3, int(r * 0.18))
            for k in range(6):
                a = spin + k * (2 * math.pi / 6)
                pygame.draw.line(self.screen, (150, 156, 168), (cx, cy),
                                 (int(cx + r * 0.7 * math.cos(a)),
                                  int(cy + r * 0.7 * math.sin(a))), 2)
            if load > 0.25:                                # binary on/off orange
                pygame.draw.circle(self.screen, (255, 140, 50), (cx, cy), int(r * 0.45))
            pygame.draw.circle(self.screen, (90, 96, 108), (cx, cy), hub)
            if label:
                lab = self.font_small.render(self.tr("TURBO"), True, DIM)
                self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))
            return
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
        if label:
            lab = self.font_small.render(self.tr("TURBO"), True, DIM)
            self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))

    def _draw_blower(self, cx, cy, r, spin, load, centri=False, label=True):
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
        if label:
            lab = self.font_small.render(self.tr(txt), True, DIM)
            self.screen.blit(lab, (cx - lab.get_width() // 2, cy + r + 5))

    def _bay_turbo(self, cx, cy, r, spin, load, electric=False, twin_scroll=False,
                   inlet_dir=None):
        """A detailed turbocharger for the engine bay: brushed-alloy snail volute,
        a spinning compressor wheel, a discharge snout, a hot-side glow and a
        chrome centre — lit & brushed for the Apple-grade look.  ``twin_scroll``
        draws a divided housing (a dividing rib + a second inlet throat).
        ``inlet_dir`` (radians) draws a short RED turbine inlet throat at that
        angle so the exhaust is seen flowing into the volute."""
        cx, cy, r = int(cx), int(cy), int(r)
        sc = self.screen
        if self.low_quality:                          # flat turbo, NO cast shadow
            self._draw_turbo(cx, cy, r, spin, load, label=False)
            if inlet_dir is not None:                 # short red inlet throat
                ca, sa = math.cos(inlet_dir), math.sin(inlet_dir)
                pygame.draw.line(sc, (178, 64, 40),
                                 (cx + ca * (r - 1), cy + sa * (r - 1)),
                                 (cx + ca * (r + 10), cy + sa * (r + 10)), 6)
            self._turbo_pts.append((cx, cy, r))       # for downstream piping
            return
        # soft cast shadow grounding the turbo on whatever is behind it
        shp = pygame.Surface((2 * r + 12, 2 * r + 12), pygame.SRCALPHA)
        pygame.draw.circle(shp, (0, 0, 0, 110), (r + 6, r + 6), r + 2)
        shp = pygame.transform.smoothscale(shp, (2 * r + 12, 2 * r + 12))
        sc.blit(shp, (cx - r - 2, cy - r + 2))
        # a crisp dark gap ring isolates the turbo from the conrods/headers behind
        # it (a little black breathing room so the linework doesn't merge)
        pygame.draw.circle(sc, (15, 16, 21), (cx, cy), r + 3)
        if inlet_dir is not None:                     # red turbine inlet throat
            ca, sa = math.cos(inlet_dir), math.sin(inlet_dir)
            ix, iy = cx + ca * (r - 1), cy + sa * (r - 1)
            ox, oy = cx + ca * (r + 10), cy + sa * (r + 10)
            pygame.draw.line(sc, (50, 16, 12), (ix, iy), (ox, oy), 9)
            pygame.draw.line(sc, (178, 64, 40), (ix, iy), (ox, oy), 6)
            pygame.draw.line(sc, (230, 122, 86), (ix, iy), (ox, oy), 2)
            pygame.draw.circle(sc, (60, 20, 14), (int(ox), int(oy)), 5)
            pygame.draw.circle(sc, (200, 92, 60), (int(ox), int(oy)), 5, 1)
        # compressor housing — brushed alloy disc
        sc.blit(self._grad_surf(2 * r, 2 * r, (152, 158, 172), (68, 74, 88), r, gloss=True),
                (cx - r, cy - r))
        sc.blit(self._brushed(2 * r, 2 * r, r), (cx - r, cy - r))
        pygame.draw.circle(sc, (32, 35, 44), (cx, cy), r, 2)
        # smooth specular bloom (blurred) on the upper-left + a bright rim arc so
        # the alloy reads as polished, not pixel-grainy
        bloom = pygame.Surface((max(r // 2, 4), max(r // 2, 4)), pygame.SRCALPHA)
        pygame.draw.circle(bloom, (255, 255, 255, 110),
                           (int(bloom.get_width() * 0.4), int(bloom.get_height() * 0.36)),
                           max(2, bloom.get_width() // 3))
        sc.blit(pygame.transform.smoothscale(bloom, (2 * r, 2 * r)), (cx - r, cy - r),
                special_flags=pygame.BLEND_RGBA_ADD)
        rimr = pygame.Rect(cx - r + 1, cy - r + 1, 2 * r - 2, 2 * r - 2)
        pygame.draw.arc(sc, (214, 220, 232), rimr, math.radians(120), math.radians(220), 2)
        # volute scroll (the snail) — two tapering wrapped arcs
        for rad, wd, col in ((r * 0.92, 4, (58, 62, 76)), (r * 0.72, 3, (46, 50, 62))):
            rr = pygame.Rect(cx - int(rad), cy - int(rad), int(2 * rad), int(2 * rad))
            pygame.draw.arc(sc, col, rr, math.radians(28), math.radians(332), int(wd))
        if twin_scroll:
            # divided turbine housing: a second, inner scroll wrap + two split
            # inlet throats on the hot side and a dividing rib between them
            rr = pygame.Rect(cx - int(r * 0.82), cy - int(r * 0.82),
                             int(r * 1.64), int(r * 1.64))
            pygame.draw.arc(sc, (52, 56, 70), rr, math.radians(40), math.radians(300), 2)
            ix, iy = cx - int(r * 0.78), cy + int(r * 0.30)
            pygame.draw.rect(sc, (120, 126, 140), (ix - 8, iy - 9, 12, 18), border_radius=2)
            pygame.draw.rect(sc, (38, 42, 52), (ix - 8, iy - 9, 12, 18), 1, border_radius=2)
            pygame.draw.line(sc, (46, 50, 62), (ix - 8, iy), (ix + 4, iy), 2)  # divider
        # compressor discharge snout (tangent, top-right)
        sx, sy = cx + int(r * 0.6), cy - int(r * 0.66)
        pygame.draw.rect(sc, (126, 132, 146), (sx - 5, sy - 6, 15, 13), border_radius=3)
        pygame.draw.rect(sc, (38, 42, 52), (sx - 5, sy - 6, 15, 13), 1, border_radius=3)
        # blow-off valve on the charge discharge — a short GREEN relief branch
        bvx, bvy = sx + 10, sy - 8
        pygame.draw.line(sc, (24, 60, 32), (sx + 6, sy - 2), (bvx, bvy), 4)
        pygame.draw.line(sc, (70, 168, 100), (sx + 6, sy - 2), (bvx, bvy), 2)
        pygame.draw.circle(sc, (120, 130, 144), (bvx, bvy), 3)
        pygame.draw.circle(sc, (30, 34, 42), (bvx, bvy), 3, 1)
        # wastegate dump off the turbine volute — a short RED branch + actuator can
        wgx, wgy = cx - int(r * 0.5), cy + r + 7
        pygame.draw.line(sc, (50, 16, 12), (cx - int(r * 0.3), cy + r - 2), (wgx, wgy), 4)
        pygame.draw.line(sc, (176, 62, 38), (cx - int(r * 0.3), cy + r - 2), (wgx, wgy), 2)
        pygame.draw.circle(sc, (96, 102, 116), (wgx, wgy), 4)
        pygame.draw.circle(sc, (34, 38, 48), (wgx, wgy), 4, 1)
        # spinning compressor wheel
        ir = int(r * 0.52)
        pygame.draw.circle(sc, (20, 22, 28), (cx, cy), ir)
        hub = max(3, int(r * 0.18))
        for k in range(11):
            a = spin + k * (2 * math.pi / 11)
            x1, y1 = cx + hub * math.cos(a), cy + hub * math.sin(a)
            x2 = cx + ir * 0.9 * math.cos(a + 0.5)
            y2 = cy + ir * 0.9 * math.sin(a + 0.5)
            pygame.draw.line(sc, (188, 194, 208), (x1, y1), (int(x2), int(y2)), 2)
        if load > 0.02:                               # hot-side glow: a real heat
            # gradient (transparent rim -> solid white-hot core), the same radial
            # bloom used for combustion, so the turbine looks like it's glowing hot
            gs = self._flash_surf(int(r * 0.5), min(load, 1.0))
            sc.blit(gs, (cx - gs.get_width() // 2, cy - gs.get_height() // 2))
        sc.blit(self._grad_surf(2 * hub, 2 * hub, (226, 232, 242), (110, 118, 132),
                                hub, gloss=True), (cx - hub, cy - hub))
        pygame.draw.circle(sc, (60, 64, 74), (cx, cy), hub, 1)
        pygame.draw.circle(sc, (255, 255, 255), (cx - max(1, hub // 3),
                                                 cy - max(1, hub // 3)), max(1, hub // 4))
        if electric:                                  # e-turbo: a blue stator ring
            pygame.draw.circle(sc, (88, 178, 255), (cx, cy), r + 2, 2)
        self._turbo_pts.append((cx, cy, r))           # for downstream piping

    def _bay_blower(self, cx, cy, r, spin, load, centri=False):
        """A detailed supercharger for the bay: a Roots blower (brushed case, two
        meshing 3-lobe rotors, an intake hat and a driven pulley + belt), or a
        centrifugal impeller."""
        cx, cy = int(cx), int(cy)
        sc = self.screen
        if centri:
            self._bay_turbo(cx, cy, r, spin, load)
            return
        w, h = int(r * 2.6), int(r * 1.6)
        rect = pygame.Rect(cx - w // 2, cy - h // 2, w, h)
        # soft cast shadow grounding the blower (same depth cue as the turbos)
        shp = pygame.Surface((w + 14, h + 14), pygame.SRCALPHA)
        pygame.draw.rect(shp, (0, 0, 0, 115), (5, 7, w, h), border_radius=10)
        sc.blit(pygame.transform.smoothscale(shp, (w + 14, h + 14)), (rect.x - 5, rect.y - 2))
        sc.blit(self._grad_surf(w, h, (166, 172, 186), (58, 64, 78), 8, gloss=True),
                rect.topleft)
        sc.blit(self._brushed(w, h, 8), rect.topleft)
        # cast cooling ribs down the case face
        for rx in range(rect.x + 7, rect.right - 5, 5):
            pygame.draw.line(sc, (44, 48, 58), (rx, rect.y + int(h * 0.42)),
                             (rx, rect.bottom - 4), 1)
            pygame.draw.line(sc, (150, 156, 170), (rx + 1, rect.y + int(h * 0.42)),
                             (rx + 1, rect.bottom - 4), 1)
        pygame.draw.rect(sc, (30, 33, 42), rect, 2, border_radius=8)
        # specular bloom + bright top-edge highlight = wet polished alloy
        sp = pygame.Surface((max(w // 4, 4), max(h // 4, 4)), pygame.SRCALPHA)
        pygame.draw.ellipse(sp, (255, 255, 255, 120),
                            (int(sp.get_width() * 0.13), int(sp.get_height() * 0.07),
                             int(sp.get_width() * 0.74), int(sp.get_height() * 0.32)))
        sc.blit(pygame.transform.smoothscale(sp, (w, h)), rect.topleft,
                special_flags=pygame.BLEND_RGBA_ADD)
        pygame.draw.line(sc, (228, 233, 244), (rect.x + 6, rect.y + 2),
                         (rect.right - 6, rect.y + 2), 1)
        hat = pygame.Rect(cx - int(r * 0.55), rect.y - 9, int(r * 1.1), 11)   # intake hat
        sc.blit(self._grad_surf(hat.w, hat.h, (150, 156, 170), (58, 64, 78), 3,
                                gloss=True), hat.topleft)
        pygame.draw.line(sc, (224, 229, 240), (hat.x + 3, hat.y + 2),
                         (hat.right - 3, hat.y + 2), 1)
        pygame.draw.rect(sc, (38, 42, 52), hat, 1, border_radius=3)
        rb = int(h * 0.34)                                # two meshing rotors
        for ox, sgn in ((-r * 0.62, 1), (r * 0.62, -1)):
            bx = cx + int(ox)
            pygame.draw.circle(sc, (20, 22, 28), (bx, cy), rb)
            # polished rotor end-plate: radial gloss gradient + rim
            ep = self._grad_surf(rb * 2, rb * 2, (150, 156, 170), (40, 44, 54), rb,
                                 gloss=True)
            mask = pygame.Surface((rb * 2, rb * 2), pygame.SRCALPHA)
            pygame.draw.circle(mask, (255, 255, 255, 255), (rb, rb), rb)
            ep = ep.copy()
            ep.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            sc.blit(ep, (bx - rb, cy - rb))
            pygame.draw.circle(sc, (26, 28, 36), (bx, cy), rb, 1)
            for k in range(3):
                a = sgn * spin + k * (2 * math.pi / 3)
                pygame.draw.line(sc, (206, 212, 226), (bx, cy),
                                 (int(bx + rb * 0.86 * math.cos(a)),
                                  int(cy + rb * 0.86 * math.sin(a))), 3)
                pygame.draw.line(sc, (60, 64, 76), (bx, cy),
                                 (int(bx + rb * 0.86 * math.cos(a + 0.12)),
                                  int(cy + rb * 0.86 * math.sin(a + 0.12))), 1)
            sc.blit(self._grad_surf(8, 8, (236, 240, 250), (110, 118, 132), 4,
                                    gloss=True), (bx - 4, cy - 4))
            pygame.draw.circle(sc, (255, 255, 255), (bx - 1, cy - 1), 1)
        px = rect.x - 7                                   # drive pulley + belt
        pygame.draw.circle(sc, (54, 58, 70), (px, cy), 6)
        pygame.draw.circle(sc, (120, 126, 140), (px, cy), 6, 1)
        for k in range(8):
            a = spin + k * (2 * math.pi / 8)
            pygame.draw.line(sc, (84, 90, 104), (px, cy),
                             (int(px + 5 * math.cos(a)), int(cy + 5 * math.sin(a))), 1)
        pygame.draw.line(sc, (28, 30, 38), (px, cy - 6), (rect.x, rect.y + 3), 2)
        pygame.draw.line(sc, (28, 30, 38), (px, cy + 6), (rect.x, rect.bottom - 3), 2)
        if load > 0.02:
            gs = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.ellipse(gs, (90, 170, 255, int(90 * min(load, 1.0))),
                                (int(w * 0.2), int(h * 0.2), int(w * 0.6), int(h * 0.6)))
            sc.blit(gs, rect.topleft)

    def _draw_ancillary(self, cx, cy, kind, throttle=0.0):
        """A small icon for a forced-induction ancillary part."""
        sc = self.screen
        if self.low_quality:                             # flat single-colour box
            cols = {"tb": (110, 118, 132), "ic": (110, 118, 132),
                    "cat": (132, 138, 152), "dpf": (120, 122, 130),
                    "scr": (120, 122, 130), "def": (110, 150, 196),
                    "res": (118, 124, 138), "muf": (118, 124, 138),
                    "tail": (90, 96, 108), "wg": (150, 92, 60), "bov": (110, 140, 200)}
            r = pygame.Rect(cx - 17, cy - 8, 34, 16)
            pygame.draw.rect(sc, cols.get(kind, (112, 120, 134)), r, 0, border_radius=4)
            pygame.draw.rect(sc, (40, 44, 54), r, 1, border_radius=4)
            if kind == "tb":                             # keep the butterfly (throttle)
                ang = math.radians(90.0 - 80.0 * min(max(throttle, 0.0), 1.0))
                dx, dy = math.cos(ang) * 12, math.sin(ang) * 6
                pygame.draw.line(sc, (206, 212, 224),
                                 (cx - dx, cy - dy), (cx + dx, cy + dy), 3)
            return
        # soft contact shadow so every unit SITS in the bay instead of floating
        sh = pygame.Surface((48, 10), pygame.SRCALPHA)
        pygame.draw.ellipse(sh, (0, 0, 0, 70), (2, 2, 44, 6))
        sc.blit(sh, (cx - 24, cy + 8))
        if kind == "tb":                                 # throttle body: live butterfly
            r = pygame.Rect(cx - 15, cy - 10, 30, 20)
            sc.blit(self._grad_surf(r.w, r.h, (128, 136, 150), (58, 64, 78), 6), r.topleft)
            pygame.draw.rect(sc, (40, 44, 54), r, 1, border_radius=6)
            pygame.draw.circle(sc, (20, 22, 28), (cx, cy), 8)                # round bore
            pygame.draw.circle(sc, (166, 172, 186), (cx, cy), 8, 1)          # bore lip
            ang = math.radians(90.0 - 80.0 * min(max(throttle, 0.0), 1.0))   # closed->open
            dx, dy = math.cos(ang) * 7, math.sin(ang) * 7
            pygame.draw.line(sc, (216, 222, 234), (cx - dx, cy - dy), (cx + dx, cy + dy), 3)
            pygame.draw.circle(sc, (96, 102, 116), (cx, cy), 2)              # spindle
            pygame.draw.rect(sc, (86, 92, 106), (r.x - 3, cy - 5, 3, 10))    # TPS pod
        elif kind == "ic":                               # intercooler: core + end tanks
            r = pygame.Rect(cx - 20, cy - 9, 40, 18)
            sc.blit(self._grad_surf(r.w - 12, r.h, (150, 158, 172), (84, 92, 106), 2),
                    (r.x + 6, r.y))
            for k in range(-2, 7):                       # diagonal intercooler hatch
                x0 = r.x + 6 + k * 5
                pygame.draw.line(sc, (66, 72, 86), (x0, r.bottom - 2),
                                 (x0 + 6, r.y + 2), 1)
            for tx in (r.x, r.right - 6):                # cast end tanks
                pygame.draw.rect(sc, (52, 56, 68), (tx, r.y - 1, 6, r.h + 2),
                                 border_radius=3)
            pygame.draw.rect(sc, (150, 156, 170), r, 1, border_radius=3)
        elif kind == "cat":                              # catalytic: tapered capsule
            body = [(cx - 19, cy), (cx - 13, cy - 8), (cx + 13, cy - 8),
                    (cx + 19, cy), (cx + 13, cy + 8), (cx - 13, cy + 8)]
            pygame.draw.polygon(sc, (108, 114, 128), body)
            pygame.draw.polygon(sc, (140, 146, 160),  # lit upper half
                                [(cx - 19, cy), (cx - 13, cy - 8), (cx + 13, cy - 8),
                                 (cx + 19, cy)])
            pygame.draw.polygon(sc, (46, 50, 60), body, 1)
            win = pygame.Rect(cx - 8, cy - 4, 16, 8)     # honeycomb window
            pygame.draw.rect(sc, (222, 176, 96), win, border_radius=2)
            for hx in range(win.x + 2, win.right - 1, 4):
                for hy in range(win.y + 2, win.bottom - 1, 4):
                    pygame.draw.circle(sc, (150, 108, 42), (hx, hy), 1)
            pygame.draw.line(sc, (170, 176, 190),        # heat-shield crease
                             (cx - 12, cy - 6), (cx + 12, cy - 6), 1)
        elif kind == "dpf":                              # diesel particulate filter
            r = pygame.Rect(cx - 17, cy - 8, 34, 16)
            sc.blit(self._grad_surf(r.w, r.h, (120, 122, 130), (58, 60, 68), 6), r.topleft)
            for fx in range(r.x + 3, r.right - 2, 2):    # fine soot channels
                pygame.draw.line(sc, (40, 42, 48), (fx, r.y + 2), (fx, r.bottom - 2), 1)
            pygame.draw.rect(sc, (150, 152, 162), r, 1, border_radius=6)
        elif kind == "scr":                              # SCR catalyst + DEF injector
            r = pygame.Rect(cx - 16, cy - 8, 32, 16)
            sc.blit(self._grad_surf(r.w, r.h, (118, 132, 150), (62, 72, 90), 8), r.topleft)
            for hx in range(r.x + 5, r.right - 3, 5):
                for hy in range(r.y + 4, r.bottom - 2, 5):
                    pygame.draw.circle(sc, (52, 60, 74), (hx, hy), 1)
            pygame.draw.rect(sc, (150, 160, 176), r, 1, border_radius=8)
            pygame.draw.line(sc, (90, 150, 220), (cx, r.y - 6), (cx, r.y), 3)  # DEF nozzle
            pygame.draw.circle(sc, (120, 180, 240), (cx, r.y - 7), 2)
        elif kind == "def":                              # DEF / AdBlue urea tank
            r = pygame.Rect(cx - 10, cy - 9, 20, 18)
            sc.blit(self._grad_surf(r.w, r.h, (96, 130, 170), (40, 62, 92), 4), r.topleft)
            pygame.draw.rect(sc, (30, 44, 60), r, 1, border_radius=4)
            pygame.draw.rect(sc, (150, 200, 240), (cx - 4, r.y - 3, 8, 4), border_radius=1)
        elif kind == "res":                              # mid resonator: ribbed cylinder
            r = pygame.Rect(cx - 14, cy - 6, 28, 12)
            sc.blit(self._grad_surf(r.w, r.h, (128, 134, 148), (58, 64, 78), 6), r.topleft)
            for rx in (cx - 6, cx, cx + 6):              # rolled ribs
                pygame.draw.line(sc, (84, 90, 104), (rx, r.y + 1), (rx, r.bottom - 1), 1)
            pygame.draw.rect(sc, (150, 156, 170), r, 1, border_radius=6)
            for ex in (r.x - 3, r.right):                # domed end caps
                pygame.draw.rect(sc, (96, 102, 116), (ex, cy - 4, 3, 8), border_radius=2)
        elif kind == "tail":                             # chrome slash-cut tip
            body = [(cx - 8, cy - 6), (cx + 8, cy - 6), (cx + 13, cy + 6), (cx - 8, cy + 6)]
            pygame.draw.polygon(sc, (150, 158, 172), body)
            pygame.draw.polygon(sc, (216, 224, 236),    # top chrome highlight
                                [(cx - 8, cy - 6), (cx + 8, cy - 6),
                                 (cx + 9, cy - 3), (cx - 8, cy - 3)])
            pygame.draw.polygon(sc, (60, 64, 76), body, 1)
            pygame.draw.ellipse(sc, (14, 15, 19), (cx + 4, cy - 5, 8, 11))   # dark bore
            pygame.draw.ellipse(sc, (228, 234, 244), (cx + 4, cy - 5, 8, 11), 1)
        elif kind == "muf":                              # muffler: fat oval + stubs
            r = pygame.Rect(cx - 21, cy - 9, 42, 18)
            pygame.draw.line(sc, (70, 74, 86), (r.x - 6, cy + 3), (r.x, cy + 3), 4)
            pygame.draw.line(sc, (70, 74, 86), (r.right, cy - 3), (r.right + 6, cy - 3), 4)
            sc.blit(self._grad_surf(r.w, r.h, (136, 142, 156), (60, 66, 80), 9), r.topleft)
            for sx in (cx - 8, cx + 8):                  # rolled body seams
                pygame.draw.line(sc, (92, 98, 112), (sx, r.y + 1), (sx, r.bottom - 1), 1)
            pygame.draw.line(sc, (188, 194, 208),        # long top sheen
                             (r.x + 5, r.y + 4), (r.right - 5, r.y + 4), 1)
            pygame.draw.rect(sc, (150, 156, 170), r, 1, border_radius=9)
        elif kind == "wg":                               # wastegate: actuator + valve
            pygame.draw.rect(sc, (96, 102, 116), (cx - 2, cy - 15, 4, 8))    # rod
            pygame.draw.ellipse(sc, (76, 82, 96), (cx - 8, cy - 21, 16, 9))  # diaphragm
            pygame.draw.ellipse(sc, (120, 126, 140), (cx - 8, cy - 21, 16, 9), 1)
            pygame.draw.circle(sc, (122, 128, 142), (cx, cy), 9)
            pygame.draw.circle(sc, (40, 44, 54), (cx, cy), 9, 1)
            pygame.draw.circle(sc, (168, 174, 188), (cx - 3, cy - 3), 3)     # gloss
            pygame.draw.circle(sc, (70, 76, 90), (cx, cy), 3)
        elif kind == "bov":                              # blow-off valve + vent trumpet
            for k, rr in enumerate((8, 6, 4)):           # stacked spring coils
                pygame.draw.line(sc, (110, 150, 200), (cx - rr, cy - 10 - k * 3),
                                 (cx + rr, cy - 10 - k * 3), 2)
            pygame.draw.circle(sc, (122, 128, 142), (cx, cy), 8)
            pygame.draw.circle(sc, (40, 44, 54), (cx, cy), 8, 1)
            pygame.draw.circle(sc, (170, 176, 190), (cx - 2, cy - 2), 2)
            pygame.draw.polygon(sc, (196, 104, 78),
                                [(cx + 6, cy - 5), (cx + 15, cy - 8), (cx + 15, cy + 8),
                                 (cx + 6, cy + 5)])      # vent trumpet
            pygame.draw.polygon(sc, (255, 170, 130),
                                [(cx + 6, cy - 5), (cx + 15, cy - 8), (cx + 15, cy - 4),
                                 (cx + 6, cy - 2)])      # trumpet highlight

    def _pipe(self, p0, p1, rad):
        """A short straight metallic pipe (dark casing, steel body, upper-left
        sheen) used to plumb the ancillaries together."""
        sc = self.screen
        if self.low_quality:                          # flat single-colour pipe
            pygame.draw.line(sc, (116, 122, 136), p0, p1, max(2, rad * 2))
            return
        pygame.draw.line(sc, (24, 26, 34), p0, p1, rad * 2 + 2)
        pygame.draw.line(sc, (116, 122, 136), p0, p1, max(2, rad * 2))
        pygame.draw.line(sc, (176, 182, 196), (p0[0] - 1, p0[1] - 1),
                         (p1[0] - 1, p1[1] - 1), max(1, rad // 2))

    def _draw_oil_pan(self, bay):
        """The oil pan / sump bolted under the crankcase — a ribbed trapezoidal
        pan with a drain plug, hung below the crankshaft."""
        cx, cy = self._crank_xy
        top_y = int(cy + self._crank_h + 7)
        h = 20
        if top_y + h > bay.bottom - 72:               # keep clear of the rail/icon row
            top_y = bay.bottom - 72 - h
        if top_y < cy + 4:
            return
        solid = self.low_quality                      # low-Q: draw opaque, no alpha
        sc = self.screen if solid else self._get_layer(2)   # 75% layer (cached)
        cxp = int(cx); wt = int(bay.width * 0.28); wb = int(bay.width * 0.19)
        pts = [(cxp - wt // 2, top_y), (cxp + wt // 2, top_y),
               (cxp + wb // 2, top_y + h), (cxp - wb // 2, top_y + h)]
        pygame.draw.polygon(sc, (74, 80, 94), pts)
        pygame.draw.polygon(sc, (92, 98, 114), [pts[0], pts[1],   # lit upper band
                            (cxp + (wt + wb) // 4, top_y + h // 2),
                            (cxp - (wt + wb) // 4, top_y + h // 2)])
        for ry in range(top_y + 5, top_y + h - 2, 4):  # cast ribs
            pygame.draw.line(sc, (52, 56, 68), (cxp - wt // 2 + 4, ry),
                             (cxp + wt // 2 - 4, ry), 1)
        pygame.draw.polygon(sc, (30, 33, 42), pts, 2)
        pygame.draw.circle(sc, (120, 126, 140), (cxp, top_y + h), 3)   # drain plug
        pygame.draw.circle(sc, (40, 44, 54), (cxp, top_y + h), 3, 1)
        if not solid:
            sc.set_alpha(191)                         # ~75% opacity
            br = getattr(self, "_bay_rect", None)
            self.screen.blit(sc, br.topleft if br else (0, 0), br)

    def _bay_ancillaries(self, bay, eng, throttle=0.0):
        """A labelled row of intake/boost ancillaries along the bay floor, PLUMBED
        together by a charge/exhaust rail with a riser up to the engine: throttle
        body, intercooler, catalytic converter, wastegate, blow-off."""
        aircraft = (getattr(eng, "is_radial", False)
                    or getattr(eng, "gearbox_type", "") == "aircraft")
        diesel = eng.cylinders[0].compression_ratio >= 14.5 and not aircraft
        if aircraft:                                     # piston aircraft: NO civilian cat
            items = [("Throttle Body", "tb"), ("Intercooler", "ic")]
            if eng.induction != "na":
                items += [("Wastegate", "wg"), ("Blow-off", "bov")]
        elif eng.induction == "na":                      # NA road: full exhaust line
            items = [("Throttle Body", "tb")]
            if eng.has_cat:
                items += [("Catalytic", "cat"), ("Resonator", "res"),
                          ("Muffler", "muf"), ("Tailpipe", "tail")]
            else:
                items.append(("Megaphone", "muf"))       # open race exhaust
        elif diesel:                                     # diesel after-treatment train
            items = [("Intercooler", "ic"), ("DOC", "cat"), ("DPF", "dpf"),
                     ("SCR", "scr"), ("DEF", "def"), ("Wastegate", "wg")]
        else:
            items = [("Throttle Body", "tb"), ("Intercooler", "ic")]
            if eng.has_cat:
                items.append(("Catalytic", "cat"))
            if getattr(eng, "has_gpf", False):
                items.append(("GPF", "cat"))
            items.append(("Wastegate", "wg"))
            items.append(("Blow-off", "bov"))
        y = bay.bottom - 50                              # lifted off the bottom edge
        x0, span = bay.x + 30, bay.width - 60
        step = span / max(len(items), 1)
        xs = [int(x0 + step * (i + 0.5)) for i in range(len(items))]
        # one INLINE run at the units' own centreline — the components sit ON the
        # pipe like a real underfloor system (the old elevated rail + drops read
        # as parts hanging from a clothesline).
        self._pipe((xs[0] - 12, y), (xs[-1] + 12, y), 3)
        # riser to the engine ONLY when no coloured exhaust plumbing will connect
        # the row anyway (aircraft etc.) — for road cars the red down-pipe / NA
        # header exit already tells the flow story, and the old always-on riser
        # just crossed the last unit's label.
        has_flow_pipes = (getattr(self, "_turbo_pts", [])
                          or getattr(self, "_exh_exit", None) is not None)
        if not has_flow_pipes:
            self._pipe((xs[-1] + 12, y),
                       (xs[-1] + 12, int(self._crank_xy[1] + self._crank_h + 4)), 4)
        tps = getattr(self, "_turbo_pts", [])
        catx = next((xs[i] for i, (n, k) in enumerate(items) if k == "cat"), None)
        icx = next((xs[i] for i, (n, k) in enumerate(items) if k == "ic"), None)
        self._begin_pipe_layers()                    # same translucency as the manifolds
        # charge pipe: GREEN, turbo compressor outlet -> intercooler
        if icx is not None and tps:
            tx, ty, tr = min(tps, key=lambda p: abs(p[0] - icx))
            self._draw_ortho_pipe([(int(tx + tr * 0.6), int(ty - tr * 0.5)),
                                   (int(tx + tr * 0.6), y - 26), (icx, y - 26),
                                   (icx, y - 8)], 3, self._INT_COLS)
        # exhaust after-treatment: a RED down-pipe from each turbo to the catalytic
        if catx is not None and tps:
            for tx, ty, tr in tps:
                self._draw_ortho_pipe([(int(tx), int(ty + tr)), (int(tx), y - 12),
                                       (catx, y - 12), (catx, y - 8)], 3, self._EXH_COLS)
        # NA exhaust line: engine outlet -> cat -> resonator -> muffler -> tailpipe
        if catx is not None and getattr(self, "_exh_exit", None) is not None:
            kx = {}
            for i, (n, k) in enumerate(items):
                kx.setdefault(k, xs[i])
            ex = self._exh_exit
            self._draw_ortho_pipe([(int(ex[0]), int(ex[1])), (int(ex[0]), y - 12),
                                   (catx, y - 12), (catx, y - 6)], 3, self._EXH_COLS)
            seq = [k for k in ("cat", "res", "muf", "tail") if k in kx]
            for a, b in zip(seq, seq[1:]):
                self._draw_ortho_pipe([(kx[a], y), (kx[b], y)], 2, self._EXH_COLS)
        self._end_pipe_layers()
        for i, (name, kind) in enumerate(items):
            cx = xs[i]
            self._draw_ancillary(cx, y, kind, throttle)
            t = self.font_small.render(self.tr(name), True, (140, 148, 164))
            self.screen.blit(t, (cx - t.get_width() // 2, y + 14))
        intake_x = next((xs[i] for i, (n, k) in enumerate(items) if k in ("tb", "ic")),
                        bay.x + 70)
        self._draw_front_intake(bay, eng, intake_x, y)   # cold-air front of the chain
        if not getattr(eng, "is_radial", False):         # liquid-cooled: radiator
            self._draw_cooling(bay)
        ccx, ccy = self._crank_xy
        if getattr(eng, "mgu_whine", 0.0) > 0.0:     # F1 PU: MGU-H + MGU-K
            tps = getattr(self, "_turbo_pts", [])
            if tps:
                tx, ty, tr = tps[0]
                self._draw_mgu(tx, ty, tr + 2, "MGU-H", ring_only=True)
            self._draw_mgu(int(ccx), int(ccy + self._crank_h + 16), 12, "MGU-K")
            self._draw_battery(bay.right - 42, bay.y + 28, int(ccx),
                               int(ccy + self._crank_h + 16))
        elif getattr(eng, "hybrid_kw", 0.0) > 0.0:   # road hybrid: e-motor + HV battery
            self._draw_mgu(int(ccx), int(ccy + self._crank_h + 16), 13, "E-Motor")
            self._draw_battery(bay.right - 42, bay.y + 28, int(ccx),
                               int(ccy + self._crank_h + 16))

    def _draw_battery(self, x, y, mx, my):
        """A high-voltage hybrid battery pack (cells) with an orange HV cable to
        the motor and a violet electronics-cooling loop."""
        sc = self.screen
        b = pygame.Rect(int(x), int(y), 34, 20)
        sc.blit(self._grad_surf(b.w, b.h, (70, 92, 78), (32, 50, 40), 3), b.topleft)
        for fx in range(b.x + 4, b.right - 2, 5):     # cells
            pygame.draw.line(sc, (40, 60, 48), (fx, b.y + 2), (fx, b.bottom - 2), 1)
        pygame.draw.rect(sc, (120, 180, 140), b, 1, border_radius=3)
        pygame.draw.line(sc, (210, 150, 40), (b.x + 4, b.y - 3), (b.x + 12, b.y - 3), 2)  # +HV
        t = self.font_small.render("HV Battery", True, (130, 190, 150))
        sc.blit(t, (b.centerx - t.get_width() // 2, b.bottom + 1))
        # orange HV cable from the battery to the motor
        self._draw_ortho_pipe([(b.left, b.centery), (mx + 30, b.centery),
                               (mx + 30, my)], 2,
                              ((70, 44, 8), (210, 140, 40), (240, 190, 90)))
        # violet electronics-cooling loop tap
        pygame.draw.line(sc, (150, 110, 210), (b.right, b.y + 4), (b.right + 8, b.y + 4), 2)

    def _draw_cooling(self, bay):
        """The cooling pack on the bay's front (left) edge: a proper crossflow
        radiator — top/bottom header tanks, filler cap, finned core that shifts
        WARM with the live coolant temperature — plus upper/lower hoses that
        actually reach the block."""
        sc = self.screen
        core = pygame.Rect(bay.x + 8, bay.centery - 46, 16, 84)
        hot = min(max((getattr(self.sim, "coolant_c", 88.0) - 60.0) / 55.0, 0.0), 1.0)
        # header tanks (black plastic, rounded), filler cap on the top tank
        for ty in (core.y - 10, core.bottom):
            pygame.draw.rect(sc, (38, 41, 50), (core.x - 2, ty, core.w + 4, 10),
                             border_radius=4)
            pygame.draw.rect(sc, (74, 79, 92), (core.x - 2, ty, core.w + 4, 10),
                             1, border_radius=4)
        pygame.draw.circle(sc, (150, 156, 170), (core.centerx, core.y - 10), 4)
        pygame.draw.circle(sc, (52, 56, 68), (core.centerx, core.y - 10), 4, 1)
        # core: cool blue-grey that warms toward amber as the coolant heats up
        c_hi = (88 + int(60 * hot), 108 - int(10 * hot), 130 - int(48 * hot))
        c_lo = (38 + int(38 * hot), 54 - int(6 * hot), 72 - int(30 * hot))
        sc.blit(self._grad_surf(core.w, core.h, c_hi, c_lo, 2), core.topleft)
        for fy in range(core.y + 3, core.bottom - 2, 3):     # fin rows
            pygame.draw.line(sc, (40, 50, 64), (core.x + 1, fy), (core.right - 1, fy), 1)
        for fx in (core.x + 5, core.x + 10):                 # crossflow tubes
            pygame.draw.line(sc, (30, 40, 52), (fx, core.y + 1), (fx, core.bottom - 1), 1)
        pygame.draw.rect(sc, (30, 40, 54), core, 1, border_radius=2)
        # hoses: upper (hot, from the head) and lower (cool return) — black rubber
        # with clamps, routed with one gentle bend each to the block
        cx, cy = self._crank_xy
        top_y = core.y - 5
        bot_y = core.bottom + 5
        self._draw_ortho_pipe([(core.right + 2, top_y), (core.right + 20, top_y),
                               (core.right + 20, int(cy - self._crank_h - 8))], 3,
                              ((14, 15, 19), (52, 56, 66), (96, 102, 116)))
        self._draw_ortho_pipe([(core.right + 2, bot_y), (core.right + 12, bot_y),
                               (core.right + 12, int(cy + self._crank_h - 2))], 3,
                              ((14, 15, 19), (52, 56, 66), (96, 102, 116)))
        for hx, hy in ((core.right + 3, top_y), (core.right + 3, bot_y)):
            pygame.draw.line(sc, (150, 156, 170), (hx, hy - 4), (hx, hy + 4), 2)
        # end FITTINGS where the hoses head into the engine circuit, so the runs
        # terminate in hardware instead of stopping mid-air
        for fxx, fyy in ((core.right + 20, int(cy - self._crank_h - 8)),
                         (core.right + 12, int(cy + self._crank_h - 2))):
            pygame.draw.circle(sc, (96, 102, 116), (fxx, fyy), 4)
            pygame.draw.circle(sc, (40, 44, 54), (fxx, fyy), 4, 1)
        t = self.font_small.render(self.tr("Radiator"), True, (104, 148, 168))
        sc.blit(t, (core.x - 4, bot_y + 8))

    def _draw_mgu(self, cx, cy, r, label, ring_only=False):
        """An F1 motor-generator unit: a blue-glowing electric machine with copper
        stator windings.  ``ring_only`` overlays it as a ring on the turbo (MGU-H);
        otherwise a full motor disc (MGU-K on the crank)."""
        sc = self.screen
        cx, cy, r = int(cx), int(cy), int(r)
        glow = pygame.Surface((2 * r + 10, 2 * r + 10), pygame.SRCALPHA)
        pygame.draw.circle(glow, (80, 170, 255, 70), (r + 5, r + 5), r + 5)
        sc.blit(glow, (cx - r - 5, cy - r - 5), special_flags=pygame.BLEND_RGBA_ADD)
        if not ring_only:
            pygame.draw.circle(sc, (40, 46, 58), (cx, cy), r)
        pygame.draw.circle(sc, (96, 184, 255), (cx, cy), r, 2)
        for k in range(8):                            # copper stator windings
            a = k * math.pi / 4
            pygame.draw.line(sc, (206, 142, 74),
                             (int(cx + math.cos(a) * r * 0.55), int(cy + math.sin(a) * r * 0.55)),
                             (int(cx + math.cos(a) * r * 0.92), int(cy + math.sin(a) * r * 0.92)), 2)
        if not ring_only:
            pygame.draw.circle(sc, (150, 160, 176), (cx, cy), max(2, int(r * 0.3)))
        t = self.font_small.render(label, True, (130, 196, 255))
        sc.blit(t, (cx - t.get_width() // 2, cy + r + 2))

    # cool pre-turbo intake air (light blue) — (casing, body, sheen)
    _COOL_COLS = ((20, 44, 60), (70, 138, 180), (162, 212, 240))  # coolant = blue

    def _draw_front_intake(self, bay, eng, intake_x, row_y):
        """The cold-air FRONT of the charge path: a body scoop -> air filter ->
        light-blue ducts.  On a turbo engine they feed each turbo's compressor
        inlet; on an NA engine the duct runs to the throttle body (scoop -> filter
        -> throttle -> manifold)."""
        tps = getattr(self, "_turbo_pts", [])
        sc = self.screen
        cool = self._COOL_COLS
        afx = bay.x + 96
        afy = bay.y + 26
        # body intake scoop at the bay's top edge
        pygame.draw.polygon(sc, (58, 64, 78), [(afx - 11, bay.y + 5), (afx + 11, bay.y + 5),
                                               (afx + 6, afy - 7), (afx - 6, afy - 7)])
        pygame.draw.polygon(sc, (28, 31, 40), [(afx - 11, bay.y + 5), (afx + 11, bay.y + 5),
                                               (afx + 6, afy - 7), (afx - 6, afy - 7)], 1)
        # air-filter element (ribbed cylinder)
        af = pygame.Rect(afx - 17, afy - 7, 34, 15)
        if self.low_quality:                          # flat single colour
            pygame.draw.rect(sc, (120, 126, 140), af, 0, border_radius=7)
            pygame.draw.rect(sc, (40, 44, 54), af, 1, border_radius=7)
        else:
            sc.blit(self._grad_surf(af.w, af.h, (150, 156, 170), (70, 76, 90), 7),
                    af.topleft)
            for fx in range(af.x + 3, af.right - 2, 3):
                pygame.draw.line(sc, (60, 66, 80), (fx, af.y + 2), (fx, af.bottom - 2), 1)
            pygame.draw.rect(sc, (40, 44, 54), af, 1, border_radius=7)
        lab = self.font_small.render(self.tr("Air Filter"), True, (120, 168, 196))
        sc.blit(lab, (af.centerx - lab.get_width() // 2, af.bottom))
        # cool-air ducts from the filter down to each turbo's compressor inlet,
        # drawn onto a temp layer at ~35% opacity (semi-transparent blue)
        real = self.screen
        solid = self.low_quality                       # low-Q: opaque ducts, no alpha
        ds = real if solid else self._get_layer(3)
        self.screen = ds
        if tps:                                       # turbo: filter -> compressors
            for tx, ty, tr in tps:
                inx = int(tx - tr - 4) if tx > bay.centerx else int(tx + tr + 4)
                self._draw_ortho_pipe([(af.centerx, af.bottom + 1), (af.centerx, int(ty)),
                                       (inx, int(ty))], 3, cool)
                pygame.draw.circle(ds, cool[1], (inx, int(ty)), 3)
        else:                                         # NA: filter -> throttle body
            self._draw_ortho_pipe([(af.centerx, af.bottom + 1),
                                   (af.centerx, int(row_y) - 9),
                                   (int(intake_x), int(row_y) - 9)], 3, cool)
            pygame.draw.circle(ds, cool[1], (int(intake_x), int(row_y) - 9), 3)
        self.screen = real
        if not solid:
            ds.set_alpha(89)
            br = getattr(self, "_bay_rect", None)
            real.blit(ds, br.topleft if br else (0, 0), br)

    def _forced_drive(self, eng, sim):
        """(spin, load) for the forced-induction hardware — shaft spin scaled by
        the turbo/blower drive ratio, boost fraction as load."""
        rpm = max(sim.rpm, 1.0)
        # Forza: freeze the spinning hardware (keep only pistons + dashboard moving)
        spin = 0.0 if self.telemetry_mode else \
            sim.crank_angle * max(sim.forced_induction_rpm() / rpm, 1.0)
        load = (sim.boost / max(eng.boost_bar, 0.05)) if eng.boost_bar else 0.0
        return spin, load

    def _draw_bay_induction(self, bay, eng, sim):
        """Draw the forced-induction hardware in the engine bay, placed by type:
        a turbo in the VALLEY for a hot-V, OUTSIDE the banks for a cold-V, one per
        bank (twin/quad), a single for an inline, a blower on top for an SC."""
        self._draw_oil_pan(bay)                        # furniture for every engine
        self._turbo_pts = []                           # collected as turbos are drawn
        ind = eng.induction
        if ind == "na":                                # NA still gets the intake/
            self._bay_ancillaries(bay, eng, min(max(sim.throttle, 0.0), 1.0))  # exhaust row
            return
        rpm = max(sim.rpm, 1.0)
        spin = sim.crank_angle * max(sim.forced_induction_rpm() / rpm, 1.0)
        load = (sim.boost / max(eng.boost_bar, 0.05)) if eng.boost_bar else 0.0
        cyl = eng.cylinders
        has_banks = (any(c.bank_angle_deg < -0.1 for c in cyl)
                     and any(c.bank_angle_deg > 0.1 for c in cyl))
        cyv = (bay.y + bay.bottom) // 2

        def lab(text, x, y):
            t = self.font_small.render(self.tr(text), True, (150, 158, 174))
            # clamp inside the bay so long labels (e.g. "Parallel Twin-turbo")
            # never run off the panel edge or under the turbo icons
            tx = min(max(int(x - t.get_width() // 2), bay.x + 8),
                     bay.right - 8 - t.get_width())
            self.screen.blit(t, (tx, int(y)))

        thr = min(max(sim.throttle, 0.0), 1.0)
        if ind in ("roots", "centrifugal"):           # supercharger sits on top
            self._bay_blower(bay.centerx, bay.y + 50, 26, spin, load,
                             centri=(ind == "centrifugal"))
            lab("Supercharger" if ind == "roots" else "Centrifugal SC",
                bay.centerx, bay.y + 6)
            self._bay_ancillaries(bay, eng, thr)
            return
        hot = getattr(eng, "hot_v", False)
        etb = getattr(eng, "electric_turbo", False)
        sub = getattr(eng, "induction_subtype", "")
        if sub == "Twincharge":                       # supercharger + turbo compound
            self._bay_blower(bay.centerx, bay.y + 48, 22, spin, load)
            if has_banks:
                for x in (bay.x + 52, bay.right - 52):
                    self._bay_turbo(x, cyv + 8, 17, spin, load)
            else:
                self._bay_turbo(bay.right - 50, cyv + 8, 20, spin, load)
            lab("Twincharge · Supercharger + Turbo", bay.centerx, bay.y + 6)
            self._bay_ancillaries(bay, eng, thr)
            return
        if sub == "sequential":                       # small (primary) + big (secondary)
            if has_banks:
                pts = ((bay.x + 50, cyv, 15), (bay.right - 50, cyv, 24))
            else:
                pts = ((bay.right - 52, cyv - 24, 15), (bay.right - 46, cyv + 22, 24))
            for x, y, rr in pts:
                self._bay_turbo(x, y, rr, spin, load, electric=etb)
            lab("Sequential Twin-turbo · Small + Big", bay.centerx, bay.y + 26)
            self._bay_ancillaries(bay, eng, thr)
            return
        if sub == "twin_scroll":                      # divided-housing single turbo
            tx = bay.centerx if hot else bay.right - 50
            self._bay_turbo(tx, cyv, 24, spin, load, electric=etb, twin_scroll=True,
                            inlet_dir=(-math.pi / 2 if hot else math.pi))
            lab("Twin-scroll Single Turbo", tx, cyv + 32)
            self._bay_ancillaries(bay, eng, thr)
            return
        if getattr(eng, "is_w", False):               # quad-turbo (Veyron W16)
            for x, y in ((bay.x + 46, cyv - 34), (bay.x + 46, cyv + 34),
                         (bay.right - 46, cyv - 34), (bay.right - 46, cyv + 34)):
                self._bay_turbo(x, y, 18, spin, load)
            lab("Quad-turbo", bay.centerx, bay.y + 26)
        elif has_banks:
            r = 22
            if hot:
                # Two EQUAL-size turbos in the V valley, staged in depth purely by
                # OVERLAP: the back one is drawn first, a conrod crosses in front of
                # it, then the front one overlaps its ~left half — a twin wedged in
                # the valley, not a single turbo (and not forced perspective).
                sc = self.screen
                self._bay_turbo(bay.centerx + 12, cyv - 5, r, spin, load,
                                electric=etb, inlet_dir=-math.pi / 2)
                rx0, ry0 = bay.centerx + 2, cyv - 30          # a conrod crossing
                rx1, ry1 = bay.centerx + 12, cyv - 2          # in front of the back turbo
                pygame.draw.line(sc, (26, 28, 36), (rx0, ry0), (rx1, ry1), 8)
                pygame.draw.line(sc, (118, 124, 140), (rx0, ry0), (rx1, ry1), 5)
                pygame.draw.line(sc, (180, 186, 200), (rx0 - 1, ry0), (rx1 - 1, ry1), 1)
                self._bay_turbo(bay.centerx - 12, cyv + 6, r, spin, load,
                                electric=etb, inlet_dir=-math.pi / 2)
                lab("Hot-V Twin-turbo · In the Valley", bay.centerx, bay.y + 22)
            else:                                      # outside the banks
                self._bay_turbo(bay.x + 48, cyv, r, spin, load, electric=etb,
                                inlet_dir=0.0)
                self._bay_turbo(bay.right - 48, cyv, r, spin, load, electric=etb,
                                inlet_dir=math.pi)
                lab("Twin-turbo · Outboard (Cold-V)", bay.centerx, bay.y + 26)
        elif sub == "twin":                            # inline parallel twin-turbo
            self._bay_turbo(bay.right - 78, cyv - 13, 18, spin, load, electric=etb,
                            inlet_dir=math.pi)
            self._bay_turbo(bay.right - 44, cyv + 14, 20, spin, load, electric=etb,
                            inlet_dir=math.pi)
            lab("Parallel Twin-turbo", bay.right - 70, cyv - 52)   # above the pair,
            # clear of the vertical down-pipes that cross everything below it
        else:                                          # inline: single turbo, side
            self._bay_turbo(bay.right - 50, cyv, 22, spin, load, electric=etb,
                            inlet_dir=math.pi)
            lab("Single Turbo", bay.right - 50, cyv + 30)
        self._bay_ancillaries(bay, eng, thr)

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
        # oil consumption: a little oil burns past the rings / valve guides /
        # turbo seals every revolution — scales with engine speed (and a bit with
        # load), so it climbs faster when you're on it.
        if sim.rpm > 1.0:
            load = 0.5 + 0.5 * min(max(getattr(sim, "throttle", 0.0), 0.0), 1.0)
            self._oil_total_l += (sim.rpm / 60.0) / FPS * 2.2e-7 * load
        # odometer: integrate road/air speed -> total distance (km)
        self._odo_km += getattr(sim.drivetrain, "v", 0.0) / FPS / 1000.0
        # spin the road wheel at the TREAD rate (road speed + wheelspin), so
        # lighting the tyres up visibly over-spins the wheel animation
        dt = sim.drivetrain
        surf_v = dt.wheel_surface_speed() if hasattr(dt, "wheel_surface_speed") else dt.v
        self._wheel_ang += (surf_v / max(dt.wheel_radius, 0.1)) / FPS

    def _draw_ignition_bank(self, x, y, w, lights=True):
        """One light per cylinder, flashing as that cylinder fires (power stroke) —
        the original game's IGNITION column.  Returns the y below the bank.
        ``lights=False`` (Forza) keeps the label + layout but skips the live lamps."""
        sim, eng = self.sim, self.sim.engine
        n = eng.num_cylinders
        lab = self.font_small.render(self.tr("IGNITION"), True, DIM)
        self.screen.blit(lab, (x, y))
        label_h = lab.get_height()
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
        # Start the lights BELOW the actual rendered label height (so they never
        # overlap "IGNITION" no matter the font), and keep the whole bank within a
        # fixed height (so a 4-bank W doesn't shove the readouts off the bottom).
        top0 = y + label_h + 10               # clear breathing room below the label
        pitch = min(20, 52 // max(nrows, 1))
        r = 6 if pitch >= 17 else (5 if pitch >= 12 else 4)
        fade = self._ign_flash
        # The lamp layer is drawn into its own surface so Low-Q can refresh it at a
        # lower rate (~10 fps): the firing sequence still reads fine and the
        # per-cylinder fade just decays in coarser steps.  Normal draws it live.
        sx, sy = x, top0 - (r + 2)                    # surface origin on screen
        sw, sh = w, nrows * pitch + 2 * (r + 2)

        def _build_lamps():
            surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
            if lights:
                for rr, rw in enumerate(rows_list):
                    for cc, i in enumerate(rw):
                        cxp = int(8 + dx * (cc + 0.5))           # local coords
                        cyp = int((r + 2) + rr * pitch)
                        phi = sim.cycle_phase_deg(i)
                        firing = (sim.ignition_on and not sim._fuel_cut
                                  and 360.0 <= phi < 455.0)
                        fade[i] = max(1.0 if firing else 0.0, fade.get(i, 0.0) * 0.70)
                        f = fade[i]
                        col = (int(38 + 214 * f), int(44 + 150 * f), int(52 + 36 * f))
                        pygame.draw.circle(surf, (22, 24, 30), (cxp, cyp), r + 2)
                        pygame.draw.circle(surf, col, (cxp, cyp), r)
                        pygame.draw.circle(surf, (140, 146, 160), (cxp, cyp), r, 1)
            return surf
        self.screen.blit(self._slow_surf("ignition", 3, _build_lamps), (sx, sy))
        return top0 + min(nrows * pitch + 4, 46)      # bounded height for any layout

    def _slow_surf(self, key, period, render_fn):
        """Low-Q: call ``render_fn`` (which returns a Surface) only once every
        ``period`` draws and reuse the cached Surface in between — for secondary
        readouts that don't need the full frame rate.  Normal mode renders live
        every draw.  The caller blits the returned Surface itself."""
        if not self.low_quality:
            return render_fn()
        c = self._elem_cache.get(key)
        if c is None or (self._draw_n % period) == 0:
            c = render_fn()
            self._elem_cache[key] = c
        return c

    # ---------------------------------------------------- focus oscilloscope
    # The original engine-sim layout: one big FOCUS OSCILLOSCOPE window with a
    # row of channel tiles under it (waveform / exhaust flow / valve lift /
    # cylinder pressure) — click a tile to put that channel on the big screen.
    _SCOPE_CHANNELS = [("wave", "WAVEFORM"), ("flow", "EXH FLOW"),
                       ("lift", "VALVE LIFT"), ("press", "CYL PRESS")]
    _SCOPE_PINK = (238, 148, 196)          # the design sheet's pixel-pink caps

    def _draw_focus_scope(self, x, y, w, h):
        # ADAPTIVE: the channel tiles need vertical room — when the gauges panel
        # squeezes the scope (small h), skip the tiles and show the plain flow
        # scope full-height instead of overlapping the status rows below.
        if h < 56:
            self._scope_tile_rects = {}
            self._draw_scope(x, y, w, h, "EXHAUST FLOW")
            return
        tile_h = 16
        fh = max(24, h - tile_h - 2)       # focus window height
        chan = self._scope_chan
        if chan == "flow":                 # per-cylinder exhaust-flow envelopes
            self._draw_scope(x, y, w, fh, "EXHAUST FLOW")
        else:
            pygame.draw.rect(self.screen, (10, 11, 14), (x, y, w, fh))
            pygame.draw.rect(self.screen, (44, 48, 56), (x, y, w, fh), 1)
            if chan == "wave":
                self._focus_wave(x, y, w, fh)
            elif chan == "lift":
                self._focus_lift(x, y, w, fh)
            else:
                self._focus_press(x, y, w, fh)
        # --- channel tiles (click to focus) ---------------------------------
        n = len(self._SCOPE_CHANNELS)
        gap = 3
        tw = (w - gap * (n - 1)) // n
        ty = y + fh + 2
        self._scope_tile_rects = {}
        for i, (key, label) in enumerate(self._SCOPE_CHANNELS):
            tr = pygame.Rect(x + i * (tw + gap), ty, tw, tile_h)
            self._scope_tile_rects[key] = tr
            sel = (key == chan)
            pygame.draw.rect(self.screen, (26, 20, 25) if sel else (12, 13, 16), tr)
            pygame.draw.rect(self.screen, self._SCOPE_PINK if sel else (44, 48, 56),
                             tr, 1)
            ts = self.font_hint.render(label, True,
                                       self._SCOPE_PINK if sel else (110, 116, 128))
            self.screen.blit(ts, (tr.centerx - ts.get_width() // 2,
                                  tr.centery - ts.get_height() // 2))

    def _focus_wave(self, x, y, w, fh):
        """Master audio output waveform (what the tailpipe is radiating NOW)."""
        self.screen.blit(self.font_small.render("WAVEFORM", True, self._SCOPE_PINK),
                         (x + 6, y + 2))
        wave = getattr(self.synth, "last_wave", None) if self.synth else None
        mid = y + fh // 2
        pygame.draw.line(self.screen, (30, 33, 40), (x + 2, mid), (x + w - 2, mid))
        if wave is None or len(wave) < 2:
            return
        amp = (fh - 10) * 0.5
        xs = x + 2 + np.arange(len(wave)) * (w - 4) / (len(wave) - 1)
        ys = mid - np.clip(wave, -1.0, 1.0) * amp
        pts = np.column_stack((xs, ys)).astype(np.int32).tolist()
        pygame.draw.lines(self.screen, (120, 235, 170), False, pts, 1)

    def _focus_lift(self, x, y, w, fh):
        """Intake/exhaust valve lift over the 720-deg cycle + live phase cursor
        (same raised-cosine lift model the animated valvetrain uses)."""
        self.screen.blit(self.font_small.render("VALVE LIFT", True, self._SCOPE_PINK),
                         (x + 6, y + 2))
        base = y + fh - 5
        amp = fh - 18
        ang = np.arange(0, 720, 6, dtype=np.float64)
        for open_deg, dur, col in ((700.0, 240.0, (110, 196, 255)),
                                   (500.0, 230.0, (255, 146, 110))):
            t = ((ang - open_deg) % 720.0) / dur
            lift = np.where(t <= 1.0, 0.5 * (1.0 - np.cos(2 * math.pi * t)), 0.0)
            xs = x + 2 + ang * (w - 4) / 720.0
            ys = base - lift * amp
            pts = np.column_stack((xs, ys)).astype(np.int32).tolist()
            pygame.draw.lines(self.screen, col, False, pts, 1)
        phi = self.sim.cycle_phase_deg(0)
        cx = int(x + 2 + phi * (w - 4) / 720.0)
        pygame.draw.line(self.screen, (200, 206, 216), (cx, y + 14), (cx, base), 1)

    def _focus_press(self, x, y, w, fh):
        """Cylinder-1 pressure over the 720-deg cycle (log scale) + live cursor."""
        self.screen.blit(self.font_small.render("CYL PRESSURE", True, self._SCOPE_PINK),
                         (x + 6, y + 2))
        sim = self.sim
        cyl = sim.engine.cylinders[0]
        p_man = sim._manifold_pressure()
        k = getattr(sim, "_k_burn", 3.0)
        burning = sim.ignition_on and not sim._fuel_cut and not sim._shift_cut
        ang = np.arange(0, 721, 8, dtype=np.float64)
        p = np.array([sim._cylinder_pressure(cyl, a, p_man, burning, k)
                      for a in ang])
        logp = np.log10(np.maximum(p, 1e3))
        lo, hi = math.log10(0.1 * 101325.0), max(float(logp.max()), 6.0)
        norm = np.clip((logp - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        base = y + fh - 5
        xs = x + 2 + ang * (w - 4) / 720.0
        ys = base - norm * (fh - 18)
        pts = np.column_stack((xs, ys)).astype(np.int32).tolist()
        pygame.draw.lines(self.screen, (235, 200, 120), False, pts, 1)
        phi = sim.cycle_phase_deg(0)
        cx = int(x + 2 + phi * (w - 4) / 720.0)
        pygame.draw.line(self.screen, (200, 206, 216), (cx, y + 14), (cx, base), 1)

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
        # The per-cylinder pulse ENVELOPE is fixed (it depends only on the static
        # crank offsets), so precompute it once per engine/scope-size instead of
        # re-running np.where/exp every frame for every cylinder.  In Low-Q the
        # trace is decimated horizontally (every 2nd pixel) — half the points to
        # transform + draw, visually indistinguishable on a phone.  Display only;
        # the audio is untouched.
        step = 2 if self.low_quality else 1
        key = (n, w, step, id(offs))
        if getattr(self, "_scope_key", None) != key:
            xs = np.arange(0, w, step)
            ang = xs / (w - 1) * 720.0                  # FIXED window (refresh, no scroll)
            envs = []
            for i in range(n):
                d = (ang + offs[i]) % 720.0 - 505.0     # 0 at exhaust-valve open
                envs.append(np.where((d >= 0) & (d < 210.0),
                            np.clip(d / 4.0, 0.0, 1.0) * np.exp(-np.clip(d, 0, None) / 30.0),
                            0.0))
            self._scope_key = key
            self._scope_xs = xs
            self._scope_envs = envs
        xs, envs = self._scope_xs, self._scope_envs

        def _build_traces():
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            for i in range(n):
                # exaggerate the (small) per-cylinder voicing x3 so the strong/weak
                # difference is legible (display only — audio is unchanged)
                vdev = (voice.amp[i] - 1.0) * 3.0 if voice and i < len(voice.amp) else 0.0
                # live firing flash: each pulse brightens & swells as its cylinder
                # actually fires now (reuses the ignition-light fade).
                flash = self._ign_flash.get(i, 0.0)
                amp_i = load * (1.0 + vdev) * (0.45 + 0.55 * flash)
                yv = base - np.clip(envs[i] * amp_i, 0.0, 1.1) * amp
                sh = (i % 6) * 8
                a = int(90 + 150 * flash)                # brighter as it fires
                pts = np.column_stack((xs, yv)).astype(np.int32).tolist()
                pygame.draw.lines(surf, (236, 150 - sh, 60 + sh, a), False, pts, 1)
            return surf
        # Low-Q: rebuild the waveform every 2nd draw (~15 fps) — plenty for a scope.
        self.screen.blit(self._slow_surf("scope", 2, _build_traces), (x, y))
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

    def _ascope(self, x, y, w, h, title, series, bipolar=False, vmax=None):
        """A framed signal window plotting one or more (array, colour) traces."""
        x, y, w, h = int(x), int(y), int(w), int(h)
        pygame.draw.rect(self.screen, (10, 11, 14), (x, y, w, h))
        pygame.draw.rect(self.screen, (52, 58, 70), (x, y, w, h), 1)
        self.screen.blit(self.font_small.render(self.tr(title), True, (150, 158, 172)),
                         (x + 5, y + 3))
        base = y + h * 0.5 if bipolar else y + h - 7
        pygame.draw.line(self.screen, (32, 36, 44), (x, int(base)), (x + w, int(base)), 1)
        good = [(a, c) for a, c in series if a is not None and len(a) > 1]
        if not good:
            return
        vm = vmax or max((float(np.max(np.abs(a))) for a, _ in good), default=1e-6) or 1e-6
        amp = (h * 0.40) if bipolar else (h - 22)
        for a, col in good:
            nn = len(a)
            xs = x + np.arange(nn) / (nn - 1) * (w - 2) + 1
            yv = base - (a / vm) * amp
            pts = np.column_stack((xs, yv)).astype(np.int32).tolist()
            pygame.draw.lines(self.screen, col, False, pts, 1)

    @staticmethod
    def _valve_lift(ang, open_deg, dur):
        """Raised-cosine valve-lift curve over [open, open+dur], wrapping the cycle
        so an intake event spanning 720 -> 0 still draws (valve-overlap region)."""
        res = np.zeros_like(ang)
        for sh in (-720.0, 0.0, 720.0):
            t = (ang + sh - open_deg) / dur
            m = (t >= 0) & (t <= 1)
            res = np.where(m, np.maximum(res, 0.5 * (1 - np.cos(2 * np.pi * t))), res)
        return res

    @staticmethod
    def _cycle_pressure(ang, cyl, load, thr):
        """Single-cylinder in-cylinder pressure (Pa) across one 720-deg cycle:
        intake (manifold) -> adiabatic compression -> combustion peak + expansion
        -> exhaust blowdown.  Power TDC at 360 deg."""
        PATM, k = 101325.0, 1.33
        Pman = PATM * (0.40 + 0.55 * thr)
        Vmin = cyl.volume(0.0)
        Vmax = cyl.volume(math.radians(180.0))
        Vol = np.array([cyl.volume(math.radians(a % 360.0)) for a in ang])
        P = np.full(len(ang), Pman)
        comp = (ang >= 180) & (ang < 360)
        P[comp] = Pman * (Vmax / Vol[comp]) ** k
        Ppk = PATM * (6.0 + 60.0 * load)
        powr = (ang >= 360) & (ang < 540)
        P[powr] = Ppk * (Vmin / Vol[powr]) ** k
        P540 = Ppk * (Vmin / Vmax) ** k
        exh = ang >= 540
        P[exh] = PATM + (P540 - PATM) * np.exp(-(ang[exh] - 540.0) / 35.0)
        return P

    def _draw_timing(self, x, y, w, h):
        """Ignition + cam event timeline over the 720-deg cycle, with a live crank
        cursor — spark advances with rpm; valve events mark the overlap."""
        x, y, w, h = int(x), int(y), int(w), int(h)
        pygame.draw.rect(self.screen, (10, 11, 14), (x, y, w, h))
        pygame.draw.rect(self.screen, (52, 58, 70), (x, y, w, h), 1)
        self.screen.blit(self.font_small.render(
            self.tr("Ignition & cam timing"), True, (150, 158, 172)), (x + 5, y + 3))
        sim = self.sim
        axy = y + h - 16
        pygame.draw.line(self.screen, (60, 66, 78), (x + 6, axy), (x + w - 6, axy), 1)

        def px(deg):
            return int(x + 6 + (deg % 720) / 720.0 * (w - 12))
        rpmf = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
        adv = 15.0 + 22.0 * rpmf
        events = [("IGN", 360 - adv, (255, 90, 90)), ("EVO", 500, (120, 180, 255)),
                  ("EVC", 10, (120, 180, 255)), ("IVO", 700, (110, 220, 130)),
                  ("IVC", 220, (110, 220, 130))]
        for j, (lab, deg, col) in enumerate(events):
            xx = px(deg)
            pygame.draw.line(self.screen, col, (xx, y + 30), (xx, axy), 1)
            t = self.font_small.render(lab, True, col)
            ly = y + 22 + (16 if j % 2 else 0)            # stagger so labels clear
            self.screen.blit(t, (min(max(xx - t.get_width() // 2, x + 2),
                                     x + w - t.get_width() - 2), ly))
        cx = px(math.degrees(sim.crank_angle))
        pygame.draw.line(self.screen, (255, 255, 255), (cx, y + 20), (cx, axy), 1)
        self.screen.blit(self.font_small.render(f"spark adv {adv:.0f}°", True, ACCENT),
                         (x + w - 92, axy + 2))

    def _draw_exhaust_scopes(self, rect):
        """Engine-analyzer overlay: final audio waveform on top, then per-cycle
        physical signals (combustion pulses, exhaust pressure, valve lift, crank
        torque, single-cylinder pressure, ignition/cam timing)."""
        self._panel(rect, screws=False)
        sim, eng = self.sim, self.sim.engine
        self.screen.blit(self.font.render(self.tr("ENGINE ANALYZER"), True, INK),
                         (rect.x + 18, rect.y + 12))
        self.screen.blit(self.font_small.render(
            self.tr("Live engine signals  ·  E / click to close"), True, DIM),
            (rect.x + 18, rect.y + 36))
        PATM = 101325.0
        pad, gap = 14, 12
        x0, fullw = rect.x + pad, rect.width - 2 * pad
        topy, toph = rect.y + 58, 116

        # --- analytic per-cycle signals -----------------------------------------
        W = 240
        ang = np.linspace(0, 720, W, endpoint=False)
        n = eng.num_cylinders
        offs = getattr(sim, "_offset_deg", [0.0] * n)
        shifts = [int(round((offs[i] % 720.0) / 720.0 * W)) for i in range(n)]
        load = min(max((sim.blowdown_pressure() - PATM) / (0.9 * PATM), 0.25), 1.1)
        thr = min(max(sim.throttle, 0.0), 1.0)
        cyl = eng.cylinders[0]
        Pcyl = self._cycle_pressure(ang, cyl, load, thr)
        arm = cyl.piston_area * np.array(
            [cyl.d_displacement_d_theta(math.radians(a % 360.0)) for a in ang])
        g = (Pcyl - PATM) * arm
        dC = ang - 360.0
        cbase = np.where((dC >= -6) & (dC < 150),
                         np.clip((dC + 6) / 8.0, 0, 1) * np.exp(-np.clip(dC, 0, None) / 45.0), 0.0)
        dE = ang - 505.0
        ebase = np.where((dE >= 0) & (dE < 140),
                         np.clip(dE / 4.0, 0, 1) * np.exp(-dE / 22.0), 0.0)
        torque = np.zeros(W); comb = np.zeros(W); exh = np.zeros(W)
        for s in shifts:
            torque += np.roll(g, -s)
            comb += np.roll(cbase, -s)
            exh += np.roll(ebase, -s)
        # amplitude tracks BOTH load and throttle so opening the throttle visibly
        # grows the combustion/exhaust pulses (not just the rpm)
        drive = 0.15 + 0.85 * (0.45 * load + 0.55 * thr)
        comb *= drive
        exh = self._smooth(exh * drive, max(3, int(W * 0.05)))
        ivl = self._valve_lift(ang, 700.0, 240.0)
        evl = self._valve_lift(ang, 500.0, 230.0)
        aud = getattr(self.synth, "last_wave", None) if self.synth else None

        # torque & horsepower curves vs RPM (a live dyno chart) ------------------
        rl = eng.redline_rpm
        rlo = eng.idle_rpm * 0.7
        rpms = np.linspace(rlo, rl, W)
        peak = eng.ve_peak_frac * rl
        width = max(eng.ve_width_frac * rl, 1.0)
        tq = eng.ve_floor + (eng.ve_max - eng.ve_floor) * np.exp(-((rpms - peak) / width) ** 2)
        if eng.induction != "na" and eng.boost_bar > 0:
            rf = rpms / rl
            spool = (np.clip((rf - eng.turbo_spool_frac) / max(eng.turbo_spool_width, 1e-3),
                             0, 1) if eng.induction == "turbo" else rf)
            tq = tq * (1.0 + 0.8 * eng.boost_bar * spool)
        # WOT reference maxima (fixed scale), then scale the drawn curves by the
        # live throttle so they shrink/grow as you lift/press
        tq_ref = max(tq.max(), 1e-9)
        hp_ref = max((tq * rpms).max(), 1e-9)
        tqd = tq * (0.10 + 0.90 * thr)
        tq_n = tqd / tq_ref
        hp_n = (tqd * rpms) / hp_ref
        rpmfrac = float(np.clip((sim.rpm - rlo) / max(rl - rlo, 1.0), 0, 1))
        # spark-advance timing: ignition spike vs the valve-event reference ------
        adv = 15.0 + 22.0 * min(sim.rpm / max(rl, 1.0), 1.0)
        dS = ((ang - (360.0 - adv) + 360.0) % 720.0) - 360.0
        ign = np.exp(-(dS / 9.0) ** 2)
        vref = np.maximum(ivl, evl) * 0.7

        # --- top: master audio output waveform ----------------------------------
        self._ascope(x0, topy, fullw, toph, "WAVEFORM · master audio output",
                     [(aud, (120, 230, 150))], bipolar=True)
        cw = (fullw - 2 * gap) / 3.0
        rowy = topy + toph + gap
        rh = (rect.bottom - pad - rowy - gap) / 2.0
        cx = [x0, x0 + cw + gap, x0 + 2 * (cw + gap)]
        # --- row 2: firing pulses · exhaust flow · valve lift -------------------
        self._ascope(cx[0], rowy, cw, rh, "FIRING PULSES · cylinder combustion",
                     [(comb, (255, 150, 70))])
        self._ascope(cx[1], rowy, cw, rh, "EXHAUST FLOW · system pressure",
                     [(exh, (255, 165, 70))])
        self._ascope(cx[2], rowy, cw, rh, "VALVE LIFT · intake / exhaust",
                     [(ivl, (110, 220, 130)), (evl, (240, 120, 120))])
        # --- row 3: torque/hp · cylinder pressure · spark advance ---------------
        rowy2 = rowy + rh + gap
        self._ascope(cx[0], rowy2, cw, rh, "TORQUE / HP · output curves",
                     [(tq_n, (255, 190, 70)), (hp_n, (90, 200, 255))])
        curx = int(cx[0] + 1 + rpmfrac * (cw - 3))
        pygame.draw.line(self.screen, (255, 255, 255),
                         (curx, int(rowy2 + 18)), (curx, int(rowy2 + rh - 4)), 1)
        self.screen.blit(self.font_small.render("T", True, (255, 190, 70)),
                         (int(cx[0] + cw - 40), int(rowy2 + 3)))
        self.screen.blit(self.font_small.render("HP", True, (90, 200, 255)),
                         (int(cx[0] + cw - 26), int(rowy2 + 3)))
        self._ascope(cx[1], rowy2, cw, rh, "CYLINDER PRESSURE · 4-stroke",
                     [(Pcyl - PATM, (255, 120, 160))])
        self._ascope(cx[2], rowy2, cw, rh, "SPARK ADVANCE · ignition timing",
                     [(vref, (110, 200, 130)), (ign, (255, 90, 90))])
        self.screen.blit(self.font_small.render(f"adv {adv:.0f}°", True, ACCENT),
                         (int(cx[2] + cw - 60), int(rowy2 + rh - 16)))

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
        db = ez('db', self._exhaust_db())
        h2o = ez('h2o', getattr(self.sim, 'coolant_c', 88.0), 0.06)
        oil = ez('oil', getattr(self.sim, 'oil_c', 85.0), 0.06)
        # (label, value-text, fraction 0..1, danger?)  Seven instruments — the
        # thermal model gives us real water/oil temps, so they get REAL dials
        # (needle sweeps 40..130 C, red past ~105/115).
        gauges = [
            ("MAP", f"{mapk:.0f}", mapk / 250.0, mapk > 200),
            ("VE", f"{ve:.0f}%", ve / 120.0, False),
            ("AFR", f"{afr:.1f}", (afr - 10.0) / 6.0, afr < 11.5),
            ("O2", f"{o2:.1f}", o2 / 21.0, False),
            ("H2O", f"{h2o:.0f}°", (h2o - 40.0) / 90.0, h2o > 105),
            ("OIL", f"{oil:.0f}°", (oil - 40.0) / 90.0, oil > 115),
            ("dB", f"{db:.0f}", (db - 60.0) / 60.0, db > 108),
        ]
        r = 27
        cy = top_y + r + 6
        x0 = rect.x + 16
        gap = (rect.width - 32) / 7.0
        fi = eng.induction != "na"
        n_gauges = 6 if fi else 7              # last slot becomes the FI visualiser
        for k in range(n_gauges):
            lab, val, frac, danger = gauges[k]
            self._air_gauge(x0 + gap * (k + 0.5), cy, r, frac, lab, val, danger)
        if fi:
            # Spin the wheel at the REAL shaft speed: the turbine turns
            # (shaft_rpm / engine_rpm)x faster than the crank, so the blades
            # blur as the turbo spools — a live turbo-speed tachometer.
            fi_rpm = ez('fi_rpm', t.get('fi_rpm', 0.0), 0.18)
            ratio = fi_rpm / max(self.sim.rpm, 1.0)
            # Forza: freeze the turbo visualiser (only pistons + dashboard move)
            spin = 0.0 if self.telemetry_mode else self.sim.crank_angle * max(ratio, 0.4)
            load = (self.sim.boost / max(eng.boost_bar, 0.05)) if eng.boost_bar else 0.0
            fcx, fcy = x0 + gap * 6.5, cy
            # no inline label — the rpm/boost readouts go below, clear of the wheel
            if eng.induction == "turbo":
                self._draw_turbo(fcx, fcy, r - 2, spin, load, label=False)
            else:
                self._draw_blower(fcx, fcy, r - 4, spin, load,
                                  centri=(eng.induction == "centrifugal"), label=False)
            # shaft-speed + boost, stacked BELOW the wheel so nothing overlaps it
            rpm_txt = (f"{fi_rpm / 1000.0:.0f}k rpm" if fi_rpm >= 1000.0 else "0 rpm")
            rt = self.font_small.render(rpm_txt, True, GOOD)
            self.screen.blit(rt, (int(fcx) - rt.get_width() // 2, int(fcy + r + 4)))
            bt = self.font_small.render(f"{self.sim.boost:.2f} bar", True, ACCENT)
            self.screen.blit(bt, (int(fcx) - bt.get_width() // 2, int(fcy + r + 17)))

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
        self._draw_tach(cx, cy, r, sim.rpm, eng.redline_rpm,
                        sim.drivetrain.speed_kmh)
        # --- spinning road wheel — or a PROPELLER for aircraft engines ---
        if getattr(eng, "is_radial", False) or \
                getattr(eng, "gearbox_type", "") == "aircraft":
            self._draw_propeller(rect.x + 48, rect.y + 78, 34, self._wheel_ang,
                                 sim.drivetrain.speed_kmh)
        else:
            self._draw_wheel(rect.x + 48, rect.y + 78, 34, self._wheel_ang,
                             sim.drivetrain.speed_kmh)
        # --- throttle / brake pedal bars (opposite corner) ---
        self._draw_pedal_bars(rect.right - 64, rect.y + 52, 96)

        # --- per-cylinder ignition bank (original-game IGNITION lights) ---
        yb = self._draw_ignition_bank(rect.x + 24, rect.y + 220, rect.width - 48,
                                      lights=not self.telemetry_mode)

        # --- digital readouts ---
        tq = self._disp_torque
        hp = nm_to_hp_at(max(tq, 0.0), max(sim.rpm, 1.0))
        dt = sim.drivetrain
        y = yb + 8
        mode = T("Auto") if dt.auto else T("Manual")
        rows = [
            ("RPM", f"{sim.rpm:.0f}", ACCENT),
            ("TORQUE", f"{tq:.0f} Nm  ({nm_to_lbft(tq):.0f} lb-ft)", INK),
            ("POWER", f"{hp:.0f} hp", INK),
            ("THROTTLE", f"{sim.throttle*100:.0f} %", INK),
            ("GEAR", f"{dt.gear_name} {mode}"
                     f"  [{self.tr(_GBX_LABEL.get(dt.gearbox_type, dt.gearbox_type)).upper()}]",
             GOOD),
            ("SPEED", f"{self._speed_disp(dt.speed_kmh)[0]:.0f} "
                      f"{self._speed_disp(dt.speed_kmh)[1]}", INK),
        ]
        val_x = rect.x + 136
        val_w = rect.right - 22 - val_x
        for label, value, col in rows:
            self.screen.blit(self.font.render(T(label), True, DIM), (rect.x + 24, y))
            self._recess(pygame.Rect(val_x, y - 1, val_w, 19), 4)   # embedded LCD
            self.screen.blit(self.font.render(value, True, col), (val_x + 8, y))
            y += 21

        # --- engine flow / fuel instrument block (the original game's readouts) ---
        t = sim.telemetry()
        y += 5
        pygame.draw.line(self.screen, (44, 48, 56),
                         (rect.x + 24, y - 4), (rect.right - 24, y - 4))
        flow = [
            ("MANIFOLD", f"{t['vacuum_inhg']:+.1f} inHg"),
            ("AIR", f"{t['scfm']:.0f} scfm"),
            ("VOL EFF", f"{t['ve_pct']:.0f} %"),
            ("IN AFR", f"{t['afr']:.1f}"),
            ("EX O2", f"{t['o2_pct']:.1f} %"),
            ("FUEL", f"{self._fuel_lph:.1f} L/h"),
        ]
        col_lab = [24, 220]                            # local x within the block
        col_rt = [200, rect.width - 26]                # values right-aligned per column
        fh = ((len(flow) + 1) // 2) * 18

        def _build_flow():
            surf = pygame.Surface((rect.width, fh), pygame.SRCALPHA)
            for i, (lab, val) in enumerate(flow):
                c = i % 2; ry = (i // 2) * 18
                surf.blit(self.font_small.render(T(lab), True, DIM), (col_lab[c], ry))
                vs = self.font_small.render(val, True, INK)
                surf.blit(vs, (col_rt[c] - vs.get_width(), ry))
            return surf
        # Low-Q: these flow/consumption readouts crawl, so rebuild every 6th draw.
        self.screen.blit(self._slow_surf("flow", 6, _build_flow), (rect.x, y))
        y += fh + 2
        odo = self._odo_km * (0.621371 if self.speed_mph else 1.0)
        odo_u = "mi" if self.speed_mph else "km"
        dist_km, fuel_l = self._odo_km, self._fuel_total_l
        # line 1: fuel used · cost · distance, with a RESET button at the far right
        line1 = (f"{T('USED')} {fuel_l:.3f} L · "
                 f"${fuel_l * 1.5:.2f} · {odo:.2f} {odo_u}")
        self.screen.blit(self.font_small.render(line1, True, ACCENT), (rect.x + 24, y))
        rsurf = self.font_small.render(T("RESET"), True, (255, 255, 255))
        rrect = pygame.Rect(rect.right - 26 - (rsurf.get_width() + 18), y - 3,
                            rsurf.get_width() + 18, 20)
        self._trip_reset_rect = rrect
        self._ios_button(rrect, (198, 96, 92), (150, 50, 48), radius=6)  # red glass
        self.screen.blit(rsurf, (rrect.centerx - rsurf.get_width() // 2,
                                 rrect.centery - rsurf.get_height() // 2))
        y += 19
        # line 2: fuel economy (mpg + L/100km) and oil consumed
        if dist_km > 1e-3 and fuel_l > 1e-6:
            mpg = (dist_km * 0.621371) / (fuel_l * 0.264172)
            l100 = fuel_l / dist_km * 100.0
            econ = f"{mpg:.1f} mpg · {l100:.1f} L/100km"
        else:
            econ = "-- mpg · -- L/100km"
        line2 = (f"{econ} · {T('Oil')} {self._oil_total_l * 1000.0:.0f} mL"
                 f" · {getattr(sim, 'coolant_c', 88.0):.0f}/"
                 f"{getattr(sim, 'oil_c', 85.0):.0f}°C")
        self.screen.blit(self.font_small.render(line2, True, ACCENT), (rect.x + 24, y))
        y += 19

        # Status rows below the exhaust-flow scope, each column CENTRED under its
        # third of the chart (so the block lines up with the chart, not jammed to
        # one side), and lifted off the very bottom edge.
        status_y2 = rect.bottom - 36
        status_y1 = status_y2 - 22
        scope_h = max(34, min(74, int(status_y1 - 10 - y)))
        chart_x, chart_w = rect.x + 24, rect.width - 48
        if self.telemetry_mode:                  # Forza: no scope at all, just a frame
            self._scope_tile_rects = {}
            pygame.draw.rect(self.screen, (12, 13, 16), (chart_x, y, chart_w, scope_h))
            pygame.draw.rect(self.screen, (44, 48, 56), (chart_x, y, chart_w, scope_h), 1)
        else:
            self._draw_focus_scope(chart_x, y, chart_w, scope_h)
        row1 = [("IGNITION", sim.ignition_on, GOOD, WARN),
                ("STARTER", sim.starter_engaged, ACCENT, DIM),
                ("REV LIMIT", sim._fuel_cut, WARN, DIM)]
        row2 = [("CLUTCH IN", dt.clutch < 0.5, ACCENT, DIM),
                ("IN GEAR", dt.gear > 0, GOOD, DIM),
                ("AUDIO", self.synth.enabled and self.synth.volume > 0, GOOD, DIM)]

        # FIXED column starts so row 2 lines up directly under row 1
        col_x = [int(chart_x + 4 + j * chart_w / 3.0) for j in range(3)]

        def status_row(items, yy):
            for j, (lab, on, c1, c2) in enumerate(items):
                self._status_dot(col_x[j], yy, T(lab), on, c1, c2)
        status_row(row1, status_y1)
        status_row(row2, status_y2)

    def _draw_wheel(self, cx, cy, R, ang, speed_kmh):
        """A Lamborghini-style 5-spoke forged wheel — black tyre with the red
        Pirelli P Zero sidewall stripe + lettering, a dark carbon 5-twin-spoke
        star, a red brake caliper and a drilled disc."""
        cx, cy = int(cx), int(cy)
        sc = self.screen
        if self.low_quality:
            # static, flat wheel — no spin, no rotozoom lettering, no shading
            pygame.draw.circle(sc, (12, 13, 16), (cx, cy), R + 4)          # tyre
            pygame.draw.circle(sc, (44, 48, 56), (cx, cy), int(R * 0.8))   # rim
            pygame.draw.circle(sc, (70, 75, 88), (cx, cy), int(R * 0.8), 1)
            for k in range(5):                                            # flat spokes
                a = k * (2 * math.pi / 5.0)
                pygame.draw.line(sc, (90, 95, 108), (cx, cy),
                                 (int(cx + R * 0.74 * math.cos(a)),
                                  int(cy + R * 0.74 * math.sin(a))), 3)
            pygame.draw.circle(sc, (28, 30, 36), (cx, cy), int(R * 0.22))  # hub
            sval, sunit = self._speed_disp(speed_kmh)
            lab = self.font_small.render(f"{sval:.0f} {sunit}", True, DIM)
            sc.blit(lab, (cx - lab.get_width() // 2, cy + R + 6))
            return
        # tyre (black) + soft sheen
        pygame.draw.circle(sc, (10, 11, 13), (cx, cy), R + 4)
        sh = pygame.Surface((2 * (R + 5), 2 * (R + 5)), pygame.SRCALPHA)
        pygame.draw.circle(sh, (255, 255, 255, 26), (R + 5 - 3, R + 5 - 4), R + 2, 3)
        sc.blit(sh, (cx - R - 5, cy - R - 5))
        # red Pirelli rim-edge stripe
        pygame.draw.circle(sc, (224, 46, 40), (cx, cy), int(R * 0.86), 3)
        # red P ZERO / PIRELLI lettering on the sidewall (orbits with the wheel)
        for txt, off in (("P ZERO", 0.0), ("PIRELLI", math.pi)):
            lg = self.font_small.render(txt, True, (236, 58, 48))
            zoom = min((R * 0.95) / max(lg.get_width(), 1), 0.62)
            lg = pygame.transform.rotozoom(lg, -math.degrees(ang + off) - 90, zoom)
            a = ang + off
            lx, ly = cx + R * 0.93 * math.cos(a), cy + R * 0.93 * math.sin(a)
            sc.blit(lg, (int(lx - lg.get_width() / 2), int(ly - lg.get_height() / 2)))
        # rim well (dark)
        pygame.draw.circle(sc, (16, 17, 20), (cx, cy), int(R * 0.80))
        # red brake caliper + drilled disc behind the spokes
        cal = pygame.Rect(cx - int(R * 0.66), cy - int(R * 0.66), int(R * 1.32), int(R * 1.32))
        pygame.draw.arc(sc, (206, 40, 34), cal, math.radians(120), math.radians(168), 5)
        pygame.draw.circle(sc, (44, 48, 56), (cx, cy), int(R * 0.6))
        pygame.draw.circle(sc, (66, 72, 84), (cx, cy), int(R * 0.6), 1)
        for k in range(12):
            da = k * (2 * math.pi / 12)
            pygame.draw.circle(sc, (18, 20, 26),
                               (int(cx + R * 0.46 * math.cos(da)),
                                int(cy + R * 0.46 * math.sin(da))), 1)
        # five twin (forked) carbon spokes (spin at the SAME rate as the tyre logo)
        rr = R * 0.78
        for k in range(5):
            a = ang + k * (2 * math.pi / 5.0)
            ca, sa = math.cos(a), math.sin(a)
            pa, ps = -sa, ca
            hub = (cx + R * 0.18 * ca, cy + R * 0.18 * sa)
            fork = (cx + R * 0.42 * ca, cy + R * 0.42 * sa)

            def bar(p0, w0, p1, w1, perp, col):
                px, py = perp
                pygame.draw.polygon(sc, col, [
                    (p0[0] + px * w0, p0[1] + py * w0), (p1[0] + px * w1, p1[1] + py * w1),
                    (p1[0] - px * w1, p1[1] - py * w1), (p0[0] - px * w0, p0[1] - py * w0)])
            bar(hub, R * 0.15, fork, R * 0.085, (pa, ps), (46, 49, 56))   # stem
            for sgn in (-1, 1):                          # two prongs to the rim
                a2 = a + sgn * 0.30
                ca2, sa2 = math.cos(a2), math.sin(a2)
                tip = (cx + rr * ca2, cy + rr * sa2)
                bar(fork, R * 0.07, tip, R * 0.045, (-sa2, ca2), (42, 45, 52))
                pygame.draw.line(sc, (96, 100, 112), fork, tip, 1)       # carbon sheen
            pygame.draw.line(sc, (104, 108, 120), hub, fork, 1)
        # rim lip + centre cap with the marque dome
        pygame.draw.circle(sc, (150, 156, 170), (cx, cy), int(rr), 2)
        pygame.draw.circle(sc, (70, 75, 88), (cx, cy), int(rr), 1)
        hub = int(R * 0.2)
        pygame.draw.circle(sc, (28, 30, 36), (cx, cy), hub)
        pygame.draw.circle(sc, (120, 126, 140), (cx, cy), hub, 1)
        pygame.draw.circle(sc, (180, 186, 200), (cx - 2, cy - 2), max(1, int(hub * 0.5)))
        sval, sunit = self._speed_disp(speed_kmh)
        lab = self.font_small.render(f"{sval:.0f} {sunit}", True, DIM)
        sc.blit(lab, (cx - lab.get_width() // 2, cy + R + 6))

    def _draw_propeller(self, cx, cy, R, ang, speed_kmh):
        """A spinning 3-blade aircraft PROPELLER (replaces the road wheel for
        aircraft engines): tapered blades with yellow tips, a metal spinner, a
        motion-blur disc, and the airspeed readout."""
        sc = self.screen
        cx, cy = int(cx), int(cy)
        if self.low_quality:
            ang = 0.0                                     # frozen prop in low-Q
        pygame.draw.circle(sc, (12, 14, 18), (cx, cy), R + 4)
        pygame.draw.circle(sc, (40, 44, 54), (cx, cy), R + 4, 1)
        spd = min(speed_kmh / 240.0, 1.0)                 # prop blur with airspeed
        if spd > 0.05 and not self.low_quality:
            bl = pygame.Surface((2 * R, 2 * R), pygame.SRCALPHA)
            pygame.draw.circle(bl, (120, 130, 150, int(70 * spd)), (R, R), int(R * 0.92))
            sc.blit(bl, (cx - R, cy - R))
        for k in range(3):
            a = ang + k * (2 * math.pi / 3)
            ca, sa = math.cos(a), math.sin(a)
            pa, ps = -sa, ca
            tip = (cx + ca * R * 0.95, cy + sa * R * 0.95)
            root = (cx + ca * R * 0.16, cy + sa * R * 0.16)
            wr, wt = R * 0.18, R * 0.06
            blade = [(root[0] + pa * wr, root[1] + ps * wr),
                     (tip[0] + pa * wt, tip[1] + ps * wt),
                     (tip[0] - pa * wt, tip[1] - ps * wt),
                     (root[0] - pa * wr, root[1] - ps * wr)]
            pygame.draw.polygon(sc, (42, 45, 54), blade)
            pygame.draw.polygon(sc, (22, 24, 30), blade, 1)
            pygame.draw.line(sc, (96, 102, 116), (root[0] + pa * wr, root[1] + ps * wr),
                             (tip[0] + pa * wt, tip[1] + ps * wt), 1)
            pygame.draw.circle(sc, (224, 200, 60), (int(tip[0]), int(tip[1])), 2)
        sr = int(R * 0.22)                                # spinner cone
        sc.blit(self._grad_surf(2 * sr, 2 * sr, (172, 178, 192), (70, 76, 90), sr,
                                gloss=True), (cx - sr, cy - sr))
        pygame.draw.circle(sc, (40, 44, 54), (cx, cy), sr, 1)
        pygame.draw.circle(sc, (224, 228, 238), (cx - 2, cy - 2), max(1, sr // 3))
        sval, sunit = self._speed_disp(speed_kmh)
        lab = self.font_small.render(f"{sval:.0f} {sunit}", True, DIM)
        sc.blit(lab, (cx - lab.get_width() // 2, cy + R + 6))

    def _draw_pedal_bars(self, x, y, h):
        """Two inset vertical bars — throttle (green) and brake (red) — showing
        the live pedal positions."""
        sim = self.sim
        th = min(max(sim.throttle, 0.0), 1.0)
        br = min(max(getattr(sim.drivetrain, "brake", 0.0),
                     getattr(self, "_touch_brake", 0.0)), 1.0)
        self.screen.blit(self.font_small.render(self.tr("THR  BRK"), True, DIM),
                         (x - 2, y - 16))
        for i, (val, col) in enumerate(((th, GOOD), (br, (232, 92, 80)))):
            bx = x + i * 26
            self._recess(pygame.Rect(bx, y, 17, h), 3)
            fh = int((h - 4) * val)
            if fh > 0:
                pygame.draw.rect(self.screen, col, (bx + 2, y + h - 2 - fh, 13, fh),
                                 border_radius=2)

    def _draw_tach(self, cx, cy, r, rpm, redline, speed_kmh=0.0):
        """A glossy iOS 6 / aircraft-style tachometer dial (needle = rpm, the
        centre digital window shows road SPEED — rpm has its own readout)."""
        cx, cy = int(cx), int(cy)
        start = math.radians(225)
        span = math.radians(270)
        max_rpm = math.ceil((redline + 500) / 1000.0) * 1000

        def pt(rad, ang):
            return (cx + rad * math.cos(ang), cy - rad * math.sin(ang))

        if self.low_quality:                          # flat tach: face + numbers + needle
            pygame.draw.circle(self.screen, (40, 44, 52), (cx, cy), r + 6)
            pygame.draw.circle(self.screen, (20, 22, 27), (cx, cy), r)
            for i in range(14):                       # flat redline band
                k = redline + (max_rpm - redline) * i / 13.0
                a = start - span * (k / max_rpm)
                pygame.draw.line(self.screen, (212, 60, 52), pt(r - 3, a), pt(r - 8, a), 4)
            for k in range(0, int(max_rpm) + 1, 1000):
                a = start - span * (k / max_rpm)
                col = WARN if k >= redline else (200, 206, 216)
                num = self.font.render(str(k // 1000), True, col)
                nx, ny = pt(r - 30, a)
                self.screen.blit(num, (nx - num.get_width() // 2, ny - num.get_height() // 2))
            frac = min(max(rpm / max_rpm, 0.0), 1.0)
            a = start - span * frac
            ct, st = math.cos(a), math.sin(a)
            pygame.draw.line(self.screen, WARN if rpm >= redline else ACCENT,
                             (cx, cy), (cx + (r - 16) * ct, cy - (r - 16) * st), 3)
            pygame.draw.circle(self.screen, (120, 126, 140), (cx, cy), 6)
            sval, sunit = self._speed_disp(speed_kmh)
            txt = self.font_small.render(f"{int(sval)} {sunit}", True, ACCENT)
            self.screen.blit(txt, (cx - txt.get_width() // 2, cy + int(r * 0.40)))
            cap = self.font_small.render(self.tr("x1000 rpm"), True, DIM)
            self.screen.blit(cap, (cx - cap.get_width() // 2, cy + int(r * 0.40) + 18))
            return

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
            if major:                                # tach numbers incl. 0 (lower-left)
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

        # digital SPEED window — narrow, in the lower waist (the bottom-centre gap
        # between the "0" at lower-left and "8" at lower-right is empty)
        sval, sunit = self._speed_disp(speed_kmh)
        win = pygame.Rect(cx - 33, cy + int(r * 0.40), 66, 20)
        self._recess(win, 5)
        txt = self.font_small.render(f"{int(sval)} {sunit}", True, ACCENT)
        if txt.get_width() > win.w - 8:
            txt = pygame.transform.smoothscale(
                txt, (win.w - 8, int(txt.get_height() * (win.w - 8) / txt.get_width())))
        self.screen.blit(txt, (win.centerx - txt.get_width() // 2,
                               win.centery - txt.get_height() // 2))
        cap = self.font_small.render(self.tr("x1000 rpm"), True, DIM)
        self.screen.blit(cap, (cx - cap.get_width() // 2, win.bottom + 2))

    def _status_dot(self, x, y, label, on, on_col, off_col):
        col = on_col if on else off_col
        pygame.draw.circle(self.screen, col, (x + 7, y + 9), 7)
        self.screen.blit(self.font_small.render(label, True, INK), (x + 22, y))

    # ------------------------------------------------------------------ loop
    def run(self):
        # Render is DECOUPLED from physics.  Physics + the audio-thread state stay
        # at the full 60 Hz; in Low-Q (phones / Forza) the heavy 2D draw is capped
        # to 30 fps so it stops hogging the CPU/GIL from the real-time audio — the
        # single biggest win for weak SoCs (e.g. Snapdragon 845).  Normal mode is
        # untouched: it draws every frame.  Purely a scheduling change — no physics
        # or audio fidelity is lost, and a phone can't tell 30 vs 60 fps here.
        render_accum = 0.0
        RENDER_PERIOD = 1.0 / 30.0
        while self.running:
            dt = self.clock.tick(FPS) / 1000.0
            dt = min(dt, 0.05)              # clamp huge hitches
            self.handle_events()
            self.update(dt)
            if self.low_quality:
                render_accum += dt
                if render_accum >= RENDER_PERIOD:
                    render_accum = 0.0
                    self.draw()
            else:
                render_accum = 0.0
                self.draw()
        self.synth.stop()
        pygame.quit()


def main():
    App().run()


if __name__ == "__main__":
    main()
