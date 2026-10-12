//
//  TurboTests.swift
//
//  The white-box turbo machine against the Python's (tools/export_turbo_ref.py
//  -> Fixtures/turbo.json): twelve cars covering every layout -- single (the
//  A3's EA211, the RS3), a NAMED wheel with no blow-off valve (the 2JZ single:
//  it surges), quad (Veyron), sequential (RX-7, Supra), twincharge (Delta S4),
//  MGU-H (SF-25), anti-lag (S1), twin-scroll (Golf R), diesel (F-450), a
//  surging twin (F40) -- each driven 5 s: idle, a wide-open pull through the
//  sequential hand-over, a LIFT, part throttle, the limiter's fuel cut.
//
//  Nothing is restored between frames: the machine runs on its own from the
//  baked spec and the live engine-side functions (EngineBreathing, on the
//  SHIPPED tables), so a wrong hook, a wrong constant or a wrong sign anywhere
//  in the integration shows up as a drift that grows.  The audio-rate twin is
//  checked through the lift (where it leaves the physics and resolves the
//  surge), and the car-mode observer on a boost ramp.
//

import XCTest
@testable import EngineSimCore

final class TurboTests: XCTestCase {
    struct TwinBlock: Decodable {
        let busy: Int
        let w_c: [[Double]]
        let p_p: [Double]
        let w_bov: [Double]
    }
    struct Case: Decodable {
        let spec: TurboSpec
        let coolant_c: Double
        let ic_soak: Double
        let dt: Double
        let frames: [[Double]]
        let state: [[Double]]
        let twin_sub: Int
        let twin_from: Int
        let twin: [TwinBlock]
        let follow: [[Double]]
        let follow_state: [[Double]]
        let hooks: [[Double]]
    }

    func fixture() throws -> [String: Case] {
        let url = Bundle.module.url(forResource: "turbo", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "turbo", withExtension: "json")
        guard let url else { throw XCTSkip("turbo.json missing") }
        return try JSONDecoder().decode([String: Case].self, from: Data(contentsOf: url))
    }

    /// swift/EngineSimCore/Tests/EngineSimCoreTests/ -> the repo's docs/
    func shipped(_ name: String) throws -> Data {
        let docs = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("docs")
        let url = docs.appendingPathComponent("\(name).json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw XCTSkip("\(url.path) is not here (package checked out alone)")
        }
        return try Data(contentsOf: url)
    }

    struct Fleet {
        let presets: [String: EnginePreset]
        let tables: [String: EngineTables]
    }

    func fleet() throws -> Fleet {
        Fleet(presets: try PresetLibrary.load(jsonData: try shipped("presets")),
              tables: try EngineTables.load(jsonData: try shipped("engine_tables")))
    }

    func machine(_ key: String, _ c: Case, _ f: Fleet) throws -> TurboSystem {
        guard let eng = f.presets[key], let tab = f.tables[key] else {
            throw XCTSkip("\(key) not in the shipped data")
        }
        let hooks = EngineBreathing(engine: eng, tables: tab, spec: c.spec)
        hooks.coolantC = c.coolant_c
        hooks.icSoak = c.ic_soak
        return TurboSystem(spec: c.spec, hooks: hooks)
    }

    // ------------------------------------------------------------------ data
    func testTheShippedTablesCarryTheSameMachines() throws {
        let cases = try fixture()
        let f = try fleet()
        for (key, c) in cases {
            XCTAssertEqual(f.tables[key]?.turbo, c.spec,
                           "\(key): docs/engine_tables.json is not the machine the fixture ran")
            let ts = try machine(key, c, f)
            for (u, s) in zip(ts.units, c.spec.units) {
                XCTAssertEqual(u.j, s.j, accuracy: 1e-12 * s.j, "\(key): rotor inertia")
                XCTAssertEqual(u.cFric, s.c_fric, accuracy: 1e-12 * s.c_fric,
                               "\(key): bearing friction")
            }
        }
        // every turbo car on the phone has a machine to run
        let fl = try fleet()
        var n = 0
        for (k, e) in fl.presets where e.induction == "turbo" && e.boostBar > 0 {
            XCTAssertNotNil(fl.tables[k]?.turbo, "\(k): no machine baked")
            n += 1
        }
        XCTAssertGreaterThanOrEqual(n, 61)
    }

    func testTheEngineSideMatchesThePython() throws {
        let cases = try fixture()
        let f = try fleet()
        for (key, c) in cases.sorted(by: { $0.key < $1.key }) {
            let ts = try machine(key, c, f)
            let h = ts.hooks
            var worst = 0.0
            for s in c.hooks {
                let (rpm, thr, pP) = (s[0], s[1], s[2])
                let (w, m, tm) = h.engineFlow(rpm: rpm, thr: thr, pP: pP)
                let load = min(m / Turbo.pAtm, 1.0)
                let t03 = h.exhaustTemp(rpm: rpm, load: load, pr: pP / Turbo.pAtm)
                let afr = h.afr(load: load, rpm: rpm)
                worst = max(worst, abs(w - s[3]) / max(abs(s[3]), 1e-6),
                            abs(m - s[4]) / s[4], abs(tm - s[5]) / s[5],
                            abs(t03 - s[6]) / s[6], abs(afr - s[7]) / s[7])
            }
            print(String(format: "  hooks %-8@ worst rel %.2e", key, worst))
            XCTAssertLessThan(worst, 1e-9, "\(key): the engine side drifts from the Python")
        }
    }

