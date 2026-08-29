"""AVAudioEngine output for iOS, plugged into ``Synthesizer.sink``.

There is no PortAudio and no SDL on iOS, so this is the platform backend the
sink seam exists for. It is deliberately a *push* design:

    synth.sink = sink          # sink(block) is called from the render thread
    synth.start()

Python must never run inside a real-time audio callback -- the GIL and the
allocator make that a guaranteed glitch. So instead of an ``AVAudioSourceNode``
render callback, this schedules finished ``AVAudioPCMBuffer``s onto an
``AVAudioPlayerNode``. CoreAudio pulls from its own queue on the real-time
thread; our Python only ever fills buffers ahead of time, and a semaphore
released by each buffer's completion handler blocks the render thread when the
queue is full. That backpressure IS the pacing -- the sink contract explicitly
allows a blocking sink.

The negotiated hardware sample rate is read back from the audio session and
exposed as :attr:`sample_rate`, so the Synthesizer can be built at exactly the
rate the device will play, rather than being resampled by the mixer.

Nothing here touches the voicing; it is strictly downstream of the render.
"""

from __future__ import annotations

import threading

import numpy as np

QUEUE_DEPTH = 6          # buffers in flight: 6 x 256 @ 32 kHz = 48 ms of
                         # cushion.  Python's jitter -- GC, the UI loop,
                         # a slow block -- has to fit inside this or the
                         # speaker hears the gap.  Costs latency, and the
                         # OBD round trip already costs more.
DEFAULT_RATE = 32000     # what we ask the session for (the Android rate too)

# An iPhone's own speaker is a few millimetres across.  A quarter to a half of
# this engine's energy sits below 300 Hz -- an RS3 at 3500 rpm puts 28 % of it
# below 150 Hz -- and the driver cannot move that much air, so it hits its
# excursion limit and distorts.  Headphones and a car stereo can; the phone
# speaker cannot.  So when (and only when) we are playing out of the built-in
# speaker, high-pass away what it can only turn into distortion.
#
# This is loudspeaker correction, not voicing: it happens after the render, and
# the ear-approved sound is untouched on every output that can carry it.
SMALL_SPEAKER_HP_HZ = 320.0
SMALL_SPEAKER_GAIN = 0.85       # a little headroom back for the excursion


def _val(attr):
    """Read an Objective-C property whether rubicon exposes it as a value or
    as a bound method.

    rubicon-objc only knows something is a `@property` if it has the metadata
    to say so; without it, `session.sampleRate` hands back an ObjCBoundMethod
    instead of the number.  Which way round it lands varies by class and by
    rubicon version, so never assume -- ask.
    """
    return attr() if callable(attr) else attr


