//
//  App.swift
//  The entry point -- Xcode's, not the package's.
//
//  Kept in its own file and EXCLUDED from the SwiftPM target: an @main
//  attribute cannot live in a library, but the rest of the app should still
//  type-check from the command line.  That way a broken view or a renamed API
//  is caught by `swift build` on the way past, instead of by opening Xcode and
//  finding out there.
//

import SwiftUI

@main
struct PyEngineSimApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
