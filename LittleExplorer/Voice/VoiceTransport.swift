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
}

/// Thin seam between the app and whatever carries audio to the voice agent.
/// The live implementation is LiveKit; tests and demo screenshots use fakes.
protocol VoiceTransport: AnyObject {
    var onEvent: ((VoiceTransportEvent) -> Void)? { get set }
    /// Joins with the page that is open right now; the agent reads it from the token.
    func connect(page: PageContext?) async throws
    /// Tells the agent about a page turn.
    func updatePage(_ context: PageContext) async throws
    /// Mutes the mic while the app reads the page aloud, so Pip doesn't hear the app's own voice.
    func setMicrophone(enabled: Bool) async
    func disconnect() async
}

enum VoiceError: LocalizedError {
    case tokenServer(status: Int)
    case unavailable
    case microphoneDenied

    var errorDescription: String? {
        switch self {
        case .tokenServer: "Pip's server said no. Ask a grown-up to check it."
        case .unavailable: "Pip isn't available in this build."
        case .microphoneDenied: "Pip needs the microphone. Ask a grown-up to turn it on in Settings."
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

    init(activity: BuddyActivity) { self.activity = activity }

    func connect(page: PageContext?) async throws {
        try await Task.sleep(nanoseconds: 300_000_000)
        let activity = activity
        await MainActor.run { onEvent?(.buddyActivity(activity)) }
    }

    func updatePage(_ context: PageContext) async throws {}
    func setMicrophone(enabled: Bool) async {}
    func disconnect() async {}
}
