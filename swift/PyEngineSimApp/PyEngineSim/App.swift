//
//  App.swift
//  The entry point -- Xcode's, not the package's.
//
//  This is the ONLY source file the Xcode target needs.  Everything else lives
//  in the package, so adding the app to Xcode is one file plus one local
//  package reference -- and the whole UI still type-checks from the command
//  line, which is where a broken view should be caught rather than by opening
//  a window and finding out.
//
//  An @main attribute cannot live in a library, which is the only reason this
//  file is outside the package at all.
//

import SwiftUI
import PyEngineSimUI

@main
struct PyEngineSimApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
