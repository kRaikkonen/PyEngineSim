//
//  BayAncillaries.swift
//  The things bolted to the engine that are not the engine.
//
//  An engine drawn as only pistons and pipes reads as a diagram of a cycle.
//  What makes the PC build read as a CAR is the furniture around it: the sump
//  hanging under the crank, the radiator, the belt turning the alternator off
//  the crank nose, the timing gears at the front.  None of it moves the
//  simulation and none of it is faked either -- the belt and the gears turn at
//  the ratios they would really turn at, off the same crank angle everything
//  else is drawn from.
//

import SwiftUI
import EngineSimCore

extension BayScene {

    /// The sump, hanging off the crankcase.
    ///
    /// Drawn as a trapezoid because that is the shape of one: wide where it
    /// bolts to the block, narrower at the pan, with a drain plug at the low
    /// corner.
    func drawSump(_ ctx: GraphicsContext, from p0: CGPoint, to p1: CGPoint,
                  thickness t: CGFloat, vertical: Bool) {
        let depth = t * 3.2
        var p = Path()
        if vertical {
            // crank runs down the picture: the sump hangs off its lower end
            let y = max(p0.y, p1.y)
            let x = p0.x
            p.move(to: CGPoint(x: x - t, y: y))
            p.addLine(to: CGPoint(x: x + t, y: y))
            p.addLine(to: CGPoint(x: x + t * 0.62, y: y + depth))
            p.addLine(to: CGPoint(x: x - t * 0.62, y: y + depth))
        } else {
            let y = p0.y + t
            p.move(to: CGPoint(x: p0.x, y: y))
            p.addLine(to: CGPoint(x: p1.x, y: y))
            p.addLine(to: CGPoint(x: p1.x - depth * 0.7, y: y + depth))
            p.addLine(to: CGPoint(x: p0.x + depth * 0.7, y: y + depth))
        }
        p.closeSubpath()
        ctx.fill(p, with: .linearGradient(
            Gradient(colors: [BayMetal.block.f(1.05), BayMetal.block.f(0.45)]),
            startPoint: CGPoint(x: p0.x, y: p0.y),
            endPoint: CGPoint(x: p1.x, y: p1.y + depth)))
        ctx.stroke(p, with: .color(BayMetal.outline.color), lineWidth: 1)
        // drain plug at the lowest corner
        let plug = vertical
            ? CGPoint(x: p0.x, y: max(p0.y, p1.y) + depth - 2)
            : CGPoint(x: (p0.x + p1.x) * 0.5, y: p0.y + t + depth - 2)
        BayPaint.dome(ctx, at: plug, radius: max(t * 0.16, 2), metal: .brass)
    }

    /// The radiator: a finned core with the coolant showing through, warming
    /// from blue toward amber as the engine heats up.
    func drawRadiator(_ ctx: GraphicsContext, rect: CGRect, warmth: Double) {
        guard rect.width > 6, rect.height > 10 else { return }
        ctx.fill(Path(roundedRect: rect, cornerRadius: 3),
                 with: .color(BayMetal.coolant.f(0.35 + 0.25 * warmth)))
        // the core, as vertical fins
        var x = rect.minX + 2
        while x < rect.maxX - 1 {
            var f = Path()
            f.move(to: CGPoint(x: x, y: rect.minY + 2))
            f.addLine(to: CGPoint(x: x, y: rect.maxY - 2))
            ctx.stroke(f, with: .color(BayMetal.coolant.f(0.85 + 0.5 * warmth)
                .opacity(0.55)), lineWidth: 1)
            x += 3
        }
        // top and bottom tanks
        for y in [rect.minY, rect.maxY - 4] {
            ctx.fill(Path(roundedRect: CGRect(x: rect.minX - 1, y: y,
                                              width: rect.width + 2, height: 4),
                          cornerRadius: 2),
                     with: .color(BayMetal.block.f(1.1)))
        }
        ctx.stroke(Path(roundedRect: rect, cornerRadius: 3),
                   with: .color(BayMetal.outline.color), lineWidth: 1)
    }

