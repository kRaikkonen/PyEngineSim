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
                        crankDeg: Double, cams: Int,
                        phaserAdvance: Double = 0) {
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
            // THE PHASER.  A cam phaser rotates the cam against the crank, so
            // it is drawn as a vane inside the sprocket that swings round as
            // the advance comes in -- which is the thing itself, not a lamp
            // stuck on to represent it.  A lift switch (VTEC) is a different
            // device and shows up on the valves instead.
            if phaserAdvance > 0.001 {
                let pr = camR * 0.56
                ctx.fill(Path(ellipseIn: CGRect(x: gc.x - pr, y: gc.y - pr,
                                                width: pr * 2, height: pr * 2)),
                         with: .color(Color(red: 0.17, green: 0.38, blue: 0.59)))
                ctx.stroke(Path(ellipseIn: CGRect(x: gc.x - pr, y: gc.y - pr,
                                                  width: pr * 2, height: pr * 2)),
                           with: .color(Color(red: 0.47, green: 0.77, blue: 1.0)),
                           lineWidth: 1)
                let va = -crankDeg * 0.5 * .pi / 180.0 + phaserAdvance * .pi
                var vane = Path()
                vane.move(to: gc)
                vane.addLine(to: CGPoint(x: gc.x + CGFloat(cos(va)) * pr * 0.92,
                                         y: gc.y + CGFloat(sin(va)) * pr * 0.92))
                ctx.stroke(vane, with: .color(Color(red: 0.59, green: 0.82,
                                                    blue: 1.0)),
                           lineWidth: max(pr * 0.28, 1.4))
            }
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
    /// The variable-valve badge: what the maker calls it, and whether it is IN.
    ///
    /// Worth its own indicator because it is the one thing in the bay that
    /// changes the engine's character rather than its speed -- and it lights at
    /// 74% of redline, the same point the sound switches to the aggressive cam.
    func drawValveBadge(_ ctx: GraphicsContext, at p: CGPoint, bay b: EngineBay,
                        rpm: Double) {
        guard let name = b.variableValveName else { return }
        let on = b.variableValveEngaged(rpm: rpm)
        let col: Color = on ? Color(red: 0.35, green: 0.86, blue: 0.47)
                            : Color(white: 0.58)
        let r: CGFloat = 3.6
        ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r,
                                        width: r * 2, height: r * 2)),
                 with: .color(col))
        if on {                               // a soft halo when it is in
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - r * 2.4, y: p.y - r * 2.4,
                                            width: r * 4.8, height: r * 4.8)),
                     with: .color(col.opacity(0.22)))
        }
        ctx.draw(Text(on ? "\(name)  IN" : name)
            .font(.system(size: 9, weight: on ? .bold : .regular,
                          design: .monospaced))
            .foregroundColor(col),
                 at: CGPoint(x: p.x + 8, y: p.y), anchor: .leading)
    }

    /// The transmission badge, with a whine bar for straight-cut gears.
    ///
    /// A dog box and a DCT are different machines, not settings of one, and the
    /// straight-cut whine is a thing you can HEAR -- so the bar rides with rpm
    /// rather than sitting at a fixed length.
    func drawGearboxBadge(_ ctx: GraphicsContext, at p: CGPoint,
                          bay b: EngineBay, rpm: Double) {
        let whine = b.straightCutWhine
        let tint: Color = whine > 0 ? Color(red: 0.95, green: 0.78, blue: 0.35)
                                    : Color(white: 0.58)
        ctx.draw(Text(b.gearboxLabel)
            .font(.system(size: 9, design: .monospaced))
            .foregroundColor(tint), at: p, anchor: .leading)
        guard whine > 0 else { return }
        // straight-cut: say so, and show it rising with the revs
        let rf = min(max(rpm / max(b.engine.redlineRpm, 1), 0), 1)
        let w: CGFloat = 34
        let x = p.x + 78, y = p.y - 2
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w, height: 4),
                      cornerRadius: 2), with: .color(.white.opacity(0.13)))
        ctx.fill(Path(roundedRect: CGRect(x: x, y: y,
                                          width: w * CGFloat(rf * whine * 2.0),
                                          height: 4), cornerRadius: 2),
                 with: .color(tint))
        ctx.draw(Text("straight-cut").font(.system(size: 8))
            .foregroundColor(tint.opacity(0.85)),
                 at: CGPoint(x: x + w + 4, y: p.y), anchor: .leading)
    }

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
