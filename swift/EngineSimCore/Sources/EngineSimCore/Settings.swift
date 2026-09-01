//
//  Settings.swift
//  What the app remembers.
//
//  Everything here is a CHOICE the listener made, and having to make it again
//  every launch is the kind of small friction that stops a thing being used.
//  So it is written on every change -- not on quit, because an app that is
//  killed by the system (which is what happens when you unplug the phone and
//  walk away) never gets to run a quit handler.
//
//  Named slots would need a keyboard, and a keyboard in a car is not a
//  control.  So the slots are numbered and the gesture carries the meaning:
//  tap to load, hold to save over.  Nothing to type, nothing to dismiss.
//

//  It lives in the CORE, not the UI: what the listener chose is not a view
//  concern, and keeping it here means it can be tested without linking
//  SwiftUI -- which on macOS is the difference between a test suite that runs
//  and one the system kills.

import Foundation

public struct Settings: Codable, Equatable {
    public var engineKey = "a3"
    /// DIRECT by default because demo mode drives the engine ITSELF -- there
    /// is no other car to stretch from, so any other mapping would be
    /// distorting a number it invented.  It matters once a real car is
    /// talking, and that is when the app offers the choice.
    public var mapModeRaw = RpmMap.Mode.direct.rawValue
    public var hidden: [Stage] = []
    /// sliders | pedal | live.  The pedal is an OFFLINE mode by definition:
    /// it invents the car, so it cannot be the source while a real one is
    /// talking.
    /// demo | live.  Demo is what the app opens on: it is the mode you can
    /// use without a dongle, a car, or reading anything first.
    public var source = "demo"
    public var pops = true
    /// Keep the note up when you lift.  Not physical, and on by choice.
    public var sustainOnLift = 0.85
    public var host = "192.168.0.10"
    public var port = 35000
    /// The BLE dongle last connected to, so it reconnects without a scan.
    public var bleDeviceID = ""
    public var bleDeviceName = ""
    /// wifi | ble.  Two completely different radios; only one is plugged in.
    public var linkKind = "ble"
    public var carIdle = 760.0
    public var carRedline = 6500.0
    /// Whether the rev range keeps learning from the car.  Set by hand once
    /// and it stops: an automatic value that overwrites what you just typed
    /// is worse than no automatic value.
    public var learnRange = true
    /// The engine-bay animation.  OFF by default and off means OFF: the view
    /// is not built, so nothing is allocated and nothing is stepped, and the
    /// app costs exactly what it did before the animation existed.
    public var showBay = false
    /// 1 = real time.  At 6000 rpm the crank turns a hundred times a second
    /// and any screen samples it far too slowly, so the pistons alias into a
    /// crawl -- slowing the clock is the honest fix.
    public var bayTimeScale = 1.0

    /// A public struct's memberwise init is INTERNAL by default, so from
    /// another module the only visible initialiser was the one Decodable
    /// synthesises -- which is why `Settings()` read as "missing argument
    /// for parameter 'from'".
    public init() {}

    /// Decode field by field, keeping the default for anything absent.
    ///
    /// The synthesised decoder throws on the FIRST missing key, and the store
    /// treats a throw as "no settings" -- so simply adding a property here
    /// would silently wipe every saved setting and slot the next time the app
    /// launched.  Doing it by hand costs a few lines once and makes the format
    /// additive from now on.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func str(_ k: CodingKeys, _ d: String) -> String {
            (try? c.decodeIfPresent(String.self, forKey: k)).flatMap { $0 } ?? d
        }
        func dbl(_ k: CodingKeys, _ d: Double) -> Double {
            (try? c.decodeIfPresent(Double.self, forKey: k)).flatMap { $0 } ?? d
        }
        func bul(_ k: CodingKeys, _ d: Bool) -> Bool {
            (try? c.decodeIfPresent(Bool.self, forKey: k)).flatMap { $0 } ?? d
        }
        engineKey = str(.engineKey, "a3")
        mapModeRaw = str(.mapModeRaw, RpmMap.Mode.direct.rawValue)
        hidden = (try? c.decodeIfPresent([Stage].self, forKey: .hidden))
            .flatMap { $0 } ?? []
        source = str(.source, "demo")
        pops = bul(.pops, true)
        sustainOnLift = dbl(.sustainOnLift, 0.85)
        host = str(.host, "192.168.0.10")
        port = (try? c.decodeIfPresent(Int.self, forKey: .port))
            .flatMap { $0 } ?? 35000
        bleDeviceID = str(.bleDeviceID, "")
        bleDeviceName = str(.bleDeviceName, "")
        linkKind = str(.linkKind, "ble")
        carIdle = dbl(.carIdle, 760.0)
        carRedline = dbl(.carRedline, 6500.0)
        learnRange = bul(.learnRange, true)
        showBay = bul(.showBay, false)
        bayTimeScale = dbl(.bayTimeScale, 1.0)
    }

    enum CodingKeys: String, CodingKey {
        case engineKey, mapModeRaw, hidden, source, pops, sustainOnLift
        case host, port, bleDeviceID, bleDeviceName, linkKind
        case carIdle, carRedline, learnRange, showBay, bayTimeScale
    }

    public var mapMode: RpmMap.Mode {
        get { RpmMap.Mode(rawValue: mapModeRaw) ?? .stretch }
        set { mapModeRaw = newValue.rawValue }
    }

    public static let `default` = Settings()
}

/// UserDefaults-backed, because this is a handful of scalars and a list of
/// strings -- a file would be a second thing that can go wrong for no gain.
public enum SettingsStore {
    static let currentKey = "swiftEngineSim.settings"
    static let slotKey = "swiftEngineSim.slot."
    public static let slotCount = 3

    public static func load() -> Settings {
        guard let d = UserDefaults.standard.data(forKey: currentKey),
              let s = try? JSONDecoder().decode(Settings.self, from: d)
        else { return .default }
        return s
    }

    public static func save(_ s: Settings) {
        guard let d = try? JSONEncoder().encode(s) else { return }
        UserDefaults.standard.set(d, forKey: currentKey)
    }

    public static func loadSlot(_ i: Int) -> Settings? {
        guard let d = UserDefaults.standard.data(forKey: slotKey + "\(i)"),
              let s = try? JSONDecoder().decode(Settings.self, from: d)
        else { return nil }
        return s
    }

    public static func saveSlot(_ i: Int, _ s: Settings) {
        guard let d = try? JSONEncoder().encode(s) else { return }
        UserDefaults.standard.set(d, forKey: slotKey + "\(i)")
    }

    public static func slotFilled(_ i: Int) -> Bool {
        UserDefaults.standard.data(forKey: slotKey + "\(i)") != nil
    }
}
