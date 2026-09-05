//
//  EngineBayView.swift
//  The engine bay, drawn.
//
//  WHY EVERY MOVING PART LIVES IN HERE.  The animation is a switch, and off has
//  to mean OFF -- not "drawn but hidden", and not "still integrated but not
//  shown".  So the crank angle, the travelling exhaust pulses and the redraw
//  clock are all owned by THIS view.  SwiftUI does not build a view an `if`
//  excluded, so with the switch off nothing here is allocated, nothing steps,
//  and the app costs exactly what it cost before this file existed.  Nothing
//  was added to AppModel's 50 Hz tick for the same reason.
//
//  The clock is a TimelineView rather than a Timer feeding @Published: at 60 Hz
//  a published crank angle would invalidate the whole screen sixty times a
//  second and redraw the readouts, the layer list and the pedal along with it.
//  A TimelineView redraws only the Canvas.
//
//  All the SHADING is in BayPaint -- strip-shaded round metal, domed caps,
//  I-beam rods, a radial fire bloom.  This file decides WHERE the parts are;
//  that file decides what they look like; EngineBay decides what the mechanism
//  is doing.  Keeping those three apart is what stops the picture drifting away
//  from the sound.
//

import SwiftUI
import EngineSimCore

// MARK: - the moving state

/// Crank angle and exhaust pulses.  A plain class, deliberately NOT
/// ObservableObject: it is stepped and read inside the Canvas draw, and
/// publishing it would put SwiftUI back in the loop it is being kept out of.
final class BayAnimator {
    let bay: EngineBay
    var crankDeg = 0.0
    var pulses: ExhaustPulseField
    private var last: Double?

    init(engine: EnginePreset) {
        let b = EngineBay(engine: engine)
        bay = b
        pulses = ExhaustPulseField(bay: b)
    }

    func step(now: Double, rpm: Double, load: Double, timeScale: Double,
              soundSpeed: Double) {
        guard let l = last else { last = now; return }
        // Cap the step: coming back from the background hands you a dt of
        // several seconds, and integrating that would spin the crank through
        // thousands of revolutions and launch a pulse for every one.
        let dt = min(max(now - l, 0.0), 0.1)
        last = now
        let scaled = dt * max(timeScale, 0.0)
        let advanced = crankDeg + rpm * 6.0 * scaled          // deg/s = rpm*6
        // 2160, not 720.  A piston repeats every 720 but a ROTOR takes 1080 of
        // shaft to come round, and 2160 is the first angle that is a whole
        // number of both -- wrapping at 720 would jog every rotary a third of
        // a turn backwards twice per cycle.
        crankDeg = advanced.truncatingRemainder(dividingBy: 2160.0)
        pulses.update(bay: bay, crankAngleDeg: crankDeg, dt: scaled,
                      soundSpeed: soundSpeed, load: load, rpm: rpm)
    }
}

/// Where one cylinder's parts are this frame, all resolved to CGFloat once.
struct CylGeo {
    var crank = CGPoint.zero        // crank centre for this station
    var axis = BayAxis(angleDeg: 0)
    var boreBase: CGFloat = 0       // distance from the crank up to the sleeve
    var deck: CGFloat = 0           // distance from the crank up to the head
    var pinDist: CGFloat = 0        // crank centre -> wrist pin
    var crankPin = CGPoint.zero
    var halfBore: CGFloat = 0
    var crankRadius: CGFloat = 0
    var pistonLen: CGFloat = 0
    var port = CGPoint.zero          // exhaust, outboard side
    var intakePort = CGPoint.zero    // inboard side
    var headTop: CGFloat = 0         // top of the head casting
    var side: CGFloat = 1            // which way its plumbing leaves
    /// Which HEADER this cylinder joins.  Not the same as `side`: a W's two
    /// VR units each contain two sub-banks, and a header follows a sub-bank.
    /// Grouping by side instead ran one pipe zig-zagging between the two.
    var group: Int = 0
}

// MARK: - the view

struct EngineBayView: View {
    @ObservedObject var model: AppModel
    /// 1 = real time.  Below that the strobe unwinds and the strokes become
    /// followable -- the honest way to fix aliasing, since it slows the clock
    /// instead of lying about where the piston is.
    @Binding var timeScale: Double

