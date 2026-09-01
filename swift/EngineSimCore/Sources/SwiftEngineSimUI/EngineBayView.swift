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
//  On the arithmetic: every geometric value in here is CGFloat and every
//  conversion from the model's Doubles is written out.  Swift will bridge the
//  two implicitly, but a mixed expression is both a type-checker time bomb and
//  genuinely hard to read, so the conversions are explicit and the expressions
//  are kept short.
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
        crankDeg = advanced.truncatingRemainder(dividingBy: 720.0)
        pulses.update(bay: bay, crankAngleDeg: crankDeg, dt: scaled,
                      soundSpeed: soundSpeed, load: load, rpm: rpm)
    }
}

// MARK: - per-cylinder drawing geometry

/// Everything needed to draw one cylinder, resolved to CGFloat once.
private struct CylGeo {
    var centre = CGPoint.zero        // crank centre for this station
    var axisX: CGFloat = 0           // unit vector up the bore
    var axisY: CGFloat = -1
    var perpX: CGFloat = -1          // across the bore
    var perpY: CGFloat = 0
    var pistonPin = CGPoint.zero
    var crankPin = CGPoint.zero
    var deck = CGPoint.zero
    var skirt = CGPoint.zero
    var halfBore: CGFloat = 0
    var port = CGPoint.zero
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
            TimelineView(.animation) { tl in
                Canvas { ctx, size in
                    guard let a = anim else { return }
                    a.step(now: tl.date.timeIntervalSinceReferenceDate,
                           rpm: model.simRPM,
                           load: max(model.pedal, model.pedalThrottle),
                           timeScale: timeScale,
                           soundSpeed: model.exhaustSoundSpeed)
                    render(ctx, size: size, a: a)
                }
                .frame(height: 268)
                .background(Color.black.opacity(0.28))
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

    // MARK: - drawing

    private func render(_ ctx: GraphicsContext, size: CGSize, a: BayAnimator) {
        let b = a.bay
        guard b.cylinderCount > 0 else { return }

        // The crank line runs across the picture and every cylinder stands on
        // it at its own station, which is the ordinary side-on cutaway.  A V
        // fans its two banks off that same line, because that is what a V is.
        let margin: CGFloat = 26
        let crankY = size.height * 0.74
        let stations = CGFloat(max(b.stationsPerBank, 1))
        let spacing = (size.width - margin * 2) / stations
        let strokePx = min(spacing * 0.42, (crankY - margin) * 0.30)
        let borePx = min(spacing * 0.62, strokePx * 2.1)
        let multiBank = b.bankAngles.count > 1

        var geo: [CylGeo] = []
        geo.reserveCapacity(b.cylinderCount)
        for s in b.slots {
            geo.append(makeGeo(s, bay: b, crank: a.crankDeg, crankY: crankY,
                               margin: margin, spacing: spacing,
                               strokePx: strokePx, borePx: borePx,
                               multiBank: multiBank))
        }

        drawCrankLine(ctx, size: size, margin: margin, crankY: crankY)
        drawHeaders(ctx, size: size, a: a, geo: geo, crankY: crankY)
        for (i, s) in b.slots.enumerated() {
            drawCylinder(ctx, g: geo[i], slot: s, bay: b, a: a,
                         strokePx: strokePx)
        }
        if b.charger != .na { drawCharger(ctx, size: size, a: a) }
        drawBoost(ctx, size: size, a: a)
    }

    private func makeGeo(_ s: EngineBay.Slot, bay b: EngineBay, crank: Double,
                         crankY: CGFloat, margin: CGFloat, spacing: CGFloat,
                         strokePx: CGFloat, borePx: CGFloat,
                         multiBank: Bool) -> CylGeo {
        var g = CylGeo()
        let aRad = s.bankAngleDeg * Double.pi / 180.0
        g.axisX = CGFloat(sin(aRad))
        g.axisY = CGFloat(-cos(aRad))
        g.perpX = g.axisY
        g.perpY = -g.axisX
        // banks are nudged apart along the crank the way real ones are offset
        // by the width of a rod
        let nudge = multiBank ? (CGFloat(s.bank) - 0.5) * spacing * 0.16 : 0
        let x = margin + spacing * (CGFloat(s.station) + 0.5) + nudge
        g.centre = CGPoint(x: x, y: crankY)
        g.halfBore = borePx / 2

        let r = strokePx / 2
        let ratio = max(s.rodLength / max(s.stroke / 2, 1e-6), 1.2)
        let rodPx = r * CGFloat(ratio)

        let frac = CGFloat(b.pistonFraction(s.index, crankAngleDeg: crank))
        let th = b.crankPinDeg(s.index, crankAngleDeg: crank) * Double.pi / 180.0
        let ct = CGFloat(cos(th))
        let st = CGFloat(sin(th))

        // Distance out to the piston pin comes straight from the slider-crank,
        // so the rod always measures rodPx and never has to be faked.
        let pinDist = (r + rodPx) - frac * strokePx
        g.pistonPin = CGPoint(x: g.centre.x + g.axisX * pinDist,
                              y: g.centre.y + g.axisY * pinDist)
        g.crankPin = CGPoint(x: g.centre.x + (g.axisX * ct + g.perpX * st) * r,
                             y: g.centre.y + (g.axisY * ct + g.perpY * st) * r)

        let deckDist = (r + rodPx) + strokePx * 0.14
        g.deck = CGPoint(x: g.centre.x + g.axisX * deckDist,
                         y: g.centre.y + g.axisY * deckDist)
        let skirtDist = max(rodPx - r, strokePx * 0.2)
        g.skirt = CGPoint(x: g.centre.x + g.axisX * skirtDist,
                          y: g.centre.y + g.axisY * skirtDist)

        // the exhaust port, on the outboard side of the head
        let outboard: CGFloat = s.bankAngleDeg < 0 ? -1 : 1
        g.port = CGPoint(x: g.deck.x + g.perpX * g.halfBore * outboard,
                         y: g.deck.y + g.perpY * g.halfBore * outboard)
        return g
    }

    private func drawCrankLine(_ ctx: GraphicsContext, size: CGSize,
                               margin: CGFloat, crankY: CGFloat) {
        var p = Path()
        p.move(to: CGPoint(x: margin * 0.5, y: crankY))
        p.addLine(to: CGPoint(x: size.width - margin * 0.5, y: crankY))
        ctx.stroke(p, with: .color(.gray.opacity(0.35)), lineWidth: 2)
    }

    private func drawCylinder(_ ctx: GraphicsContext, g: CylGeo,
                              slot s: EngineBay.Slot, bay b: EngineBay,
                              a: BayAnimator, strokePx: CGFloat) {
        let lights = model.cylinderLight
        let lit = s.index < lights.count ? lights[s.index] : 0
        let phase = b.stroke(s.index, crankAngleDeg: a.crankDeg)
        let hw = g.halfBore

        // barrel
        let barrel = quad(g.skirt, g.deck, perpX: g.perpX, perpY: g.perpY, w: hw)
        ctx.fill(barrel, with: .color(.white.opacity(0.05)))
        ctx.stroke(barrel, with: .color(.gray.opacity(0.5)), lineWidth: 1)

        // The burning charge is the volume ABOVE the piston, lit by the same
        // per-cylinder lamp the audio drives -- so a cylinder the limiter cut
        // goes dark on screen because it went quiet in the sound.
        if lit > 0.01 {
            let top = CGPoint(x: g.pistonPin.x + g.axisX * hw * 0.22,
                              y: g.pistonPin.y + g.axisY * hw * 0.22)
            let chamber = quad(top, g.deck, perpX: g.perpX, perpY: g.perpY, w: hw)
            let glow = 0.15 + 0.85 * lit
            ctx.fill(chamber, with: .color(Color(red: 1.0, green: 0.55,
                                                 blue: 0.12).opacity(glow)))
        }

        // rod, then the piston on top of it
        var rod = Path()
        rod.move(to: g.crankPin)
        rod.addLine(to: g.pistonPin)
        ctx.stroke(rod, with: .color(.gray.opacity(0.95)), lineWidth: 3)

        let crown = CGPoint(x: g.pistonPin.x + g.axisX * strokePx * 0.24,
                            y: g.pistonPin.y + g.axisY * strokePx * 0.24)
        let piston = quad(g.pistonPin, crown, perpX: g.perpX, perpY: g.perpY,
                          w: hw)
        ctx.fill(piston, with: .color(strokeColour(phase)))
        ctx.stroke(piston, with: .color(.white.opacity(0.55)), lineWidth: 1)

        // crank throw and journal
        var thr = Path()
        thr.move(to: g.centre)
        thr.addLine(to: g.crankPin)
        ctx.stroke(thr, with: .color(.orange.opacity(0.8)), lineWidth: 2.5)
        let j = CGRect(x: g.centre.x - 3, y: g.centre.y - 3, width: 6, height: 6)
        ctx.fill(Path(ellipseIn: j), with: .color(.gray))

        // valves, opening the way the cam the preset names says they do
        let lift = b.valveLift(s.index, crankAngleDeg: a.crankDeg,
                               rpm: model.simRPM)
        drawValve(ctx, g: g, side: -1, lift: lift.intake, colour: .cyan)
        drawValve(ctx, g: g, side: 1, lift: lift.exhaust, colour: .red)
    }

    /// A four-sided band from `from` to `to`, `w` either side of the axis.
    private func quad(_ from: CGPoint, _ to: CGPoint, perpX: CGFloat,
                      perpY: CGFloat, w: CGFloat) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: from.x + perpX * w, y: from.y + perpY * w))
        p.addLine(to: CGPoint(x: to.x + perpX * w, y: to.y + perpY * w))
        p.addLine(to: CGPoint(x: to.x - perpX * w, y: to.y - perpY * w))
        p.addLine(to: CGPoint(x: from.x - perpX * w, y: from.y - perpY * w))
        p.closeSubpath()
        return p
    }

    private func strokeColour(_ s: Stroke) -> Color {
        switch s {
        case .intake: return Color.cyan.opacity(0.75)
        case .compression: return Color.yellow.opacity(0.7)
        case .power: return Color.orange.opacity(0.9)
        case .exhaust: return Color.gray.opacity(0.8)
        }
    }

    private func drawValve(_ ctx: GraphicsContext, g: CylGeo, side: CGFloat,
                           lift: Double, colour: Color) {
        guard lift > 0.005 else { return }
        let open = CGFloat(lift) * g.halfBore * 0.6
        let rx = g.deck.x + g.perpX * g.halfBore * 0.5 * side
        let ry = g.deck.y + g.perpY * g.halfBore * 0.5 * side
        var p = Path()
        p.move(to: CGPoint(x: rx, y: ry))
        p.addLine(to: CGPoint(x: rx - g.axisX * open, y: ry - g.axisY * open))
        ctx.stroke(p, with: .color(colour.opacity(0.35 + 0.65 * lift)),
                   lineWidth: 3)
    }

    // Each bank runs back to its own collector and the pulses crawl down the
    // primaries at the gas's own speed of sound -- so on a 4-into-1 you watch
    // them arrive staggered by the firing interval, which is the whole reason
    // a header is a shape and not just a pipe.
    private func drawHeaders(_ ctx: GraphicsContext, size: CGSize,
                             a: BayAnimator, geo: [CylGeo], crankY: CGFloat) {
        let b = a.bay
        let collY = size.height - 14
        let nBank = max(b.bankAngles.count, 1)
        let tip = CGPoint(x: size.width - 8, y: collY)

        func collector(_ bank: Int) -> CGPoint {
            let f: CGFloat = nBank == 1 ? 0.80 : (bank == 0 ? 0.17 : 0.80)
            return CGPoint(x: size.width * f, y: collY)
        }
        func control(_ port: CGPoint, _ coll: CGPoint) -> CGPoint {
            CGPoint(x: (port.x + coll.x) * 0.5,
                    y: port.y + (coll.y - port.y) * 0.18)
        }

        for (i, s) in b.slots.enumerated() where i < geo.count {
            let port = geo[i].port
            let coll = collector(s.bank)
            var p = Path()
            p.move(to: port)
            p.addQuadCurve(to: coll, control: control(port, coll))
            ctx.stroke(p, with: .color(.gray.opacity(0.42)), lineWidth: 4)
        }

        for pulse in a.pulses.pulses {
            guard pulse.cylinder < geo.count else { continue }
            let port = geo[pulse.cylinder].port
            let coll = collector(pulse.bank)
            var pt: CGPoint
            if pulse.primary < 1.0 {
                pt = bezier(port, control(port, coll), coll,
                            CGFloat(pulse.primary))
            } else {
                let t = CGFloat(max(pulse.tail, 0.0))
                pt = CGPoint(x: coll.x + (tip.x - coll.x) * t, y: collY)
            }
            let rr = CGFloat(3.0 + 4.0 * pulse.strength)
            let box = CGRect(x: pt.x - rr, y: pt.y - rr,
                             width: rr * 2, height: rr * 2)
            let hot = Color(red: 1.0, green: 0.62, blue: 0.2)
            ctx.fill(Path(ellipseIn: box),
                     with: .color(hot.opacity(0.25 + 0.75 * pulse.strength)))
        }

        for bk in 0..<nBank {
            let c = collector(bk)
            let box = CGRect(x: c.x - 5, y: c.y - 5, width: 10, height: 10)
            ctx.fill(Path(ellipseIn: box), with: .color(.gray.opacity(0.7)))
        }
        var tail = Path()
        tail.move(to: collector(nBank - 1))
        tail.addLine(to: tip)
        ctx.stroke(tail, with: .color(.gray.opacity(0.5)), lineWidth: 5)

        if a.pulses.exitFlash > 0.02 {
            let f = a.pulses.exitFlash
            let rr = CGFloat(5.0 + 12.0 * f)
            let box = CGRect(x: tip.x - rr, y: tip.y - rr,
                             width: rr * 2, height: rr * 2)
            ctx.fill(Path(ellipseIn: box), with: .color(.orange.opacity(0.55 * f)))
        }
    }

    private func bezier(_ p0: CGPoint, _ c: CGPoint, _ p1: CGPoint,
                        _ t: CGFloat) -> CGPoint {
        let u = 1 - t
        let x = u * u * p0.x + 2 * u * t * c.x + t * t * p1.x
        let y = u * u * p0.y + 2 * u * t * c.y + t * t * p1.y
        return CGPoint(x: x, y: y)
    }

    // A turbo is driven by the exhaust and hangs on after a lift; a blower is
    // belted to the crank and cannot.  The wheel spins from whichever of those
    // two the preset actually has.
    private func drawCharger(_ ctx: GraphicsContext, size: CGSize,
                             a: BayAnimator) {
        let b = a.bay
        let spin = b.chargerSpin(rpm: model.simRPM, boostBar: model.boostBar)
        let c = CGPoint(x: size.width * 0.10, y: size.height * 0.16)
        let r: CGFloat = 21
        let box = CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)
        ctx.stroke(Path(ellipseIn: box), with: .color(.gray.opacity(0.6)),
                   lineWidth: 2)

        let blades = b.charger == .roots ? 3 : 9
        // turned by the SAME integrated crank, so wheel and pistons stay in step
        let phase = a.crankDeg * 0.0175 * (0.4 + 3.0 * spin)
        for k in 0..<blades {
            let ang = phase + Double(k) * 2.0 * Double.pi / Double(blades)
            var p = Path()
            p.move(to: c)
            p.addLine(to: CGPoint(x: c.x + CGFloat(cos(ang)) * r * 0.85,
                                  y: c.y + CGFloat(sin(ang)) * r * 0.85))
            ctx.stroke(p, with: .color(.cyan.opacity(0.3 + 0.6 * spin)),
                       lineWidth: 2)
        }
        let name = b.charger == .twinTurbo ? "turbo ×2" : b.charger.rawValue
        ctx.draw(Text(name).font(.system(size: 9)).foregroundColor(.secondary),
                 at: CGPoint(x: c.x, y: c.y + r + 9))
    }

    private func drawBoost(_ ctx: GraphicsContext, size: CGSize, a: BayAnimator) {
        let maxB = max(a.bay.engine.boostBar, 0.35)
        let f = CGFloat(min(max(model.boostBar / maxB, 0.0), 1.1))
        let w = size.width * 0.30
        let x = size.width - w - 14
        let y: CGFloat = 13
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w, height: 7),
                      cornerRadius: 3.5),
                 with: .color(.white.opacity(0.12)))
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w * f, height: 7),
                      cornerRadius: 3.5),
                 with: .color(f > 0.98 ? .red : .green))
        let bar = (model.boostBar * 100).rounded() / 100
        ctx.draw(Text("\(bar) bar")
            .font(.system(size: 9, design: .monospaced))
            .foregroundColor(.secondary),
                 at: CGPoint(x: x + w * 0.5, y: y + 17))
    }
}
