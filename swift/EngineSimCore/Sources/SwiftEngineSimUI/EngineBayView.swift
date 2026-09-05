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
private struct CylGeo {
    var crank = CGPoint.zero        // crank centre for this station
    var axis = Axis(angleDeg: 0)
    var boreBase: CGFloat = 0       // distance from the crank up to the sleeve
    var deck: CGFloat = 0           // distance from the crank up to the head
    var pinDist: CGFloat = 0        // crank centre -> wrist pin
    var crankPin = CGPoint.zero
    var halfBore: CGFloat = 0
    var crankRadius: CGFloat = 0
    var pistonLen: CGFloat = 0
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
                .frame(height: 300)
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

    // MARK: - top level

    private func render(_ ctx: GraphicsContext, size: CGSize, a: BayAnimator) {
        let b = a.bay
        guard b.cylinderCount > 0 else { return }
        if let r = b.rotary {
            renderRotary(ctx, size: size, a: a, geo: r)
            return
        }
        renderPistons(ctx, size: size, a: a)
    }

    // MARK: - piston engines

    private func renderPistons(_ ctx: GraphicsContext, size: CGSize,
                               a: BayAnimator) {
        let b = a.bay
        let margin: CGFloat = 22
        let crankY = size.height * 0.70
        let stations = CGFloat(max(b.stationsPerBank, 1))
        let spacing = (size.width - margin * 2) / stations
        let multiBank = b.bankAngles.count > 1
        // the sleeve has to fit between the crank and the top of the frame
        let headroom = crankY - margin
        let strokePx = min(spacing * 0.40, headroom * 0.26)
        let borePx = min(spacing * 0.58, strokePx * 2.0)

        var geo: [CylGeo] = []
        geo.reserveCapacity(b.cylinderCount)
        for s in b.slots {
            geo.append(makeGeo(s, bay: b, crank: a.crankDeg, crankY: crankY,
                               margin: margin, spacing: spacing,
                               strokePx: strokePx, borePx: borePx,
                               multiBank: multiBank))
        }

        // crankcase slab behind everything, so the parts sit IN an engine
        drawCrankcase(ctx, size: size, crankY: crankY, margin: margin)
        drawHeaders(ctx, size: size, a: a, geo: geo,
                    pipeIndices: Array(geo.indices))
        // back to front: sleeves, then what moves inside them
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b, a: a)
        }
        drawCrankshaft(ctx, geo: geo, size: size, crankY: crankY, margin: margin)
        if b.charger != .na { drawCharger(ctx, size: size, a: a) }
        drawBoost(ctx, size: size, a: a)
    }

    private func makeGeo(_ s: EngineBay.Slot, bay b: EngineBay, crank: Double,
                         crankY: CGFloat, margin: CGFloat, spacing: CGFloat,
                         strokePx: CGFloat, borePx: CGFloat,
                         multiBank: Bool) -> CylGeo {
        var g = CylGeo()
        g.axis = Axis(angleDeg: s.bankAngleDeg)
        // banks nudged apart along the crank the way real ones are offset by
        // the width of a rod
        let nudge = multiBank ? (CGFloat(s.bank) - 0.5) * spacing * 0.14 : 0
        let x = margin + spacing * (CGFloat(s.station) + 0.5) + nudge
        g.crank = CGPoint(x: x, y: crankY)
        g.halfBore = borePx / 2

        let r = strokePx / 2
        g.crankRadius = r
        let ratio = max(s.rodLength / max(s.stroke / 2, 1e-6), 1.2)
        let rodPx = r * CGFloat(ratio)
        g.pistonLen = strokePx * 0.34

        let frac = CGFloat(b.pistonFraction(s.index, crankAngleDeg: crank))
        let th = b.crankPinDeg(s.index, crankAngleDeg: crank) * Double.pi / 180.0
        let ct = CGFloat(cos(th)), st = CGFloat(sin(th))

        // Straight from the slider-crank, so the rod always measures rodPx and
        // never has to be faked to reach.
        g.pinDist = (r + rodPx) - frac * strokePx
        g.crankPin = CGPoint(x: g.crank.x + (g.axis.ux * ct + g.axis.qx * st) * r,
                             y: g.crank.y + (g.axis.uy * ct + g.axis.qy * st) * r)
        g.boreBase = r * 1.45
        g.deck = (r + rodPx) + g.pistonLen + strokePx * 0.12

        let outboard: CGFloat = s.bankAngleDeg < 0 ? -1 : 1
        g.port = g.axis.at(g.crank, g.deck - strokePx * 0.18,
                           g.halfBore * 1.05 * outboard)
        return g
    }

    /// The crankcase the whole thing is bolted to.
    private func drawCrankcase(_ ctx: GraphicsContext, size: CGSize,
                               crankY: CGFloat, margin: CGFloat) {
        let top = crankY - 6
        let h = size.height - top - 4
        let rect = CGRect(x: margin * 0.4, y: top,
                          width: size.width - margin * 0.8, height: h)
        ctx.fill(Path(roundedRect: rect, cornerRadius: 7),
                 with: .linearGradient(
                    Gradient(colors: [Metal.block.f(1.25), Metal.block.f(0.55)]),
                    startPoint: CGPoint(x: rect.minX, y: rect.minY),
                    endPoint: CGPoint(x: rect.minX, y: rect.maxY)))
        ctx.stroke(Path(roundedRect: rect, cornerRadius: 7),
                   with: .color(Metal.outline.color), lineWidth: 1)
        // sump bolts along the lower flange
        var x = rect.minX + 12
        while x < rect.maxX - 6 {
            let r: CGFloat = 1.6
            ctx.fill(Path(ellipseIn: CGRect(x: x - r, y: rect.maxY - 7,
                                            width: r * 2, height: r * 2)),
                     with: .color(Metal.block.f(1.7)))
            x += 16
        }
    }

    /// The static shell: sleeve, cooling fins, head, valves.
    private func drawSleeve(_ ctx: GraphicsContext, g: CylGeo,
                            slot s: EngineBay.Slot, bay b: EngineBay) {
        let ax = g.axis, o = g.crank
        let hw = g.halfBore
        // sleeve, strip-shaded so it reads round
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.boreBase - 3,
                        to: g.deck, halfWidth: hw + 3, metal: .sleeve, strips: 16)
        // cooling fins across the barrel
        var d = g.boreBase + 4
        while d < g.deck - 6 {
            var f = Path()
            f.move(to: ax.at(o, d, hw + 3))
            f.addLine(to: ax.at(o, d, -(hw + 3)))
            ctx.stroke(f, with: .color(Metal.sleeve.f(0.55)), lineWidth: 1)
            d += 7
        }
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.boreBase - 3,
                                 to: g.deck, halfWidth: hw + 3),
                   with: .color(Metal.outline.color), lineWidth: 1)
        // head casting on top
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.deck,
                        to: g.deck + g.pistonLen * 0.85, halfWidth: hw + 4,
                        metal: .head, strips: 12)
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.deck,
                                 to: g.deck + g.pistonLen * 0.85,
                                 halfWidth: hw + 4),
                   with: .color(Metal.outline.color), lineWidth: 1)
        // dark bore interior, which everything below is seen against
        ctx.fill(BayPaint.band(origin: o, axis: ax, from: g.boreBase,
                               to: g.deck, halfWidth: hw),
                 with: .color(Metal.bore.color))
    }

    /// Piston, rod, valvetrain, combustion -- everything that moves.
    private func drawMoving(_ ctx: GraphicsContext, g: CylGeo,
                            slot s: EngineBay.Slot, bay b: EngineBay,
                            a: BayAnimator) {
        let ax = g.axis, o = g.crank, hw = g.halfBore
        let lights = model.cylinderLight
        let lit = s.index < lights.count ? lights[s.index] : 0

        // combustion, in the volume above the crown
        if lit > 0.02 {
            let crown = g.pinDist + g.pistonLen
            let mid = (crown + g.deck) * 0.5
            BayPaint.fire(ctx, at: ax.at(o, mid, 0), radius: hw * 0.85,
                          intensity: 0.25 + 0.75 * lit)
        }

        // valvetrain on the head: two poppets and their cam lobes
        let lift = b.valveLift(s.index, crankAngleDeg: a.crankDeg,
                               rpm: model.simRPM)
        drawValve(ctx, g: g, side: -1, lift: lift.intake, tint: .cyan)
        drawValve(ctx, g: g, side: 1, lift: lift.exhaust, tint: .orange)

        // piston: shaded barrel, ring lands, wrist pin
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.pinDist,
                        to: g.pinDist + g.pistonLen, halfWidth: hw - 1.5,
                        metal: .piston, strips: 12)
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.pinDist,
                                 to: g.pinDist + g.pistonLen,
                                 halfWidth: hw - 1.5),
                   with: .color(Metal.piston.f(0.5)), lineWidth: 1)
        for k in 0..<3 {
            let d = g.pinDist + g.pistonLen - 3 - CGFloat(k) * 3.5
            guard d > g.pinDist + 1 else { break }
            var r = Path()
            r.move(to: ax.at(o, d, hw - 2.5))
            r.addLine(to: ax.at(o, d, -(hw - 2.5)))
            ctx.stroke(r, with: .color(Metal.piston.f(0.42)), lineWidth: 1)
        }
        let pin = ax.at(o, g.pinDist, 0)
        BayPaint.rod(ctx, small: pin, big: g.crankPin,
                     width: max(g.crankRadius * 0.40, 2.2))
        BayPaint.dome(ctx, at: pin, radius: max(hw * 0.22, 2.4), metal: .journal)
    }

    private func drawValve(_ ctx: GraphicsContext, g: CylGeo, side: CGFloat,
                           lift: Double, tint: Color) {
        let ax = g.axis, o = g.crank, hw = g.halfBore
        let e = hw * 0.5 * side
        let seat = g.deck
        let open = CGFloat(lift) * g.pistonLen * 0.75
        let head = seat - open                       // valve head drops INTO the bore
        // stem up through the head casting
        var stem = Path()
        stem.move(to: ax.at(o, head, e))
        stem.addLine(to: ax.at(o, seat + g.pistonLen * 0.8, e))
        ctx.stroke(stem, with: .color(Metal.piston.f(0.78)), lineWidth: 2)
        // spring: a few coils, compressing as it opens
        let s0 = seat + g.pistonLen * 0.12, s1 = seat + g.pistonLen * 0.72
        let coils = 4
        var sp = Path()
        for k in 0...coils {
            let t = CGFloat(k) / CGFloat(coils)
            let d = s0 + (s1 - s0) * t
            let w = (k % 2 == 0 ? CGFloat(3.0) : -3.0)
            let p = ax.at(o, d, e + w)
            if k == 0 { sp.move(to: p) } else { sp.addLine(to: p) }
        }
        ctx.stroke(sp, with: .color(Metal.journal.f(1.25)), lineWidth: 1)
        // the valve head itself, tinted so intake and exhaust read apart
        var vh = Path()
        vh.move(to: ax.at(o, head, e - hw * 0.30))
        vh.addLine(to: ax.at(o, head, e + hw * 0.30))
        ctx.stroke(vh, with: .color(tint.opacity(0.35 + 0.65 * lift)),
                   lineWidth: 3)
    }

    /// One crankshaft line with counterweights, drawn under the rods.
    private func drawCrankshaft(_ ctx: GraphicsContext, geo: [CylGeo],
                                size: CGSize, crankY: CGFloat, margin: CGFloat) {
        for g in geo {
            // counterweight fan, opposite the rod journal
            let dx = g.crankPin.x - g.crank.x, dy = g.crankPin.y - g.crank.y
            let opp = atan2(-dy, -dx)
            let cwr = g.crankRadius * 1.45
            var fan = Path()
            fan.move(to: g.crank)
            for t in -6...6 {
                let aa = opp + Double(t) * 11.5 * .pi / 180.0
                fan.addLine(to: CGPoint(x: g.crank.x + cwr * CGFloat(cos(aa)),
                                        y: g.crank.y + cwr * CGFloat(sin(aa))))
            }
            fan.closeSubpath()
            ctx.fill(fan, with: .color(Metal.brass.f(0.72)))
            ctx.stroke(fan, with: .color(Metal.brass.f(0.35)), lineWidth: 1)
            // lit inner face
            var inner = Path()
            inner.move(to: CGPoint(x: g.crank.x - 1.5, y: g.crank.y - 2))
            for t in -6...6 {
                let aa = opp + Double(t) * 11.5 * .pi / 180.0
                inner.addLine(to: CGPoint(
                    x: g.crank.x + cwr * 0.76 * CGFloat(cos(aa)) - 1.5,
                    y: g.crank.y + cwr * 0.76 * CGFloat(sin(aa)) - 2))
            }
            inner.closeSubpath()
            ctx.fill(inner, with: .color(Metal.brass.f(1.25)))
            BayPaint.dome(ctx, at: g.crank, radius: g.crankRadius * 0.62,
                          metal: .journal, specular: true)
            BayPaint.dome(ctx, at: g.crankPin, radius: g.crankRadius * 0.34,
                          metal: .journal)
        }
    }

    // MARK: - headers

    // Each bank runs back to its own collector and the pulses crawl down the
    // primaries at the gas's own speed of sound -- so on a 4-into-1 you watch
    // them arrive staggered by the firing interval, which is the whole reason
    // a header is a shape and not just a pipe.
    private func drawHeaders(_ ctx: GraphicsContext, size: CGSize,
                             a: BayAnimator, geo: [CylGeo],
                             pipeIndices: [Int]) {
        let b = a.bay
        let collY = size.height - 12
        let nBank = max(b.bankAngles.count, 1)
        let tip = CGPoint(x: size.width - 6, y: collY)
        let rad: CGFloat = 3.4

        func collector(_ bank: Int) -> CGPoint {
            let f: CGFloat = nBank == 1 ? 0.80 : (bank == 0 ? 0.17 : 0.80)
            return CGPoint(x: size.width * f, y: collY)
        }
        func control(_ p: CGPoint, _ c: CGPoint) -> CGPoint {
            CGPoint(x: (p.x + c.x) * 0.5, y: p.y + (c.y - p.y) * 0.18)
        }
        func curve(_ p: CGPoint, _ c: CGPoint, _ n: Int) -> [CGPoint] {
            (0...n).map { bezier(p, control(p, c), c, CGFloat($0) / CGFloat(n)) }
        }

        for i in pipeIndices where i < geo.count && i < b.slots.count {
            let coll = collector(b.slots[i].bank)
            BayPaint.tube(ctx, points: curve(geo[i].port, coll, 12),
                          radius: rad, metal: .pipe)
        }

        for pulse in a.pulses.pulses {
            guard pulse.cylinder < geo.count else { continue }
            let port = geo[pulse.cylinder].port
            let coll = collector(pulse.bank)
            var pt: CGPoint
            if pulse.primary < 1.0 {
                pt = bezier(port, control(port, coll), coll, CGFloat(pulse.primary))
            } else {
                let t = CGFloat(max(pulse.tail, 0.0))
                pt = CGPoint(x: coll.x + (tip.x - coll.x) * t, y: collY)
            }
            BayPaint.fire(ctx, at: pt, radius: rad * 1.5,
                          intensity: 0.3 + 0.7 * pulse.strength)
        }

        for bk in 0..<nBank {
            BayPaint.dome(ctx, at: collector(bk), radius: rad * 1.5, metal: .pipe)
        }
        BayPaint.tube(ctx, points: [collector(nBank - 1), tip],
                      radius: rad * 1.3, metal: .pipe)
        if a.pulses.exitFlash > 0.02 {
            BayPaint.fire(ctx, at: tip, radius: rad * 3.0,
                          intensity: a.pulses.exitFlash)
        }
    }

    private func bezier(_ p0: CGPoint, _ c: CGPoint, _ p1: CGPoint,
                        _ t: CGFloat) -> CGPoint {
        let u = 1 - t
        return CGPoint(x: u * u * p0.x + 2 * u * t * c.x + t * t * p1.x,
                       y: u * u * p0.y + 2 * u * t * c.y + t * t * p1.y)
    }

    // MARK: - induction

    // A turbo is driven by the exhaust and hangs on after a lift; a blower is
    // belted to the crank and cannot.  The wheel spins from whichever of those
    // two the preset actually has.
    private func drawCharger(_ ctx: GraphicsContext, size: CGSize,
                             a: BayAnimator) {
        let b = a.bay
        let spin = b.chargerSpin(rpm: model.simRPM, boostBar: model.boostBar)
        let c = CGPoint(x: size.width * 0.10, y: size.height * 0.17)
        let r: CGFloat = 23

        // volute: a fat shaded ring, not an outline
        BayPaint.dome(ctx, at: c, radius: r, metal: .head)
        ctx.fill(Path(ellipseIn: CGRect(x: c.x - r * 0.74, y: c.y - r * 0.74,
                                        width: r * 1.48, height: r * 1.48)),
                 with: .color(Metal.bore.f(1.1)))

        let blades = b.charger == .roots ? 3 : 10
        let phase = a.crankDeg * 0.0175 * (0.4 + 3.0 * spin)
        for k in 0..<blades {
            let ang = phase + Double(k) * 2.0 * Double.pi / Double(blades)
            let tipP = CGPoint(x: c.x + CGFloat(cos(ang)) * r * 0.70,
                               y: c.y + CGFloat(sin(ang)) * r * 0.70)
            let ax = Axis(from: c, to: tipP)
            BayPaint.shaded(ctx, origin: c, axis: ax, from: r * 0.16,
                            to: r * 0.70, halfWidth: 1.9,
                            metal: .piston, strips: 5)
        }
        BayPaint.dome(ctx, at: c, radius: r * 0.20, metal: .journal,
                      specular: true)
        let name = b.charger == .twinTurbo ? "turbo ×2" : b.charger.rawValue
        ctx.draw(Text(name).font(.system(size: 9)).foregroundColor(.secondary),
                 at: CGPoint(x: c.x, y: c.y + r + 10))
    }

    private func drawBoost(_ ctx: GraphicsContext, size: CGSize, a: BayAnimator) {
        let maxB = max(a.bay.engine.boostBar, 0.35)
        let f = CGFloat(min(max(model.boostBar / maxB, 0.0), 1.1))
        let w = size.width * 0.30
        let x = size.width - w - 12, y: CGFloat = 12
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w, height: 7),
                      cornerRadius: 3.5), with: .color(.white.opacity(0.12)))
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w * f, height: 7),
                      cornerRadius: 3.5),
                 with: .color(f > 0.98 ? .red : .green))
        let bar = (model.boostBar * 100).rounded() / 100
        ctx.draw(Text("\(bar) bar")
            .font(.system(size: 9, design: .monospaced))
            .foregroundColor(.secondary),
                 at: CGPoint(x: x + w * 0.5, y: y + 17))
    }

    // MARK: - rotary

    /// A Wankel drawn as a Wankel: epitrochoid housing, a REULEAUX rotor
    /// orbiting inside it at a third of shaft speed, and the three chambers
    /// coloured by the stroke each one is actually on.
    ///
    /// The apexes are not placed against the housing by hand -- they land on it
    /// because the geometry says they must, which is the whole trick of the
    /// thing and worth being able to see.
    private func renderRotary(_ ctx: GraphicsContext, size: CGSize,
                              a: BayAnimator, geo r: RotaryGeometry) {
        let b = a.bay
        let n = r.rotors
        let cellW = size.width / CGFloat(n)
        let span = CGFloat(r.radius + r.eccentricity)
        let scale = min(cellW * 0.40, size.height * 0.34) / span
        let lights = model.cylinderLight
        var ports: [CylGeo] = []

        for rotor in 0..<n {
            let o = CGPoint(x: cellW * (CGFloat(rotor) + 0.5),
                            y: size.height * 0.40)
            func P(_ x: Double, _ y: Double) -> CGPoint {
                CGPoint(x: o.x + CGFloat(x) * scale, y: o.y - CGFloat(y) * scale)
            }
            func housingPt(_ phi: Double) -> CGPoint {
                let h = r.housing(phi)
                return P(h.x, h.y)
            }

            var shell = Path()
            for i in 0...144 {
                let p = housingPt(Double(i) * 2.5)
                if i == 0 { shell.move(to: p) } else { shell.addLine(to: p) }
            }
            shell.closeSubpath()
            // housing casting: lit from the upper left like everything else
            ctx.fill(shell, with: .linearGradient(
                Gradient(colors: [Metal.head.f(1.5), Metal.head.f(0.5)]),
                startPoint: CGPoint(x: o.x - 60, y: o.y - 60),
                endPoint: CGPoint(x: o.x + 60, y: o.y + 60)))

            let beta = r.rotorAngleDeg(a.crankDeg, rotor: rotor)
            let apexPt: (Int) -> CGPoint = { kk in
                let p = r.apex(a.crankDeg, rotor: rotor, kk)
                return P(p.x, p.y)
            }

            // chambers: housing arc between two apexes, closed by the rotor flank
            for kk in 0..<3 {
                let phi0 = beta + Double(kk) * 120.0
                var ch = Path()
                ch.move(to: housingPt(phi0))
                var t = 5.0
                while t <= 120.0 {
                    ch.addLine(to: housingPt(phi0 + t))
                    t += 5.0
                }
                // back along the true Reuleaux flank: an arc centred on the
                // opposite apex, not a bulged straight line
                let va = apexPt((kk + 1) % 3), vb = apexPt(kk)
                let vc = apexPt((kk + 2) % 3)
                appendArc(&ch, from: va, to: vb, centre: vc)
                ch.closeSubpath()

                let phase = r.chamberStroke(a.crankDeg, rotor: rotor, kk)
                let fill = r.chamberFill(a.crankDeg, rotor: rotor, kk)
                if phase == .power {
                    var glow = 1.0 - fill
                    for i in stride(from: rotor, to: lights.count, by: n) {
                        glow = max(glow, lights[i])
                    }
                    ctx.fill(ch, with: .color(Color(red: 1.0, green: 0.45,
                                                    blue: 0.08)
                        .opacity(0.20 + 0.70 * glow)))
                } else {
                    ctx.fill(ch, with: .color(strokeTint(phase).opacity(0.20)))
                }
            }

            // the rotor itself
            let verts = [apexPt(0), apexPt(1), apexPt(2)]
            let rotorPath = BayPaint.reuleaux(verts)
            ctx.fill(rotorPath, with: .linearGradient(
                Gradient(colors: [Metal.piston.f(0.95), Metal.piston.f(0.42)]),
                startPoint: CGPoint(x: o.x - 40, y: o.y - 40),
                endPoint: CGPoint(x: o.x + 40, y: o.y + 40)))
            ctx.stroke(rotorPath, with: .color(Metal.outline.color), lineWidth: 1.4)

            // apex seals
            for kk in 0..<3 {
                BayPaint.dome(ctx, at: apexPt(kk), radius: 3.2, metal: .brass)
            }

            // eccentric shaft: the throw, then the journals
            let rc = r.rotorCentre(a.crankDeg, rotor: rotor)
            let rcPt = P(rc.x, rc.y)
            let ax = Axis(from: o, to: rcPt)
            BayPaint.shaded(ctx, origin: o, axis: ax, from: 0,
                            to: max(((rcPt.x - o.x) * (rcPt.x - o.x)
                                     + (rcPt.y - o.y) * (rcPt.y - o.y)).squareRoot(),
                                    1),
                            halfWidth: 3.0, metal: .brass, strips: 6)
            BayPaint.dome(ctx, at: rcPt, radius: 5, metal: .journal)
            BayPaint.dome(ctx, at: o, radius: 4, metal: .journal, specular: true)

            ctx.stroke(shell, with: .color(Metal.outline.color), lineWidth: 2)

            // ports and plug, at the angles the plug position dictates
            portMark(ctx, at: housingPt(r.intakePortDeg), tint: .cyan)
            let exPt = housingPt(r.exhaustPortDeg)
            portMark(ctx, at: exPt, tint: .orange)
            BayPaint.dome(ctx, at: housingPt(r.plugDeg), radius: 3.4,
                          metal: Metal(r: 0.95, g: 0.78, b: 0.25))

            var g = CylGeo()
            g.port = exPt
            g.crank = o
            for i in stride(from: rotor, to: b.cylinderCount, by: n) {
                while ports.count <= i { ports.append(CylGeo()) }
                ports[i] = g
            }
        }

        drawHeaders(ctx, size: size, a: a, geo: ports,
                    pipeIndices: Array(0..<min(n, ports.count)))
        if b.charger != .na { drawCharger(ctx, size: size, a: a) }
        drawBoost(ctx, size: size, a: a)
    }

    /// Append the circular arc from `from` to `to` centred on `centre` -- the
    /// real Reuleaux flank.
    private func appendArc(_ p: inout Path, from: CGPoint, to: CGPoint,
                           centre c: CGPoint, samples: Int = 12) {
        let rad = ((from.x - c.x) * (from.x - c.x)
                   + (from.y - c.y) * (from.y - c.y)).squareRoot()
        let a0 = atan2(from.y - c.y, from.x - c.x)
        let a1 = atan2(to.y - c.y, to.x - c.x)
        var d = a1 - a0
        while d > .pi { d -= 2 * .pi }
        while d < -.pi { d += 2 * .pi }
        for k in 1...samples {
            let a = a0 + d * Double(k) / Double(samples)
            p.addLine(to: CGPoint(x: c.x + rad * CGFloat(cos(a)),
                                  y: c.y + rad * CGFloat(sin(a))))
        }
    }

    private func portMark(_ ctx: GraphicsContext, at p: CGPoint, tint: Color) {
        let r: CGFloat = 4.5
        ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r,
                                        width: r * 2, height: r * 2)),
                 with: .color(tint.opacity(0.85)))
        ctx.stroke(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r,
                                          width: r * 2, height: r * 2)),
                   with: .color(Metal.outline.color), lineWidth: 1)
    }

    private func strokeTint(_ s: Stroke) -> Color {
        switch s {
        case .intake: return .cyan
        case .compression: return .yellow
        case .power: return .orange
        case .exhaust: return .gray
        }
    }
}