    @State private var anim: BayAnimator?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // 30 Hz, not display rate.  At any real rpm the crank is
            // aliasing regardless, so the second thirty frames a second
            // buy nothing and cost half the draw budget.
            TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { tl in
                Canvas { ctx, size in
                    guard let a = anim else { return }
                    a.step(now: tl.date.timeIntervalSinceReferenceDate,
                           rpm: model.simRPM,
                           load: max(model.pedal, model.pedalThrottle),
                           timeScale: timeScale,
                           soundSpeed: model.exhaustSoundSpeed)
                    BayScene(bay: a.bay, crankDeg: a.crankDeg,
                             pulses: a.pulses, rpm: model.simRPM,
                             boostBar: model.boostBar,
                             load: max(model.pedal, model.pedalThrottle))
                        .draw(ctx, size: size)
                }
                .frame(height: 358)
                .background(
                    LinearGradient(colors: [Color(white: 0.10), Color(white: 0.04)],
                                   startPoint: .top, endPoint: .bottom))
                .cornerRadius(12)
            }
            legend
            speedControl
        }
        // Built HERE and not inside the draw: allocating in a Canvas pass would
        // rebuild the engine on every frame until the assignment landed.
        .onAppear { rebuild() }
        .onChange(of: model.engineKey) { _ in rebuild() }
    }

    private func rebuild() {
        anim = model.enginePreset.map { BayAnimator(engine: $0) }
    }

    // ------------------------------------------------------------- legend
    private var legend: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(model.engineName).font(.caption).bold()
            if let b = anim?.bay {
                Text(bayLine(b))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.secondary)
                Text(firingLine(b))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.secondary)
            }
        }
    }

    // Interpolation rather than String(format:) with %@ -- this project has
    // already been bitten once by bridging a Swift string through a C format.
    private func bayLine(_ b: EngineBay) -> String {
        // A rotary's bore, stroke and therefore displacement are PLACEHOLDERS
        // in the preset -- they exist so the pulse model has a pulse size, and
        // reading them out would claim a 787B is 3.2 L when it is 2.6.
        if b.layout == .rotary {
            let kk = (b.rotary?.k ?? 7).rounded()
            return "\(b.rotorCount)-rotor Wankel  ·  R/e \(Int(kk))"
                + "  ·  " + b.charger.label
        }
        let c = b.engine.cylinders.first
        let bore = ((c?.bore ?? 0) * 1000.0).rounded()
        let str = ((c?.stroke ?? 0) * 1000.0).rounded()
        let disp = b.engine.cylinders.reduce(0.0) { $0 + $1.displacement } * 1000.0
        var head = ""
        if b.bankAngles.count > 1 {
            let spread = (b.bankAngles.last ?? 0) - (b.bankAngles.first ?? 0)
            head = "\(Int(spread.rounded()))° "
        }
        let litres = (disp * 10).rounded() / 10
        return head + b.layout.label + "\(b.cylinderCount)"
            + "  ·  \(litres) L  ·  \(Int(bore)) × \(Int(str)) mm"
            + "  ·  " + b.charger.label
    }

    private func firingLine(_ b: EngineBay) -> String {
        let every = Int((720.0 / Double(max(b.cylinderCount, 1))).rounded())
        if b.layout == .rotary {
            let order = (1...b.rotorCount).map(String.init).joined(separator: "-")
            return "fires every \(every)°  ·  rotors \(order)  ·  each face 1080°"
        }
        let order = b.firingOrder.map { String($0 + 1) }.joined(separator: "-")
        return "fires every \(every)°  ·  \(order)"
    }

    // -------------------------------------------------------- speed control
    // Not a gimmick: at 6000 rpm the crank turns a hundred times a second and
    // any screen samples it far too slowly, so the pistons alias into a crawl.
    // Slowing TIME is the fix that keeps the geometry honest.
    private var speedControl: some View {
        HStack(spacing: 8) {
            Text("speed").font(.caption2).foregroundColor(.secondary)
            ForEach([1.0, 0.25, 0.05, 0.01], id: \.self) { s in
                Button(s >= 1.0 ? "live" : "\(s)×") { timeScale = s }
                    .font(.caption2)
                    .padding(.horizontal, 9).padding(.vertical, 5)
                    .background(abs(timeScale - s) < 1e-6
                                ? Color.accentColor.opacity(0.25)
                                : Color.secondary.opacity(0.12))
                    .cornerRadius(6)
            }
            Spacer()
        }
    }
}
