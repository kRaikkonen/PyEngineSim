//
//  BayPaint.swift
//  The materials the engine bay is drawn WITH.
//
//  The first version of the bay was flat rectangles and single-pixel lines, and
//  it looked like a wiring diagram next to the PC build.  The difference is not
//  detail for its own sake -- it is that the PC version SHADES things, so a
//  cylinder reads as a round object with a highlight down it rather than as a
//  grey box, and that is what makes the picture legible as machinery.
//
//  These are the same four tricks the Python uses, ported:
//
//    * strip shading -- a band is filled as N thin quads across its width with
//      brightness 0.30 + 0.85·(1-|t-0.18|)^1.3.  The peak sits OFF centre, so
//      the surface reads as a cylinder lit from the upper left instead of a
//      flat fill with a gradient stuck on it.
//    * domed caps and discs -- concentric circles of rising brightness, each
//      nudged toward the light, which turns a flat circle into a ball end.
//    * the I-beam rod -- narrow at the wrist pin, wide at the big end, with a
//      bright web line down the middle.
//    * a radial fire bloom, white-hot core through yellow and orange to deep
//      red, for combustion.
//
//  Everything here is pure drawing.  No geometry decisions live in this file;
//  those are in EngineBay, so the picture and the sound cannot drift apart.
//

import SwiftUI

/// A metal colour that can be scaled in brightness the way the Python does.
struct BayMetal {
    var r: Double, g: Double, b: Double

    func f(_ k: Double) -> Color {
        Color(red: min(r * k, 1.0), green: min(g * k, 1.0), blue: min(b * k, 1.0))
    }
    var color: Color { f(1.0) }

    static let sleeve = BayMetal(r: 78 / 255, g: 84 / 255, b: 96 / 255)
    static let piston = BayMetal(r: 196 / 255, g: 202 / 255, b: 214 / 255)
    static let rod = BayMetal(r: 126 / 255, g: 132 / 255, b: 146 / 255)
    static let journal = BayMetal(r: 98 / 255, g: 104 / 255, b: 120 / 255)
    static let brass = BayMetal(r: 150 / 255, g: 110 / 255, b: 44 / 255)
    static let head = BayMetal(r: 70 / 255, g: 76 / 255, b: 90 / 255)
    static let pipe = BayMetal(r: 116 / 255, g: 122 / 255, b: 136 / 255)
    static let bore = BayMetal(r: 24 / 255, g: 26 / 255, b: 32 / 255)
    static let block = BayMetal(r: 58 / 255, g: 63 / 255, b: 74 / 255)
    static let outline = BayMetal(r: 22 / 255, g: 24 / 255, b: 30 / 255)
    /// Hot exhaust is red, cool intake is green -- the same coding the PC build
    /// uses, and the reason its plumbing reads at a glance.
    static let exhaustPipe = BayMetal(r: 168 / 255, g: 72 / 255, b: 46 / 255)
    static let intakePipe = BayMetal(r: 62 / 255, g: 146 / 255, b: 94 / 255)
    /// Cool air on its way IN, before the compressor has done anything to it --
    /// blue because it is the only cold thing in the bay, and the reason a
    /// turbo's plumbing reads as three stages rather than two.
    static let coolAir = BayMetal(r: 70 / 255, g: 138 / 255, b: 180 / 255)
    static let coolant = BayMetal(r: 62 / 255, g: 126 / 255, b: 168 / 255)
    static let chrome = BayMetal(r: 226 / 255, g: 232 / 255, b: 242 / 255)
    static let belt = BayMetal(r: 34 / 255, g: 36 / 255, b: 42 / 255)
}

/// A direction pair: along the part, and across it.
struct Axis {
    var ux: CGFloat, uy: CGFloat        // along
    var qx: CGFloat, qy: CGFloat        // across

    init(angleDeg a: Double) {
        let r = a * Double.pi / 180.0
        ux = CGFloat(sin(r)); uy = CGFloat(-cos(r))
        qx = uy; qy = -ux
    }
    init(from p0: CGPoint, to p1: CGPoint) {
        let dx = p1.x - p0.x, dy = p1.y - p0.y
        let l = max((dx * dx + dy * dy).squareRoot(), 0.0001)
        ux = dx / l; uy = dy / l
        qx = uy; qy = -ux
    }

