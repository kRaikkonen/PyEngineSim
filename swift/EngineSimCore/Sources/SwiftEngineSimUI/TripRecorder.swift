//
//  TripRecorder.swift
//  The phone's own sensors into the trip log (docs/REDESIGN_V2.md, phase 0).
//
//  The accelerometer is the FAST view of the car: 100 samples a second, a few
//  milliseconds old, where the OBD link gives ten a second, each 30-70 ms old.
//  In gear the revs follow the road speed, so the car's acceleration IS the
//  rate the revs are changing at -- which is what the v2 link is built on.
//  Phase 0 only writes it down, next to the OBD traffic, so that design is
//  made on this car's real numbers.
//
//  The phone has to sit still in the car (a cup holder, a tray); which way it
//  faces does not matter.  GPS is a slow, independent check of the speed.
//  Nothing here is needed for the sound: if a sensor is missing or refused,
//  the trip is simply recorded without it.
//

import Foundation
import EngineSimCore
#if canImport(CoreMotion)
import CoreMotion
#endif
#if canImport(CoreLocation)
import CoreLocation
#endif

public final class TripRecorder: NSObject {
    public let log = TripLog()
    public private(set) var recording = false
    public private(set) var started = 0.0

    #if canImport(CoreMotion) && os(iOS)
    let motion = CMMotionManager()
    let motionQueue: OperationQueue = {
        let q = OperationQueue()
        q.name = "enginesim.motion"
        q.maxConcurrentOperationCount = 1
        return q
    }()
    #endif
    #if canImport(CoreLocation) && os(iOS)
    let location = CLLocationManager()
    #endif

    /// Documents/trips -- visible in the Files app (UIFileSharingEnabled).
    public static var directory: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)
        return (docs.first ?? FileManager.default.temporaryDirectory)
            .appendingPathComponent("trips", isDirectory: true)
    }

    /// Begin a new trip file; `meta` is what was running (engine, settings).
    public func start(meta: String) {
        guard !recording else { return }
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        f.locale = Locale(identifier: "en_US_POSIX")
        do {
            try log.open(directory: TripRecorder.directory,
                         name: "trip-" + f.string(from: Date()) + ".csv")
        } catch {
            return
        }
        recording = true
        started = Clock.now()
        // the wall-clock time this monotonic instant is, for the record
        log.line("M", started, "wall=" + String(format: "%.3f", Date().timeIntervalSince1970)
                 + ";" + meta)
        startSensors()
    }

    public func stop() {
        guard recording else { return }
        recording = false
        stopSensors()
        log.line("E", Clock.now(), "trip stopped")
        log.close()
    }

    func startSensors() {
        #if canImport(CoreMotion) && os(iOS)
        if motion.isDeviceMotionAvailable {
            motion.deviceMotionUpdateInterval = 0.01
            let log = self.log
            motion.startDeviceMotionUpdates(to: motionQueue) { m, _ in
                guard let m else { return }
                let a = m.userAcceleration, g = m.gravity, r = m.rotationRate
                // m.timestamp is seconds since boot: the trip's own clock
                log.line("I", m.timestamp, String(
                    format: "%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f",
                    a.x, a.y, a.z, g.x, g.y, g.z, r.x, r.y, r.z))
            }
        } else {
            log.line("E", Clock.now(), "no device motion on this device")
        }
        #endif
        #if canImport(CoreLocation) && os(iOS)
        location.delegate = self
        location.desiredAccuracy = kCLLocationAccuracyBest
        location.pausesLocationUpdatesAutomatically = false
        // only with the background mode declared: without it this property
        // raises an exception, and a trip must never take the sound down
        let modes = Bundle.main.object(forInfoDictionaryKey: "UIBackgroundModes") as? [String]
        if modes?.contains("location") == true {
            location.allowsBackgroundLocationUpdates = true
        }
        location.requestWhenInUseAuthorization()
        location.startUpdatingLocation()
        #endif
    }

    func stopSensors() {
        #if canImport(CoreMotion) && os(iOS)
        motion.stopDeviceMotionUpdates()
        #endif
        #if canImport(CoreLocation) && os(iOS)
        location.stopUpdatingLocation()
        #endif
    }
}

#if canImport(CoreLocation) && os(iOS)
extension TripRecorder: CLLocationManagerDelegate {
    public func locationManager(_ manager: CLLocationManager,
                                didUpdateLocations locations: [CLLocation]) {
        let wallNow = Date()
        let monoNow = Clock.now()
        for l in locations {
            // the fix's own time, moved onto the trip's clock
            let t = monoNow - wallNow.timeIntervalSince(l.timestamp)
            log.line("G", t, String(format: "%.3f,%.1f,%.1f,%.2f",
                                    l.speed, l.course, l.horizontalAccuracy,
                                    l.speedAccuracy))
        }
    }

    public func locationManager(_ manager: CLLocationManager,
                                didFailWithError error: Error) {
        log.line("E", Clock.now(), "gps: " + TripLog.field(error.localizedDescription))
    }
}
#endif
