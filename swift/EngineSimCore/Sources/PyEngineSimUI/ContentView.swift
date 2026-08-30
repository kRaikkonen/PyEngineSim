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

struct ContentView: View {
    @StateObject private var model = AppModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                readouts
                if model.manual { manualControls }
                engineChooser
                mapping
                link
                if let e = model.errorText {
                    Text(e).font(.footnote).foregroundColor(.red)
                }
            }
            .padding(20)
        }
        .onAppear { model.boot() }
    }

    // ------------------------------------------------------------- header
    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(model.engineName).font(.title2).bold()
            HStack(spacing: 10) {
                Label(model.linkState, systemImage: model.manual
                      ? "slider.horizontal.3" : "antenna.radiowaves.left.and.right")
                    .font(.caption)
                    .foregroundColor(model.linkState == "LIVE" ? .green : .secondary)
                if model.pollHz > 0 {
                    Text(String(format: "%.0f Hz", model.pollHz))
                        .font(.caption).foregroundColor(.secondary)
                }
                if model.shifting {
                    Text("SHIFT").font(.caption).bold().foregroundColor(.orange)
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

    // ------------------------------------------------------ engine chooser
    private var engineChooser: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("ENGINE").font(.caption2).foregroundColor(.secondary)
            // a horizontal strip of BUTTONS, not a picker: a wheel cannot be
            // dismissed one-handed and is unusable while driving
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(model.engineKeys, id: \.self) { key in
                        Button(key) { model.selectEngine(key) }
                            .font(.caption)
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(key == model.engineKey
                                        ? Color.accentColor.opacity(0.25)
                                        : Color.secondary.opacity(0.12))
                            .cornerRadius(7)
                    }
                }
            }
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

    // ---------------------------------------------------------------- link
    private var link: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("SOURCE").font(.caption2).foregroundColor(.secondary)
            HStack(spacing: 8) {
                Button("sliders") { model.useManual(true) }
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(model.manual ? Color.accentColor.opacity(0.25)
                                             : Color.secondary.opacity(0.12))
                    .cornerRadius(7)
                Button("the car") { model.useManual(false) }
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(!model.manual ? Color.accentColor.opacity(0.25)
                                              : Color.secondary.opacity(0.12))
                    .cornerRadius(7)
            }
            .font(.caption)
            Text("dongle at \(model.host):\(String(model.port))")
                .font(.caption2).foregroundColor(.secondary)
        }
    }
}
