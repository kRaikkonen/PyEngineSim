//
//  BLELink.swift
//  An ELM327 over Bluetooth Low Energy.
//
//  READ THIS BEFORE BUYING A DONGLE.  There are two completely different
//  things sold as "ELM327 Bluetooth" and only one of them can ever work here:
//
//    Bluetooth CLASSIC (SPP)  -- what most cheap v1.5 clones are.  iOS will
//      not open a serial port to a non-MFi accessory, full stop.  No app can,
//      no entitlement exists for it, and the dongle pairing in Settings means
//      nothing.  If it appears in Settings > Bluetooth, that is the bad sign.
//
//    Bluetooth LOW ENERGY (BLE) -- Vgate iCar Pro BLE, Veepeak OBDCheck BLE,
//      LELink and similar, usually sold as "works with iPhone".  These speak
//      GATT and this file talks to them.  They generally do NOT appear in
//      Settings > Bluetooth, because there is nothing to pair.
//
//  The wire protocol above BLE is the same ELM327 text as over TCP, so
//  everything upstream is unchanged -- this is a transport and nothing more.
//
//  One BLE-specific thing shapes the code: a notification carries about
//  twenty bytes, so a reply arrives in PIECES.  Reading until the ">" prompt
//  rather than reading "a reply" is what makes that a non-issue, and it is the
//  same rule the TCP link follows for the same reason.
//
//  Three things this file used to get wrong on a real phone (Leo, 2026-10-09:
//  "iOS蓝牙没用上，无法尝试连接"), each of which looks like "Bluetooth does
//  not work":
//    * "powered on" was a one-shot semaphore.  The scan consumed it, so the
//      connect that followed waited for a signal that never came again and
//      failed with "Bluetooth is not available" every time.  The radio's
//      STATE is read now, not an event.
//    * the scan waited for the radio on the main thread, so on first launch
//      it froze the screen behind the permission prompt and then gave up
//      before anyone could tap "Allow".  Nothing waits on the main thread.
//    * only names containing OBD/ELM/VGATE... were listed, so a dongle called
//      anything else was simply never there to tap.  Everything with a name
//      is listed now, likely dongles first.
//

import Foundation
#if canImport(CoreBluetooth)
import CoreBluetooth

/// The GATT services these dongles actually use.  There is no standard, so
/// this is the list of what is on the market; anything else is found by its
/// characteristics.
public let obdServiceUUIDs = [
    CBUUID(string: "FFF0"),      // the common clone service (FFF1 notify, FFF2 write)
    CBUUID(string: "FFE0"),      // HM-10 style modules (FFE1 both)
    CBUUID(string: "18F0"),      // Vgate iCar Pro (2AF0 notify, 2AF1 write)
    CBUUID(string: "FFB0"),
    CBUUID(string: "E7810A71-73AE-499D-8C15-FAA9AEF0C3F2"),   // OBDLink CX / MX+
]

/// The standard services every BLE device carries -- device name, battery,
/// manufacturer info.  Several of them have a WRITABLE characteristic (the
/// device name), which is exactly the wrong place to send "ATZ".
private let genericServiceUUIDs = [
    CBUUID(string: "1800"), CBUUID(string: "1801"),
    CBUUID(string: "180A"), CBUUID(string: "180F"),
]

private let obdNameHints = ["OBD", "ELM", "VGATE", "ICAR", "VEEPEAK", "LELINK",
                            "VLINK", "V-LINK", "OBDII", "KONNWEI", "CARISTA",
                            "VIECAR", "BLUEDRIVER", "IOS-", "SCAN", "CAR"]

public final class BLELink: NSObject, OBDLink, ResetSensitiveLink {
    /// What a scan found, for a picker.
    public struct Found: Identifiable, Equatable {
        public let id: UUID
        public let name: String
        public let rssi: Int
        /// Named like a dongle or advertising a dongle's service.
        public let likely: Bool
    }

    private var central: CBCentralManager!
    private let queue = DispatchQueue(label: "obd.ble")
    private var peripheral: CBPeripheral?
    private var writeChar: CBCharacteristic?
    private var notifyChar: CBCharacteristic?
    private var chosenPreferred = false
    private var buffer = Data()
    private let lock = NSLock()

    /// Signalled on every radio state change; waiters re-read the state.
    private let stateChanged = DispatchSemaphore(value: 0)
    private var connected = DispatchSemaphore(value: 0)
    private var ready = false
    private var wantID: UUID?

    /// Everything seen while scanning, newest RSSI wins.
    public private(set) var discovered: [Found] = []
    private var onDiscover: (([Found]) -> Void)?
    private var scanToken = 0

