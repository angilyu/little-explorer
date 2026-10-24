import Foundation

/// Wakes Milo's agent when a book opens, so "Talk to Milo" doesn't wait 10–20 s for LiveKit to
/// start a replica (the free plan stops it once nobody is talking). Fire and forget: it never
/// blocks the reader, ignores failures and changes nothing about how a session starts.
///
/// To keep LiveKit's free minutes for real talks it only warms on a device that used Milo in the
/// last week, at most once per ``gap``, and not right after a session (the replica is still up).
/// The server applies its own limits too (`POST /milo/warm`).
@MainActor
final class MiloWarmer {
    static let gap: TimeInterval = 10 * 60
    static let recentUse: TimeInterval = 7 * 24 * 60 * 60
    static let stillWarm: TimeInterval = 5 * 60
    static let lastUsedKey = "milo.lastUsedAt"

    private let send: @MainActor () async throws -> Void
    private let defaults: UserDefaults
    private let now: () -> Date
    private var lastAttempt: Date?
    /// The last warm-up request (tests wait for it).
    private(set) var pending: Task<Void, Never>?

    init(
        defaults: UserDefaults = .standard,
        now: @escaping () -> Date = Date.init,
        send: @escaping @MainActor () async throws -> Void
    ) {
        self.defaults = defaults
        self.now = now
        self.send = send
    }

    /// Sends a warm-up if it's likely to help; returns whether it did.
    @discardableResult
    func warmIfUseful() -> Bool {
        let now = now()
        guard let used = defaults.object(forKey: Self.lastUsedKey) as? Date else { return false }
        let sinceUse = now.timeIntervalSince(used)
        guard sinceUse < Self.recentUse, sinceUse >= Self.stillWarm else { return false }
        if let lastAttempt, now.timeIntervalSince(lastAttempt) < Self.gap { return false }
        lastAttempt = now
        pending = Task { [send] in try? await send() }
        return true
    }

    /// Milo talked on this device (connected, or a session just ended).
    func miloWasUsed() {
        defaults.set(now(), forKey: Self.lastUsedKey)
    }
}

extension MiloWarmer {
    static func live() -> MiloWarmer {
        MiloWarmer {
            try await TokenService(
                baseURL: VoiceSettings.tokenServerURL(), device: DeviceIdentity.current(),
                sessionToken: { KeychainSessionTokenStore().read() }
            ).warm()
        }
    }
}
