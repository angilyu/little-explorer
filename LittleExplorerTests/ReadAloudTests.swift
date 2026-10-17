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
final class FakeNarrationPlayer: NarrationPlayer {
    var events: ReadAloudController.EngineEvents?
    var canPlay = true
    private(set) var played: [String] = []
    private(set) var stopCount = 0

    func play(_ narration: Narration) -> Bool {
        guard canPlay else { return false }
        played.append(narration.file)
        return true
    }
    func stop() { stopCount += 1 }

    func word(_ range: NSRange) { events?.willSpeak(range) }
    func finish() { events?.finished() }
}

@MainActor
final class FakeReadingAudioSession: ReadingAudioSession {
    private(set) var calls: [String] = []
    private(set) var isHeld = false

    func begin() {
        calls.append("begin")
        isHeld = true
    }

    func end() {
        calls.append("end")
        isHeld = false
    }
}

@MainActor
final class ReadAloudControllerTests: XCTestCase {
    private var engine: FakeSpeechEngine!
    private var reader: ReadAloudController!
    private var audio: FakeReadingAudioSession!
    private var speakingChanges: [Bool] = []

    override func setUp() async throws {
        engine = FakeSpeechEngine()
        audio = FakeReadingAudioSession()
        reader = ReadAloudController(engine: engine, player: FakeNarrationPlayer(), narrations: { _, _ in nil }, audioSession: audio)
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

    func testHoldsTheAudioSessionOnlyWhileReading() {
        reader.toggle(pageId: "paris/welcome", text: "One.")
        XCTAssertTrue(audio.isHeld)
        reader.toggle(pageId: "paris/bonjour", text: "Two.")
        XCTAssertTrue(audio.isHeld)
        engine.finish()
        XCTAssertFalse(audio.isHeld)

        reader.toggle(pageId: "paris/welcome", text: "One.")
        reader.toggle(pageId: "paris/welcome", text: "One.")
        reader.toggle(pageId: "paris/welcome", text: "One.")
        reader.pageDidChange(to: "paris/bonjour")
        XCTAssertFalse(audio.isHeld)
        XCTAssertEqual(audio.calls, ["begin", "begin", "end", "begin", "end", "begin", "end"])
    }

    func testTakesTheAudioSessionBeforePlaying() {
        final class OrderPlayer: NarrationPlayer {
            var events: ReadAloudController.EngineEvents?
            var heldWhenPlayed: Bool?
            let audio: FakeReadingAudioSession
            init(audio: FakeReadingAudioSession) { self.audio = audio }
            func play(_ narration: Narration) -> Bool {
                heldWhenPlayed = audio.isHeld
                return true
            }
            func stop() {}
        }
        let audio = FakeReadingAudioSession()
        let player = OrderPlayer(audio: audio)
        let clip = Narration(file: "clip", text: "One.", duration: 1, words: [])
        let reader = ReadAloudController(engine: FakeSpeechEngine(), player: player, narrations: { _, _ in clip }, audioSession: audio)
        reader.toggle(pageId: "paris/welcome", text: "One.")
        XCTAssertEqual(player.heldWhenPlayed, true)
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
final class NarrationClipTests: XCTestCase {
    private let clip = Narration(
        file: "read-paris-welcome",
        text: "This is Paris.",
        duration: 2,
        words: [
            .init(location: 0, length: 4, start: 0.2, end: 0.5),
            .init(location: 5, length: 2, start: 0.5, end: 0.7),
            .init(location: 8, length: 5, start: 1.0, end: 1.6),
        ]
    )

    func testPlaysTheNaturalVoiceClipWhenThereIsOne() {
        let engine = FakeSpeechEngine(), player = FakeNarrationPlayer()
        var changes: [Bool] = []
        let reader = ReadAloudController(engine: engine, player: player, narrations: { [clip] id, _ in id == "paris/welcome" ? clip : nil }, audioSession: FakeReadingAudioSession())
        reader.onSpeakingChanged = { changes.append($0) }

        reader.toggle(pageId: "paris/welcome", text: clip.text)
        XCTAssertTrue(reader.usingClip)
        XCTAssertEqual(player.played, ["read-paris-welcome"])
        XCTAssertTrue(engine.spoken.isEmpty)

        player.word(NSRange(location: 8, length: 5))
        XCTAssertEqual(reader.spokenRange, NSRange(location: 8, length: 5))
        player.finish()
        XCTAssertFalse(reader.isSpeaking)
        XCTAssertFalse(reader.usingClip)
        XCTAssertEqual(changes, [true, false])
    }

    func testTapAgainAndPageTurnStopTheClip() {
        let engine = FakeSpeechEngine(), player = FakeNarrationPlayer()
        let reader = ReadAloudController(engine: engine, player: player, narrations: { [clip] _, _ in clip }, audioSession: FakeReadingAudioSession())
        reader.toggle(pageId: "paris/welcome", text: clip.text)
        reader.toggle(pageId: "paris/welcome", text: clip.text)
        XCTAssertFalse(reader.isSpeaking)
        reader.toggle(pageId: "paris/welcome", text: clip.text)
        reader.pageDidChange(to: "paris/bonjour")
        XCTAssertFalse(reader.isSpeaking)
        XCTAssertEqual(player.stopCount, 2)
        XCTAssertEqual(engine.stopCount, 0)
    }

    func testFallsBackToSystemVoiceWithoutAClip() {
        let engine = FakeSpeechEngine(), player = FakeNarrationPlayer()
        player.canPlay = false
        let reader = ReadAloudController(engine: engine, player: player, narrations: { [clip] id, _ in id == "paris/welcome" ? clip : nil }, audioSession: FakeReadingAudioSession())
        reader.toggle(pageId: "paris/welcome", text: clip.text)
        XCTAssertFalse(reader.usingClip)
        XCTAssertEqual(engine.spoken.map(\.text), ["This is Paris."])

        reader.toggle(pageId: "london/bus", text: "A red bus.")
        XCTAssertEqual(engine.spoken.map(\.text), ["This is Paris.", "A red bus."])
        XCTAssertEqual(engine.stopCount, 1)
        XCTAssertEqual(player.stopCount, 0)
    }

    func testWordAtTimeHoldsThroughPauses() {
        XCTAssertNil(clip.word(at: 0.1))
        XCTAssertEqual(clip.word(at: 0.3)?.location, 0)
        XCTAssertEqual(clip.word(at: 0.85)?.location, 5)
        XCTAssertEqual(clip.word(at: 1.9)?.location, 8)
    }

    func testEveryPageHasAMatchingNaturalVoiceClip() throws {
        let library = NarrationLibrary.bundled
        let books = try BookLibrary.loadBundled()
        var checked = 0
        for book in books {
            for page in book.pages {
                let id = HeartStore.id(bookId: book.id, pageId: page.id)
                let clip = try XCTUnwrap(library.narration(for: id, text: page.text), "no clip for \(id): run scripts/generate_narration.py")
                let text = page.text as NSString
                XCTAssertFalse(clip.words.isEmpty, id)
                XCTAssertGreaterThan(clip.duration, 1, id)
                var lastStart = -1.0
                for word in clip.words {
                    XCTAssertLessThanOrEqual(word.location + word.length, text.length, id)
                    XCTAssertFalse(text.substring(with: word.range).contains(" "), id)
                    XCTAssertGreaterThanOrEqual(word.start, lastStart, id)
                    XCTAssertLessThanOrEqual(word.start, clip.duration, id)
                    lastStart = word.start
                }
                checked += 1
            }
        }
        XCTAssertEqual(checked, 225)
    }

    func testChangedSentenceDoesNotUseAStaleClip() throws {
        let library = NarrationLibrary.bundled
        XCTAssertNotNil(library.clips["paris/welcome"])
        XCTAssertNil(library.narration(for: "paris/welcome", text: "Some new sentence."))
        XCTAssertNil(library.narration(for: "paris/no-such-page", text: "Hi."))
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
        XCTAssertEqual(voice.statusText, "Milo waits while the book reads")
        await voice.appReadAloudDidChange(false)
        XCTAssertEqual(transport.microphoneChanges, [false, true])
        XCTAssertEqual(voice.statusText, "Milo is listening")
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
