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

    static let napNotice = "Milo is taking a nap — tap to talk again"
    static let restNotice = "Milo needs a rest today. Let's talk again tomorrow!"

    @ObservationIgnored private let makeTransport: @MainActor () -> VoiceTransport
    @ObservationIgnored private var transport: VoiceTransport?
    @ObservationIgnored private var generation = 0

    init(makeTransport: @escaping @MainActor () -> VoiceTransport) {
        self.makeTransport = makeTransport
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
        if isEnabled { await turnOff() } else { await turnOn() }
    }

    func turnOn() async {
        guard state == .off else { return }
        notice = nil
        generation += 1
        let gen = generation
        state = .connecting
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
        do {
            try await transport.connect(page: openingPage)
            guard gen == generation else {
                await transport.disconnect()
                return
            }
            if state == .connecting { state = .waitingForBuddy }
            if isAppReadingAloud { await transport.setMicrophone(enabled: false) }
            if let currentPage, currentPage != openingPage { try await transport.updatePage(currentPage) }
        } catch {
            guard gen == generation else { return }
            self.transport = nil
            await transport.disconnect()
            if case VoiceError.resting = error {
                state = .off
                notice = Self.restNotice
            } else {
                state = .failed(Self.message(for: error))
            }
        }
    }

    func turnOff() async {
        notice = nil
        generation += 1
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
        guard let transport, state != .connecting else { return }
        await transport.setMicrophone(enabled: !reading)
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
            case .listening: state = .listening
            case .thinking: state = .thinking
            case .speaking: state = .speaking
            }
        case .disconnected(let reason):
            transport = nil
            state = .failed(reason ?? "Milo fell asleep. Tap to wake Milo up.")
        case .ended(let reason):
            let transport = self.transport
            self.transport = nil
            generation += 1
            state = .off
            notice = reason == "limit" ? Self.restNotice : Self.napNotice
            Task { await transport?.disconnect() }
        }
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
            return VoiceModeController { DemoVoiceTransport(activity: activity, endReason: demo.voiceEnd) }
        }
        #endif
        return VoiceModeController { makeLiveVoiceTransport() }
    }
}
