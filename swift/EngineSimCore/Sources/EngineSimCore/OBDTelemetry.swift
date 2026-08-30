//
//  OBDTelemetry.swift
//  The live link to the car: an ELM327 over WiFi, polled off the audio thread.
//
//  Two things here are not obvious and both were learned the hard way in the
//  Python:
//
//  The BANNER.  An adapter greets a new connection with its identification
//  string and a prompt.  If that is not drained before the first command,
//  every reply is read ONE COMMAND LATE -- discovery parses the wrong answers
//  and every sample afterwards carries an extra round trip of lag.
//
//  The LEAD.  Polling costs 40-100 ms and the audio output adds its own
//  buffer, so by the time you hear a sample the crankshaft has moved on.  The
//  rpm is therefore projected forward over the measured round trip plus the
//  output latency, using a smoothed derivative.  On a turbo car the pedal
//  channel leads the crank by 100-300 ms anyway (turbo lag), which is free
//  latency payback -- it is why the pedal, not the rpm, drives the throttle.
//

import Foundation
#if canImport(Network)
import Network
#endif

public let pidRPM = 0x0C, pidSpeed = 0x0D, pidTPS = 0x11
public let pidMAP = 0x0B, pidLoad = 0x04, pidBaro = 0x33
/// Pedal channels, best first.  A relative pedal reads 0 at rest on every car;
/// the absolute ones have a per-car offset.
public let pedalOrder = [0x5A, 0x49, 0x4A, 0x45, 0x11]

/// Anything that can carry AT commands to an adapter and read a prompt back.
public protocol OBDLink: AnyObject {
    func write(_ s: String) throws
    /// Read until the ELM327's ">" prompt, or `timeout` elapses.
    func readPrompt(_ timeout: TimeInterval) -> String
    func close()
}

#if canImport(Network)
/// A WiFi ELM327 -- a raw TCP socket, no third-party dependency.
public final class TCPLink: OBDLink {
    private let conn: NWConnection
    private let queue = DispatchQueue(label: "obd.tcp")
    private var buffer = Data()
    private let lock = NSLock()
    private var open = false

    public init(host: String, port: UInt16, timeout: TimeInterval = 5.0) throws {
        conn = NWConnection(host: NWEndpoint.Host(host),
                            port: NWEndpoint.Port(rawValue: port)!, using: .tcp)
        let ready = DispatchSemaphore(value: 0)
        conn.stateUpdateHandler = { [weak self] st in
            switch st {
            case .ready: self?.open = true; ready.signal()
            case .failed, .cancelled: ready.signal()
            default: break
            }
        }
        conn.start(queue: queue)
        _ = ready.wait(timeout: .now() + timeout)
        guard open else {
            conn.cancel()
            throw NSError(domain: "OBD", code: 1, userInfo: [
                NSLocalizedDescriptionKey:
                    "no answer from \(host):\(port) -- is the dongle's WiFi joined?"])
        }
        receiveLoop()
    }

    private func receiveLoop() {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 4096) {
            [weak self] data, _, done, _ in
            guard let self else { return }
            if let d = data, !d.isEmpty {
                self.lock.lock(); self.buffer.append(d); self.lock.unlock()
            }
            if !done { self.receiveLoop() }
        }
    }

    public func write(_ s: String) throws {
        conn.send(content: Data((s + "\r").utf8),
                  completion: .contentProcessed { _ in })
    }

    public func readPrompt(_ timeout: TimeInterval) -> String {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            lock.lock()
            if let i = buffer.firstIndex(of: UInt8(ascii: ">")) {
                let out = buffer[..<i]
                buffer.removeSubrange(...i)
                lock.unlock()
                return String(decoding: out, as: UTF8.self)
            }
            lock.unlock()
            usleep(2000)
        }
        lock.lock()
        let out = String(decoding: buffer, as: UTF8.self)
        buffer.removeAll()
        lock.unlock()
        return out
    }

    public func close() { conn.cancel(); open = false }
}
#endif

