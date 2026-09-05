//
//  BayScene.swift
//  Everything the engine bay draws, as a plain value.
//
//  Split out of the view so it can be RENDERED WITHOUT AN APP: it holds no
//  @ObservedObject, touches no audio and owns no clock, so a snapshot tool can
//  hand it a preset and a crank angle and get a PNG back.  That matters
//  because every visual bug in this file so far -- the bang on the wrong
//  stroke, the V laid out sideways, pipes through the block -- was invisible
//  from here and had to be reported by someone looking at a phone.
//
//  It decides WHERE the parts go.  BayPaint decides what they look like, and
//  EngineBay decides what the mechanism is doing.
//

import SwiftUI
import EngineSimCore

/// How solid the hot-side plumbing is drawn.  It runs over the banks, so it
/// has to let them through.
private let EXHAUST_ALPHA = 0.62

struct BayScene {
    let bay: EngineBay
    let crankDeg: Double
    let pulses: ExhaustPulseField
    let rpm: Double
    let boostBar: Double
    var load: Double = 0
    /// 0 cold, 1 up to temperature -- the radiator warms with it.
    var warmth: Double = 0.6

    func draw(_ ctx: GraphicsContext, size: CGSize) {
        render(ctx, size: size)
    }

    // MARK: - top level

    func render(_ ctx: GraphicsContext, size: CGSize) {
        let b = bay
        guard b.cylinderCount > 0 else { return }
        if let r = b.rotary {
            renderRotary(ctx, size: size, geo: r)
            return
        }
        renderPistons(ctx, size: size)
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
    func renderPistons(_ ctx: GraphicsContext, size: CGSize) {
        let top: CGFloat = 18
        let bottom = size.height - 16
        switch bay.layout {
        case .w:
            renderW(ctx, size: size, top: top, bottom: bottom)
        case .vee, .flat:
            renderVee(ctx, size: size, top: top, bottom: bottom)
        default:
            renderInline(ctx, size: size, top: top, bottom: bottom)
        }
    }

    /// Per-cylinder size from displacement, ~500 cc as the reference, cube-root
    /// so an eight-times-bigger cylinder is twice the size each way.
    func cylScale(_ b: EngineBay) -> CGFloat {
        let total = b.engine.cylinders.reduce(0.0) { $0 + $1.displacement }
        let perCC = total * 1.0e6 / Double(max(b.cylinderCount, 1))
        return CGFloat(min(max(pow(perCC / 500.0, 1.0 / 3.0), 0.55), 1.30))
    }

    /// Build one cylinder's drawing geometry for a crank centre and bank angle.
    ///
    /// `deckLen` is how far the head sits from the crank centre; the stroke is
    /// solved back out of it so the REAL slider-crank still places the piston
    /// and the rod never has to be stretched to reach.
    func geoAt(_ s: EngineBay.Slot, bay b: EngineBay, crank: Double,
                       at c: CGPoint, bankDeg: Double, deckLen: CGFloat,
                       halfBore: CGFloat) -> CylGeo {
        var g = CylGeo()
        g.axis = BayAxis(angleDeg: bankDeg)
        g.crank = c
        g.halfBore = halfBore
        g.side = bankDeg < 0 ? -1 : 1

        // The throw is drawn COMPACT, not to scale.  A cutaway shows a long
        // barrel over a small crank; scaling the throw to a true stroke/2 of a
        // barrel this long gave counterweights wider than the bore and buried
        // the bottom of the picture in brass.  The Python does the same thing
        // (a fixed 9 px crank whatever the cylinder).  What stays exact is the
        // piston POSITION -- still the real slider-crank fraction -- so the
        // motion is honest even though the throw is drawn small.
        g.crankRadius = min(halfBore * 0.60, deckLen * 0.13)
        g.pistonLen = min(halfBore * 0.90, deckLen * 0.15)
        g.boreBase = g.crankRadius * 1.5
        g.deck = deckLen
        g.headTop = deckLen + g.pistonLen * 0.85

        let frac = CGFloat(b.pistonFraction(s.index, crankAngleDeg: crank))
        let th = b.crankPinDeg(s.index, crankAngleDeg: crank) * Double.pi / 180.0
        let ct = CGFloat(cos(th)), st = CGFloat(sin(th))
        let travel = max(deckLen - g.pistonLen - g.boreBase - deckLen * 0.06, 4)
        g.pinDist = g.boreBase + (1 - frac) * travel
        let r = g.crankRadius
        g.crankPin = CGPoint(x: c.x + (g.axis.ux * ct + g.axis.qx * st) * r,
                             y: c.y + (g.axis.uy * ct + g.axis.qy * st) * r)
        g.port = g.axis.at(c, g.deck + g.pistonLen * 0.4, halfBore * 1.15 * g.side)
        g.intakePort = g.axis.at(c, g.deck + g.pistonLen * 0.4,
                                 -halfBore * 1.15 * g.side)
        return g
    }

    // ---------------------------------------------------------------- inline
    func renderInline(_ ctx: GraphicsContext, size: CGSize, top: CGFloat, bottom: CGFloat) {
        let b = bay
        let n = CGFloat(max(b.cylinderCount, 1))
        let sc = cylScale(b)
        // lanes: radiator and front drive on the left, exhaust rail on the
        // right.  What is left belongs to the cylinders, and they are CENTRED
        // in it -- the first cut left-aligned them and wasted the right third.
        let leftLane: CGFloat = size.width * 0.13
        let rightLane: CGFloat = 26
        let usable = size.width - leftLane - rightLane
        let pitch = usable / n
        var halfBore = pitch * 0.40
        let x0 = leftLane + pitch * 0.5

        // FILL the frame.  The PC build's 62 px came off a much bigger panel;
        // carried over literally it left the engine in one corner with two
        // thirds of the picture empty.  Size off the space available and use
        // the displacement scale only to nudge it.
        // PROPORTION, not just size.  Stretching the barrel to whatever height
        // was going spare made each cylinder four bores tall -- a dark chimney
        // with a plate sliding in it.  A real barrel is a bit over two bores
        // from crank centre to deck, so tie the length to the BORE and let the
        // leftover height go to the plumbing and the sump.
        var deckLen = min(halfBore * 4.4, (bottom - top) * 0.66)
        deckLen = max(deckLen, 30)
        halfBore = min(halfBore, deckLen * 0.30)
        // sit the block low-ish, leaving the top for the manifolds
        let engineH = deckLen * 1.24
        let crankY = min(top + (bottom - top - engineH) * 0.62 + engineH,
                         bottom - size.height * 0.10)

        var geo: [CylGeo] = []
        for s in b.slots {
            geo.append(geoAt(s, bay: b, crank: crankDeg,
                             at: CGPoint(x: x0 + pitch * CGFloat(s.station),
                                         y: crankY),
                             bankDeg: 0, deckLen: deckLen, halfBore: halfBore))
        }
        drawCrankcase(ctx, from: CGPoint(x: leftLane * 0.4, y: crankY),
                      to: CGPoint(x: size.width - rightLane * 0.4, y: crankY),
                      thickness: max(halfBore * 0.30, 6))
        // intake first, so the banks cover its long runners
        intakeManifold(ctx, size: size, geo: geo, vertical: false)
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b)
        }
        drawCrankshaft(ctx, geo: geo)
        // Plumbing goes ON TOP.  Routed behind the banks it is
        // simply invisible, which is the same as not drawing it --
        // the PC build puts its manifolds over the banks too.
        manifolds(ctx, size: size, geo: geo, vertical: false)
        finish(ctx, size: size,
               crankFrom: CGPoint(x: leftLane * 0.4, y: crankY),
               crankTo: CGPoint(x: size.width - rightLane * 0.4, y: crankY),
               thickness: max(halfBore * 0.30, 6), vertical: false)
    }

    // ------------------------------------------------------------------ vee
    func renderVee(_ ctx: GraphicsContext, size: CGSize, top: CGFloat, bottom: CGFloat) {
        let b = bay
        let sc = cylScale(b)
        let ns = CGFloat(max(b.stationsPerBank, 1))
        let maxAng = b.slots.map { abs($0.bankAngleDeg) }.max() ?? 0
        let bankDeg = min(maxAng, 82.0)
        let bank = bankDeg * Double.pi / 180.0

        var halfBore = size.width * 0.075 * sc
        // Longer barrels than the first cut: a V looked stubby because the
        // station pitch was eating the height.  Packing the stations tighter
        // (0.62 rather than 0.78 of the vertical reach) buys the length back --
        // real V banks overlap when you look at them end-on anyway.
        var deckLen = min(size.height * 0.42, size.width * 0.40) * sc
        // the fan-out has to stay inside the frame, with a lane for the rails
        let reach = deckLen * CGFloat(sin(bank)) + halfBore
        let maxReach = size.width * 0.5 - 30
        if reach > maxReach {
            let f = maxReach / reach
            halfBore *= f; deckLen *= f
        }
        var dy = max(halfBore * 1.4, deckLen * CGFloat(cos(bank)) * 0.45)
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
            geo.append(geoAt(s, bay: b, crank: crankDeg,
                             at: CGPoint(x: cx, y: jy),
                             bankDeg: side * bankDeg, deckLen: deckLen,
                             halfBore: halfBore))
        }
        let cy0 = mtop + dy * 0.5 - 14, cy1 = mtop + dy * (ns - 0.5) + 14
        drawCrankcase(ctx, from: CGPoint(x: cx, y: cy0),
                      to: CGPoint(x: cx, y: cy1), thickness: max(halfBore * 0.30, 5))
        // intake first, so the banks cover its long runners
        intakeManifold(ctx, size: size, geo: geo, vertical: true)
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b)
        }
        drawCrankshaft(ctx, geo: geo)
        // Plumbing goes ON TOP.  Routed behind the banks it is
        // simply invisible, which is the same as not drawing it --
        // the PC build puts its manifolds over the banks too.
        manifolds(ctx, size: size, geo: geo, vertical: true)
        finish(ctx, size: size, crankFrom: CGPoint(x: cx, y: cy0),
               crankTo: CGPoint(x: cx, y: cy1),
               thickness: max(halfBore * 0.30, 5), vertical: true)
    }

    // -------------------------------------------------------------------- W
    /// Two narrow-angle VR units side by side, each with its own crank -- which
    /// is what a W16 is, rather than one enormous vee.
    func renderW(_ ctx: GraphicsContext, size: CGSize, top: CGFloat, bottom: CGFloat) {
        let b = bay
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

            var halfBore = size.width * 0.040
            var deckLen = min(size.height * 0.30, size.width * 0.26)
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
                          to: CGPoint(x: ux, y: cy1), thickness: max(halfBore * 0.30, 5))
            // each VR unit gets its own sump, because each has its own crank
            drawSump(ctx, from: CGPoint(x: ux, y: cy0),
                     to: CGPoint(x: ux, y: cy1),
                     thickness: max(halfBore * 0.30, 5), vertical: true)
            frontAnchor = CGPoint(x: ux, y: cy0)
            frontR = max(halfBore * 0.8, 6)

            for (sub, sgn) in [(subA, -1.0), (subB, 1.0)] {
                for (k, s) in sub.enumerated() {
                    let jy = uTop + dy * (CGFloat(k) + 0.5)
                    var g = geoAt(s, bay: b, crank: crankDeg,
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
        // intake first, so the banks cover its long runners
        intakeManifold(ctx, size: size, geo: geo, vertical: true)
        for (i, s) in b.slots.enumerated() {
            drawSleeve(ctx, g: geo[i], slot: s, bay: b)
            drawMoving(ctx, g: geo[i], slot: s, bay: b)
        }
        drawCrankshaft(ctx, geo: geo)
        // Plumbing goes ON TOP.  Routed behind the banks it is
        // simply invisible, which is the same as not drawing it --
        // the PC build puts its manifolds over the banks too.
        manifolds(ctx, size: size, geo: geo, vertical: true)
        // the sumps are drawn per unit above, so this pass only wants the
        // shared furniture -- hand it a zero-length crank line
        finish(ctx, size: size, crankFrom: frontAnchor,
               crankTo: frontAnchor, thickness: frontR * 1.25, vertical: true)
    }

    /// The furniture: sump, radiator, front drive, charger, badges.  Called by
    /// every layout with that layout's crank line, so the sump hangs off the
    /// crank and the gears sit at the front of it wherever the crank happens
    /// to point.
    func finish(_ ctx: GraphicsContext, size: CGSize,
                        crankFrom: CGPoint, crankTo: CGPoint,
                        thickness: CGFloat, vertical: Bool) {
        drawSump(ctx, from: crankFrom, to: crankTo, thickness: thickness,
                 vertical: vertical)
        // front of the engine: the top of a vertical crank, the left of a
        // horizontal one
        let front = vertical
            ? CGPoint(x: crankFrom.x, y: min(crankFrom.y, crankTo.y))
            : CGPoint(x: min(crankFrom.x, crankTo.x), y: crankFrom.y)
        let cams = bay.engine.valvesPerCyl >= 4 ? 2 : 1
        drawFrontDrive(ctx, at: front, radius: max(thickness * 0.42, 5),
                       crankDeg: crankDeg, cams: cams)
        // a core, not a wall: the first one was 280 px tall and dominated the
        // whole picture
        drawRadiator(ctx, rect: CGRect(x: 6, y: 26, width: 13,
                                       height: min(size.height * 0.22, 76)),
                     warmth: warmth)
        if bay.charger != .na { drawCharger(ctx, size: size) }
        drawBoost(ctx, size: size)
        drawPlaneBadge(ctx, at: CGPoint(x: 21, y: 6), bay: bay)
    }

    /// The crankcase, as a shaded bar along the crank axis.
    func drawCrankcase(_ ctx: GraphicsContext, from p0: CGPoint,
                               to p1: CGPoint, thickness t: CGFloat) {
        let ax = BayAxis(from: p0, to: p1)
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
    /// Intake only.  It is drawn BEFORE the cylinders: a V's plenum genuinely
    /// lives in the valley and its runners genuinely reach across to the inner
    /// side of each head, so the runs are long by nature -- putting them behind
    /// the banks is what stops them looking like bars laid over the engine.
    func intakeManifold(_ ctx: GraphicsContext, size: CGSize,
                        geo: [CylGeo], vertical: Bool) {
        guard !geo.isEmpty else { return }
        let rad = max(min(size.width, size.height) * 0.011, 2.6)
        let plenum: [CGPoint]
        if vertical {
            let cx = size.width * 0.5 - rad * 2.2
            let ys = geo.filter { $0.halfBore > 0 }.map { $0.intakePort.y }
            plenum = [CGPoint(x: cx, y: (ys.min() ?? 0) - 6),
                      CGPoint(x: cx, y: (ys.max() ?? 0) + 6)]
        } else {
            let live = geo.filter { $0.halfBore > 0 }
            let topY = (live.map { $0.intakePort.y }.min() ?? 0) - 16
            plenum = [CGPoint(x: (live.map { $0.intakePort.x }.min() ?? 0) - 6,
                              y: topY),
                      CGPoint(x: (live.map { $0.intakePort.x }.max() ?? 0) + 6,
                              y: topY)]
        }
        for g in geo where g.halfBore > 0 {
            let joint = vertical
                ? CGPoint(x: plenum[0].x, y: g.intakePort.y)
                : CGPoint(x: g.intakePort.x, y: plenum[0].y)
            BayPaint.orthoPipe(ctx, points: [joint, g.intakePort],
                               radius: rad * 0.70, metal: .intakePipe)
        }
        BayPaint.orthoPipe(ctx, points: plenum, radius: rad, metal: .intakePipe)
    }

    func manifolds(_ ctx: GraphicsContext, size: CGSize,
                           geo: [CylGeo], vertical: Bool) {
        let b = bay
        guard !geo.isEmpty else { return }
        let rad = max(min(size.width, size.height) * 0.011, 2.6)
        let collY = size.height - 9
        // Where the rails go depends on which way the engine is lying.  On an
        // INLINE the ports all face the same way and the rail has to clear the
        // whole row, so it goes to the frame edge.  On a VEE the ports face
        // outward at every station, and running each of them to the frame edge
        // drew a full-width bar per cylinder straight across the picture --
        // twelve of them on a V12.  There the rail belongs just OUTBOARD of
        // its own bank, so each run is a short stub.
        let lx: CGFloat
        let rx: CGFloat
        if vertical {
            let leftPorts = geo.filter { $0.halfBore > 0 && $0.side < 0 }
                .map { $0.port.x }
            let rightPorts = geo.filter { $0.halfBore > 0 && $0.side >= 0 }
                .map { $0.port.x }
            let pad = (geo.first { $0.halfBore > 0 }?.halfBore ?? 10) * 0.55
            lx = max((leftPorts.min() ?? size.width * 0.2) - pad, 7)
            rx = min((rightPorts.max() ?? size.width * 0.8) + pad,
                     size.width - 7)
        } else {
            lx = max(size.width * 0.035, 7)
            rx = size.width - max(size.width * 0.035, 7)
        }

        // ---- exhaust -----------------------------------------------------
        var routes = [[CGPoint]](repeating: [], count: geo.count)
        if vertical {
            // A REAL HEADER, running ALONG the bank.  Each cylinder joins a
            // collector pipe that follows the line of its own heads and then
            // drops to the tailpipe -- which is both what a V manifold looks
            // like and the only routing that does not lay a bar across the
            // whole engine once per cylinder.  Grouped by `side`, which the W
            // sets per UNIT so each VR gets its own pair.
            for side in [CGFloat(-1), CGFloat(1)] {
                let idx = geo.indices.filter {
                    geo[$0].halfBore > 0
                        && (side < 0 ? geo[$0].side < 0 : geo[$0].side >= 0)
                }.sorted { geo[$0].port.y < geo[$1].port.y }
                guard !idx.isEmpty else { continue }
                let rail = side < 0 ? lx : rx
                let spine = idx.map { geo[$0].port }
                    + [CGPoint(x: rail, y: collY)]
                // SEE-THROUGH: the header is drawn on top of the bank it
                // is bolted to, so at full opacity it hides the cylinders it
                // is meant to be attached to.
                BayPaint.orthoPipe(ctx, points: spine, radius: rad * 1.1,
                                   metal: .exhaustPipe, alpha: EXHAUST_ALPHA)
                // each cylinder rides its own header from where it joins
                for (k, i) in idx.enumerated() {
                    routes[i] = Array(spine[k...])
                }
            }
        } else {
            for (i, g) in geo.enumerated() {
                guard g.halfBore > 0 else { continue }
                // up and OVER: a band above every head, so the run to the rail
                // never crosses a cylinder
                let band = (geo.map { $0.port.y }.min() ?? 0) - 12
                let rail = g.side < 0 ? lx : rx
                routes[i] = [g.port, CGPoint(x: g.port.x, y: band),
                             CGPoint(x: rail, y: band),
                             CGPoint(x: rail, y: collY)]
            }
            for r in routes where r.count > 1 {
                BayPaint.orthoPipe(ctx, points: r, radius: rad,
                                   metal: .exhaustPipe, alpha: EXHAUST_ALPHA)
            }
        }
        // collector along the bottom, then the tailpipe
        let collectors = Set(routes.compactMap { $0.last.map { Int($0.x) } })
        let tip = CGPoint(x: size.width - 7, y: collY)
        for cxi in collectors.sorted() {
            BayPaint.orthoPipe(ctx, points: [CGPoint(x: CGFloat(cxi), y: collY), tip],
                               radius: rad * 1.15, metal: .exhaustPipe,
                               alpha: EXHAUST_ALPHA)
        }

        // ---- the pulses, riding their own route --------------------------
        for p in pulses.pulses {
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
        if pulses.exitFlash > 0.02 {
            BayPaint.fire(ctx, at: tip, radius: rad * 3.2,
                          intensity: pulses.exitFlash)
        }
        _ = b
    }

    /// A point a fraction of the way along a polyline, by arc length.
    func along(_ pts: [CGPoint], _ t: CGFloat) -> CGPoint {
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


    func drawSleeve(_ ctx: GraphicsContext, g: CylGeo,
                            slot s: EngineBay.Slot, bay b: EngineBay) {
        let ax = g.axis, o = g.crank
        let hw = g.halfBore
        // sleeve, strip-shaded so it reads round
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.boreBase - 3,
                        to: g.deck, halfWidth: hw, metal: .sleeve, strips: 16)
        // cooling fins across the barrel
        var d = g.boreBase + 4
        while d < g.deck - 6 {
            var f = Path()
            f.move(to: ax.at(o, d, hw))
            f.addLine(to: ax.at(o, d, -hw))
            ctx.stroke(f, with: .color(BayMetal.sleeve.f(0.55)), lineWidth: 1)
            d += 7
        }
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.boreBase - 3,
                                 to: g.deck, halfWidth: hw),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
        // head casting on top
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.deck,
                        to: g.deck + g.pistonLen * 0.85, halfWidth: hw + 4,
                        metal: .head, strips: 12)
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.deck,
                                 to: g.deck + g.pistonLen * 0.85,
                                 halfWidth: hw + 4),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
        // dark bore interior, INSIDE the wall -- the metal either side of it
        // is what makes a barrel read as a barrel
        ctx.fill(BayPaint.band(origin: o, axis: ax, from: g.boreBase,
                               to: g.deck, halfWidth: hw * 0.76),
                 with: .color(BayMetal.bore.color))
    }

    /// Piston, rod, valvetrain, combustion -- everything that moves.
    func drawMoving(_ ctx: GraphicsContext, g: CylGeo,
                            slot s: EngineBay.Slot, bay b: EngineBay) {
        let ax = g.axis, o = g.crank, hw = g.halfBore
        let lit = b.combustion(s.index, crankAngleDeg: crankDeg)

        // combustion, in the volume above the crown
        if lit > 0.02 {
            let crown = g.pinDist + g.pistonLen
            let mid = (crown + g.deck) * 0.5
            BayPaint.fire(ctx, at: ax.at(o, mid, 0), radius: hw * 0.42,
                          intensity: 0.25 + 0.75 * lit)
        }

        // valvetrain on the head: two poppets and their cam lobes
        let lift = b.valveLift(s.index, crankAngleDeg: crankDeg,
                               rpm: rpm)
        drawValve(ctx, g: g, side: -1, lift: lift.intake, tint: .cyan)
        drawValve(ctx, g: g, side: 1, lift: lift.exhaust, tint: .orange)

        // piston: shaded barrel, ring lands, wrist pin
        let ph = hw * 0.72
        BayPaint.shaded(ctx, origin: o, axis: ax, from: g.pinDist,
                        to: g.pinDist + g.pistonLen, halfWidth: ph,
                        metal: .piston, strips: 12)
        ctx.stroke(BayPaint.band(origin: o, axis: ax, from: g.pinDist,
                                 to: g.pinDist + g.pistonLen, halfWidth: ph),
                   with: .color(BayMetal.piston.f(0.5)), lineWidth: 1)
        for k in 0..<3 {
            let d = g.pinDist + g.pistonLen - 3 - CGFloat(k) * 3.5
            guard d > g.pinDist + 1 else { break }
            var r = Path()
            r.move(to: ax.at(o, d, ph * 0.92))
            r.addLine(to: ax.at(o, d, -ph * 0.92))
            ctx.stroke(r, with: .color(BayMetal.piston.f(0.42)), lineWidth: 1)
        }
        let pin = ax.at(o, g.pinDist, 0)
        BayPaint.rod(ctx, small: pin, big: g.crankPin,
                     width: max(g.crankRadius * 0.40, 2.2))
        BayPaint.dome(ctx, at: pin, radius: max(hw * 0.22, 2.4), metal: .journal)
    }

    func drawValve(_ ctx: GraphicsContext, g: CylGeo, side: CGFloat,
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
    func drawCrankshaft(_ ctx: GraphicsContext, geo: [CylGeo]) {
        for g in geo where g.halfBore > 0 {
            // counterweight fan, opposite the rod journal
            let dx = g.crankPin.x - g.crank.x, dy = g.crankPin.y - g.crank.y
            let opp = atan2(-dy, -dx)
            let cwr = g.crankRadius * 1.05
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
    func drawCharger(_ ctx: GraphicsContext, size: CGSize) {
        let b = bay
        let spin = b.chargerSpin(rpm: rpm, boostBar: boostBar)
        let heat = min(max(boostBar / max(b.engine.boostBar, 0.30),
                           0.0), 1.0)
        let r = min(size.height * 0.085, 21)
        let twin = b.charger == .twinTurbo
        let xs = size.width * 0.115
        let centres: [CGPoint] = twin
            ? [CGPoint(x: xs, y: size.height * 0.14),
               CGPoint(x: xs, y: size.height * 0.40)]
            : [CGPoint(x: xs, y: size.height * 0.17)]
        for c in centres {
            drawOneCharger(ctx, at: c, radius: r, spin: spin, heat: heat)
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
    func drawOneCharger(_ ctx: GraphicsContext, at c: CGPoint,
                                radius r: CGFloat, spin: Double, heat: Double) {
        let b = bay
        // turbine housing glowing with the heat going through it
        if heat > 0.02 {
            // r*1.9 inside fire(), so 1.15 here made a halo twice the turbo's
            // width -- an orange blob with a wheel in it
            BayPaint.fire(ctx, at: c, radius: r * 0.62,
                          intensity: 0.18 + 0.45 * heat)
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
        let phase = crankDeg * 0.0175 * (0.4 + 3.0 * spin)
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

    func drawBoost(_ ctx: GraphicsContext, size: CGSize) {
        let maxB = max(bay.engine.boostBar, 0.35)
        let f = CGFloat(min(max(boostBar / maxB, 0.0), 1.1))
        let w = size.width * 0.30
        let x = size.width - w - 12, y: CGFloat = 12
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w, height: 7),
                      cornerRadius: 3.5), with: .color(.white.opacity(0.12)))
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w * f, height: 7),
                      cornerRadius: 3.5),
                 with: .color(f > 0.98 ? .red : .green))
        // Two decimals by integer maths: interpolating the Double directly
        // printed "1.100000 bar".
        let cent = max(Int((boostBar * 100).rounded()), 0)
        let fr = cent % 100
        let bar = "\(cent / 100)." + (fr < 10 ? "0\(fr)" : "\(fr)")
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
    func renderRotary(_ ctx: GraphicsContext, size: CGSize, geo r: RotaryGeometry) {
        let b = bay
        let n = r.rotors
        let cellW = size.width / CGFloat(n)
        let span = CGFloat(r.radius + r.eccentricity)
        let scale = min(cellW * 0.40, size.height * 0.34) / span
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

            let beta = r.rotorAngleDeg(crankDeg, rotor: rotor)
            let apexPt: (Int) -> CGPoint = { kk in
                let p = r.apex(crankDeg, rotor: rotor, kk)
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

                let phase = r.chamberStroke(crankDeg, rotor: rotor, kk)
                let fill = r.chamberFill(crankDeg, rotor: rotor, kk)
                if phase == .power {
                    let glow = 1.0 - fill
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
            let rc = r.rotorCentre(crankDeg, rotor: rotor)
            let rcPt = P(rc.x, rc.y)
            let ax = BayAxis(from: o, to: rcPt)
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

        manifolds(ctx, size: size, geo: ports, vertical: true)
        finish(ctx, size: size,
               crankFrom: CGPoint(x: size.width * 0.5, y: size.height - 26),
               crankTo: CGPoint(x: size.width * 0.5, y: size.height - 26),
               thickness: 10, vertical: true)
    }

    /// Append the circular arc from `from` to `to` centred on `centre` -- the
    /// real Reuleaux flank.
    func appendArc(_ p: inout Path, from: CGPoint, to: CGPoint,
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

    func portMark(_ ctx: GraphicsContext, at p: CGPoint, tint: Color) {
        let r: CGFloat = 4.5
        ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r,
                                        width: r * 2, height: r * 2)),
                 with: .color(tint.opacity(0.85)))
        ctx.stroke(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r,
                                          width: r * 2, height: r * 2)),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
    }

    func strokeTint(_ s: Stroke) -> Color {
        switch s {
        case .intake: return .cyan
        case .compression: return .yellow
        case .power: return .orange
        case .exhaust: return .gray
        }
    }
}
