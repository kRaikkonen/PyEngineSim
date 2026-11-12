//
//  MicRecorder.swift
//  The car's OWN sound, recorded beside its OBD data (docs/REDESIGN_V2.md
//  s.6.8, stage S0).
//
//  The A3 is the one engine we can have both the sound and the truth for: the
//  trip log holds its rpm, manifold pressure and load at every moment, and the
//  phone's microphone holds what that made.  Averaging a recording over many
//  cycles at a known rpm gives the single firing event at the microphone --
//  the ground truth the v2 event model is held to.
//
//  So this mode plays NOTHING: the synth's output is stopped, the session is
//  switched to recording in MEASUREMENT mode (the system's own gain control
//  and voice processing off -- they would reshape exactly what is being
//  measured), and the built-in microphone is written to a 48 kHz / 24-bit WAV
//  next to the trip file.
//

import Foundation
import EngineSimCore
#if os(iOS)
import AVFoundation
#endif

public final class MicRecorder {
    public private(set) var recording = false
    public private(set) var url: URL?
    public private(set) var started = 0.0
    public private(set) var inputName = ""
    #if os(iOS)
    var recorder: AVAudioRecorder?
    #endif

    public init() {}

    /// Is the microphone allowed?  Asks (the system's own prompt) the first
    /// time; `done` is called on the main queue.
    public func ensurePermission(_ done: @escaping (Bool) -> Void) {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        switch session.recordPermission {
        case .granted:
            done(true)
        case .denied:
            done(false)
        default:
            session.requestRecordPermission { ok in
                DispatchQueue.main.async { done(ok) }
            }
        }
        #else
        done(false)
        #endif
    }

    /// Start recording to `url`.  Returns the monotonic time it started at.
    @discardableResult
    public func start(url u: URL) throws -> TimeInterval {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement)
        // the phone's own microphone, not the car's hands-free one
        if let mic = session.availableInputs?.first(where: { $0.portType == .builtInMic }) {
            try? session.setPreferredInput(mic)
        }
        try session.setActive(true)
        inputName = session.currentRoute.inputs
            .map { $0.portType.rawValue + ":" + $0.portName }.joined(separator: "+")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 48000.0,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 24,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let r = try AVAudioRecorder(url: u, settings: settings)
        guard r.record() else {
            throw NSError(domain: "MicRecorder", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "the microphone would not start"])
        }
        recorder = r
        url = u
        started = Clock.now()
        recording = true
        return started
        #else
        throw NSError(domain: "MicRecorder", code: 2, userInfo: [
            NSLocalizedDescriptionKey: "no microphone recording on this platform"])
        #endif
    }

    public func stop() {
        #if os(iOS)
        recorder?.stop()
        recorder = nil
        #endif
        recording = false
    }
}