/// The car, as the synth wants to see it.
public final class OBDTelemetry {
    public private(set) var rpm = 0.0          // EXTRAPOLATED: what you play
    public private(set) var rawRPM = 0.0       // un-projected, for shift detection
    public private(set) var throttle = 0.0
    public private(set) var speed = 0.0        // m/s
    public private(set) var gear = 0
    public private(set) var boostPSI = 0.0
    public private(set) var mapKPa = 0.0
    public private(set) var baroKPa = 101.3
    public private(set) var maxRPM = 6500.0
    public private(set) var supported = Set<Int>()
    public private(set) var pedalSource: Int?
    public private(set) var speedValid = false
    public private(set) var multiPID = false
    public private(set) var rtt = 0.0          // measured round trip, seconds
    public private(set) var hz = 0.0           // achieved sample rate
    public private(set) var adapterID = ""
    public private(set) var protocolName = ""
    public private(set) var status = "idle"
    public private(set) var error: String?

    /// Seconds of audio output lag, folded into the forward projection.
    public var outLatency = 0.0

    let link: OBDLink
    let protocolCode: String
    let gears = GearLearner()
    var pollPIDs = [pidRPM]
    var roundRobin = 0
    var dRPM = 0.0
    var lastRPMTime: TimeInterval = 0
    var lastPacket: TimeInterval = 0
    var thread: Thread?
    var running = false
    public var pollInterval = 1.0 / 50.0

    public init(link: OBDLink, protocolCode: String = "6") {
        self.link = link
        self.protocolCode = protocolCode
    }

    // ------------------------------------------------------------ commands
    @discardableResult
    func command(_ s: String, _ timeout: TimeInterval) -> String {
        try? link.write(s)
        return link.readPrompt(timeout)
    }

    /// ATZ plus the usual quiet-mode setup, then find out what the car offers.
    public func initialiseAdapter() -> Bool {
        status = "init"
        // Drain the greeting FIRST -- see the file comment.  Skipping this is
        // the difference between live telemetry and telemetry one command
        // behind, which looks like lag and is actually an off-by-one.
        _ = link.readPrompt(0.4)
        command("ATZ", 4.0)                     // reset; the chip reboots
        for c in ["ATE0",       // echo off, or every reply arrives doubled
                  "ATL0",       // no linefeeds
                  "ATS0",       // no spaces: shorter frames, less parsing
                  "ATH0",       // no headers
                  "ATAT1"] {    // adaptive timing: back off only when needed
            command(c, 1.5)
        }
        // ISO 15765-4 CAN 11-bit / 500 kbaud is what every MQB-platform VAG car
        // speaks.  Protocol "0" lets the adapter hunt instead, at the cost of a
        // slow first request.
        command("ATSP" + protocolCode, 2.0)
        adapterID = command("ATI", 1.5).split(separator: "\r")
            .joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        protocolName = command("ATDP", 2.0).split(separator: "\r")
            .joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        discover()
        status = "live"
        return true
    }

    /// Read the car's own supported-PID bitmasks, then pick our channels.
    func discover() {
        var sup = Set<Int>()
        for (base, req) in [(0x00, "0100"), (0x20, "0120"), (0x40, "0140")] {
            let got = OBDParse.pids(OBDParse.cleanFrames(command(req, 2.0)),
                                    lengths: [base: 4])
            guard let data = got[base] else { break }
            sup.formUnion(OBDParse.supported(data, base: base))
            if !sup.contains(base + 0x20) { break }   // no continuation bit
        }
        // a dongle that refuses the bitmask query still answers the basics
        supported = sup.isEmpty
            ? [pidRPM, pidSpeed, pidTPS, pidMAP, pidLoad] : sup
        pedalSource = pedalOrder.first { supported.contains($0) }
        speedValid = supported.contains(pidSpeed)

        if supported.contains(pidBaro) {        // ambient reference for boost
            let got = OBDParse.pids(OBDParse.cleanFrames(
                command(String(format: "01%02X", pidBaro), 1.5)))
            if let v = got[pidBaro]?.first { baroKPa = Double(v) }
        }
        pollPIDs = [pidRPM]
        if let p = pedalSource { pollPIDs.append(p) }
        for p in [pidSpeed, pidMAP] where supported.contains(p) {
            pollPIDs.append(p)
        }
        // A multi-PID request answers every channel in ONE round trip.  Cheap
        // clones reject it, so it is tried rather than assumed.
        let probe = "01" + pollPIDs.map { String(format: "%02X", $0) }.joined()
        multiPID = OBDParse.pids(OBDParse.cleanFrames(command(probe, 2.0))).count
            >= pollPIDs.count
    }

