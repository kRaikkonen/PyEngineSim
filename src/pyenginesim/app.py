"""PyEngineSim on iOS -- a shell around engine_sim.carmode.CarMode.

Deliberately plain, and deliberately built from BUTTONS.  Toga's Selection
renders as a picker wheel on iOS that can be impossible to dismiss, and a
control you cannot close is worse than useless in a car.  Every choice here is
a button that cycles: one tap, big target, no modal state.

Everything real happens in ``engine_sim``; this file only wires a Toga view to
:class:`~engine_sim.carmode.CarMode` and hands the synthesizer an
:class:`~pyenginesim.ios_audio.IOSAudioSink`.
"""

from __future__ import annotations

import asyncio
import os
import time

import toga
from toga.style import Pack

from engine_sim import presets
from engine_sim.audio import Synthesizer
from engine_sim.carmode import CarMode, ManualSource
from engine_sim.obd import CAR_PROFILES, OBDTelemetry, RpmMap

LOG_PATH = os.path.join(os.path.expanduser("~"), "Documents",
                        "pyenginesim.log")


def log(msg):
    """Append a line to a file inside the app container.

    print() on iOS goes through NSLog and is easy to lose; a file in Documents
    survives, can be read out of the container, and is the only diagnostic that
    works on a phone in a car with no console attached.
    """
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            fh.write("%.3f  %s\n" % (time.time(), msg))
    except Exception:
        pass
    print("[pyenginesim] %s" % msg, flush=True)


TICK_HZ = 50.0                 # control loop; the synth runs at audio rate
DEFAULT_ENGINE = "rs3"         # the 8Y RS3 five-cylinder
DEFAULT_HOST = "192.168.0.10"  # what almost every WiFi ELM327 answers on
DEFAULT_PORT = 35000
DEFAULT_RATE = 32000

POVS = ("chase", "cockpit", "trackside")
SPEAKERS = ("auto", "small", "full-range")
RATES = ("device", "32000", "24000")
BLOCKS = ("256", "512")
# a thumbful worth cycling; anything else by typing its key
QUICK = ("rs3", "s1", "aven", "8", "9", "conti", "787b")


class _Cycle:
    """A button that steps through a fixed list of choices."""

    def __init__(self, label, options, on_change=None):
        self.label = label
        self.options = list(options)
        self.index = 0
        self.on_change = on_change
        self.button = toga.Button(self._text(), on_press=self._press)

    @property
    def value(self):
        return self.options[self.index]

    def _text(self):
        return "%s: %s" % (self.label, self.options[self.index])

    def _press(self, widget):
        self.index = (self.index + 1) % len(self.options)
        self.button.text = self._text()
        if self.on_change:
            self.on_change(self.value)