    func at(_ o: CGPoint, _ d: CGFloat, _ e: CGFloat) -> CGPoint {
        CGPoint(x: o.x + ux * d + qx * e, y: o.y + uy * d + qy * e)
    }
}

enum BayPaint {

    /// Fill a band as strips so it reads as a ROUND surface.
    ///
    /// `t` runs -1..1 across the width and the highlight sits at +0.18, which
    /// is what stops it looking like a flat panel: a real cylinder's brightest
    /// line is off to the lit side, not down the middle.
    static func shaded(_ ctx: GraphicsContext, origin o: CGPoint, axis ax: Axis,
                       from d0: CGFloat, to d1: CGFloat, halfWidth hw: CGFloat,
                       metal m: BayMetal, strips n: Int = 14) {
        guard hw > 0.4, n > 0 else { return }
        for s in 0..<n {
            let e0 = (CGFloat(s) / CGFloat(n) * 2 - 1) * hw
            let e1 = (CGFloat(s + 1) / CGFloat(n) * 2 - 1) * hw
            let t = (Double(s) + 0.5) / Double(n) * 2 - 1
            let k = 0.30 + 0.85 * pow(max(0.0, 1.0 - abs(t - 0.18)), 1.3)
            var p = Path()
            p.move(to: ax.at(o, d0, e0))
            p.addLine(to: ax.at(o, d1, e0))
            p.addLine(to: ax.at(o, d1, e1))
            p.addLine(to: ax.at(o, d0, e1))
            p.closeSubpath()
            ctx.fill(p, with: .color(m.f(k)))
        }
    }

    /// A flat four-sided band, for outlines and dark interiors.
    static func band(origin o: CGPoint, axis ax: Axis, from d0: CGFloat,
                     to d1: CGFloat, halfWidth hw: CGFloat) -> Path {
        var p = Path()
        p.move(to: ax.at(o, d0, hw))
        p.addLine(to: ax.at(o, d1, hw))
        p.addLine(to: ax.at(o, d1, -hw))
        p.addLine(to: ax.at(o, d0, -hw))
        p.closeSubpath()
        return p
    }

