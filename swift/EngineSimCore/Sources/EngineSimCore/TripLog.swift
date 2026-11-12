//
//  TripLog.swift
//  A drive, written down (docs/REDESIGN_V2.md s.7.7, phase 0).
//
//  Everything the car link sees, on ONE clock, in one file: every request and
//  reply with the adapter, the phone's motion sensors, GPS, what the audio
//  path is doing and what was actually played.  A drive is then replayed on
//  the desk -- the lag and the smoothness are tuned on the car's own timing,
//  and a fault seen once in the car is a file, not another night in it.
//
//  The format is the v2 trip format (the core's replay reads these):
//
//      # enginesim-trip 1
//      M,<t>,key=value;key=value...        what was running
//      O,<t_reply>,<t_sent>,<request>,<reply, CR as |>
//      I,<t>,ax,ay,az,gx,gy,gz,rx,ry,rz    user acceleration (g), gravity (g),
//                                          rotation rate (rad/s), device frame
//      G,<t>,speed_mps,course_deg,h_acc_m,speed_acc_mps
//      A,<t>,route_ms,io_ms,queue,underruns,starvations,load,rate,route
//      P,<t>,rpm_played,rpm_raw,rpm_projected,pedal,boost_bar,gear,speed_mps
//      E,<t>,text                          events
//
//  <t> is seconds on the monotonic clock (Clock.now: since boot -- the
//  timebase CoreMotion stamps its samples with, and the audio host clock's).
//

import Foundation

/// The one clock every measurement is stamped with.  Monotonic: a wall clock
/// steps when the phone syncs its time, and a step is a lag that is not there.
public enum Clock {
    public static func now() -> TimeInterval { ProcessInfo.processInfo.systemUptime }
}

public final class TripLog {
    public static let header = "# enginesim-trip 1"

    let queue = DispatchQueue(label: "enginesim.trip", qos: .utility)
    var handle: FileHandle?
    var pending = Data()
    var lastFlush = 0.0
    var written = 0
    var fileURL: URL?

    public init() {}

    /// The file being written (or the last one), and how much is in it.
    public var url: URL? { queue.sync { fileURL } }
    public var bytes: Int { queue.sync { written + pending.count } }
    public var isOpen: Bool { queue.sync { handle != nil } }

    /// Start a new file `name` in `directory` (created if missing).
    public func open(directory: URL, name: String) throws {
        try FileManager.default.createDirectory(at: directory,
                                                withIntermediateDirectories: true)
        let u = directory.appendingPathComponent(name)
        FileManager.default.createFile(atPath: u.path, contents: nil)
        let h = try FileHandle(forWritingTo: u)
        queue.sync {
            if let old = handle { flushLocked(); try? old.close() }
            handle = h
            fileURL = u
            written = 0
            pending = Data((TripLog.header + "\n").utf8)
            lastFlush = Clock.now()
        }
    }

    /// One line: `kind,t,fields`.  Safe from any thread; never blocks on disk.
    public func line(_ kind: String, _ t: TimeInterval, _ fields: String) {
        let s = kind + "," + String(format: "%.4f", t) + "," + fields + "\n"
        queue.async { [self] in
            guard handle != nil else { return }
            pending.append(contentsOf: s.utf8)
            let now = Clock.now()
            if pending.count > 32768 || now - lastFlush > 1.0 { flushLocked() }
        }
    }

    func flushLocked() {
        guard let h = handle, !pending.isEmpty else { return }
        h.write(pending)
        written += pending.count
        pending.removeAll(keepingCapacity: true)
        lastFlush = Clock.now()
    }

    /// Everything so far onto disk (before sharing the file).
    public func flush() { queue.sync { flushLocked() } }

    public func close() {
        queue.sync {
            flushLocked()
            try? handle?.close()
            handle = nil
        }
    }

    /// A reply as one field: CR and LF become '|', commas ';'.
    public static func field(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.count)
        for ch in s {
            switch ch {
            case "\r", "\n": out.append("|")
            case ",": out.append(";")
            default: out.append(ch)
            }
        }
        return out
    }
}