    /// Timing gears and the accessory belt at the front of the engine.
    ///
    /// The cam gears turn at HALF crank speed because a four-stroke cam does --
    /// that is the whole reason a timing gear is twice the diameter -- and the
    /// alternator is a small pulley, so it spins faster than the crank.  All
    /// three come off the same crank angle as the pistons.
    func drawFrontDrive(_ ctx: GraphicsContext, at c: CGPoint, radius r: CGFloat,
                        crankDeg: Double, cams: Int) {
        guard r > 4 else { return }
        let camR = r * 1.05                       // twice the teeth, half the speed
        let altR = r * 0.52

        // belt loop around the crank pulley and the alternator
        // ABOVE, not beside: at the front of the crank an alternator placed
        // 2.6 radii to the left simply fell off the picture.
        let alt = CGPoint(x: c.x + r * 0.15, y: c.y - r * 2.5)
        var loop = Path()
        loop.move(to: CGPoint(x: c.x - r, y: c.y))
        loop.addLine(to: CGPoint(x: alt.x - altR, y: alt.y))
        loop.addLine(to: CGPoint(x: alt.x + altR, y: alt.y))
        loop.addLine(to: CGPoint(x: c.x + r, y: c.y))
        loop.closeSubpath()
        ctx.stroke(loop, with: .color(BayMetal.belt.f(1.0)), lineWidth: 2.4)
        ctx.stroke(loop, with: .color(BayMetal.belt.f(2.2).opacity(0.5)),
                   lineWidth: 0.8)

        // cam gears above, meshing with the crank gear
        for k in 0..<max(cams, 1) {
            let ang = Double(k) * .pi / 3.0 - .pi / 6.0
            let gc = CGPoint(x: c.x + CGFloat(sin(ang)) * (r + camR) * 0.98,
                             y: c.y - CGFloat(cos(ang)) * (r + camR) * 0.98)
            gear(ctx, at: gc, radius: camR, teeth: 16,
                 phase: -crankDeg * 0.5, metal: .journal)
        }
        gear(ctx, at: c, radius: r, teeth: 12, phase: crankDeg, metal: .brass)
        // alternator body + its pulley
        BayPaint.dome(ctx, at: alt, radius: altR * 1.5, metal: .head)
        gear(ctx, at: alt, radius: altR, teeth: 8,
             phase: crankDeg * (r / max(altR, 0.1)), metal: .chrome)
    }

    /// A toothed wheel.  Teeth as short radial spokes around the rim -- enough
    /// to read as a gear and to show it turning.
    private func gear(_ ctx: GraphicsContext, at c: CGPoint, radius r: CGFloat,
                      teeth: Int, phase: Double, metal m: BayMetal) {
        guard r > 2 else { return }
        BayPaint.dome(ctx, at: c, radius: r, metal: m)
        let ph = phase * .pi / 180.0
        for k in 0..<teeth {
            let a = ph + Double(k) * 2.0 * .pi / Double(teeth)
            var t = Path()
            t.move(to: CGPoint(x: c.x + CGFloat(cos(a)) * r * 0.82,
                               y: c.y + CGFloat(sin(a)) * r * 0.82))
            t.addLine(to: CGPoint(x: c.x + CGFloat(cos(a)) * r * 1.12,
                                  y: c.y + CGFloat(sin(a)) * r * 1.12))
            ctx.stroke(t, with: .color(m.f(0.55)), lineWidth: max(r * 0.16, 1))
        }
        BayPaint.dome(ctx, at: c, radius: r * 0.28, metal: .journal)
    }

    /// The crank-plane badge.  A V8 fires every 90 deg whichever plane it has,
    /// so the interval cannot tell them apart -- but they sound completely
    /// different, which makes it worth saying out loud.
    func drawPlaneBadge(_ ctx: GraphicsContext, at p: CGPoint, bay b: EngineBay) {
        let text = b.configLabel
        let flat = b.crankPlane == "flat-plane"
        let tint: Color = b.crankPlane == nil ? .secondary
            : (flat ? Color(red: 0.45, green: 0.85, blue: 1.0)
                    : Color(red: 1.0, green: 0.72, blue: 0.30))
        ctx.draw(Text(text)
            .font(.system(size: 9, weight: .semibold, design: .monospaced))
            .foregroundColor(tint), at: p, anchor: .topLeading)
    }
}