    /// A domed disc: concentric circles brightening and creeping toward the
    /// light, which is what turns a circle into a ball.
    static func dome(_ ctx: GraphicsContext, at c: CGPoint, radius r: CGFloat,
                     metal m: BayMetal, lit: CGVector = CGVector(dx: -1, dy: -1),
                     specular: Bool = false) {
        guard r >= 1 else { return }
        let steps: [(CGFloat, Double, CGFloat)] = [
            (1.00, 0.62, 0.00), (0.80, 0.86, 0.16), (0.60, 1.12, 0.30),
            (0.40, 1.40, 0.44), (0.22, 1.70, 0.56),
        ]
        for (sr, k, off) in steps {
            let rr = r * sr
            guard rr >= 0.6 else { continue }
            let cx = c.x + lit.dx * r * off, cy = c.y + lit.dy * r * off
            ctx.fill(Path(ellipseIn: CGRect(x: cx - rr, y: cy - rr,
                                            width: rr * 2, height: rr * 2)),
                     with: .color(m.f(k)))
        }
        ctx.stroke(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r,
                                          width: r * 2, height: r * 2)),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
        if specular, r >= 4 {
            let sx = c.x + lit.dx * r * 0.5, sy = c.y + lit.dy * r * 0.5
            let sr = max(r / 5, 1)
            ctx.fill(Path(ellipseIn: CGRect(x: sx - sr, y: sy - sr,
                                            width: sr * 2, height: sr * 2)),
                     with: .color(.white.opacity(0.92)))
        }
    }

    /// The connecting rod: narrow at the wrist pin, wide at the big end, with a
    /// bright web down the centre.  A plain line reads as wire.
    static func rod(_ ctx: GraphicsContext, small: CGPoint, big: CGPoint,
                    width w: CGFloat) {
        let ax = Axis(from: small, to: big)
        let nx = ax.qx * w, ny = ax.qy * w
        var beam = Path()
        beam.move(to: CGPoint(x: small.x + nx * 0.5, y: small.y + ny * 0.5))
        beam.addLine(to: CGPoint(x: big.x + nx, y: big.y + ny))
        beam.addLine(to: CGPoint(x: big.x - nx, y: big.y - ny))
        beam.addLine(to: CGPoint(x: small.x - nx * 0.5, y: small.y - ny * 0.5))
        beam.closeSubpath()
        ctx.fill(beam, with: .color(BayMetal.rod.color))
        ctx.stroke(beam, with: .color(BayMetal.rod.f(0.62)), lineWidth: 1)
        var web = Path()
        web.move(to: small)
        web.addLine(to: big)
        ctx.stroke(web, with: .color(BayMetal.rod.f(1.42)), lineWidth: 1)
    }

    /// Combustion: white-hot core, yellow, orange, deep red edge.
    static func fire(_ ctx: GraphicsContext, at c: CGPoint, radius r: CGFloat,
                     intensity g: Double) {
        guard g > 0.02, r > 1 else { return }
        let stops = Gradient(stops: [
            .init(color: Color(red: 1.0, green: 1.0, blue: 0.94).opacity(g), location: 0.0),
            .init(color: Color(red: 1.0, green: 0.93, blue: 0.27).opacity(g * 0.95), location: 0.18),
            .init(color: Color(red: 1.0, green: 0.59, blue: 0.05).opacity(g * 0.80), location: 0.42),
            .init(color: Color(red: 1.0, green: 0.22, blue: 0.02).opacity(g * 0.50), location: 0.68),
            .init(color: Color(red: 0.90, green: 0.05, blue: 0.02).opacity(0.0), location: 1.0),
        ])
        let R = r * 1.9
        ctx.fill(Path(ellipseIn: CGRect(x: c.x - R, y: c.y - R,
                                        width: R * 2, height: R * 2)),
                 with: .radialGradient(stops, center: c,
                                       startRadius: 0, endRadius: R))
    }

    /// A pipe drawn as a shaded tube along a path sample, so headers read as
    /// round steel rather than as a stroked line.
    static func tube(_ ctx: GraphicsContext, points: [CGPoint], radius r: CGFloat,
                     metal m: BayMetal) {
        guard points.count > 1 else { return }
        for i in 0..<(points.count - 1) {
            let a = points[i], b = points[i + 1]
            let ax = Axis(from: a, to: b)
            // extend slightly so the segments overlap and leave no seam
            let o = CGPoint(x: a.x - ax.ux * 0.6, y: a.y - ax.uy * 0.6)
            let len = ((b.x - a.x) * (b.x - a.x)
                       + (b.y - a.y) * (b.y - a.y)).squareRoot() + 1.2
            shaded(ctx, origin: o, axis: ax, from: 0, to: len,
                   halfWidth: r, metal: m, strips: 7)
        }
    }

    /// Plumbing routed in straight runs with rounded corners, the way a real
    /// manifold is bent -- rather than a single swooping curve across the whole
    /// engine, which is what made the first version's pipework look like
    /// spaghetti draped over the block.
    static func orthoPipe(_ ctx: GraphicsContext, points: [CGPoint],
                          radius r: CGFloat, metal m: BayMetal) {
        guard points.count > 1 else { return }
        for i in 0..<(points.count - 1) {
            tube(ctx, points: [points[i], points[i + 1]], radius: r, metal: m)
        }
        // a domed elbow at each corner so the runs join instead of butting
        for i in 1..<(points.count - 1) {
            dome(ctx, at: points[i], radius: r * 1.12, metal: m)
        }
        dome(ctx, at: points[0], radius: r * 1.05, metal: m)
    }

    /// A true Reuleaux flank: an arc centred on the OPPOSITE apex, which is the
    /// actual rotor shape rather than a bulged straight line.
    static func reuleaux(_ verts: [CGPoint], samples: Int = 14) -> Path {
        var p = Path()
        guard verts.count == 3 else { return p }
        for s in 0..<3 {
            let va = verts[s], vb = verts[(s + 1) % 3], vc = verts[(s + 2) % 3]
            let rad = ((va.x - vc.x) * (va.x - vc.x)
                       + (va.y - vc.y) * (va.y - vc.y)).squareRoot()
            let a0 = atan2(va.y - vc.y, va.x - vc.x)
            let a1 = atan2(vb.y - vc.y, vb.x - vc.x)
            var d = a1 - a0
            while d > .pi { d -= 2 * .pi }
            while d < -.pi { d += 2 * .pi }
            for k in 0...samples {
                let a = a0 + d * Double(k) / Double(samples)
                let pt = CGPoint(x: vc.x + rad * CGFloat(cos(a)),
                                 y: vc.y + rad * CGFloat(sin(a)))
                if s == 0 && k == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
            }
        }
        p.closeSubpath()
        return p
    }
}
