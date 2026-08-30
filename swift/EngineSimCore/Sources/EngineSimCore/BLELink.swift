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

import Foundation
#if canImport(CoreBluetooth)
import CoreBluetooth

/// The GATT services these dongles actually use.  There is no standard, so
/// this is the list of what is on the market; anything else is found by name.
public let obdServiceUUIDs = [
    CBUUID(string: "FFF0"),      // the common clone service
    CBUUID(string: "FFE0"),      // HM-10 style modules
    CBUUID(string: "18F0"),      // Vgate iCar Pro
    CBUUID(string: "FFB0"),
]

private let obdNameHints = ["OBD", "ELM", "VGATE", "ICAR", "VEEPEAK", "LELINK",
                            "VLINK", "OBDII", "KONNWEI"]

public final class BLELink: NSObject, OBDLink {
    /// What a scan found, for a picker.
    public struct Found: Identifiable, Equatable {
        public let id: UUID
        public let name: String
        public let rssi: Int
    }

    private var central: CBCentralManager!
    private let queue = DispatchQueue(label: "obd.ble")
    private var peripheral: CBPeripheral?
    private var writeChar: CBCharacteristic?
    private var buffer = Data()
    private let lock = NSLock()

    private var poweredOn = DispatchSemaphore(value: 0)
    private var connected = DispatchSemaphore(value: 0)
    private var ready = false
    private var wantName: String?
    private var wantID: UUID?

    /// Everything seen while scanning, newest RSSI wins.
    public private(set) var discovered: [Found] = []
    private var onDiscover: (([Found]) -> Void)?

    public private(set) var deviceName = ""
    public private(set) var failure: String?

    override public init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: queue)
    }

    /// Look for dongles for `seconds`, calling back as the list grows.
    ///
    /// Scans with no service filter: these dongles are inconsistent about
    /// what they advertise, and a filtered scan silently finds nothing at all,
    /// which is indistinguishable from "no dongle here".
    public func scan(seconds: TimeInterval = 6.0,
                     onUpdate: @escaping ([Found]) -> Void) {
        onDiscover = onUpdate
        discovered = []
        guard poweredOn.wait(timeout: .now() + 4.0) == .success else {
            failure = "Bluetooth is not available -- is it switched on?"
            return
        }
        central.scanForPeripherals(withServices: nil, options: nil)
        queue.asyncAfter(deadline: .now() + seconds) { [weak self] in
            self?.central.stopScan()
        }
    }

    /// Connect to a specific device found by `scan`, and wait until the
    /// characteristics are usable.
    public func connect(to id: UUID, timeout: TimeInterval = 12.0) throws {
        wantID = id
        guard poweredOn.wait(timeout: .now() + 4.0) == .success else {
            throw NSError(domain: "OBD", code: 10, userInfo: [
                NSLocalizedDescriptionKey:
                    "Bluetooth is not available -- is it switched on?"])
        }
        central.scanForPeripherals(withServices: nil, options: nil)
        guard connected.wait(timeout: .now() + timeout) == .success, ready else {
            central.stopScan()
            throw NSError(domain: "OBD", code: 11, userInfo: [
                NSLocalizedDescriptionKey: failure
                    ?? "the dongle did not answer.  If it appears in Settings > "
                     + "Bluetooth it is a CLASSIC adapter, and iOS cannot talk "
                     + "to those at all -- it needs a BLE one."])
        }
    }

    // ------------------------------------------------------------ OBDLink
    public func write(_ s: String) throws {
        guard let p = peripheral, let c = writeChar else {
            throw NSError(domain: "OBD", code: 12, userInfo: [
                NSLocalizedDescriptionKey: "not connected"])
        }
        let data = Data((s + "\r").utf8)
        // withoutResponse where the dongle allows it: an ELM327 is a stream of
        // short commands and waiting for an ack on each one halves the rate
        let mode: CBCharacteristicWriteType =
            c.properties.contains(.writeWithoutResponse) ? .withoutResponse
                                                         : .withResponse
        p.writeValue(data, for: c, type: mode)
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

    public func close() {
        if let p = peripheral { central.cancelPeripheralConnection(p) }
        peripheral = nil
        writeChar = nil
        ready = false
    }
}

extension BLELink: CBCentralManagerDelegate, CBPeripheralDelegate {
    public func centralManagerDidUpdateState(_ c: CBCentralManager) {
        if c.state == .poweredOn { poweredOn.signal() }
        else if c.state == .unauthorized {
            failure = "this app is not allowed to use Bluetooth -- Settings > "
                + "Privacy > Bluetooth"
            poweredOn.signal()
        } else if c.state == .poweredOff {
            failure = "Bluetooth is off"
            poweredOn.signal()
        }
    }

    public func centralManager(_ c: CBCentralManager,
                               didDiscover p: CBPeripheral,
                               advertisementData: [String: Any],
                               rssi RSSI: NSNumber) {
        let name = p.name
            ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
            ?? ""
        if let want = wantID, p.identifier == want {
            c.stopScan()
            peripheral = p
            p.delegate = self
            deviceName = name
            c.connect(p, options: nil)
            return
        }
        guard wantID == nil else { return }
        // Show anything that LOOKS like a dongle, and anything advertising an
        // OBD service even if it is namelessly called nothing at all.
        let upper = name.uppercased()
        let looksRight = obdNameHints.contains { upper.contains($0) }
        let services = (advertisementData[CBAdvertisementDataServiceUUIDsKey]
                        as? [CBUUID]) ?? []
        let advertisesOBD = services.contains { obdServiceUUIDs.contains($0) }
        guard looksRight || advertisesOBD, !name.isEmpty else { return }
        let f = Found(id: p.identifier, name: name, rssi: RSSI.intValue)
        if let i = discovered.firstIndex(where: { $0.id == f.id }) {
            discovered[i] = f
        } else {
            discovered.append(f)
        }
        let snapshot = discovered.sorted { $0.rssi > $1.rssi }
        DispatchQueue.main.async { [weak self] in self?.onDiscover?(snapshot) }
    }

    public func centralManager(_ c: CBCentralManager,
                               didConnect p: CBPeripheral) {
        p.discoverServices(nil)
    }

    public func centralManager(_ c: CBCentralManager,
                               didFailToConnect p: CBPeripheral,
                               error: Error?) {
        failure = error?.localizedDescription ?? "could not connect"
        connected.signal()
    }

    public func peripheral(_ p: CBPeripheral,
                           didDiscoverServices error: Error?) {
        for s in p.services ?? [] { p.discoverCharacteristics(nil, for: s) }
    }

    public func peripheral(_ p: CBPeripheral,
                           didDiscoverCharacteristicsFor service: CBService,
                           error: Error?) {
        // Take the first characteristic that can be written and the first
        // that can notify.  They are often the same pair (FFF1/FFF2) but the
        // numbering is not consistent across clones, so go by PROPERTIES
        // rather than by a hard-coded UUID -- that is what makes this work on
        // a dongle nobody has seen before.
        for ch in service.characteristics ?? [] {
            if ch.properties.contains(.notify) {
                p.setNotifyValue(true, for: ch)
            }
            if writeChar == nil,
               ch.properties.contains(.write)
                || ch.properties.contains(.writeWithoutResponse) {
                writeChar = ch
            }
        }
        if writeChar != nil && !ready {
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
