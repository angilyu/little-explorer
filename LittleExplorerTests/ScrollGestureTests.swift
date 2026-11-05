import XCTest
@testable import LittleExplorer

/// On iOS 18 and later a SwiftUI `DragGesture` on content inside a scroll view, even a simultaneous one,
/// stops the scroll view starting a drag there. The star calendar's month swipe did that: at the end of the
/// Passport (landscape iPad: the calendar fills the screen) he couldn't scroll back up and the page just
/// bounced. These checks keep drags out of the app's scrolling content.
final class ScrollGestureTests: XCTestCase {
    private static let appDir = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("LittleExplorer")

    /// Files allowed a SwiftUI drag, and how many.
    private static let allowedDrags: [String: Int] = [
        // The calendar's month swipe on iOS 17 only; iOS 18 and later use UIKit swipes (SideSwipe).
        "StarCalendarView.swift": 1,
    ]

    private func sources() throws -> [String: String] {
        let files = try XCTUnwrap(FileManager.default.enumerator(at: Self.appDir, includingPropertiesForKeys: nil))
        let swift = files.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        XCTAssertGreaterThan(swift.count, 20, "the app's sources are at \(Self.appDir.path)")
        return Dictionary(uniqueKeysWithValues: try swift.map { ($0.lastPathComponent, try String(contentsOf: $0, encoding: .utf8)) })
    }

    func testNoNewSwiftUIDrags() throws {
        for (name, text) in try sources() {
            let drags = text.components(separatedBy: "DragGesture(").count - 1
            XCTAssertLessThanOrEqual(
                drags, Self.allowedDrags[name] ?? 0,
                "\(name): a SwiftUI DragGesture inside a ScrollView blocks scrolling on iOS 18+; use a UIKit recognizer (see SideSwipe)"
            )
        }
    }

    func testCalendarMonthSwipeUsesUIKitOnIOS18() throws {
        let calendar = try XCTUnwrap(sources()["StarCalendarView.swift"])
        let swipe = try XCTUnwrap(calendar.range(of: "func pageSwipe(")).upperBound
        let body = calendar[swipe...]
        let modern = try XCTUnwrap(body.range(of: "if #available(iOS 18.0, *) {"))
        let fallback = try XCTUnwrap(body.range(of: "} else {"))
        let iOS18 = body[modern.upperBound..<fallback.lowerBound]
        XCTAssertTrue(iOS18.contains("SideSwipe(direction: .left)") && iOS18.contains("SideSwipe(direction: .right)"))
        XCTAssertFalse(iOS18.contains("DragGesture"))
        XCTAssertTrue(calendar.contains("shouldRecognizeSimultaneouslyWith"), "the swipe must run alongside the Passport's scroll")
    }
}
