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
    var port = CGPoint.zero          // exhaust, outboard side
    var intakePort = CGPoint.zero    // inboard side
    var headTop: CGFloat = 0         // top of the head casting
    var side: CGFloat = 1            // which way its plumbing leaves
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

    /// Three layouts, because three engines want the space used differently.
    ///
    ///  * inline -- one upright row, crank across the bottom.  The long axis of
    ///    the frame is the long axis of the engine.
    ///  * V and flat -- the crank runs DOWN the middle and the banks fan out
    ///    left and right.  Laying a V out sideways like an inline row and then
    ///    tilting the banks throws the outer cylinders off the frame and leaves
    ///    the middle empty; going vertical spends the tall axis on stations and
    ///    the wide axis on the fan, which is what the PC build does.
    ///  * W -- not one wide vee but TWO narrow-angle VR units side by side,
    ///    each with its own crank, which is what a W actually is.
    private func renderPistons(_ ctx: GraphicsContext, size: CGSize,
                               a: BayAnimator) {
        let top: CGFloat = 18
        let bottom = size.height - 16
        switch a.bay.layout {
        case .w:
            renderW(ctx, size: size, a: a, top: top, bottom: bottom)
        case .vee, .flat:
            renderVee(ctx, size: size, a: a, top: top, bottom: bottom)
        default:
            renderInline(ctx, size: size, a: a, top: top, bottom: bottom)
        }
    }

    /// Per-cylinder size from displacement, ~500 cc as the reference, cube-root
    /// so an eight-times-bigger cylinder is twice the size each way.
    private func cylScale(_ b: EngineBay) -> CGFloat {
        let total = b.engine.cylinders.reduce(0.0) { $0 + $1.displacement }
        let perCC = total * 1.0e6 / Double(max(b.cylinderCount, 1))
        return CGFloat(min(max(pow(perCC / 500.0, 1.0 / 3.0), 0.55), 1.30))
    }

    /// Build one cylinder's drawing geometry for a crank centre and bank angle.
    ///
    /// `deckLen` is how far the head sits from the crank centre; the stroke is
    /// solved back out of it so the REAL slider-crank still places the piston
    /// and the rod never has to be stretched to reach.
    private func geoAt(_ s: EngineBay.Slot, bay b: EngineBay, crank: Double,
                       at c: CGPoint, bankDeg: Double, deckLen: CGFloat,
                       halfBore: CGFloat) -> CylGeo {
        var g = CylGeo()
        g.axis = Axis(angleDeg: bankDeg)
        g.crank = c
        g.halfBore = halfBore
        g.side = bankDeg < 0 ? -1 : 1

        let ratio = CGFloat(max(s.rodLength / max(s.stroke / 2, 1e-6), 1.2))
        // deck = r + rod + pistonLen + clearance, all multiples of the stroke
        let strokePx = deckLen / (0.96 + 0.5 * ratio)
        let r = strokePx / 2
        g.crankRadius = r
        g.pistonLen = strokePx * 0.34
        let rodPx = r * ratio

        let frac = CGFloat(b.pistonFraction(s.index, crankAngleDeg: crank))
        let th = b.crankPinDeg(s.index, crankAngleDeg: crank) * Double.pi / 180.0
        let ct = CGFloat(cos(th)), st = CGFloat(sin(th))
        g.pinDist = (r + rodPx) - frac * strokePx
        g.crankPin = CGPoint(x: c.x + (g.axis.ux * ct + g.axis.qx * st) * r,
                             y: c.y + (g.axis.uy * ct + g.axis.qy * st) * r)
        g.boreBase = r * 1.45
        g.deck = (r + rodPx) + g.pistonLen + strokePx * 0.12
        g.headTop = g.deck + g.pistonLen * 0.85
        g.port = g.axis.at(c, g.deck + g.pistonLen * 0.4, halfBore * 1.15 * g.side)
        g.intakePort = g.axis.at(c, g.deck + g.pistonLen * 0.4,
                                 -halfBore * 1.15 * g.side)
        return g
    }

    // ---------------------------------------------------------------- inline
    private func renderInline(_ ctx: GraphicsContext, size: CGSize,
                              a: BayAnimator, top: CGFloat, bottom: CGFloat) {
        let b = a.bay
        let n = CGFloat(max(b.cylinderCount, 1))
        let sc = cylScale(b)
        let margin: CGFloat = 14
        // leave a lane down the right for the exhaust rail
        let usable = size.width - margin * 2 - 34
        var pitch = usable / n
        var halfBore = min(pitch * 0.34, 18 * sc)
        pitch = min(pitch, halfBore * 2.8)
        let x0 = margin + 6 + pitch * 0.5

        let crankY = bottom - 26
        var deckLen = min(62 * sc, (crankY - top) * 0.70)
        deckLen = max(deckLen, 26)
        halfBore = min(halfBore, deckLen * 0.28)

        var geo: [CylGeo] = []
        for s in b.slots {
            geo.append(geoAt(s, bay: b, crank: a.crankDeg,
                             at: CGPoint(x: x0 + pitch * CGFloat(s.station),
                                         y: crankY),
                             bankDeg: 0, deckLen: deckLen, halfBore: halfBore))
        }
        drawCrankcase(ctx, from: CGPoint(x: margin * 0.5, y: crankY),
                      to: CGPoint(x: size.width - margin * 0.5, y: crankY),
                      thickness: halfBore * 1.4)
        manifolds(ctx, size: size, a: a, geo: geo, vertical: false)
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b, a: a)
        }
        drawCrankshaft(ctx, geo: geo)
        finish(ctx, size: size, a: a,
               crankFrom: CGPoint(x: margin * 0.5, y: crankY),
               crankTo: CGPoint(x: size.width - margin * 0.5, y: crankY),
               thickness: halfBore * 1.4, vertical: false)
    }

    // ------------------------------------------------------------------ vee
    private func renderVee(_ ctx: GraphicsContext, size: CGSize,
                           a: BayAnimator, top: CGFloat, bottom: CGFloat) {
        let b = a.bay
        let sc = cylScale(b)
        let ns = CGFloat(max(b.stationsPerBank, 1))
        let maxAng = b.slots.map { abs($0.bankAngleDeg) }.max() ?? 0
        let bankDeg = min(maxAng, 82.0)
        let bank = bankDeg * Double.pi / 180.0

        var halfBore = 18 * sc
        // Longer barrels than the first cut: a V looked stubby because the
        // station pitch was eating the height.  Packing the stations tighter
        // (0.62 rather than 0.78 of the vertical reach) buys the length back --
        // real V banks overlap when you look at them end-on anyway.
        var deckLen = 80 * sc
        // the fan-out has to stay inside the frame, with a lane for the rails
        let reach = deckLen * CGFloat(sin(bank)) + halfBore
        let maxReach = size.width * 0.5 - 30
        if reach > maxReach {
            let f = maxReach / reach
            halfBore *= f; deckLen *= f
        }
        var dy = max(halfBore * 1.5, deckLen * CGFloat(cos(bank)) * 0.62)
        var reachUp = deckLen * CGFloat(cos(bank))
        var blockH = dy * (ns - 1) + reachUp + 18
        let avail = bottom - top
        if blockH > avail {
            let f = avail / blockH
            dy *= f; halfBore *= f; deckLen *= f; reachUp *= f
            blockH = dy * (ns - 1) + reachUp + 18
        }
        let mtop = (top + bottom) * 0.5 - blockH * 0.5 - dy * 0.5 + reachUp
        let cx = size.width * 0.5

        var geo: [CylGeo] = []
        for s in b.slots {
            let jy = mtop + dy * (CGFloat(s.station) + 0.5)
            let side: Double = s.bankAngleDeg < 0 ? -1 : 1
            geo.append(geoAt(s, bay: b, crank: a.crankDeg,
                             at: CGPoint(x: cx, y: jy),
                             bankDeg: side * bankDeg, deckLen: deckLen,
                             halfBore: halfBore))
        }
        let cy0 = mtop + dy * 0.5 - 14, cy1 = mtop + dy * (ns - 0.5) + 14
        drawCrankcase(ctx, from: CGPoint(x: cx, y: cy0),
                      to: CGPoint(x: cx, y: cy1), thickness: halfBore * 1.05)
        manifolds(ctx, size: size, a: a, geo: geo, vertical: true)
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b, a: a)
        }
        drawCrankshaft(ctx, geo: geo)
        finish(ctx, size: size, a: a, crankFrom: CGPoint(x: cx, y: cy0),
               crankTo: CGPoint(x: cx, y: cy1),
               thickness: halfBore * 1.05, vertical: true)
    }

    // -------------------------------------------------------------------- W
    /// Two narrow-angle VR units side by side, each with its own crank -- which
    /// is what a W16 is, rather than one enormous vee.
    private func renderW(_ ctx: GraphicsContext, size: CGSize,
                         a: BayAnimator, top: CGFloat, bottom: CGFloat) {
        let b = a.bay
        let left = b.slots.filter { $0.bankAngleDeg < 0 }
        let right = b.slots.filter { $0.bankAngleDeg >= 0 }
        var geo = [CylGeo](repeating: CylGeo(), count: b.cylinderCount)
        var frontAnchor = CGPoint(x: size.width * 0.5, y: top)
        var frontR: CGFloat = 8
        let tiltDeg = 30.0
        let ct = CGFloat(cos(tiltDeg * .pi / 180))

        for (ui, grp) in [left, right].enumerated() {
            guard !grp.isEmpty else { continue }
            let ux = size.width * (ui == 0 ? 0.29 : 0.71)
            // split the unit into its two sub-banks by their own mean angle
            let mid = grp.reduce(0.0) { $0 + $1.bankAngleDeg } / Double(grp.count)
            let subA = grp.filter { $0.bankAngleDeg < mid }
            let subB = grp.filter { $0.bankAngleDeg >= mid }
            let nsu = CGFloat(max(max(subA.count, subB.count), 1))

            var halfBore = min(size.width * 0.055, 14)
            var deckLen: CGFloat = 66
            var dy = max(halfBore * 1.7, deckLen * ct * 0.50)
            var reachUp = deckLen * ct
            var blockH = dy * (nsu - 1) + reachUp + 14
            let avail = bottom - top
            if blockH > avail {
                let f = avail / blockH
                dy *= f; halfBore *= f; deckLen *= f; reachUp *= f
                blockH = dy * (nsu - 1) + reachUp + 14
            }
            let uTop = (top + bottom) * 0.5 - blockH * 0.5 - dy * 0.5 + reachUp
            let cy0 = uTop + dy * 0.5 - 10, cy1 = uTop + dy * (nsu - 0.5) + 10
            drawCrankcase(ctx, from: CGPoint(x: ux, y: cy0),
                          to: CGPoint(x: ux, y: cy1), thickness: halfBore * 1.05)
            // each VR unit gets its own sump, because each has its own crank
            drawSump(ctx, from: CGPoint(x: ux, y: cy0),
                     to: CGPoint(x: ux, y: cy1),
                     thickness: halfBore * 1.05, vertical: true)
            frontAnchor = CGPoint(x: ux, y: cy0)
            frontR = max(halfBore * 0.8, 6)

            for (sub, sgn) in [(subA, -1.0), (subB, 1.0)] {
                for (k, s) in sub.enumerated() {
                    let jy = uTop + dy * (CGFloat(k) + 0.5)
                    var g = geoAt(s, bay: b, crank: a.crankDeg,
                                  at: CGPoint(x: ux, y: jy),
                                  bankDeg: sgn * tiltDeg,
                                  deckLen: deckLen, halfBore: halfBore)
                    // plumbing leaves each VR unit on its OWN outboard side,
                    // not on the side its narrow sub-vee happens to lean
                    g.side = ui == 0 ? -1 : 1
                    g.port = g.axis.at(g.crank, g.deck + g.pistonLen * 0.4,
                                       halfBore * 1.15 * CGFloat(sgn))
                    geo[s.index] = g
                }
            }
        }
        manifolds(ctx, size: size, a: a, geo: geo, vertical: true)
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b, a: a)
        }
        drawCrankshaft(ctx, geo: geo)
        // the sumps are drawn per unit above, so this pass only wants the
        // shared furniture -- hand it a zero-length crank line
        finish(ctx, size: size, a: a, crankFrom: frontAnchor,
               crankTo: frontAnchor, thickness: frontR * 1.25, vertical: true)
    }

    /// The furniture: sump, radiator, front drive, charger, badges.  Called by
    /// every layout with that layout's crank line, so the sump hangs off the
    /// crank and the gears sit at the front of it wherever the crank happens
    /// to point.
    private func finish(_ ctx: GraphicsContext, size: CGSize, a: BayAnimator,
                        crankFrom: CGPoint, crankTo: CGPoint,
                        thickness: CGFloat, vertical: Bool) {
        drawSump(ctx, from: crankFrom, to: crankTo, thickness: thickness,
                 vertical: vertical)
        // front of the engine: the top of a vertical crank, the left of a
        // horizontal one
        let front = vertical
            ? CGPoint(x: crankFrom.x, y: min(crankFrom.y, crankTo.y))
            : CGPoint(x: min(crankFrom.x, crankTo.x), y: crankFrom.y)
        let cams = a.bay.engine.valvesPerCyl >= 4 ? 2 : 1
        drawFrontDrive(ctx, at: front, radius: max(thickness * 0.8, 6),
                       crankDeg: a.crankDeg, cams: cams)
        drawRadiator(ctx, rect: CGRect(x: 4, y: 30, width: 11,
                                       height: max(size.height - 78, 20)),
                     warmth: 0.65)
        if a.bay.charger != .na { drawCharger(ctx, size: size, a: a) }
        drawBoost(ctx, size: size, a: a)
        drawPlaneBadge(ctx, at: CGPoint(x: 21, y: 6), bay: a.bay)
    }

    /// The crankcase, as a shaded bar along the crank axis.
    private func drawCrankcase(_ ctx: GraphicsContext, from p0: CGPoint,
                               to p1: CGPoint, thickness t: CGFloat) {
        let ax = Axis(from: p0, to: p1)
        let len = ((p1.x - p0.x) * (p1.x - p0.x)
                   + (p1.y - p0.y) * (p1.y - p0.y)).squareRoot()
        BayPaint.shaded(ctx, origin: p0, axis: ax, from: 0, to: len,
                        halfWidth: t, metal: .block, strips: 9)
        ctx.stroke(BayPaint.band(origin: p0, axis: ax, from: 0, to: len,
                                 halfWidth: t),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
    }

    // MARK: - plumbing

    /// Green intake and red exhaust, routed in straight runs through the space
    /// the engine is NOT using.
    ///
    /// The exhaust leaves each head outboard, goes to a rail clear of the
    /// cylinders and down that rail to a collector; the intake comes off a
    /// plenum in the valley (or above the row) with a short runner per head.
    /// Everything is orthogonal, so the pipes read as bent tube rather than as
    /// strings thrown over the engine.
    private func manifolds(_ ctx: GraphicsContext, size: CGSize, a: BayAnimator,
                           geo: [CylGeo], vertical: Bool) {
        let b = a.bay
        guard !geo.isEmpty else { return }
        let rad = max(min(size.width, size.height) * 0.011, 2.6)
        let collY = size.height - 9
        let leftRail = max(geo.map { $0.port.x }.min() ?? 0, 0) - 14
        let rightRail = min(geo.map { $0.port.x }.max() ?? size.width,
                            size.width) + 14
        let lx = max(leftRail, 9), rx = min(rightRail, size.width - 9)

        // ---- intake ------------------------------------------------------
        let plenum: [CGPoint]
        if vertical {
            // just off the centreline: the timing gears sit on it
            let cx = size.width * 0.5 - rad * 2.2
            let ys = geo.map { $0.intakePort.y }
            plenum = [CGPoint(x: cx, y: (ys.min() ?? 0) - 6),
                      CGPoint(x: cx, y: (ys.max() ?? 0) + 6)]
        } else {
            let topY = (geo.map { $0.intakePort.y }.min() ?? 0) - 16
            plenum = [CGPoint(x: (geo.map { $0.intakePort.x }.min() ?? 0) - 6,
                              y: topY),
                      CGPoint(x: (geo.map { $0.intakePort.x }.max() ?? 0) + 6,
                              y: topY)]
        }
        for g in geo where g.halfBore > 0 {
            let joint = vertical
                ? CGPoint(x: plenum[0].x, y: g.intakePort.y)
                : CGPoint(x: g.intakePort.x, y: plenum[0].y)
            BayPaint.orthoPipe(ctx, points: [joint, g.intakePort],
                               radius: rad * 0.78, metal: .intakePipe)
        }
        BayPaint.orthoPipe(ctx, points: plenum, radius: rad, metal: .intakePipe)

        // ---- exhaust -----------------------------------------------------
        var routes: [[CGPoint]] = []
        for g in geo {
            guard g.halfBore > 0 else { routes.append([]); continue }
            let rail = g.side < 0 ? lx : rx
            if vertical {
                routes.append([g.port, CGPoint(x: rail, y: g.port.y),
                               CGPoint(x: rail, y: collY)])
            } else {
                // up and OVER: a band above every head, so the run to the rail
                // never crosses a cylinder
                let band = (geo.map { $0.port.y }.min() ?? 0) - 12
                routes.append([g.port, CGPoint(x: g.port.x, y: band),
                               CGPoint(x: rail, y: band),
                               CGPoint(x: rail, y: collY)])
            }
        }
        for r in routes where r.count > 1 {
            BayPaint.orthoPipe(ctx, points: r, radius: rad, metal: .exhaustPipe)
        }

        // collector along the bottom, then the tailpipe
        let collectors = Set(routes.compactMap { $0.last.map { Int($0.x) } })
        let tip = CGPoint(x: size.width - 7, y: collY)
        for cxi in collectors.sorted() {
            BayPaint.orthoPipe(ctx, points: [CGPoint(x: CGFloat(cxi), y: collY), tip],
                               radius: rad * 1.15, metal: .exhaustPipe)
        }

        // ---- the pulses, riding their own route --------------------------
        for p in a.pulses.pulses {
            guard p.cylinder < routes.count, routes[p.cylinder].count > 1 else { continue }
            let pt: CGPoint
            if p.primary < 1.0 {
                pt = along(routes[p.cylinder], CGFloat(p.primary))
            } else {
                let from = routes[p.cylinder][routes[p.cylinder].count - 1]
                let t = CGFloat(max(p.tail, 0))
                pt = CGPoint(x: from.x + (tip.x - from.x) * t, y: collY)
            }
            BayPaint.fire(ctx, at: pt, radius: rad * 1.6,
                          intensity: 0.3 + 0.7 * p.strength)
        }
        if a.pulses.exitFlash > 0.02 {
            BayPaint.fire(ctx, at: tip, radius: rad * 3.2,
                          intensity: a.pulses.exitFlash)
        }
        _ = b
    }

    /// A point a fraction of the way along a polyline, by arc length.
    private func along(_ pts: [CGPoint], _ t: CGFloat) -> CGPoint {
        guard pts.count > 1 else { return pts.first ?? .zero }
        var segs: [CGFloat] = []
        var total: CGFloat = 0
        for i in 0..<(pts.count - 1) {
            let dx = pts[i + 1].x - pts[i].x, dy = pts[i + 1].y - pts[i].y
            let l = (dx * dx + dy * dy).squareRoot()
            segs.append(l); total += l
        }
        guard total > 0 else { return pts[0] }
        var want = min(max(t, 0), 1) * total
        for (i, l) in segs.enumerated() {
            if want <= l || i == segs.count - 1 {
                let f = l > 0 ? want / l : 0
                return CGPoint(x: pts[i].x + (pts[i + 1].x - pts[i].x) * f,
                               y: pts[i].y + (pts[i + 1].y - pts[i].y) * f)
            }
            want -= l
        }
        return pts[pts.count - 1]
    }


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
            ctx.stroke(f, with: .color(BayMetal.sleeve.f(0.55)), lineWidth: 1)
            d += 7
        }
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.boreBase - 3,
                                 to: g.deck, halfWidth: hw + 3),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
        // head casting on top
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.deck,
                        to: g.deck + g.pistonLen * 0.85, halfWidth: hw + 4,
                        metal: .head, strips: 12)
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.deck,
                                 to: g.deck + g.pistonLen * 0.85,
                                 halfWidth: hw + 4),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
        // dark bore interior, which everything below is seen against
        ctx.fill(BayPaint.band(origin: o, axis: ax, from: g.boreBase,
                               to: g.deck, halfWidth: hw),
                 with: .color(BayMetal.bore.color))
    }

    /// Piston, rod, valvetrain, combustion -- everything that moves.
    private func drawMoving(_ ctx: GraphicsContext, g: CylGeo,
                            slot s: EngineBay.Slot, bay b: EngineBay,
                            a: BayAnimator) {
        let ax = g.axis, o = g.crank, hw = g.halfBore
        let lit = b.combustion(s.index, crankAngleDeg: a.crankDeg)

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
                   with: .color(BayMetal.piston.f(0.5)), lineWidth: 1)
        for k in 0..<3 {
            let d = g.pinDist + g.pistonLen - 3 - CGFloat(k) * 3.5
            guard d > g.pinDist + 1 else { break }
            var r = Path()
            r.move(to: ax.at(o, d, hw - 2.5))
            r.addLine(to: ax.at(o, d, -(hw - 2.5)))
            ctx.stroke(r, with: .color(BayMetal.piston.f(0.42)), lineWidth: 1)
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
        ctx.stroke(stem, with: .color(BayMetal.piston.f(0.78)), lineWidth: 2)
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
        ctx.stroke(sp, with: .color(BayMetal.journal.f(1.25)), lineWidth: 1)
        // the valve head itself, tinted so intake and exhaust read apart
        var vh = Path()
        vh.move(to: ax.at(o, head, e - hw * 0.30))
        vh.addLine(to: ax.at(o, head, e + hw * 0.30))
        ctx.stroke(vh, with: .color(tint.opacity(0.35 + 0.65 * lift)),
                   lineWidth: 3)
    }

    /// One crankshaft line with counterweights, drawn under the rods.
    private func drawCrankshaft(_ ctx: GraphicsContext, geo: [CylGeo]) {
        for g in geo where g.halfBore > 0 {
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
            ctx.fill(fan, with: .color(BayMetal.brass.f(0.72)))
            ctx.stroke(fan, with: .color(BayMetal.brass.f(0.35)), lineWidth: 1)
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
            ctx.fill(inner, with: .color(BayMetal.brass.f(1.25)))
            BayPaint.dome(ctx, at: g.crank, radius: g.crankRadius * 0.62,
                          metal: .journal, specular: true)
            BayPaint.dome(ctx, at: g.crankPin, radius: g.crankRadius * 0.34,
                          metal: .journal)
        }
    }

    // MARK: - induction

    // A turbo is driven by the exhaust and hangs on after a lift; a blower is
    // belted to the crank and cannot.  The wheel spins from whichever of those
    // two the preset actually has.
    private func drawCharger(_ ctx: GraphicsContext, size: CGSize,
                             a: BayAnimator) {
        let b = a.bay
        let spin = b.chargerSpin(rpm: model.simRPM, boostBar: model.boostBar)
        let heat = min(max(model.boostBar / max(b.engine.boostBar, 0.30),
                           0.0), 1.0)
        let r = min(size.height * 0.085, 21)
        let twin = b.charger == .twinTurbo
        let xs = size.width * 0.085
        let centres: [CGPoint] = twin
            ? [CGPoint(x: xs, y: size.height * 0.22),
               CGPoint(x: xs, y: size.height * 0.55)]
            : [CGPoint(x: xs, y: size.height * 0.26)]
        for c in centres {
            drawOneCharger(ctx, at: c, radius: r, spin: spin, heat: heat, a: a)
        }
        let name = twin ? "turbo ×2" : b.charger.rawValue
        if let last = centres.last {
            ctx.draw(Text(name).font(.system(size: 9))
                .foregroundColor(.secondary),
                     at: CGPoint(x: last.x, y: last.y + r + 11))
        }
    }

    /// One turbo or blower, with the FX that were missing: it runs RED HOT on
    /// boost, the wheel blurs as it spools, and it breathes hot gas in on the
    /// exhaust side and charge out on the intake side.
    private func drawOneCharger(_ ctx: GraphicsContext, at c: CGPoint,
                                radius r: CGFloat, spin: Double, heat: Double,
                                a: BayAnimator) {
        let b = a.bay
        // turbine housing glowing with the heat going through it
        if heat > 0.02 {
            BayPaint.fire(ctx, at: c, radius: r * 1.15,
                          intensity: 0.20 + 0.55 * heat)
        }
        BayPaint.dome(ctx, at: c, radius: r, metal: .head)
        // the eye of the compressor
        ctx.fill(Path(ellipseIn: CGRect(x: c.x - r * 0.76, y: c.y - r * 0.76,
                                        width: r * 1.52, height: r * 1.52)),
                 with: .color(BayMetal.bore.f(1.0 + 0.9 * heat)))

        // The wheel BLURS instead of strobing: at speed the individual blades
        // are not resolvable anyway, so extra ghosted copies are closer to the
        // truth than a slow-motion propeller.
        let blades = b.charger == .roots ? 3 : 11
        let ghosts = spin > 0.55 ? 3 : (spin > 0.2 ? 2 : 1)
        let phase = a.crankDeg * 0.0175 * (0.4 + 3.0 * spin)
        for gi in 0..<ghosts {
            let lag = Double(gi) * 0.10 * (0.3 + spin)
            let alpha = gi == 0 ? 1.0 : 0.34 / Double(gi)
            for k in 0..<blades {
                let ang = phase - lag + Double(k) * 2.0 * .pi / Double(blades)
                let tip = CGPoint(x: c.x + CGFloat(cos(ang)) * r * 0.68,
                                  y: c.y + CGFloat(sin(ang)) * r * 0.68)
                var p = Path()
                p.move(to: CGPoint(x: c.x + CGFloat(cos(ang)) * r * 0.20,
                                   y: c.y + CGFloat(sin(ang)) * r * 0.20))
                p.addLine(to: tip)
                ctx.stroke(p, with: .color(BayMetal.piston.f(1.0)
                    .opacity(alpha * (0.45 + 0.55 * heat))),
                           lineWidth: max(r * 0.10, 1.6))
            }
        }
        BayPaint.dome(ctx, at: c, radius: r * 0.22, metal: .journal,
                      specular: true)
        ctx.stroke(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r,
                                          width: r * 2, height: r * 2)),
                   with: .color(BayMetal.outline.color), lineWidth: 1)

        // THREE stages, three colours.  Cold air arrives BLUE, the compressor
        // makes it charge (green) and the turbine is fed hot gas (red) -- the
        // blue leg was missing entirely, which is why the turbo read as a
        // wheel with no plumbing.
        BayPaint.orthoPipe(ctx, points: [CGPoint(x: c.x - r - 13, y: c.y - r * 0.6),
                                         CGPoint(x: c.x - r - 4, y: c.y - r * 0.6),
                                         CGPoint(x: c.x - r - 4, y: c.y)],
                           radius: r * 0.17, metal: .coolAir)
        BayPaint.orthoPipe(ctx, points: [CGPoint(x: c.x, y: c.y + r + 10),
                                         CGPoint(x: c.x, y: c.y + r)],
                           radius: r * 0.19, metal: .exhaustPipe)
        BayPaint.orthoPipe(ctx, points: [CGPoint(x: c.x + r, y: c.y),
                                         CGPoint(x: c.x + r + 11, y: c.y)],
                           radius: r * 0.19, metal: .intakePipe)
        // the cold charge SPINNING UP: a blue swirl at the compressor eye that
        // arrives with boost, which is the effect that was missing
        if heat > 0.03 {
            let swirl = Gradient(stops: [
                .init(color: Color(red: 0.62, green: 0.90, blue: 1.0)
                    .opacity(0.85 * heat), location: 0.0),
                .init(color: Color(red: 0.30, green: 0.66, blue: 1.0)
                    .opacity(0.55 * heat), location: 0.45),
                .init(color: Color(red: 0.10, green: 0.35, blue: 0.85)
                    .opacity(0.0), location: 1.0),
            ])
            let R = r * 0.78
            ctx.fill(Path(ellipseIn: CGRect(x: c.x - R, y: c.y - R,
                                            width: R * 2, height: R * 2)),
                     with: .radialGradient(swirl, center: c,
                                           startRadius: 0, endRadius: R))
            // and a couple of streaks being dragged round by the wheel
            for k in 0..<3 {
                let ang = phase * 1.6 + Double(k) * 2.0 * .pi / 3.0
                var arc = Path()
                for j in 0...8 {
                    let t = Double(j) / 8.0
                    let aa = ang + t * 0.9
                    let rr = r * CGFloat(0.30 + 0.42 * t)
                    let p = CGPoint(x: c.x + CGFloat(cos(aa)) * rr,
                                    y: c.y + CGFloat(sin(aa)) * rr)
                    if j == 0 { arc.move(to: p) } else { arc.addLine(to: p) }
                }
                ctx.stroke(arc, with: .color(Color(red: 0.75, green: 0.95,
                                                   blue: 1.0)
                    .opacity(0.55 * heat)), lineWidth: max(r * 0.07, 1))
            }
        }
        // chrome centre nut, and the blue stator ring an e-turbo has
        BayPaint.dome(ctx, at: c, radius: r * 0.20, metal: .chrome,
                      specular: true)
        if b.engine.electricTurbo {
            ctx.stroke(Path(ellipseIn: CGRect(x: c.x - r - 2, y: c.y - r - 2,
                                              width: (r + 2) * 2,
                                              height: (r + 2) * 2)),
                       with: .color(Color(red: 0.34, green: 0.70, blue: 1.0)),
                       lineWidth: 2)
        }
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
                Gradient(colors: [BayMetal.head.f(1.5), BayMetal.head.f(0.5)]),
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
                Gradient(colors: [BayMetal.piston.f(0.95), BayMetal.piston.f(0.42)]),
                startPoint: CGPoint(x: o.x - 40, y: o.y - 40),
                endPoint: CGPoint(x: o.x + 40, y: o.y + 40)))
            ctx.stroke(rotorPath, with: .color(BayMetal.outline.color), lineWidth: 1.4)

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

            ctx.stroke(shell, with: .color(BayMetal.outline.color), lineWidth: 2)

            // ports and plug, at the angles the plug position dictates
            portMark(ctx, at: housingPt(r.intakePortDeg), tint: .cyan)
            let exPt = housingPt(r.exhaustPortDeg)
            portMark(ctx, at: exPt, tint: .orange)
            BayPaint.dome(ctx, at: housingPt(r.plugDeg), radius: 3.4,
                          metal: BayMetal(r: 0.95, g: 0.78, b: 0.25))

            var g = CylGeo()
            g.port = exPt
            g.crank = o
            for i in stride(from: rotor, to: b.cylinderCount, by: n) {
                while ports.count <= i { ports.append(CylGeo()) }
                ports[i] = g
            }
        }

        manifolds(ctx, size: size, a: a, geo: ports, vertical: true)
        finish(ctx, size: size, a: a,
               crankFrom: CGPoint(x: size.width * 0.5, y: size.height - 26),
               crankTo: CGPoint(x: size.width * 0.5, y: size.height - 26),
               thickness: 10, vertical: true)
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
                   with: .color(BayMetal.outline.color), lineWidth: 1)
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
