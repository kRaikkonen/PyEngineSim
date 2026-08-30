// swift-tools-version:5.7
//
// EngineSimCore -- the second (Swift) implementation of PyEngineSim's audio.
//
// A LIBRARY with tests, not an app: the DSP has to be provable against the
// Python it is reproducing before any of it is worth putting on a phone, and
// a SwiftPM package builds and tests from the command line, so that can
// happen without an Xcode window in the loop.  The app target comes later and
// just imports this.
import PackageDescription

let package = Package(
    name: "EngineSimCore",
    // iOS 16 because the UI target uses @StateObject and structured
    // concurrency; the DSP itself would run on far less, but one platform
    // line covers the whole package and there is nothing on iOS 13 to serve.
    // macOS 13 only so the UI module keeps type-checking from the command
    // line; nothing ships there.  iOS 16 is the real target.
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [
        .library(name: "EngineSimCore", targets: ["EngineSimCore"]),
        .library(name: "SwiftEngineSimUI", targets: ["SwiftEngineSimUI"]),
    ],
    targets: [
        .target(name: "EngineSimCore"),
        // The app's own sources, as a LIBRARY so `swift build` type-checks
        // them.  Xcode owns the actual app target (and the signing), but a
        // renamed API or a broken view should fail here first rather than
        // when a window is opened.  App.swift carries the @main and is
        // excluded, because an entry point cannot live in a library.
        .target(
            name: "SwiftEngineSimUI",
            dependencies: ["EngineSimCore"],
            path: "Sources/SwiftEngineSimUI"
        ),
        .testTarget(
            name: "EngineSimCoreTests",
            dependencies: ["EngineSimCore"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