    public private(set) var deviceName = ""
    public private(set) var failure: String?

    /// What happened on the link, newest last -- radio state, what the dongle
    /// offered and what was picked, every command and reply, every drop.  On
    /// screen, so a failure in a running car can be READ instead of guessed.
    private var logLines: [String] = []
    private let logLock = NSLock()
    private let t0 = Date()
    public var log: [String] {
        logLock.lock(); defer { logLock.unlock() }
        return logLines
    }
    func note(_ s: String) {
        let line = String(format: "%6.2f ", Date().timeIntervalSince(t0)) + s
        logLock.lock()
        logLines.append(line)
        if logLines.count > 120 { logLines.removeFirst(logLines.count - 120) }
        logLock.unlock()
    }
    /// Short and printable: replies carry CRs and the odd control byte.
    static func show(_ s: String) -> String {
        let t = s.replacingOccurrences(of: "\r", with: "|")
            .replacingOccurrences(of: "\n", with: "")
        return t.count > 70 ? String(t.prefix(70)) + "..." : t
    }

    override public init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: queue)
    }

    /// Block until the radio is on (or plainly is not going to be).  Never
    /// call this on `queue` -- the state callback arrives there -- or on the
    /// main thread.  The first time, iOS is showing its permission prompt and
    /// the state stays `.unknown` until it is answered, hence the long wait.
    private func waitUntilOn(_ timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            switch central.state {
            case .poweredOn:
                return true
            case .unauthorized:
                failure = "this app is not allowed to use Bluetooth -- turn it "
                    + "on in Settings > Privacy & Security > Bluetooth"
                return false
            case .poweredOff:
                failure = "Bluetooth is off -- switch it on in Control Centre"
                return false
            case .unsupported:
                failure = "this device has no Bluetooth LE"
                return false
            default:                     // .unknown / .resetting: not answered yet
                break
            }
            let left = deadline.timeIntervalSinceNow
            if left <= 0 {
                failure = "Bluetooth did not come up -- if iOS asked, allow it, "
                    + "then scan again"
                return false
            }
            _ = stateChanged.wait(timeout: .now() + min(left, 0.25))
        }
    }

    /// Look for dongles for `seconds`, calling back (on the main queue) as
    /// the list grows and once at the end with the failure, if any.  Returns
    /// at once: the waiting happens off the main thread.
    ///
    /// Scans with no service filter: these dongles are inconsistent about
    /// what they advertise, and a filtered scan silently finds nothing at all,
    /// which is indistinguishable from "no dongle here".
    public func scan(seconds: TimeInterval = 8.0,
                     onUpdate: @escaping ([Found]) -> Void,
                     onDone: @escaping (String?) -> Void = { _ in }) {
        queue.async {
            self.scanToken += 1
            self.onDiscover = onUpdate
            self.discovered = []
            self.wantID = nil            // a scan is for the picker, not a connect
        }
        DispatchQueue.global(qos: .userInitiated).async {
            self.failure = nil
            guard self.waitUntilOn(30.0) else {
                let f = self.failure
                DispatchQueue.main.async { onDone(f) }
                return
            }
            self.queue.async {
                let token = self.scanToken
                self.central.scanForPeripherals(withServices: nil, options: nil)
                self.queue.asyncAfter(deadline: .now() + seconds) {
                    // a newer scan or a connect owns the radio now
                    guard self.scanToken == token, self.wantID == nil else {
                        DispatchQueue.main.async { onDone(nil) }
                        return
                    }
                    self.central.stopScan()
                    DispatchQueue.main.async { onDone(nil) }
                }
            }
        }
    }

    /// Connect to a specific device found by `scan`, and wait until the
    /// dongle's data channel is open (notifications ON, so the first reply is
    /// not lost).  Blocking: call it off the main thread.
    public func connect(to id: UUID, timeout: TimeInterval = 15.0) throws {
        failure = nil
        guard waitUntilOn(30.0) else {
            throw NSError(domain: "OBD", code: 10, userInfo: [
                NSLocalizedDescriptionKey: failure ?? "Bluetooth is not available"])
        }
        let done = DispatchSemaphore(value: 0)
        queue.async {
            self.scanToken += 1                 // retire any scan's timer
            if let p = self.peripheral { self.central.cancelPeripheralConnection(p) }
            self.connected = done
            self.peripheral = nil
            self.writeChar = nil
            self.notifyChar = nil
            self.chosenPreferred = false
            self.ready = false
            self.wantID = id
            self.lock.lock(); self.buffer.removeAll(); self.lock.unlock()
            // a device the system already knows is connected to directly;
            // only an unknown one has to be found by scanning first
            if let p = self.central.retrievePeripherals(withIdentifiers: [id]).first {
                self.central.stopScan()
                self.note("connect " + (p.name ?? "?") + " (known device)")
                self.attach(p, name: p.name ?? "")
            } else {
                self.note("connect: scanning for it")
                self.central.scanForPeripherals(withServices: nil, options: nil)
            }
        }
        guard done.wait(timeout: .now() + timeout) == .success, ready else {
            note("connect FAILED: " + (failure ?? "no answer in \(Int(timeout)) s"))
            queue.sync {
                self.central.stopScan()
                if let p = self.peripheral, !self.ready {
                    self.central.cancelPeripheralConnection(p)
                }
            }
            throw NSError(domain: "OBD", code: 11, userInfo: [
                NSLocalizedDescriptionKey: failure
                    ?? "the dongle did not answer.  Is the ignition on?  If it "
                     + "appears in Settings > Bluetooth it is a CLASSIC "
                     + "adapter, and iOS cannot talk to those at all -- it "
                     + "needs a BLE one."])
        }
    }

    private func attach(_ p: CBPeripheral, name: String) {
        peripheral = p
        p.delegate = self
        deviceName = name
        central.connect(p, options: nil)
    }

    // ------------------------------------------------------------ OBDLink
    public func write(_ s: String) throws {
        guard let p = peripheral, let c = writeChar, ready else {
            throw NSError(domain: "OBD", code: 12, userInfo: [
                NSLocalizedDescriptionKey: failure ?? "not connected"])
        }
        let data = Data((s + "\r").utf8)
        note("> " + s)
        // withoutResponse where the dongle allows it: an ELM327 is a stream of
        // short commands and waiting for an ack on each one halves the rate
        let mode: CBCharacteristicWriteType =
            c.properties.contains(.writeWithoutResponse) ? .withoutResponse
                                                         : .withResponse
        queue.async {
            // a command longer than one ATT packet goes in pieces
            let mtu = max(p.maximumWriteValueLength(for: mode), 20)
            var i = 0
            while i < data.count {
                let j = min(i + mtu, data.count)
                p.writeValue(data.subdata(in: i..<j), for: c, type: mode)
                i = j
            }
        }
    }

    public func readPrompt(_ timeout: TimeInterval) -> String {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            lock.lock()
            if let i = buffer.firstIndex(of: UInt8(ascii: ">")) {
                let out = buffer[..<i]
                buffer.removeSubrange(...i)
                lock.unlock()
                let r = String(decoding: out, as: UTF8.self)
                note("< " + BLELink.show(r))
                return r
            }
            lock.unlock()
            usleep(2000)
        }
        lock.lock()
        let out = String(decoding: buffer, as: UTF8.self)
        buffer.removeAll()
        lock.unlock()
        note("< (no prompt in " + String(format: "%.1f", timeout) + " s) "
             + BLELink.show(out))
        return out
    }

    public func close() {
        queue.sync {
            if let p = self.peripheral { self.central.cancelPeripheralConnection(p) }
            self.peripheral = nil
            self.writeChar = nil
            self.notifyChar = nil
            self.ready = false
            self.wantID = nil
        }
    }
}

