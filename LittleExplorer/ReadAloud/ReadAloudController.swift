import AVFoundation
import Observation

/// What the read-aloud button needs from a speech engine. `AVSpeechSynthesizer` in the app,
/// a recorder in tests.
@MainActor
protocol SpeechEngine: AnyObject {
    var events: ReadAloudController.EngineEvents? { get set }
    func speak(_ text: String, voice: AVSpeechSynthesisVoice?, rate: Float)
    func stop()
}

/// Reads the page sentence aloud on the device (no network), slowly and clearly, and reports which
/// word is being spoken so the page can highlight it. Tapping again stops. Works with or without
/// voice mode; `onSpeakingChanged` lets voice mode pause the mic so Pip doesn't hear the app.
@MainActor
@Observable
final class ReadAloudController {
    struct EngineEvents {
        var willSpeak: (NSRange) -> Void
        var finished: () -> Void
    }

    /// About 80% of the default rate: slow enough to follow word by word, still natural.
    static let rate: Float = AVSpeechUtteranceDefaultSpeechRate * 0.8

    private(set) var pageId: String?
    private(set) var text: String?
    /// The word being spoken right now, in `text`'s UTF-16 offsets.
    private(set) var spokenRange: NSRange?

    @ObservationIgnored var onSpeakingChanged: ((Bool) -> Void)?
    @ObservationIgnored private let engine: SpeechEngine
    @ObservationIgnored private lazy var voice: AVSpeechSynthesisVoice? = Self.bestVoice(from: AVSpeechSynthesisVoice.speechVoices())

    init(engine: SpeechEngine? = nil) {
        self.engine = engine ?? SystemSpeechEngine()
        self.engine.events = EngineEvents(
            willSpeak: { [weak self] range in self?.spokenRange = range },
            finished: { [weak self] in self?.reset() }
        )
    }

    var isSpeaking: Bool { pageId != nil }

    func isSpeaking(pageId: String) -> Bool { self.pageId == pageId }

    func toggle(pageId: String, text: String) {
        if self.pageId == pageId {
            stop()
            return
        }
        if isSpeaking { engine.stop() }
        self.pageId = pageId
        self.text = text
        spokenRange = nil
        onSpeakingChanged?(true)
        engine.speak(text, voice: voice, rate: Self.rate)
    }

    func stop() {
        guard isSpeaking else { return }
        engine.stop()
        reset()
    }

    /// Stop if the page being read is no longer the one on screen.
    func pageDidChange(to pageId: String) {
        if self.pageId != nil, self.pageId != pageId { stop() }
    }

    private func reset() {
        guard isSpeaking else { return }
        pageId = nil
        text = nil
        spokenRange = nil
        onSpeakingChanged?(false)
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
        configureAudioSession()
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

    /// When voice mode is on, LiveKit already owns a play-and-record session, so leave it alone.
    /// Otherwise use a playback session so the sentence is heard even with the silent switch on.
    private func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        guard session.category != .playAndRecord else { return }
        try? session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? session.setActive(true)
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
