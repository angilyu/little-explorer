import AVFoundation
import XCTest
@testable import LittleExplorer

@MainActor
final class MiloIntroTests: XCTestCase {
    private final class FakeAudioSession: ReadingAudioSession {
        private(set) var begins = 0
        private(set) var ends = 0
        func begin() { begins += 1 }
        func end() { ends += 1 }
    }

    func testTheRecordedHelloIsBundledAndShort() throws {
        let url = try XCTUnwrap(ContentFiles.bundleOnly(.main).url(ClipIntroPlayer.file))
        let player = try AVAudioPlayer(contentsOf: url)
        XCTAssertGreaterThan(player.duration, 3)
        XCTAssertLessThan(player.duration, 8, "the mic stays off while it plays")
    }

    func testTapToHelloIsInstant() throws {
        let session = FakeAudioSession()
        let intro = ClipIntroPlayer(files: .bundleOnly(.main), audioSession: session)
        let start = Date()
        XCTAssertTrue(intro.play {})
        let elapsed = Date().timeIntervalSince(start)
        print("Milo intro: tap→play \(String(format: "%.0f", elapsed * 1000)) ms")
        XCTAssertLessThan(elapsed, 0.25)
        intro.stop()
        XCTAssertEqual(session.begins, 1)
        XCTAssertEqual(session.ends, 1)
    }

    func testMissingClipReportsItCannotPlay() {
        let empty = ContentFiles(cacheDir: URL(fileURLWithPath: "/dev/null/none"), bundle: Bundle(for: Self.self))
        let intro = ClipIntroPlayer(files: empty, audioSession: FakeAudioSession())
        XCTAssertFalse(intro.play {})
    }
}
