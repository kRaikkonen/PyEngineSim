//
//  LayerStackTests.swift
//
//  The stage names must match the Python's `Synthesizer.STAGES` exactly, or a
//  layer switched off in one implementation is a different layer in the other
//  -- and the two would drift while both looking correct.
//

import XCTest
@testable import EngineSimCore

final class LayerStackTests: XCTestCase {
    func testStageNamesMatchThePython() {
        // transcribed from engine_sim/audio.py: Synthesizer.STAGES
        let python = ["voiced", "block", "pipes", "header", "head/port",
                      "catalytic", "standing-wave", "resonator", "muffler",
                      "valve bypass", "induction+gears", "wall de-honk",
                      "metal ring", "megaphone", "thunder", "reflection",
                      "radiation", "tailpipe exit", "EQ", "cabin/room"]
        XCTAssertEqual(Stage.allCases.map(\.rawValue), python)
    }

    func testHidingALayerPassesItsInputThrough() {
        let st = LayerStack()
        let a = [1.0, 2.0, 3.0], b = [9.0, 9.0, 9.0], c = [4.0, 5.0, 6.0]

        XCTAssertEqual(st.tap(.pipes, a), a)          // all visible: unchanged
        XCTAssertEqual(st.tap(.header, b), b)

        st.set(.header, false)
        let fresh = LayerStack()
        fresh.set(.header, false)
        XCTAssertEqual(fresh.tap(.pipes, a), a)
        // header hidden -> what pipes produced comes back out
        XCTAssertEqual(fresh.tap(.header, b), a)
        // ...and the next stage sees THAT, not the discarded b
        fresh.set(.catalytic, false)
        XCTAssertEqual(fresh.tap(.catalytic, c), a)
    }

    func testSoloAndBack() {
        let st = LayerStack()
        XCTAssertEqual(st.hiddenCount, 0)
        st.solo(.muffler)
        XCTAssertEqual(st.hiddenCount, Stage.allCases.count - 1)
        XCTAssertTrue(st.isVisible(.muffler))
        st.solo(.muffler)                              // solo again -> show all
        XCTAssertEqual(st.hiddenCount, 0)
    }

    func testAdditiveLayerGoesSilentRatherThanPassingThrough() {
        // the bay bus is added to the mix, not filtered in series, so hiding
        // it has to mean "not fitted", not "passed through"
        let st = LayerStack()
        let bay = [1.0, -1.0, 0.5]
        XCTAssertEqual(st.gate(.inductionGears, bay), bay)
        st.set(.inductionGears, false)
        XCTAssertEqual(st.gate(.inductionGears, bay), [0.0, 0.0, 0.0])
    }

    /// A bus whose block length changes must not splice an old buffer in.
    func testLengthChangeIsSafe() {
        let st = LayerStack()
        st.set(.header, false)
        _ = st.tap(.pipes, [1.0, 2.0])
        XCTAssertEqual(st.tap(.header, [7.0, 8.0, 9.0]), [7.0, 8.0, 9.0])
    }
}
