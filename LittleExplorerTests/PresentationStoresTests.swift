import XCTest
@testable import LittleExplorer

/// On a Mac, a sheet or cover that has a `.sheet` of its own only sees the stores injected on it directly;
/// reading any other one crashes (1.0 crashed opening a book). These checks keep every presentation
/// passing every store (see AppStores.swift).
final class PresentationStoresTests: XCTestCase {
    private static let appDir = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("LittleExplorer")

    /// Files allowed a bare presentation, and why.
    private static let plainPresentations: Set<String> = [
        // In PageCurlView's own UIHostingController, outside the app's environment; its credit sheet reads no store.
        "PageView.swift",
    ]

    private func sources() throws -> [(name: String, text: String)] {
        let files = try XCTUnwrap(FileManager.default.enumerator(at: Self.appDir, includingPropertiesForKeys: nil))
        let swift = files.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        XCTAssertGreaterThan(swift.count, 20, "the app's sources are at \(Self.appDir.path)")
        return try swift.map { ($0.lastPathComponent, try String(contentsOf: $0, encoding: .utf8)) }
    }

    private func matches(_ pattern: String, in text: String) throws -> [String] {
        let regex = try NSRegularExpression(pattern: pattern)
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).map {
            String(text[Range($0.range(at: $0.numberOfRanges - 1), in: text)!])
        }
    }

    func testEveryPresentationPassesTheStores() throws {
        for file in try sources() where file.name != "AppStores.swift" && !Self.plainPresentations.contains(file.name) {
            let bare = try matches(#"\.(sheet|fullScreenCover|popover)\("#, in: file.text)
            XCTAssertEqual(bare, [], "\(file.name): present with appSheet / appFullScreenCover so the presented view gets every store")
        }
    }

    func testAppStoresHoldEveryEnvironmentStore() throws {
        let all = try sources()
        let appStores = try XCTUnwrap(all.first { $0.name == "AppStores.swift" }?.text)
        let structBody = try XCTUnwrap(appStores.range(of: "struct AppStores {").map { appStores[$0.upperBound...] })
        let held = Set(try matches(#"let \w+: (\w+)\n"#, in: String(structBody.prefix { $0 != "}" })))
        let names = try matches(#"let (\w+): \w+\n"#, in: String(structBody.prefix { $0 != "}" }))
        for name in names {
            XCTAssertTrue(appStores.contains("environment(stores.\(name))"), "appStores(_:) doesn't inject \(name)")
        }
        let read = Set(try all.flatMap { try matches(#"@Environment\((\w+)\.self\)"#, in: $0.text) })
        XCTAssertFalse(read.isEmpty)
        XCTAssertEqual(read.subtracting(held), [], "add these to AppStores (and appStores / AppStoresReader)")
    }
}
