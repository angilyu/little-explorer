import Foundation

/// What the voice buddy is doing, as reported by the agent (LiveKit's `lk.agent.state` attribute).
enum BuddyActivity: String, Sendable {
    case initializing
    case listening
    case thinking
    case speaking
}

enum VoiceTransportEvent: Sendable {
    case buddyActivity(BuddyActivity)
    case disconnected(reason: String?)
    /// The agent ended the session on purpose (`milo.ended`): "idle" after a quiet spell, "limit" at the daily limit.
    case ended(reason: String)
    /// Milo gave a star for a curious question (`milo.star`), already checked by the agent.
    case star(StarAward)
}

/// Thin seam between the app and whatever carries audio to the voice agent.
/// The live implementation is LiveKit; tests and demo screenshots use fakes.
protocol VoiceTransport: AnyObject {
    var onEvent: ((VoiceTransportEvent) -> Void)? { get set }
    /// Joins with the page that is open right now; the agent reads it from the token.
    func connect(page: PageContext?) async throws
    /// Tells the agent about a page turn.
    func updatePage(_ context: PageContext) async throws
    /// Mutes the mic while the app reads the page aloud, so Milo doesn't hear the app's own voice.
    func setMicrophone(enabled: Bool) async
    func disconnect() async
}

enum VoiceError: LocalizedError {
    case tokenServer(status: Int)
    case unavailable
    case microphoneDenied
    /// The server refused a token: this device is blocked or used up today's minutes.
    case resting

    var errorDescription: String? {
        switch self {
        case .tokenServer: "Milo's server said no. Ask a grown-up to check it."
        case .unavailable: "Milo isn't available in this build."
        case .microphoneDenied: "Milo needs the microphone. Ask a grown-up to turn it on in Settings."
        case .resting: VoiceModeController.restNotice
        }
    }
}

/// Used when the LiveKit SDK isn't linked.
final class UnavailableVoiceTransport: VoiceTransport {
    var onEvent: ((VoiceTransportEvent) -> Void)?
    func connect(page: PageContext?) async throws { throw VoiceError.unavailable }
    func updatePage(_ context: PageContext) async throws {}
    func setMicrophone(enabled: Bool) async {}
    func disconnect() async {}
}

/// Fake transport for previews and screenshot demos (`-demoVoice listening`).
final class DemoVoiceTransport: VoiceTransport {
    var onEvent: ((VoiceTransportEvent) -> Void)?
    private let activity: BuddyActivity
    private let endReason: String?
    private let givesStar: Bool

    /// `endReason` ("idle" or "limit") makes Milo end the session a moment after joining;
    /// `givesStar` makes Milo give one star for the opening page.
    init(activity: BuddyActivity, endReason: String? = nil, givesStar: Bool = false) {
        self.activity = activity
        self.endReason = endReason
        self.givesStar = givesStar
    }

    func connect(page: PageContext?) async throws {
        try await Task.sleep(nanoseconds: 300_000_000)
        let activity = activity
        await MainActor.run { onEvent?(.buddyActivity(activity)) }
        if givesStar, let page {
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                let award = StarAward(book: page.bookId, page: page.pageId, city: page.city, whyHow: true, stars: 1, words: 9)
                self?.onEvent?(.star(award))
            }
        }
        guard let endReason else { return }
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            self?.onEvent?(.ended(reason: endReason))
        }
    }

    func updatePage(_ context: PageContext) async throws {}
    func setMicrophone(enabled: Bool) async {}
    func disconnect() async {}
}