class PyEngineSim(toga.App):
    def startup(self):
        self.mode = None
        self.telemetry = None
        self.fake = None
        self._task = None
        self._sink = None

        self._keys = [k for k, _, _ in presets.PRESETS]
        self._labels = {k: l for k, l, _ in presets.PRESETS}
        self.engine_key = (DEFAULT_ENGINE if DEFAULT_ENGINE in self._labels
                           else self._keys[0])

        self.engine_lbl = toga.Label(self._engine_text())
        eng_row = toga.Box(style=Pack(direction="row"))
        eng_row.add(toga.Button("< prev", on_press=lambda w: self._step(-1)))
        eng_row.add(toga.Button("next >", on_press=lambda w: self._step(1)))
        eng_row.add(toga.Button("quick", on_press=self._quick))
        self.engine_in = toga.TextInput(value=self.engine_key,
                                        on_confirm=self._typed)

        self.pov = _Cycle("listener", POVS, self._pov_changed)
        self.spk = _Cycle("speaker", SPEAKERS, self._spk_changed)
        self.rate = _Cycle("rate", RATES)
        self.block = _Cycle("block", BLOCKS)

        self.manual_sw = toga.Switch("Manual (hand throttle)", value=True)
        self.rpm_slider = toga.Slider(min=500, max=9000, value=1200,
                                      on_change=self._slider_changed)
        self.thr_slider = toga.Slider(min=0.0, max=1.0, value=0.3,
                                      on_change=self._slider_changed)
        self.rpm_label = toga.Label("rpm 1200")
        self.thr_label = toga.Label("throttle 30%")

        self.host_in = toga.TextInput(value=DEFAULT_HOST)
        self.port_in = toga.TextInput(value=str(DEFAULT_PORT))
        self.demo_sw = toga.Switch("Demo (no dongle)", value=False)
        self.stretch_sw = toga.Switch("Stretch rpm to its redline", value=True)

        self.button = toga.Button("Start", on_press=self._toggle)
        self.status = toga.Label("idle")
        self.detail = toga.Label("")
        self.audio_lbl = toga.Label("")

        box = toga.Box(style=Pack(direction="column"))
        for w in (self.engine_lbl, eng_row, self.engine_in,
                  self.pov.button, self.spk.button,
                  self.rate.button, self.block.button,
                  self.manual_sw,
                  self.rpm_label, self.rpm_slider,
                  self.thr_label, self.thr_slider,
                  self.host_in, self.port_in,
                  self.demo_sw, self.stretch_sw,
                  self.button, self.status, self.detail, self.audio_lbl):
            box.add(w)

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = box
        self.main_window.show()
        self._sync_slider_range()

        if os.environ.get("PYENGINESIM_AUTOSTART", "").lower() == "demo":
            self.demo_sw.value = True
            self.manual_sw.value = False

    async def on_running(self):
        """Autostart, once there is actually a running event loop.

        It has to happen HERE and not in startup(): during startup the loop is
        not running yet, so anything scheduled on it never executes.
        """
        log("on_running; autostart=%r"
            % os.environ.get("PYENGINESIM_AUTOSTART", ""))
        if os.environ.get("PYENGINESIM_AUTOSTART", ""):
            try:
                self._start()
            except Exception as exc:
                import traceback
                log("autostart FAILED: %s" % exc)
                log(traceback.format_exc())

    # ------------------------------------------------------------ the engine
    def _engine_text(self):
        return "%s  --  %s" % (self.engine_key,
                               self._labels.get(self.engine_key, "?"))

    def _set_engine(self, key):
        if key not in self._labels:
            self.status.text = "no such engine: %s" % key
            return
        self.engine_key = key
        self.engine_lbl.text = self._engine_text()
        self.engine_in.value = key
        self._sync_slider_range()
        if self.mode is not None:
            self.mode.set_engine(key)
        log("engine -> %s" % key)

    def _step(self, delta):
        i = self._keys.index(self.engine_key)
        self._set_engine(self._keys[(i + delta) % len(self._keys)])

    def _quick(self, widget):
        picks = [k for k in QUICK if k in self._labels]
        if not picks:
            return
        i = picks.index(self.engine_key) if self.engine_key in picks else -1
        self._set_engine(picks[(i + 1) % len(picks)])

    def _typed(self, widget):
        self._set_engine((self.engine_in.value or "").strip())

    def _sync_slider_range(self):
        """Slider spans THIS engine's rev range, so the ends mean something."""
        try:
            eng = presets.ALL[self.engine_key]()
        except Exception:
            return
        lo, hi = float(eng.idle_rpm), float(eng.redline_rpm)
        self.rpm_slider.min = lo
        self.rpm_slider.max = hi
        if not (lo <= self.rpm_slider.value <= hi):
            self.rpm_slider.value = lo + (hi - lo) * 0.25
        self._slider_changed(None)

    def _slider_changed(self, widget):
        self.rpm_label.text = "rpm %.0f" % self.rpm_slider.value
        self.thr_label.text = "throttle %.0f%%" % (self.thr_slider.value * 100.0)

    # ----------------------------------------------------------- live tweaks
    def _pov_changed(self, value):
        synth = getattr(self.mode, "synth", None) if self.mode else None
        if synth is not None:
            synth.pov = value
        log("pov -> %s" % value)

    def _spk_changed(self, value):
        sink = self._sink
        if sink is None:
            return
        sink.small_speaker = (None if value == "auto" else (value == "small"))
        sink._refresh_route()
        log("speaker -> %s (route %s, compensating=%s)"
            % (value, sink.route, sink._compensate))

    # --------------------------------------------------------- start / stop
    def _toggle(self, widget):
        if self.mode is None:
            self._start()
        else:
            self._stop()

    def _start(self):
        if self.manual_sw.value:
            return self._start_manual()
        host, port = self.host_in.value.strip(), int(self.port_in.value or 0)
        if self.demo_sw.value:
            # The fake adapter runs in-process, so the whole chain can be heard
            # at a desk with no dongle and no car.
            from tools.fake_elm327 import FakeELM327
            self.fake = FakeELM327()
            host, port = self.fake.start()

        self.telemetry = OBDTelemetry(host=host, port=port)
        self.telemetry.start()

        idle_seed, red_seed = CAR_PROFILES["a3"]
        rmap = RpmMap(mode="stretch" if self.stretch_sw.value else "direct",
                      car_idle=idle_seed, car_redline=red_seed)
        self.mode = CarMode(engine_key=self.engine_key,
                            telemetry=self.telemetry, rpm_map=rmap,
                            synth_factory=self._make_synth)
        self.mode.start()
        self._after_start("link %s:%s demo=%s" % (host, port,
                                                  self.demo_sw.value))

    def _start_manual(self):
        """No link at all: the sliders ARE the telemetry."""
        self.telemetry = ManualSource(rpm=self.rpm_slider.value,
                                      throttle=self.thr_slider.value)
        # direct, never stretched: a hand-set rpm is already the rpm to hear
        self.mode = CarMode(engine_key=self.engine_key,
                            telemetry=self.telemetry, rpm_map=RpmMap("direct"),
                            synth_factory=self._make_synth, shift_pop=False)
        self.mode.start()
        self._after_start("manual")

    def _after_start(self, how):
        self.button.text = "Stop"
        self._last = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self.loop
        self._task = loop.create_task(self._tick_loop())
        log("started %s: %s, pov=%s" % (how, self.mode.label, self.pov.value))

    def _make_synth(self, sim):
        """Build the synth at the rate the DEVICE negotiated, not a guess."""
        from .ios_audio import IOSAudioSink
        import engine_sim.audio as _audio
        blk = int(self.block.value)
        if blk != _audio.BLOCK:
            _audio.BLOCK = blk          # read when the Synthesizer is built
        want = self.rate.value
        sink = IOSAudioSink(preferred_rate=(DEFAULT_RATE if want == "device"
                                            else int(want)))
        self._sink = sink
        self._spk_changed(self.spk.value)
        log("audio %d Hz (asked %s), block %d" % (sink.sample_rate, want, blk))
        synth = Synthesizer(sim, sample_rate=sink.sample_rate)
        synth.pov = self.pov.value
        synth.sink = sink
        return synth

    def _stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self.mode is not None:
            self.mode.stop()
            self.mode = None
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        if self.telemetry is not None:
            self.telemetry.stop()
            self.telemetry = None
        if self.fake is not None:
            self.fake.stop()
            self.fake = None
        self.button.text = "Start"
        self.status.text = "idle"
        self.detail.text = ""
        self.audio_lbl.text = ""

    # -------------------------------------------------------------- the loop
    async def _tick_loop(self):
        period = 1.0 / TICK_HZ
        shown = logged = 0.0
        try:
            while self.mode is not None:
                now = time.monotonic()
                if isinstance(self.telemetry, ManualSource):
                    self.telemetry.set(self.rpm_slider.value,
                                       self.thr_slider.value)
                self.mode.update(now - self._last)
                self._last = now
                if now - shown > 0.25:
                    shown = now
                    self._refresh()
                if now - logged > 5.0:
                    logged = now
                    st = self.mode.status()
                    synth = getattr(self.mode, "synth", None)
                    log("%s %.0f->%.0f rpm  load %.0f%%  under %s  %s blk%s"
                        % (st["link"], st["car_rpm"], st["sim_rpm"],
                           100.0 * getattr(synth, "load", 0.0),
                           getattr(self._sink, "underruns", "-"),
                           self.engine_key, self.block.value))
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            pass

    def _refresh(self):
        st = self.mode.status()
        self.status.text = "%s   %.0f -> %.0f rpm" % (
            st["link"], st["car_rpm"], st["sim_rpm"])
        self.detail.text = "pedal %3.0f%%  g%d  %+.2f bar  link %.0f Hz" % (
            st["pedal"] * 100.0, st["gear"], st["boost_bar"], st["hz"])
        sink = self._sink
        synth = getattr(self.mode, "synth", None)
        if sink is None:
            self.audio_lbl.text = ""
        else:
            self.audio_lbl.text = "load %.0f%%  under %d  %d Hz  %s%s" % (
                100.0 * getattr(synth, "load", 0.0), sink.underruns,
                sink.sample_rate,
                str(sink.route).replace("AVAudioSessionPort", ""),
                " +spk" if getattr(sink, "_compensate", False) else "")


def main():
    return PyEngineSim()