    // --------------------------------------------------------------- machine
    /// One frame's state, normalised so every quantity reads in the same units
    /// of 'how wrong' (pressures per bar, shaft per 10 krad/s, flows per the
    /// rated air flow, powers per kW).
    func errors(_ ts: TurboSystem, _ want: [Double]) -> [Double] {
        let wr = max(ts.wAirRated, 1e-3)
        var got = [ts.boost, ts.air.pP, ts.air.xBov, ts.air.wBov, ts.t03, ts.wEx,
                   ts.kThr, ts.mapPa, ts.tP, ts.p04, ts.wBypass]
        var scale = [1.0, 1e5, 1.0, wr, 1000.0, wr, wr / 1e5, 1e5, 300.0, 1e5, wr]
        for u in ts.units {
            got += [u.omega, u.wC, u.xWG, u.wgInt, u.p03, u.pT, u.pC, u.ecv,
                    u.active ? 1.0 : 0.0]
            scale += [1e4, wr, 1.0, 1.0, 1e5, 1e3, 1e3, 1.0, 1.0]
        }
        return zip(zip(got, want), scale).map { abs($0.0 - $0.1) / $1 }
    }

    func testTheMachineMatchesThePython() throws {
        let cases = try fixture()
        let f = try fleet()
        let names = ["boost", "p_p", "x_bov", "w_bov", "t03", "w_ex", "k_thr", "map",
                     "t_p", "p04", "w_bypass"]
        let unitNames = ["omega", "w_c", "x_wg", "wg_int", "p03", "p_t", "p_c", "ecv",
                         "active"]
        for (key, c) in cases.sorted(by: { $0.key < $1.key }) {
            let ts = try machine(key, c, f)
            var worst = 0.0, at = (0, 0)
            var twinWorst = 0.0
            var twin: Twin? = nil
            for (k, fr) in c.frames.enumerated() {
                ts.step(dt: c.dt, rpm: fr[0], thr: fr[1], fuelCut: fr[2] > 0.5, mguhW: fr[3])
                let e = errors(ts, c.state[k])
                if let m = e.max(), m > worst {
                    worst = m
                    at = (k, e.firstIndex(of: m) ?? 0)
                }
                // the audio-rate twin through the lift
                if c.twin_from >= 0 && k >= c.twin_from && k - c.twin_from < c.twin.count {
                    if twin == nil { twin = Twin(ts) }
                    let tw = twin!
                    tw.run(dt: c.dt, nSub: c.twin_sub)
                    let want = c.twin[k - c.twin_from]
                    XCTAssertEqual(tw.busy, want.busy, "\(key) frame \(k): twin busy")
                    XCTAssertEqual(tw.record.count, want.p_p.count, "\(key) frame \(k): substeps")
                    for i in 0..<min(tw.record.count, want.p_p.count) {
                        twinWorst = max(twinWorst,
                                        abs(tw.record.pP[i] - want.p_p[i]) / 1e5,
                                        abs(tw.record.wBov[i] - want.w_bov[i]) / ts.wAirRated)
                        for j in 0..<ts.units.count {
                            twinWorst = max(twinWorst, abs(tw.record.wC[j][i] - want.w_c[j][i])
                                            / ts.wAirRated)
                        }
                    }
                }
            }
            let nm = at.1 < names.count ? names[at.1]
                : "unit\((at.1 - names.count) / unitNames.count) "
                    + unitNames[(at.1 - names.count) % unitNames.count]
            print(String(format: "  machine %-8@ worst %.2e (frame %d, %@)  twin %.2e",
                         key, worst, at.0, nm, twinWorst))
            XCTAssertLessThan(worst, 1e-6, "\(key): the machine drifts from the Python (\(nm), frame \(at.0))")
            XCTAssertLessThan(twinWorst, 1e-6, "\(key): the twin drifts from the Python")

            // the observer: car mode's real boost
            var fw = 0.0
            for (k, fl) in c.follow.enumerated() {
                ts.follow(boostBarGauge: fl[2], rpm: fl[0], thr: fl[1], dt: c.dt)
                let want = c.follow_state[k]
                for (j, u) in ts.units.enumerated() {
                    fw = max(fw, abs(u.omega - want[j]) / 1e4)
                }
                let n = ts.units.count
                fw = max(fw, abs(ts.air.pP - want[n]) / 1e5,
                         abs(ts.kThr - want[n + 1]) / (ts.wAirRated / 1e5),
                         abs(ts.mapPa - want[n + 2]) / 1e5)
            }
            XCTAssertLessThan(fw, 1e-7, "\(key): the car-mode observer drifts from the Python")
        }
    }

    /// The physics the ear knows: a floored pedal spools, a lift with no
    /// blow-off valve surges (the flow REVERSES), with one it does not.
    func testSurgeOnlyWithoutAValve() throws {
        let cases = try fixture()
        let f = try fleet()
        for key in ["2jzst", "rs3"] {
            guard let c = cases[key] else { continue }
            let ts = try machine(key, c, f)
            var reversed = false
            for (k, fr) in c.frames.enumerated() {
                ts.step(dt: c.dt, rpm: fr[0], thr: fr[1], fuelCut: fr[2] > 0.5, mguhW: fr[3])
                if k >= 260 && k < 380 && ts.units.contains(where: { $0.wC < 0 }) {
                    reversed = true
                }
            }
            XCTAssertEqual(reversed, c.spec.bov_mode == "none",
                           "\(key): surge iff there is no blow-off valve")
        }
    }
}
