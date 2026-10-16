import AVFoundation
import SwiftUI
import XCTest
@testable import LittleExplorer

@MainActor
final class FakeSpeechEngine: SpeechEngine {
    var events: ReadAloudController.EngineEvents?
    private(set) var spoken: [(text: String, rate: Float)] = []
    private(set) var stopCount = 0

    func speak(_ text: String, voice: AVSpeechSynthesisVoice?, rate: Float) { spoken.append((text, rate)) }
    func stop() { stopCount += 1 }

    func word(_ range: NSRange) { events?.willSpeak(range) }
    func finish() { events?.finished() }
}

@MainActor
final class ReadAloudControllerTests: XCTestCase {
    private var engine: FakeSpeechEngine!
    private var reader: ReadAloudController!
    private var speakingChanges: [Bool] = []

    override func setUp() async throws {
        engine = FakeSpeechEngine()
        reader = ReadAloudController(engine: engine)
        speakingChanges = []
        reader.onSpeakingChanged = { [unowned self] in speakingChanges.append($0) }
    }

    func testTapReadsSlowlyAndTapAgainStops() {
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        XCTAssertTrue(reader.isSpeaking(pageId: "paris/welcome"))
        XCTAssertEqual(engine.spoken.map(\.text), ["This is Paris."])
        XCTAssertLessThan(engine.spoken[0].rate, AVSpeechUtteranceDefaultSpeechRate)

        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        XCTAssertFalse(reader.isSpeaking)
        XCTAssertEqual(engine.stopCount, 1)
        XCTAssertEqual(speakingChanges, [true, false])
    }

    func testTracksSpokenWordAndClearsWhenFinished() {
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        engine.word(NSRange(location: 8, length: 5))
        XCTAssertEqual(reader.spokenRange, NSRange(location: 8, length: 5))
        engine.finish()
        XCTAssertFalse(reader.isSpeaking)
        XCTAssertNil(reader.spokenRange)
        XCTAssertEqual(speakingChanges, [true, false])
    }

    func testPageTurnStopsReading() {
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        reader.pageDidChange(to: "paris/welcome")
        XCTAssertTrue(reader.isSpeaking)
        reader.pageDidChange(to: "paris/bonjour")
        XCTAssertFalse(reader.isSpeaking)
        XCTAssertEqual(engine.stopCount, 1)
    }

    func testStopWhenIdleDoesNothing() {
        reader.stop()
        reader.pageDidChange(to: "paris/bonjour")
        XCTAssertEqual(engine.stopCount, 0)
        XCTAssertEqual(speakingChanges, [])
    }

    func testReadingAnotherPageSwitchesWithoutTogglingMic() {
        reader.toggle(pageId: "paris/welcome", text: "One.")
        reader.toggle(pageId: "paris/bonjour", text: "Two.")
        XCTAssertTrue(reader.isSpeaking(pageId: "paris/bonjour"))
        XCTAssertEqual(engine.spoken.map(\.text), ["One.", "Two."])
        XCTAssertEqual(speakingChanges, [true, true])
    }

    func testBestVoiceIsEnglish() {
        let voice = ReadAloudController.bestVoice(from: AVSpeechSynthesisVoice.speechVoices())
        XCTAssertNotNil(voice)
        XCTAssertTrue(voice?.language.hasPrefix("en") ?? false)
    }

    func testHighlightMarksOnlyTheSpokenWord() {
        let text = "This is Paris."
        let marked = PageView.highlighted(text, range: NSRange(location: 8, length: 5), tint: .blue)
        let runs = marked.runs.filter { $0.backgroundColor != nil }
        XCTAssertEqual(runs.count, 1)
        XCTAssertEqual(runs.first.map { String(marked[$0.range].characters) }, "Paris")
        XCTAssertEqual(String(PageView.highlighted(text, range: nil, tint: .blue).characters), text)
        XCTAssertTrue(PageView.highlighted(text, range: nil, tint: .blue).runs.allSatisfy { $0.backgroundColor == nil })
    }
}

@MainActor
final class ReadAloudVoiceModeTests: XCTestCase {
    func testMicIsMutedWhileTheAppReadsAloud() async {
        let transport = MockTransport()
        let voice = VoiceModeController { transport }
        await voice.turnOn()
        transport.send(.buddyActivity(.listening))

        await voice.appReadAloudDidChange(true)
        XCTAssertEqual(voice.statusText, "Pip waits while the book reads")
        await voice.appReadAloudDidChange(false)
        XCTAssertEqual(transport.microphoneChanges, [false, true])
        XCTAssertEqual(voice.statusText, "Pip is listening")
    }

    func testReadingBeforeVoiceModeMutesMicOnceConnected() async {
        let transport = MockTransport()
        let voice = VoiceModeController { transport }
        await voice.appReadAloudDidChange(true)
        XCTAssertEqual(transport.microphoneChanges, [])
        await voice.turnOn()
        XCTAssertEqual(transport.microphoneChanges, [false])
        await voice.appReadAloudDidChange(false)
        XCTAssertEqual(transport.microphoneChanges, [false, true])
    }
}