extension BLELink: CBCentralManagerDelegate, CBPeripheralDelegate {
    public func centralManagerDidUpdateState(_ c: CBCentralManager) {
        let names: [CBManagerState: String] = [
            .unknown: "unknown", .resetting: "resetting", .unsupported: "unsupported",
            .unauthorized: "NOT ALLOWED", .poweredOff: "off", .poweredOn: "on"]
        note("bluetooth " + (names[c.state] ?? "?"))
        stateChanged.signal()
    }

    public func centralManager(_ c: CBCentralManager,
                               didDiscover p: CBPeripheral,
                               advertisementData: [String: Any],
                               rssi RSSI: NSNumber) {
        let name = p.name
            ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
            ?? ""
        if let want = wantID {
            if p.identifier == want {
                c.stopScan()
                attach(p, name: name)
            }
            return
        }
        let services = (advertisementData[CBAdvertisementDataServiceUUIDsKey]
                        as? [CBUUID]) ?? []
        let advertisesOBD = services.contains { obdServiceUUIDs.contains($0) }
        let upper = name.uppercased()
        let looksRight = obdNameHints.contains { upper.contains($0) }
        // a nameless device is listed only if it advertises a dongle's service
        guard !name.isEmpty || advertisesOBD else { return }
        let shown = name.isEmpty ? "unnamed OBD (\(services.first?.uuidString ?? "?"))" : name
        let f = Found(id: p.identifier, name: shown, rssi: RSSI.intValue,
                      likely: looksRight || advertisesOBD)
        if let i = discovered.firstIndex(where: { $0.id == f.id }) {
            discovered[i] = f
        } else {
            discovered.append(f)
        }
        // likely dongles first, then the nearest
        let snapshot = discovered.sorted {
            $0.likely != $1.likely ? $0.likely : $0.rssi > $1.rssi
        }
        let cb = onDiscover
        DispatchQueue.main.async { cb?(snapshot) }
    }

