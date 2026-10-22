import AVFoundation
import Observation
import os

/// What the read-aloud button needs from a speech engine. `AVSpeechSynthesizer` in the app,
/// a recorder in tests.
@MainActor
protocol SpeechEngine: AnyObject {
    var events: ReadAloudController.EngineEvents? { get set }
    func speak(_ text: String, voice: AVSpeechSynthesisVoice?, rate: Float)
    func stop()
}

/// Reads the page sentence aloud (no network) and reports which word is being spoken so the page can
/// highlight it. Pages use their bundled natural-voice clip when there is one for the current sentence,
/// otherwise the on-device system voice. Tapping again stops. Works with or without voice mode;
/// `onSpeakingChanged` lets voice mode pause the mic so Milo doesn't hear the app, and `onUse` reports each
/// start, finish and early stop (for the reading activity, see ``ReadingActivity``).
@MainActor
@Observable
final class ReadAloudController {
    struct EngineEvents {
        var willSpeak: (NSRange) -> Void
        var finished: () -> Void
    }

    /// One start, natural finish or early stop of reading a page aloud.
    struct Use: Equatable {
        enum Outcome: String { case started, finished, stopped }
        /// `clip` is the bundled natural-voice recording, `system` the device's own voice; both highlight words.
        enum Mode: String { case clip, system }

        /// The page, as `NarrationLibrary.key(bookId:pageId:)`.
        let pageId: String
        let outcome: Outcome
        let mode: Mode
        /// How long it played (nil for `started`).
        let seconds: Double?
    }

    /// About 80% of the default rate: slow enough to follow word by word, still natural.
    static let rate: Float = AVSpeechUtteranceDefaultSpeechRate * 0.8
    nonisolated private static let log = Logger(subsystem: "com.angilyu.littleexplorer", category: "readaloud")

    private(set) var pageId: String?
    private(set) var text: String?
    /// The word being spoken right now, in `text`'s UTF-16 offsets.
    private(set) var spokenRange: NSRange?

    /// True while a bundled natural-voice clip is playing (false for the system voice).
    private(set) var usingClip = false

    @ObservationIgnored var onSpeakingChanged: ((Bool) -> Void)?
    @ObservationIgnored var onUse: ((Use) -> Void)?
    @ObservationIgnored private var startedAt: Date?
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let engine: SpeechEngine
    @ObservationIgnored private let player: NarrationPlayer
    @ObservationIgnored private let narrations: (_ pageId: String, _ text: String) -> Narration?
    @ObservationIgnored private let audioSession: ReadingAudioSession
    @ObservationIgnored private lazy var voice: AVSpeechSynthesisVoice? = Self.bestVoice(from: AVSpeechSynthesisVoice.speechVoices())

    init(
        engine: SpeechEngine? = nil,
        player: NarrationPlayer? = nil,
        narrations: ((String, String) -> Narration?)? = nil,
        audioSession: ReadingAudioSession? = nil,
        now: @escaping () -> Date = Date.init
    ) {
        self.now = now
        self.engine = engine ?? SystemSpeechEngine()
        self.audioSession = audioSession ?? makeReadingAudioSession()
        self.player = player ?? ClipPlayer()
        self.narrations = narrations ?? { NarrationLibrary.current.narration(for: $0, text: $1) }
        let events = EngineEvents(
            willSpeak: { [weak self] range in self?.spokenRange = range },
            finished: { [weak self] in self?.reset(.finished) }
        )
        self.engine.events = events
        self.player.events = events
    }

    var isSpeaking: Bool { pageId != nil }

    func isSpeaking(pageId: String) -> Bool { self.pageId == pageId }

    func toggle(pageId: String, text: String) {
        if self.pageId == pageId {
            stop()
            return
        }
        if isSpeaking {
            stopEngine()
            report(.stopped)
        }
        self.pageId = pageId
        self.text = text
        spokenRange = nil
        onSpeakingChanged?(true)
        audioSession.begin()
        if let clip = narrations(pageId, text), player.play(clip) {
            usingClip = true
            Self.log.info("read aloud \(pageId, privacy: .public): natural voice clip")
        } else {
            usingClip = false
            Self.log.info("read aloud \(pageId, privacy: .public): system voice")
            engine.speak(text, voice: voice, rate: Self.rate)
        }
        startedAt = now()
        report(.started)
    }

    func stop() {
        guard isSpeaking else { return }
        stopEngine()
        reset(.stopped)
    }

    private func stopEngine() {
        if usingClip { player.stop() } else { engine.stop() }
    }

    /// Stop if the page being read is no longer the one on screen.
    func pageDidChange(to pageId: String) {
        if self.pageId != nil, self.pageId != pageId { stop() }
    }

    private func reset(_ outcome: Use.Outcome) {
        guard isSpeaking else { return }
        report(outcome)
        pageId = nil
        text = nil
        spokenRange = nil
        usingClip = false
        startedAt = nil
        audioSession.end()
        onSpeakingChanged?(false)
    }

    private func report(_ outcome: Use.Outcome) {
        guard let pageId, let onUse else { return }
        let seconds = outcome == .started ? nil : startedAt.map { max(now().timeIntervalSince($0), 0) }
        onUse(Use(pageId: pageId, outcome: outcome, mode: usingClip ? .clip : .system, seconds: seconds))
    }

    /// Best installed English voice: premium before enhanced before default, US before UK.
    nonisolated static func bestVoice(from voices: [AVSpeechSynthesisVoice]) -> AVSpeechSynthesisVoice? {
        let ranked = voices
            .filter { ["en-US", "en-GB"].contains($0.language) }
            .filter { !$0.voiceTraits.contains(.isNoveltyVoice) }
            .sorted { score($0) > score($1) }
        return ranked.first ?? AVSpeechSynthesisVoice(language: "en-US")
    }

    nonisolated static func score(_ voice: AVSpeechSynthesisVoice) -> Int {
        let quality = switch voice.quality {
        case .premium: 30
        case .enhanced: 20
        default: 10
        }
        return quality + (voice.language == "en-US" ? 1 : 0)
    }
}

/// `AVSpeechSynthesizer` behind `SpeechEngine`.
@MainActor
final class SystemSpeechEngine: NSObject, SpeechEngine, @preconcurrency AVSpeechSynthesizerDelegate {
    var events: ReadAloudController.EngineEvents?
    private let synthesizer = AVSpeechSynthesizer()
    /// Each `stop()` + `speak()` bumps this so a late cancel callback from the old utterance is ignored.
    private var current: AVSpeechUtterance?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String, voice: AVSpeechSynthesisVoice?, rate: Float) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = voice
        utterance.rate = rate
        utterance.pitchMultiplier = 1.05
        utterance.preUtteranceDelay = 0.15
        current = utterance
        synthesizer.speak(utterance)
    }

    func stop() {
        current = nil
        synthesizer.stopSpeaking(at: .immediate)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, willSpeakRangeOfSpeechString characterRange: NSRange, utterance: AVSpeechUtterance) {
        guard utterance === current else { return }
        events?.willSpeak(characterRange)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        guard utterance === current else { return }
        current = nil
        events?.finished()
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        guard utterance === current else { return }
        current = nil
        events?.finished()
    }
}
