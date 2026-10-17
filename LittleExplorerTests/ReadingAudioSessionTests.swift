#if canImport(LiveKit) && os(iOS)
import AVFoundation
import LiveKit
import XCTest
@testable import LittleExplorer

/// Runs LiveKit's real session observer against the real `AVAudioSession`. Milo's call is simulated
/// with the engine callbacks LiveKit's audio engine sends when a call starts and ends.
@MainActor
final class ReadingAudioSessionTests: XCTestCase {
    private var observer: AudioSessionEngineObserver!
    private var reading: LiveKitReadingAudioSession!
    private let engine = AVAudioEngine()
    private var session: AVAudioSession { .sharedInstance() }

    override func setUp() async throws {
        observer = AudioSessionEngineObserver()
        reading = LiveKitReadingAudioSession(session: observer)
        try session.setCategory(.soloAmbient, mode: .default)
    }

    override func tearDown() async throws {
        reading.end()
        miloOff()
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
        try? session.setCategory(.soloAmbient, mode: .default)
    }

    private func miloOn() {
        XCTAssertEqual(observer.engineWillEnable(engine, isPlayoutEnabled: true, isRecordingEnabled: true), 0)
    }

    /// Milo's mic muted while the book reads (LiveKit's "restart" mute mode drops recording only).
    private func miloMicMuted() {
        XCTAssertEqual(observer.engineDidDisable(engine, isPlayoutEnabled: true, isRecordingEnabled: false), 0)
    }

    /// Toggle, idle nap, daily limit and network drop all end with LiveKit's engine stopping.
    private func miloOff() {
        _ = observer.engineDidDisable(engine, isPlayoutEnabled: false, isRecordingEnabled: false)
    }

    private func assertReadingSession(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(session.category, .playback, file: file, line: line)
        XCTAssertEqual(session.mode, .spokenAudio, file: file, line: line)
    }

    private func assertCallSession(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(session.category, .playAndRecord, file: file, line: line)
        XCTAssertTrue([.videoChat, .voiceChat].contains(session.mode), "\(session.mode)", file: file, line: line)
    }

    /// The cause of the bug: LiveKit deactivates the session after the call but leaves the call category.
    func testCallCategoryOutlivesMilo() {
        miloOn()
        assertCallSession()
        miloOff()
        assertCallSession()
    }

    func testReadingBeforeMiloUsesPlaybackSession() {
        reading.begin()
        XCTAssertTrue(reading.isHeld)
        assertReadingSession()
        reading.end()
        XCTAssertFalse(reading.isHeld)
    }

    /// The reported bug: read, use Milo, read again.
    func testReadingAfterMiloUsesPlaybackSessionAgain() {
        reading.begin()
        assertReadingSession()
        reading.end()

        miloOn()
        miloOff()

        reading.begin()
        assertReadingSession()
        reading.end()
    }

    func testReadingWhileMiloIsOnKeepsTheCallSession() {
        miloOn()
        reading.begin()
        assertCallSession()
        miloMicMuted()
        assertCallSession()
        reading.end()
        assertCallSession()
    }

    func testMiloEndingWhileReadingSwitchesToPlayback() {
        miloOn()
        reading.begin()
        miloMicMuted()
        miloOff()
        assertReadingSession()
        reading.end()
    }

    func testMiloStartingWhileReadingTakesTheCallSession() {
        reading.begin()
        miloOn()
        assertCallSession()
        miloOff()
        assertReadingSession()
    }

    func testRepeatedReadAndMiloCycles() {
        for _ in 0..<3 {
            reading.begin()
            assertReadingSession()
            reading.end()
            miloOn()
            assertCallSession()
            miloOff()
        }
        reading.begin()
        assertReadingSession()
    }

    func testBeginIsIdempotent() {
        reading.begin()
        reading.begin()
        reading.end()
        XCTAssertFalse(reading.isHeld)
        miloOn()
        miloOff()
        // A leaked second requirement would keep LiveKit's playout on and the session active.
        reading.begin()
        assertReadingSession()
    }
}
#endif