class IOSAudioSink:
    """Push rendered blocks into AVAudioEngine. Call :meth:`start` first."""

    def __init__(self, preferred_rate=DEFAULT_RATE, queue_depth=QUEUE_DEPTH):
        from rubicon.objc import ObjCClass
        from rubicon.objc.runtime import load_library

        self._av = load_library("AVFoundation")
        self._ObjCClass = ObjCClass

        AVAudioSession = ObjCClass("AVAudioSession")
        self._session = AVAudioSession.sharedInstance()

        # .playback keeps us audible with the ringer switch off and, with the
        # audio background mode in Info.plist, keeps running with the screen
        # locked -- which is the whole point in a car.
        from rubicon.objc import objc_const
        category = objc_const(self._av, "AVAudioSessionCategoryPlayback")
        self._session.setCategory(category, error=None)
        self._session.setPreferredSampleRate(float(preferred_rate), error=None)
        # A small IO buffer keeps the hardware end of the chain short; the
        # queue above absorbs Python's jitter.
        self._session.setPreferredIOBufferDuration(0.010, error=None)
        self._session.setActive(True, error=None)

        # what we actually got -- ask, never assume
        self.sample_rate = int(round(float(_val(self._session.sampleRate))))
        # route-driven small-speaker correction; None = decide from the route
        self.small_speaker = None
        self.route = "?"
        self._hp_ba = None
        self._hp_zi = None
        self._route_countdown = 0
        self._refresh_route()
        self.queue_depth = queue_depth
        self._slots = threading.Semaphore(queue_depth)
        self._started = False
        self._buffers = []
        self._next = 0
        self.blocks = 0
        self.underruns = 0

    # ------------------------------------------------------------- the route
    def _refresh_route(self):
        """Ask the session what we are actually playing out of."""
        try:
            outs = _val(_val(self._session.currentRoute).outputs)
            port = str(_val(outs.objectAtIndex(0).portType)) if len(outs) else ""
        except Exception:
            port = ""
        self.route = port or "?"
        # AVAudioSessionPortBuiltInSpeaker is the only output that needs this;
        # headphones, CarPlay, USB and Bluetooth all reproduce the bottom end.
        if self.small_speaker is None:
            self._compensate = "BuiltInSpeaker" in port
        else:
            self._compensate = bool(self.small_speaker)

    def _apply_small_speaker(self, mono):
        if self._hp_ba is None:
            from engine_sim.audio import _np_butter
            self._hp_ba = _np_butter(
                2, min(SMALL_SPEAKER_HP_HZ / (self.sample_rate * 0.5), 0.95),
                "high")
            self._hp_zi = np.zeros(2)
        from engine_sim.audio import _np_lfilter
        y, self._hp_zi = _np_lfilter(self._hp_ba[0], self._hp_ba[1], mono,
                                     zi=self._hp_zi)
        return y * SMALL_SPEAKER_GAIN

    # ------------------------------------------------------------------ start
    def start(self, frames_per_block=256):
        """Build the engine and the buffer pool. Safe to call once."""
        if self._started:
            return
        ObjCClass = self._ObjCClass
        AVAudioFormat = ObjCClass("AVAudioFormat")
        AVAudioPCMBuffer = ObjCClass("AVAudioPCMBuffer")
        AVAudioEngine = ObjCClass("AVAudioEngine")
        AVAudioPlayerNode = ObjCClass("AVAudioPlayerNode")

        # "standard" format is deinterleaved float32, which is what our blocks
        # convert into cheaply (one column per channel).
        self._fmt = AVAudioFormat.alloc().initStandardFormatWithSampleRate(
            float(self.sample_rate), channels=2)

        self._engine = AVAudioEngine.alloc().init()
        self._player = AVAudioPlayerNode.alloc().init()
        self._engine.attachNode(self._player)
        self._engine.connect(self._player, to=_val(self._engine.mainMixerNode),
                             format=self._fmt)

        # A pool sized to the queue: a buffer can only be refilled once its
        # completion handler has told us CoreAudio is done reading it.
        self._frames = frames_per_block
        self._buffers = [
            AVAudioPCMBuffer.alloc().initWithPCMFormat(
                self._fmt, frameCapacity=frames_per_block)
            for _ in range(self.queue_depth + 1)
        ]

        self._engine.startAndReturnError(None)
        self._player.play()
        self._started = True

    # ------------------------------------------------------------- the sink
    def __call__(self, block):
        """Synthesizer.sink -- receives (frames, 2) float32, already panned."""
        if not self._started:
            self.start(frames_per_block=block.shape[0])

        # Backpressure: wait for CoreAudio to finish with a buffer. This is
        # what paces the render thread, so the synth never races ahead.
        if not self._slots.acquire(timeout=1.0):
            self.underruns += 1
            return

        # the route can change under us (headphones in, CarPlay plugged) --
        # re-ask about once a second rather than assuming it is what it was
        self._route_countdown -= 1
        if self._route_countdown <= 0:
            self._route_countdown = 100
            self._refresh_route()

        buf = self._buffers[self._next % len(self._buffers)]
        self._next += 1
        n = min(block.shape[0], self._frames)
        try:
            buf.frameLength = n
        except Exception:            # older rubicon: plain setter selector
            buf.setFrameLength(n)

        left = block[:n, 0]
        right = block[:n, 1]
        if self._compensate:
            left = self._apply_small_speaker(left)
            right = left           # a phone speaker is mono anyway

        # deinterleave straight into CoreAudio's own memory
        chans = _val(buf.floatChannelData)
        np.ctypeslib.as_array(chans[0], shape=(n,))[:] = left
        np.ctypeslib.as_array(chans[1], shape=(n,))[:] = right

        from rubicon.objc import Block
        done = Block(lambda: self._slots.release(), None)
        self._player.scheduleBuffer(buf, completionHandler=done)
        self._keep_alive = done          # the block must outlive the call
        self.blocks += 1

    # -------------------------------------------------------------- teardown
    def stop(self):
        if not self._started:
            return
        try:
            self._player.stop()
            self._engine.stop()
            self._session.setActive(False, error=None)
        except Exception:
            pass
        self._started = False


def is_available() -> bool:
    """True when the Objective-C audio stack can actually be reached."""
    try:
        from rubicon.objc import ObjCClass
        ObjCClass("AVAudioEngine")
        return True
    except Exception:
        return False
