//
//  ContentView.swift
//  The screen.
//
//  Deliberately plain, and deliberately WITHOUT a picker wheel or a text
//  field.  The v1 app used both and both were unusable in a car: the wheel
//  could not be dismissed, and the keyboard covered the thing you were trying
//  to set.  Everything here is a button or a slider you can hit without
//  looking, which is the only interaction that makes sense at 100 km/h.
//

import SwiftUI
import EngineSimCore

public struct ContentView: View {
    @StateObject private var model = AppModel()
    @State private var pickingEngine = false
    @State private var pendingEngine = ""
    @State private var braking = false
    @State private var blink = false

    public init() {}

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                sourceSwitch
                readouts
                shiftLights
                ignition
                if model.source == .demo { pedalControls }
                engineChooser
                slots
                layers
                // Only worth showing once a REAL car is talking: in demo
                // there is nothing to map from, so the controls would be
                // three ways of saying the same thing.
                if model.source == .live {
                    mapping
                    realCar
                }
                if let e = model.errorText {
                    Text(e).font(.footnote).foregroundColor(.red)
                }
            }
            .padding(20)
        }
        .onAppear {
            model.boot()
            // the limiter flash: a steady strip and a flashing one look
            // completely different at a glance, which is the whole job
            Timer.scheduledTimer(withTimeInterval: 0.07, repeats: true) { _ in
                blink.toggle()
            }
        }
        // A SHEET, not a bare wheel.  The v1 app put a picker inline and it
        // could not be dismissed -- a control you cannot get out of is worse
        // than no control.  A sheet closes three ways: Done, Cancel, or a
        // swipe down, and it cannot cover anything you still need.
        .sheet(isPresented: $pickingEngine) { enginePicker }
    }

    private var enginePicker: some View {
        VStack(spacing: 0) {
            HStack {
                Button("Cancel") { pickingEngine = false }
                Spacer()
                Text("Engine").font(.headline)
                Spacer()
                Button("Done") {
                    model.selectEngine(pendingEngine)
                    pickingEngine = false
                }.fontWeight(.semibold)
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            Divider()
            picker
        }
        // Sized to the wheel plus its bar, so the sheet is not two thirds
        // empty space with the wheel stranded at the top.  The drag indicator
        // is there to say out loud that it can be swiped away -- the v1 picker
        // could not be, and that is worth being obvious about.
        .presentationDetents([.height(292)])
        .presentationDragIndicator(.visible)
    }

    // The wheel itself is iOS-only; the macOS build of this module exists to
    // type-check the app, not to run it, so it gets a plain list instead.
    @ViewBuilder private var picker: some View {
        #if os(iOS)
        Picker("engine", selection: $pendingEngine) {
            ForEach(model.engineKeys, id: \.self) { k in
                Text(model.engineName(k)).tag(k)
            }
        }
        .pickerStyle(.wheel)
        .labelsHidden()
        .frame(maxHeight: .infinity)
        #else
        List(model.engineKeys, id: \.self) { k in
            Button(model.engineName(k)) { pendingEngine = k }
        }
        #endif
    }

    // ------------------------------------------------------------- header
    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(model.engineName).font(.title2).bold()
            HStack(spacing: 10) {
                Label(model.linkState, systemImage: model.sourceIcon)
                    .font(.caption)
                    .foregroundColor(model.linkState == "LIVE" ? .green : .secondary)
                if model.pollHz > 0 {
                    Text(String(format: "%.0f Hz", model.pollHz))
                        .font(.caption).foregroundColor(.secondary)
                }
                if model.shifting {
                    Text("SHIFT").font(.caption).bold().foregroundColor(.orange)
                }
                if model.limiting {
                    Text("LIMITER").font(.caption).bold().foregroundColor(.red)
                }
            }
        }
    }

    // ------------------------------------------------------------ readouts
    private var readouts: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                readout("car", String(format: "%.0f", model.carRPM), "rpm")
                readout("played", String(format: "%.0f", model.simRPM), "rpm")
                readout("gear", model.gear > 0 ? "\(model.gear)" : "N", "")
            }
            HStack {
                readout("pedal", String(format: "%.0f", model.pedal * 100), "%")
                readout("speed", String(format: "%.0f", model.speedKmh), "km/h")
                readout("boost", String(format: "%.2f", model.boostBar), "bar")
            }
            // the number that decides whether this works at all on the phone
            HStack {
                readout("cpu", String(format: "%.0f", model.renderLoad * 100), "%")
                readout("late", "\(model.underruns)", "blocks")
            }
        }
    }

    private func readout(_ label: String, _ value: String,
                         _ unit: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(label.uppercased()).font(.caption2).foregroundColor(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).font(.system(.title3, design: .monospaced)).bold()
                Text(unit).font(.caption2).foregroundColor(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // ------------------------------------------------------ manual controls
    private var manualControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("HAND CONTROL").font(.caption2).foregroundColor(.secondary)
            Slider(value: Binding(get: { model.carRPM },
                                  set: { model.setManualRPM($0) }),
                   in: 600...8000)
            Slider(value: Binding(get: { model.pedal },
                                  set: { model.setManualPedal($0) }),
                   in: 0...1)
        }
    }

    // -------------------------------------------------------- shift lights
    // The strip off a steering wheel: green as it comes on song, blue as the
    // shift point arrives, red past it, and the whole thing FLASHING at the
    // limiter.  It reads at a glance and without a number, which is the point
    // of it existing on a wheel in the first place.
    private var shiftLights: some View {
        HStack(spacing: 5) { ForEach(0..<10, id: \.self) { lamp($0) } }
    }

    /// Split out of the strip above: as one expression the type checker gives
    /// up on it, and a view that will not compile is not a clever view.
    private func lamp(_ i: Int) -> some View {
        let lit = model.revFraction >= 0.62 + Double(i) * 0.038
        let dim = model.limiting && blink
        let fill: Color = lit ? shiftColour(i) : Color.secondary.opacity(0.16)
        return RoundedRectangle(cornerRadius: 3)
            .fill(fill)
            .frame(height: 12)
            .opacity(dim ? 0.25 : 1.0)
    }

    private func shiftColour(_ i: Int) -> Color {
        if i < 4 { return .green }
        if i < 7 { return .blue }
        return .red
    }

    // ------------------------------------------------------------ ignition
    // One lamp per cylinder, lit BY the firing offsets the audio is using --
    // not by a timer that happens to look similar.  So a V12 shows its two
    // banks alternating and a rotary shows three, because that is what the
    // engine's own offsets say.
    private var ignition: some View {
        let lights = model.cylinderLight
        return VStack(alignment: .leading, spacing: 5) {
            Text("IGNITION").font(.caption2).foregroundColor(.secondary)
            let rows = lights.count > 6 ? 2 : 1
            let perRow = (lights.count + rows - 1) / max(rows, 1)
            VStack(spacing: 5) {
                ForEach(0..<rows, id: \.self) { r in
                    HStack(spacing: 7) {
                        ForEach(0..<perRow, id: \.self) { c in
                            let i = r * perRow + c
                            Circle()
                                .fill(i < lights.count
                                      ? Color.orange.opacity(0.15 + 0.85 * lights[i])
                                      : Color.clear)
                                .overlay(Circle().stroke(
                                    i < lights.count
                                    ? Color.secondary.opacity(0.45) : .clear,
                                    lineWidth: 1))
                                .frame(width: 15, height: 15)
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        }
    }

    // ------------------------------------------------------ pedal controls
    // The desktop app's way of driving, on a phone.  A pedal does not tell the
    // engine what revs to be at -- it tells it how much torque to make, and
    // the revs are the CONSEQUENCE.  So it climbs fast in first and slowly in
    // sixth, and it falls on a lift because a shut throttle makes negative
    // torque.  That is the whole difference from the rpm slider.
    //
    // The throttle is a SLIDER, not a hold: you can leave it somewhere.  A
    // press-and-hold is either all or nothing, and most of driving is neither.
    private var pedalControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Button { model.downshift() } label: {
                    Text("DOWN").font(.caption).bold()
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background(Color.secondary.opacity(0.14))
                        .cornerRadius(9)
                }.buttonStyle(.plain)

                VStack(spacing: 1) {
                    Text(model.pedalGear == 0 ? "N" : "\(model.pedalGear)")
                        .font(.system(.title, design: .monospaced)).bold()
                    Text("gear").font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
                .frame(width: 58)

                Button { model.upshift() } label: {
                    Text("UP").font(.caption).bold()
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background(Color.accentColor.opacity(0.22))
                        .cornerRadius(9)
                }.buttonStyle(.plain)
            }

            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text("THROTTLE").font(.caption2).foregroundColor(.secondary)
                    Spacer()
                    Text(String(format: "%.0f%%", model.pedalThrottle * 100))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                }
                Slider(value: Binding(get: { model.pedalThrottle },
                                      set: { model.setPedal($0) }), in: 0...1)
            }

            // The brake acts on the CAR, so in gear it drags the revs down
            // through the ratio -- which is why braking sounds like braking
            // and not like lifting.  Held, because a brake IS held.
            Text(braking ? "BRAKING" : "BRAKE")
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 56)
                .background(braking ? Color.red.opacity(0.38)
                                    : Color.secondary.opacity(0.14))
                .cornerRadius(11)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { _ in
                            if !braking { braking = true; model.setBrake(1.0) }
                        }
                        .onEnded { _ in braking = false; model.setBrake(0.0) }
                )
            Text("neutral revs free · in gear it has to drag the car along")
                .font(.system(size: 10)).foregroundColor(.secondary)
        }
    }

    // ------------------------------------------------------ engine chooser
    private var engineChooser: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("ENGINE").font(.caption2).foregroundColor(.secondary)
            Button {
                pendingEngine = model.engineKey
                pickingEngine = true
            } label: {
                HStack {
                    Text(model.engineName).lineLimit(1)
                    Spacer()
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption2)
                }
                .padding(.horizontal, 12).padding(.vertical, 10)
                .frame(maxWidth: .infinity)
                .background(Color.secondary.opacity(0.12))
                .cornerRadius(8)
            }
            .buttonStyle(.plain)
        }
    }

    // --------------------------------------------------------------- slots
    // Numbered, not named: naming needs a keyboard and a keyboard in a car is
    // not a control.  The gesture carries the meaning instead -- tap to load,
    // hold to save over -- so there is nothing to type and nothing to dismiss.
    private var slots: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("SETUPS").font(.caption2).foregroundColor(.secondary)
                Spacer()
                Button("reset") { model.resetToDefaults() }
                    .font(.caption2).foregroundColor(.orange)
            }
            HStack(spacing: 8) {
                ForEach(0..<model.slotCount, id: \.self) { i in
                    let filled = model.slotFilled(i)
                    Text("\(i + 1)")
                        .font(.system(.body, design: .monospaced)).bold()
                        .frame(maxWidth: .infinity, minHeight: 38)
                        .background(filled ? Color.accentColor.opacity(0.22)
                                           : Color.secondary.opacity(0.10))
                        .foregroundColor(filled ? .primary : .secondary)
                        .cornerRadius(8)
                        .contentShape(Rectangle())
                        .onTapGesture { if filled { model.loadSlot(i) } }
                        .onLongPressGesture { model.saveSlot(i) }
                }
            }
            Text("tap to load · hold to save here · reset does not clear them")
                .font(.system(size: 10)).foregroundColor(.secondary)
        }
    }

    // -------------------------------------------------------------- layers
    // The chain as a stack you can switch off, one stage at a time -- the
    // same idea as the eye column in an image editor.  Hiding a stage passes
    // its input straight through, so what you hear is exactly what that stage
    // contributes; it is the only honest way to answer "is this earning its
    // place?".
    //
    // Tap to hide, LONG PRESS to solo.  Both are one-handed and neither needs
    // a second screen, which is the only kind of control worth having here.
    private var layers: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("CHAIN").font(.caption2).foregroundColor(.secondary)
                Spacer()
                if !model.hidden.isEmpty {
                    Text("\(model.hidden.count) hidden")
                        .font(.caption2).foregroundColor(.orange)
                    Button("show all") { model.showAllLayers() }
                        .font(.caption2)
                }
            }
            // fixed columns so a stage never moves: you learn where `muffler`
            // is and it stays there
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(),
                                                         spacing: 6),
                                     count: 3), spacing: 6) {
                ForEach(model.stages, id: \.self) { s in
                    let on = model.isVisible(s)
                    Text(s.rawValue)
                        .font(.system(size: 11))
                        .lineLimit(1).minimumScaleFactor(0.7)
                        .frame(maxWidth: .infinity, minHeight: 30)
                        .background(on ? Color.accentColor.opacity(0.22)
                                       : Color.secondary.opacity(0.10))
                        .foregroundColor(on ? .primary : .secondary)
                        .overlay(RoundedRectangle(cornerRadius: 6)
                            .stroke(on ? Color.accentColor.opacity(0.5)
                                       : Color.clear, lineWidth: 1))
                        .cornerRadius(6)
                        .contentShape(Rectangle())
                        .onTapGesture { model.toggle(s) }
                        .onLongPressGesture { model.solo(s) }
                }
            }
            Text("tap to mute a stage · hold to hear it alone")
                .font(.system(size: 10)).foregroundColor(.secondary)
        }
    }

    // ------------------------------------------------------------- mapping
    private var mapping: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("RPM MAPPING").font(.caption2).foregroundColor(.secondary)
            HStack(spacing: 8) {
                ForEach(RpmMap.Mode.allCases, id: \.self) { m in
                    Button(m.rawValue) { model.setMapMode(m) }
                        .font(.caption)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(m == model.mapMode
                                    ? Color.accentColor.opacity(0.25)
                                    : Color.secondary.opacity(0.12))
                        .cornerRadius(7)
                }
            }
            // what the mapping WILL do, before driving off with it
            HStack(spacing: 12) {
                ForEach(Array(model.mapPreview().enumerated()), id: \.offset) { _, p in
                    VStack(spacing: 1) {
                        Text(String(format: "%.0f", p.car))
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(.secondary)
                        Text(String(format: "%.0f", p.sim))
                            .font(.system(size: 9, design: .monospaced))
                    }
                }
            }
        }
    }

    // ------------------------------------------------------- the real car
    // What the mapping stretches FROM.  Steppers, not a text field: a
    // keyboard is not a control you can use while driving, and 100 rpm is
    // finer than anyone knows their own redline anyway.
    private var realCar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("YOUR CAR").font(.caption2).foregroundColor(.secondary)
                Spacer()
                if model.learnRange {
                    Text("learning").font(.caption2).foregroundColor(.green)
                } else {
                    Button("relearn") { model.relearnRange() }
                        .font(.caption2)
                }
            }
            revRow("idle", model.carIdle,
                   minus: { model.nudgeIdle(-50) },
                   plus: { model.nudgeIdle(50) })
            revRow("redline", model.carRedline,
                   minus: { model.nudgeRedline(-100) },
                   plus: { model.nudgeRedline(100) })
            HStack(spacing: 8) {
                if model.seenMax > 0 {
                    Text(String(format: "seen %.0f", model.seenMax))
                        .font(.caption2).foregroundColor(.secondary)
                    Button("use it") { model.useSeenRedline() }
                        .font(.caption2)
                }
                Spacer()
            }
            Text("what the mapping stretches from · setting it by hand stops "
                 + "the learning, so your number sticks")
                .font(.system(size: 10)).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func revRow(_ label: String, _ value: Double,
                        minus: @escaping () -> Void,
                        plus: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Text(label).font(.caption)
                .frame(width: 58, alignment: .leading)
            Button(action: minus) {
                Image(systemName: "minus")
                    .frame(width: 44, height: 34)
                    .background(Color.secondary.opacity(0.14))
                    .cornerRadius(7)
            }.buttonStyle(.plain)
            Text(String(format: "%.0f", value))
                .font(.system(.title3, design: .monospaced))
                .frame(maxWidth: .infinity)
            Button(action: plus) {
                Image(systemName: "plus")
                    .frame(width: 44, height: 34)
                    .background(Color.secondary.opacity(0.14))
                    .cornerRadius(7)
            }.buttonStyle(.plain)
        }
    }

    // -------------------------------------------------------------- source
    // At the TOP, because it is the first decision: everything below it means
    // something different depending on which of the three is driving.
    private var sourceSwitch: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                ForEach([AppModel.Source.demo, .live], id: \.self) { s in
                    Button(s == .live ? "the car" : s.rawValue) {
                        model.setSource(s)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(model.source == s
                                ? Color.accentColor.opacity(0.25)
                                : Color.secondary.opacity(0.12))
                    .cornerRadius(7)
                }
                Spacer()
                Toggle("pops", isOn: Binding(get: { model.popsOn },
                                             set: { model.setPops($0) }))
                    .labelsHidden()
                Text("pops").font(.caption2).foregroundColor(.secondary)
            }
            .font(.caption)
            if model.source == .live {
                Text("dongle at \(model.host):\(String(model.port))")
                    .font(.caption2).foregroundColor(.secondary)
            }
            Text(model.audioInfo)
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.secondary)
        }
    }
}
