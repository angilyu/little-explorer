import Foundation
import Observation

/// Global voice-mode switch for the open book. Once on, it stays on across page turns until
/// the child (or closing the book) turns it off. Every page turn is forwarded to the agent.
@MainActor
@Observable
final class VoiceModeController {
    enum State: Equatable {
        case off
        case connecting
        case waitingForBuddy
        case listening
        case thinking
        case speaking
        case failed(String)
    }

    private(set) var state: State = .off
    private(set) var currentPage: PageContext?
    /// True while the app reads the page aloud; the mic is muted so Milo doesn't hear it.
    private(set) var isAppReadingAloud = false
    /// A gentle note shown after Milo turned itself off (a nap after a quiet spell, or rest for the day).
    private(set) var notice: String?
    /// True while Milo's recorded hello plays. The mic stays off meanwhile, so Milo doesn't hear
    /// (and answer) his own voice, and he only counts as listening once it's over.
    private(set) var isIntroPlaying = false
    /// "Talk to Milo" was tapped before a grown-up allowed Milo, so nothing connected; the reader asks a
    /// grown-up (the gate, then ``MiloConsentView``) and calls ``consentHandled()``.
    private(set) var needsConsent = false

    nonisolated static let napNotice = "Milo is taking a nap — tap to talk again"
    nonisolated static let restNotice = "Milo needs a rest today. Let's talk again tomorrow!"
    nonisolated static let offNotice = "A grown-up turned Milo off. Ask a grown-up to turn Milo on."

    @ObservationIgnored private let makeTransport: @MainActor () -> VoiceTransport
    @ObservationIgnored private var transport: VoiceTransport?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var turnedOnAt: Date?
    @ObservationIgnored private let introPlayer: IntroPlayer?
    /// `connect` has returned, so the mic can be switched on and off.
    @ObservationIgnored private var transportReady = false
    /// What the mic was last asked to be.
    @ObservationIgnored private var microphoneOn = false
    /// Milo said he was listening while the intro was still playing.
    @ObservationIgnored private var listeningAfterIntro = false
    /// How long each step of waking Milo up took, from the tap (see ``VoiceTimeline``).
    @ObservationIgnored private(set) var timeline: VoiceTimeline?
    /// A second tap this soon after "Talk to Milo" is a double tap, not "Stop talking". Turning off
    /// and on again while Milo was still waking up left an empty room for him to talk to.
    nonisolated static let doubleTapWindow: TimeInterval = 1.0
    /// Called with every star Milo gives (the app saves it in ``StarStore``).
    @ObservationIgnored var onStar: ((StarAward) -> Void)?
    /// Whether a grown-up allowed Milo (the account's consent). Until then Milo never connects, so no
    /// voice or page leaves the device.
    @ObservationIgnored var mayTalk: @MainActor () -> Bool = { true }
    /// Milo's server said a grown-up turned Milo off on another device, so the account should refresh.
    @ObservationIgnored var onMiloOff: (@MainActor () -> Void)?

    init(
        makeTransport: @escaping @MainActor () -> VoiceTransport,
        introPlayer: IntroPlayer? = nil,
        now: @escaping () -> Date = Date.init
    ) {
        self.makeTransport = makeTransport
        self.introPlayer = introPlayer
        self.now = now
    }

    var isEnabled: Bool { state != .off }

    var isConnected: Bool {
        switch state {
        case .waitingForBuddy, .listening, .thinking, .speaking: true
        default: false
        }
    }

    var statusText: String {
        if isAppReadingAloud, isConnected { return "Milo waits while the book reads" }
        return switch state {
        case .off: "Milo is asleep"
        case .connecting, .waitingForBuddy: "Waking Milo up…"
        case .listening: "Milo is listening"
        case .thinking: "Milo is thinking"
        case .speaking: "Milo is talking"
        case .failed(let message): message
        }
    }

    func toggle() async {
        guard isEnabled else { return await turnOn() }
        if state == .connecting || state == .waitingForBuddy, let turnedOnAt,
           now().timeIntervalSince(turnedOnAt) < Self.doubleTapWindow {
            return
        }
        await turnOff()
    }

