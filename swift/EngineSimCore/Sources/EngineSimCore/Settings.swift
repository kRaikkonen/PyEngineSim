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
    public var mapModeRaw = RpmMap.Mode.stretch.rawValue
    public var hidden: [Stage] = []
    /// sliders | pedal | live.  The pedal is an OFFLINE mode by definition:
    /// it invents the car, so it cannot be the source while a real one is
    /// talking.
    /// The pedal is what the app opens on: it is the mode you can use without
    /// a dongle, a car, or reading anything first.
    public var source = "pedal"
    public var pops = true
    /// Keep the note up when you lift.  Not physical, and on by choice.
    public var sustainOnLift = 0.85
    public var host = "192.168.0.10"
    public var port = 35000
    public var carIdle = 760.0
    public var carRedline = 6500.0
    /// Whether the rev range keeps learning from the car.  Set by hand once
    /// and it stops: an automatic value that overwrites what you just typed
    /// is worse than no automatic value.
    public var learnRange = true

    /// A public struct's memberwise init is INTERNAL by default, so from
    /// another module the only visible initialiser was the one Decodable
    /// synthesises -- which is why `Settings()` read as "missing argument
    /// for parameter 'from'".
    public init() {}

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
