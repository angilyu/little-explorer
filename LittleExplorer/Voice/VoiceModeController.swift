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
    /// True while the app reads the page aloud; the mic is muted so Pip doesn't hear it.
    private(set) var isAppReadingAloud = false

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
        if isAppReadingAloud, isConnected { return "Pip waits while the book reads" }
        return switch state {
        case .off: "Pip is asleep"
        case .connecting, .waitingForBuddy: "Waking Pip up…"
        case .listening: "Pip is listening"
        case .thinking: "Pip is thinking"
        case .speaking: "Pip is talking"
        case .failed(let message): message
        }
    }

    func toggle() async {
        if isEnabled { await turnOff() } else { await turnOn() }
    }

    func turnOn() async {
        guard state == .off else { return }
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
            state = .failed(Self.message(for: error))
        }
    }

    func turnOff() async {
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
            state = .failed(reason ?? "Pip fell asleep. Tap to wake Pip up.")
        }
    }

    static func message(for error: Error) -> String {
        if error is URLError { return "Pip can't reach the voice server." }
        if let voiceError = error as? VoiceError, let text = voiceError.errorDescription { return text }
        return "Pip can't talk right now."
    }
}

extension VoiceModeController {
    static func live() -> VoiceModeController {
        #if DEBUG
        if let activity = DemoLaunch.current?.voiceActivity {
            return VoiceModeController { DemoVoiceTransport(activity: activity) }
        }
        #endif
        return VoiceModeController { makeLiveVoiceTransport() }
    }
}
