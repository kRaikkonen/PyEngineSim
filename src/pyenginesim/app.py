"""PyEngineSim on iOS -- a shell around engine_sim.carmode.CarMode.

Deliberately plain. Car mode has no graphics by design, and in a car you want
a screen you never have to look at: pick an engine, press Start, drive. The
readout exists to prove the link is alive, not to be watched.

Everything real happens in ``engine_sim`` -- this file only wires a Toga view
to :class:`~engine_sim.carmode.CarMode` and hands the synthesizer an
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
from engine_sim.carmode import CarMode
from engine_sim.obd import CAR_PROFILES, OBDTelemetry, RpmMap

LOG_PATH = os.path.join(os.path.expanduser("~"), "Documents",
                        "pyenginesim.log")


def log(msg):
    """Append a line to a file inside the app container.

    print() on iOS goes through NSLog and is easy to lose; a file in Documents
    survives, can be read out of the simulator container, and is the only
    diagnostic that works on a phone in a car with no console attached.
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


class PyEngineSim(toga.App):
    def startup(self):
        log("startup")
        self.mode = None
        self.telemetry = None
        self.fake = None
        self._task = None

        labelled = [(k, "%s  --  %s" % (k, label))
                    for k, label, _ in presets.PRESETS]
        self._keys = [k for k, _ in labelled]

        self.engine_sel = toga.Selection(
            items=[text for _, text in labelled],
            on_change=self._engine_changed,
        )
        try:
            self.engine_sel.value = next(
                t for k, t in labelled if k == DEFAULT_ENGINE)
        except StopIteration:
            pass

        self.host_in = toga.TextInput(value=DEFAULT_HOST)
        self.port_in = toga.TextInput(value=str(DEFAULT_PORT))
        self.demo_sw = toga.Switch("Demo (no dongle)", value=False)
        self.stretch_sw = toga.Switch("Stretch rpm to its redline", value=True)

        self.button = toga.Button("Start", on_press=self._toggle)
        self.status = toga.Label("idle")
        self.detail = toga.Label("")

        box = toga.Box(style=Pack(direction="column"))
        for w in (toga.Label("Engine"), self.engine_sel,
                  toga.Label("WiFi ELM327 address"), self.host_in, self.port_in,
                  self.demo_sw, self.stretch_sw,
                  self.button, self.status, self.detail):
            box.add(w)

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = box
        self.main_window.show()

        auto = os.environ.get("PYENGINESIM_AUTOSTART", "")
        if auto.lower() == "demo":
            self.demo_sw.value = True

    async def on_running(self):  # noqa: D401
        """Autostart, once there is actually a running event loop.

        In the car you do not want to be tapping a screen, and in the simulator
        there is nothing to tap with -- the same switch serves both.  This has
        to happen HERE and not in startup(): during startup the loop is not
        running yet, so anything scheduled on it is simply never executed.
        """
        log("on_running fired; autostart=%r"
            % os.environ.get("PYENGINESIM_AUTOSTART", ""))
        if os.environ.get("PYENGINESIM_AUTOSTART", ""):
            try:
                self._start()
            except Exception as exc:
                import traceback
                log("autostart FAILED: %s" % exc)
                log(traceback.format_exc())

    # ------------------------------------------------------------- helpers
    def _selected_key(self) -> str:
        value = self.engine_sel.value or ""
        return value.split("  --  ")[0].strip() or DEFAULT_ENGINE

    def _engine_changed(self, widget):
        if self.mode is not None:
            self.mode.set_engine(self._selected_key())

    # -------------------------------------------------------- start / stop
    def _toggle(self, widget):
        if self.mode is None:
            self._start()
        else:
            self._stop()

    def _start(self):
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

        self.mode = CarMode(engine_key=self._selected_key(),
                            telemetry=self.telemetry, rpm_map=rmap,
                            synth_factory=self._make_synth)
        self.mode.start()

        self.button.text = "Stop"
        self._last = time.monotonic()
        # _start is only ever reached from a button press or on_running, both
        # of which run inside the loop -- so ask for the RUNNING one.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self.loop
        self._task = loop.create_task(self._tick_loop())
        log("started: %s, link %s:%s, demo=%s"
            % (self.mode.label, host, port, self.demo_sw.value))

    def _make_synth(self, sim):
        """Build the synth at the rate the DEVICE negotiated, not a guess."""
        from .ios_audio import IOSAudioSink
        sink = IOSAudioSink()
        log("audio session negotiated %d Hz" % sink.sample_rate)
        synth = Synthesizer(sim, sample_rate=sink.sample_rate)
        synth.pov = "cockpit"
        synth.sink = sink
        self._sink = sink
        return synth

    def _stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self.mode is not None:
            self.mode.stop()
            self.mode = None
        sink = getattr(self, "_sink", None)
        if sink is not None:
            sink.stop()
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

    # ------------------------------------------------------------ the loop
    async def _tick_loop(self):
        period = 1.0 / TICK_HZ
        shown = 0.0
        logged = 0.0
        try:
            while self.mode is not None:
                now = time.monotonic()
                self.mode.update(now - self._last)
                self._last = now
                if now - shown > 0.25:
                    shown = now
                    self._refresh()
                if now - logged > 5.0:
                    logged = now
                    st = self.mode.status()
                    sink = getattr(self, "_sink", None)
                    log("%s %.0f->%.0f rpm  pedal %.0f%%  g%d  link %.0f Hz"
                        "  audio blocks=%s under=%s"
                        % (st["link"], st["car_rpm"], st["sim_rpm"],
                           st["pedal"] * 100.0, st["gear"], st["hz"],
                           getattr(sink, "blocks", "-"),
                           getattr(sink, "underruns", "-")))
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            pass

    def _refresh(self):
        st = self.mode.status()
        self.status.text = "%s   %.0f -> %.0f rpm" % (
            st["link"], st["car_rpm"], st["sim_rpm"])
        sink = getattr(self, "_sink", None)
        audio = "" if sink is None else "  %d Hz  blocks %d  under %d" % (
            sink.sample_rate, sink.blocks, sink.underruns)
        self.detail.text = "pedal %3.0f%%  g%d  %+.2f bar  %.0f Hz link%s" % (
            st["pedal"] * 100.0, st["gear"], st["boost_bar"], st["hz"], audio)


def main():
    return PyEngineSim()
