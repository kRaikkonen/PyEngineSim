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

/// A link an ATZ (full chip reboot) would drop: the BLE ones.
public protocol ResetSensitiveLink {}

/// A link that can throw away whatever arrived unasked.  Some clones send a
/// reply TWICE after a failed protocol search (Leo's "ELM327 v2.1",
/// 2026-10-11: ATI got "UNABLE TO CONNECT", ATDP got ATI's "ELM327 v2.1",
/// and every answer after that belonged to the command before) -- emptying
/// the input before each command keeps every reply with its own request.
public protocol FlushableLink {
    func flushInput()
}

/// A link that keeps a readable log the telemetry can add to.
public protocol LinkNotes {
    func note(_ s: String)
}

/// A link whose writes can be switched to acknowledged ones.
public protocol WriteModeSwitchable: AnyObject {
    var writeWithResponse: Bool { get set }
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
    /// The protocol the car answered on ("6" fixed, "0" the adapter's search;
    /// empty if it has not answered yet).
    public private(set) var protocolUsed = ""
    /// The car answers only UDS (22 F4xx) on the engine ECU's own address.
    var uds = false
    var triedColdStart = false
    /// Append the ELM327's "expect one reply" digit to mode-01 requests, so
    /// it returns the moment the reply lands instead of waiting out ST.
    var oneReply = false

    func note(_ s: String) { (link as? LinkNotes)?.note("-- " + s) }

    /// A request for these PIDs in whatever form the car answers.
    func request(_ pids: [Int]) -> String {
        uds ? "22" + pids.map { String(format: "F4%02X", $0) }.joined()
            : "01" + pids.map { String(format: "%02X", $0) }.joined()
              + (oneReply ? "1" : "")
    }

    /// The values out of a reply in that form.
    func parse(_ raw: String, lengths: [Int: Int] = pidLength) -> [Int: [Int]] {
        let hex = OBDParse.cleanFrames(raw)
        return uds ? OBDParse.udsPids(hex, lengths: lengths)
                   : OBDParse.pids(hex, lengths: lengths)
    }
    /// What the adapter said instead of the car's reply -- "UNABLE TO
    /// CONNECT", "NO DATA", "CAN ERROR"... -- shown with "no reply from car".
    public private(set) var lastCarReply = ""
    /// The OBD port's supply as the adapter measures it (ATRV), volts; 0 if
    /// unread.  The one objective answer to "is the engine running": ~12.0-
    /// 12.7 V on the battery alone, ~13.5-14.7 V with the alternator charging.
    public private(set) var batteryVolts = 0.0

    func readVolts() {
        let r = command("ATRV", 1.5).uppercased()
        let num = r.filter { $0.isNumber || $0 == "." }
        if let v = Double(num), v > 5, v < 20 { batteryVolts = v }
    }

    /// "no reply from car", with what the adapter said and what the voltage
    /// says about the engine.
    func noReplyStatus() -> String {
        var t = "no reply from car"
        if !lastCarReply.isEmpty { t += ": " + lastCarReply }
        if batteryVolts > 0 {
            t += String(format: " (%.1f V", batteryVolts)
            t += batteryVolts < 13.2 ? " -- engine OFF?)" : " -- engine running)"
        }
        return t
    }