    // ------------------------------------------------------------ polling
    public func start() {
        guard thread == nil else { return }
        running = true
        let t = Thread { [weak self] in
            guard let self else { return }
            guard self.initialiseAdapter() else { return }
            while self.running { self.pollOnce() }
        }
        t.name = "obd.poll"
        t.qualityOfService = .userInitiated
        thread = t
        t.start()
    }

    public func stop() {
        running = false
        thread = nil
        link.close()
        status = "stopped"
    }

    func pollOnce() {
        let t0 = Date().timeIntervalSinceReferenceDate
        let req: String
        if multiPID {
            req = "01" + pollPIDs.map { String(format: "%02X", $0) }.joined()
        } else {
            req = String(format: "01%02X", pollPIDs[roundRobin % pollPIDs.count])
            roundRobin += 1
        }
        let got = OBDParse.pids(OBDParse.cleanFrames(command(req, 1.0)))
        let t1 = Date().timeIntervalSinceReferenceDate
        guard !got.isEmpty else { return }
        rtt += ((t1 - t0) - rtt) * 0.2
        if lastPacket > 0 {
            let dt = max(t1 - lastPacket, 1e-3)
            hz += (1.0 / dt - hz) * 0.2
        }
        ingest(got, now: t1)
        lastPacket = t1
        let left = pollInterval - (Date().timeIntervalSinceReferenceDate - t0)
        if left > 0 { usleep(useconds_t(left * 1e6)) }
    }

    func ingest(_ got: [Int: [Int]], now: TimeInterval) {
        if let v = got[pidRPM], v.count >= 2 {
            let r = Double(v[0] * 256 + v[1]) / 4.0
            let dt = lastRPMTime > 0 ? now - lastRPMTime : 0.0
            if dt > 0.0 && dt < 0.5 {
                dRPM += ((r - rawRPM) / dt - dRPM) * 0.35
            }
            lastRPMTime = now
            rawRPM = r
            // project forward over the WHOLE pipeline lag, capped so a noisy
            // derivative cannot throw the note across the rev range
            let lead = min(rtt + outLatency, 0.35)
            rpm = max(r + max(min(dRPM * lead, 800.0), -800.0), 0.0)
            if r > maxRPM { maxRPM = r }
        }
        if let p = pedalSource, let v = got[p]?.first {
            throttle = min(max(Double(v) / 255.0, 0.0), 1.0)
        }
        if let v = got[pidSpeed]?.first {
            let kmh = Double(v)
            speed = kmh / 3.6
            gear = gears.update(rpm: rawRPM, speedKmh: kmh)
        }
        if let v = got[pidMAP]?.first {
            mapKPa = Double(v)
            boostPSI = (mapKPa - baroKPa) * 0.1450377
        }
    }
}

/// SEEDS for the rpm map.  `RpmMap.observe` then learns the truth, so a wrong
/// entry here costs one drive, not the whole experience.
public let carProfiles: [String: (idle: Double, redline: Double)] = [
    "a3-35tfsi": (760, 6500),      // EA211 evo 1.5 TSI -- Leo's car
    "golf-gti": (800, 6800),
    "generic-petrol": (800, 6500),
    "generic-diesel": (750, 4800),
    "generic-sport": (900, 7500),
]