    public func centralManager(_ c: CBCentralManager,
                               didConnect p: CBPeripheral) {
        note("connected; discovering services")
        p.discoverServices(nil)
    }

    public func centralManager(_ c: CBCentralManager,
                               didFailToConnect p: CBPeripheral,
                               error: Error?) {
        failure = error?.localizedDescription ?? "could not connect"
        note("connect refused: " + (failure ?? ""))
        connected.signal()
    }

    public func centralManager(_ c: CBCentralManager,
                               didDisconnectPeripheral p: CBPeripheral,
                               error: Error?) {
        guard p.identifier == peripheral?.identifier else { return }
        let wasReady = ready
        ready = false
        failure = "the dongle disconnected"
            + (error.map { " (\($0.localizedDescription))" } ?? "")
        note("DISCONNECTED" + (error.map { ": " + $0.localizedDescription } ?? ""))
        if !wasReady { connected.signal() }
    }

    public func peripheral(_ p: CBPeripheral,
                           didDiscoverServices error: Error?) {
        note("services: " + (p.services ?? []).map { $0.uuid.uuidString }
                .joined(separator: " ")
             + (error.map { " (error " + $0.localizedDescription + ")" } ?? ""))
        let all = (p.services ?? []).filter { !genericServiceUUIDs.contains($0.uuid) }
        if all.isEmpty {
            failure = "this device has no data service -- not an OBD dongle?"
            connected.signal()
            return
        }
        for s in all { p.discoverCharacteristics(nil, for: s) }
    }

    public func peripheral(_ p: CBPeripheral,
                           didDiscoverCharacteristicsFor service: CBService,
                           error: Error?) {
        // The write and the notify channel must come from the SAME service:
        // that pair is the serial port.  They are often FFF2/FFF1 but the
        // numbering is not consistent across clones, so go by PROPERTIES
        // rather than by a hard-coded UUID -- that is what makes this work on
        // a dongle nobody has seen before.  A known OBD service wins over any
        // other that happens to have the same shape.
        let chars = service.characteristics ?? []
        func props(_ c: CBCharacteristic) -> String {
            var t = ""
            if c.properties.contains(.read) { t += "r" }
            if c.properties.contains(.write) { t += "w" }
            if c.properties.contains(.writeWithoutResponse) { t += "W" }
            if c.properties.contains(.notify) { t += "n" }
            if c.properties.contains(.indicate) { t += "i" }
            return t
        }
        note("  " + service.uuid.uuidString + ": "
             + chars.map { $0.uuid.uuidString + "[" + props($0) + "]" }
                .joined(separator: " "))
        let w = chars.first { $0.properties.contains(.writeWithoutResponse)
                              || $0.properties.contains(.write) }
        let n = chars.first { $0.properties.contains(.notify)
                              || $0.properties.contains(.indicate) }
        guard let w, let n else { return }
        let preferred = obdServiceUUIDs.contains(service.uuid)
        if notifyChar != nil && (chosenPreferred || !preferred) { return }
        if let old = notifyChar { p.setNotifyValue(false, for: old) }
        writeChar = w
        notifyChar = n
        chosenPreferred = preferred
        note("using " + service.uuid.uuidString + ": write " + w.uuid.uuidString
             + ", notify " + n.uuid.uuidString)
        p.setNotifyValue(true, for: n)
    }

    public func peripheral(_ p: CBPeripheral,
                           didUpdateNotificationStateFor ch: CBCharacteristic,
                           error: Error?) {
        guard ch == notifyChar else { return }
        if let error {
            failure = "the dongle refused notifications: \(error.localizedDescription)"
            note(failure ?? "")
            connected.signal()
            return
        }
        if ch.isNotifying && !ready {
            note("notifications on -- ready")
            ready = true
            connected.signal()
        }
    }

    public func peripheral(_ p: CBPeripheral,
                           didUpdateValueFor ch: CBCharacteristic,
                           error: Error?) {
        guard let d = ch.value, !d.isEmpty else { return }
        lock.lock(); buffer.append(d); lock.unlock()
    }
}
#endif