    static func firstLine(_ r: String) -> String {
        let t = r.split(whereSeparator: { $0 == "\r" || $0 == "\n" })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .first { !$0.isEmpty && $0 != "SEARCHING..." } ?? ""
        return t.count > 40 ? String(t.prefix(40)) : t
    }
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
    /// Polls in a row that got nothing back: the adapter is there, the car
    /// is not answering (engine control unit asleep, ignition off).
    var silent = 0
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
        (link as? FlushableLink)?.flushInput()
        do {
            try link.write(s)
        } catch {
            // the link itself is gone (a BLE drop): say so and stop polling,
            // so the app can reconnect instead of polling a dead socket
            status = "link lost"
            running = false
            return ""
        }
        return link.readPrompt(timeout)
    }

    /// ATZ plus the usual quiet-mode setup, then find out what the car offers.
    public func initialiseAdapter() -> Bool {
        status = "init"
        // Drain the greeting FIRST -- see the file comment.  Skipping this is
        // the difference between live telemetry and telemetry one command
        // behind, which looks like lag and is actually an off-by-one.
        _ = link.readPrompt(0.4)
        // ATZ: the full reset every app opens with.  (ATD was tried in its
        // place on BLE, on the guess that ATZ would drop the radio; Leo's
        // clone keeps its radio through a reset, and with ATD the car never
        // answered while Car Scanner -- ATZ -- reads it.)
        command("ATZ", 4.0)                     // reset; the chip reboots
        _ = link.readPrompt(0.5)                // a clone may print its banner twice
        for c in ["ATE0",       // echo off, or every reply arrives doubled
                  "ATL0",       // no linefeeds
                  "ATS0",       // no spaces: shorter frames, less parsing
                  "ATH0",       // no headers
                  "ATAT1"] {    // adaptive timing: back off only when needed
            command(c, 1.5)
        }
        readVolts()
        _ = probeCar()
        if status == "link lost" { return false }
        if !protocolUsed.isEmpty { tuneTiming() }
        adapterID = command("ATI", 1.5).split(separator: "\r")
            .joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        protocolName = command("ATDP", 2.0).split(separator: "\r")
            .joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        discover()
        guard status != "link lost" else { return false }
        status = "live"
        return true
    }

    /// Find a way to the car's engine control unit, cheapest first.
    ///
    /// 1. The generic OBD broadcast (7DF, mode 01) on CAN 11-bit/500k -- what
    ///    any OBD-II car answers.
    /// 2. The engine ECU's OWN address (7E0, answers on 7E8), mode 01.  A
    ///    VW-group MQB Evo (Golf 8, A3 8Y, Octavia 4...) gateway refuses the
    ///    broadcast: Leo's A3, 2026-10-11, got "UNABLE TO CONNECT" in 0.15 s
    ///    -- the bus awake and acknowledging, nobody answering -- and Car
    ///    Scanner only reads it once told "MQB Evo, EA211 1.4/1.5".
    /// 3. The same address in UDS (22 F4xx, ISO 27145's mirror of mode 01),
    ///    for a unit that has dropped the old service.
    /// 4. The adapter's own protocol search, slow, for anything else.
    func probeCar() -> Bool {
        // Leo's A3 8Y, engine running (14.2 V at the port), 2026-10-11: every
        // request below refused in ~60 ms -- while Car Scanner reads rpm
        // through the same dongle.  So each variant a working app might be
        // using is tried in turn, and the one that answers is kept and named
        // in the log.  First the most likely: a gateway slower than this
        // clone's timeout (Car Scanner has a setting just for "send ATST").
        func attempt(_ name: String, _ setup: [String], _ req: String = "0100",
                     uds u: Bool = false, timeout: TimeInterval = 4.0) -> Bool {
            guard status != "link lost" else { return false }
            note("try " + name)
            for c in setup { command(c, c == "ATZ" ? 3.0 : 1.5) }
            let r = command(req, timeout)
            lastCarReply = OBDTelemetry.firstLine(r)
            let hex = OBDParse.cleanFrames(r)
            let got = u ? OBDParse.udsPids(hex, lengths: [0x00: 4])
                        : OBDParse.pids(hex, lengths: [0x00: 4])
            guard got[0x00] != nil else { return false }
            uds = u
            protocolUsed = name
            note("ANSWERED: " + name)
            return true
        }
        uds = false
        oneReply = false
        let slow = ["ATAT0", "ATSTFF"]          // no adaptive timing, ~1 s
        if attempt("CAN 11/500 broadcast", ["ATAT1", "ATST32", "ATSP6", "ATSH7DF"]) { return true }
        if attempt("CAN 11/500 broadcast, 1 s timeout", slow + ["ATSP6", "ATSH7DF"]) { return true }
        if attempt("CAN 11/500 engine ECU 7E0, 1 s", ["ATSH7E0", "ATFCSH7E0"]) { return true }
        if attempt("CAN 11/500 engine ECU 7E0 UDS, 1 s", [], "22F400", uds: true) { return true }
        if attempt("CAN 29/500, 1 s", ["ATSP7", "ATSHDB33F1"]) { return true }
        if attempt("CAN 11/250, 1 s", ["ATSP8", "ATSH7DF"]) { return true }
        if attempt("CAN 29/250, 1 s", ["ATSP9", "ATSHDB33F1"]) { return true }
        if attempt("adapter's own search", ["ATSH7DF", "ATSP0"], timeout: 15.0) { return true }
        if !triedColdStart {
            triedColdStart = true
            // the plainest session an app opens: reset, echo off, search --
            // nothing else changed from the chip's own defaults
            if attempt("plain: ATZ, ATE0, search", ["ATZ", "ATE0", "ATSP0"], timeout: 15.0) { return true }
            if attempt("plain: ATZ, ATE0, CAN 11/500", ["ATZ", "ATE0", "ATSP6"]) { return true }
            // and the same with acknowledged Bluetooth writes
            if let w = link as? WriteModeSwitchable {
                w.writeWithResponse = true
                note("writes now acknowledged (withResponse)")
                if attempt("acked writes: CAN 11/500, 1 s", ["ATZ", "ATE0"] + slow + ["ATSP6", "ATSH7DF"]) { return true }
                if attempt("acked writes: search", ["ATSP0"], timeout: 15.0) { return true }
                w.writeWithResponse = false
                note("writes back to unacknowledged")
            }
            for c in ["ATE0", "ATL0", "ATS0", "ATH0"] { command(c, 1.5) }
        }
        // nothing answered: the standard broadcast, waiting long; polling
        // re-probes every 15 silent polls
        for c in slow + ["ATSP6", "ATSH7DF"] { command(c, 1.5) }
        protocolUsed = ""
        note("nothing answered")
        return false
    }

    /// The probe may have needed a long timeout; polling wants it short.
    func tuneTiming() {
        guard !uds else { return }
        for c in ["ATAT1", "ATST32"] { command(c, 1.5) }
        if !parse(command(request([pidRPM]), 2.0)).isEmpty {
            note("timing: adaptive (fast)")
            return
        }
        for c in ["ATAT0", "ATSTFF"] { command(c, 1.5) }
        oneReply = true
        if !parse(command(request([pidRPM]), 2.0)).isEmpty {
            note("timing: 1 s timeout + one-reply digit")
            return
        }
        oneReply = false
        note("timing: 1 s timeout")
    }

    /// Read the car's own supported-PID bitmasks, then pick our channels.
    func discover() {
        var sup = Set<Int>()
        for base in [0x00, 0x20, 0x40] {
            let got = parse(command(request([base]), 2.0), lengths: [base: 4])
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
            let got = parse(command(request([pidBaro]), 1.5))
            if let v = got[pidBaro]?.first { baroKPa = Double(v) }
        }
        pollPIDs = [pidRPM]
        if let p = pedalSource { pollPIDs.append(p) }
        for p in [pidSpeed, pidMAP] where supported.contains(p) {
            pollPIDs.append(p)
        }
        // A multi-PID request answers every channel in ONE round trip.  Cheap
        // clones reject it, so it is tried rather than assumed.
        multiPID = parse(command(request(pollPIDs), 2.0)).count >= pollPIDs.count
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
            req = request(pollPIDs)
        } else {
            // rpm is the one that matters (Leo: "我他妈只要你能读取rpm就够了"):
            // every other request is rpm, the rest take turns in between
            let others = pollPIDs.filter { $0 != pidRPM }
            if roundRobin % 2 == 0 || others.isEmpty {
                req = request([pidRPM])
            } else {
                req = request([others[(roundRobin / 2) % others.count]])
            }
            roundRobin += 1
        }
        let raw = command(req, protocolUsed.isEmpty ? 3.0 : 1.0)
        let got = parse(raw)
        let t1 = Date().timeIntervalSinceReferenceDate
        guard !got.isEmpty else {
            silent += 1
            lastCarReply = OBDTelemetry.firstLine(raw)
            if silent >= 8 && (status == "live" || status.hasPrefix("no reply")) {
                status = noReplyStatus()
            }
            // the car may have woken up (ignition on) since the last probe:
            // look for the way in again every 15 silent polls
            if silent % 15 == 0 { readVolts() }
            if silent % 15 == 0 && probeCar() {
                tuneTiming()
                discover()
                silent = 0
                status = "live"
            }
            return
        }
        silent = 0
        if protocolUsed.isEmpty { protocolUsed = "?" }
        if status.hasPrefix("no reply") { status = "live" }
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