    /// Milo says hello the moment he is tapped (the recorded intro) while the app connects behind
    /// it. The agent is told not to greet again, and the mic comes on once the hello is over.
    func turnOn() async {
        guard state == .off else { return }
        notice = nil
        guard mayTalk() else {
            needsConsent = true
            return
        }
        needsConsent = false
        generation += 1
        let gen = generation
        let tap = now()
        turnedOnAt = tap
        timeline = VoiceTimeline(tap: tap)
        transportReady = false
        listeningAfterIntro = false
        state = .connecting
        isIntroPlaying = introPlayer?.play { [weak self] in self?.introDidFinish(generation: gen) } ?? false
        if isIntroPlaying { mark(.intro) }
        let appPlaysIntro = isIntroPlaying
        let transport = makeTransport()
        self.transport = transport
        transport.onEvent = { [weak self] event in
            if Thread.isMainThread {
                MainActor.assumeIsolated { self?.handle(event, generation: gen) }
            } else {
                Task { @MainActor in self?.handle(event, generation: gen) }
            }
        }
        let openingPage = currentPage
        let microphone = wantsMicrophone
        do {
            try await transport.connect(page: openingPage, appPlaysIntro: appPlaysIntro, microphone: microphone)
            guard gen == generation else {
                await transport.disconnect()
                return
            }
            transportReady = true
            microphoneOn = microphone
            if state == .connecting { state = .waitingForBuddy }
            await syncMicrophone()
            if let currentPage, currentPage != openingPage { try await transport.updatePage(currentPage) }
        } catch {
            guard gen == generation else { return }
            stopIntro()
            self.transport = nil
            await transport.disconnect()
            if case VoiceError.resting = error {
                state = .off
                notice = Self.restNotice
            } else if case VoiceError.miloOff = error {
                state = .off
                notice = Self.offNotice
                onMiloOff?()
            } else {
                state = .failed(Self.message(for: error))
            }
        }
    }

    func turnOff() async {
        notice = nil
        generation += 1
        stopIntro()
        transportReady = false
        let transport = self.transport
        self.transport = nil
        state = .off
        await transport?.disconnect()
    }

    func retry() async {
        await turnOff()
        await turnOn()
    }

    func pageDidChange(_ page: PageContext) async {
        guard page != currentPage else { return }
        currentPage = page
        guard isConnected, let transport else { return }
        try? await transport.updatePage(page)
    }

    func appReadAloudDidChange(_ reading: Bool) async {
        guard reading != isAppReadingAloud else { return }
        isAppReadingAloud = reading
        await syncMicrophone()
    }

    private var wantsMicrophone: Bool { !isAppReadingAloud && !isIntroPlaying }

    private func syncMicrophone() async {
        guard transportReady, let transport else { return }
        let wanted = wantsMicrophone
        guard wanted != microphoneOn else { return }
        microphoneOn = wanted
        await transport.setMicrophone(enabled: wanted)
    }

    private func introDidFinish(generation gen: Int) {
        guard gen == generation, isIntroPlaying else { return }
        isIntroPlaying = false
        if listeningAfterIntro, state == .connecting || state == .waitingForBuddy {
            state = .listening
            mark(.listening)
        }
        listeningAfterIntro = false
        Task { await syncMicrophone() }
    }

    private func stopIntro() {
        guard isIntroPlaying else { return }
        introPlayer?.stop()
        isIntroPlaying = false
        listeningAfterIntro = false
    }

    private func mark(_ milestone: VoiceMilestone) {
        timeline?.mark(milestone, at: now())
    }

    func bookDidClose() async {
        await turnOff()
        currentPage = nil
    }

    private func handle(_ event: VoiceTransportEvent, generation gen: Int) {
        guard gen == generation, transport != nil else { return }
        switch event {
        case .buddyActivity(let activity):
            switch activity {
            case .initializing: state = .waitingForBuddy
            // "Waking Milo up…" until the hello is over and the mic is on.
            case .listening where isIntroPlaying:
                listeningAfterIntro = true
                if state == .connecting { state = .waitingForBuddy }
            case .listening:
                state = .listening
                mark(.listening)
            case .thinking: state = .thinking
            case .speaking: state = .speaking
            }
        case .milestone(let milestone):
            mark(milestone)
        case .disconnected(let reason):
            stopIntro()
            transport = nil
            state = .failed(reason ?? "Milo fell asleep. Tap to wake Milo up.")
        case .ended(let reason):
            stopIntro()
            let transport = self.transport
            self.transport = nil
            generation += 1
            state = .off
            notice = reason == "limit" ? Self.restNotice : Self.napNotice
            Task { await transport?.disconnect() }
        case .star(let award):
            onStar?(award)
        }
    }

    /// The grown-up answered (or cancelled); with permission, Milo wakes up straight away.
    func consentHandled() async {
        needsConsent = false
        if mayTalk() { await turnOn() }
    }

    func dismissNotice() {
        notice = nil
    }

    static func message(for error: Error) -> String {
        if error is URLError { return "Milo can't reach the voice server." }
        if let voiceError = error as? VoiceError, let text = voiceError.errorDescription { return text }
        return "Milo can't talk right now."
    }
}

extension VoiceModeController {
    static func live() -> VoiceModeController {
        #if DEBUG
        if let demo = DemoLaunch.current, let activity = demo.voiceActivity {
            return VoiceModeController { DemoVoiceTransport(activity: activity, endReason: demo.voiceEnd, givesStar: demo.voiceStar) }
        }
        #endif
        return VoiceModeController(makeTransport: { makeLiveVoiceTransport() }, introPlayer: ClipIntroPlayer())
    }
}
