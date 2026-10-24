import Foundation

/// Wakes Milo's agent when the app or a book opens, so "Talk to Milo" doesn't wait 10–20 s for LiveKit to
/// start a replica (the free plan stops it once nobody is talking). Fire and forget: it never
/// blocks the reader, ignores failures and changes nothing about how a session starts.
///
/// To keep LiveKit's free minutes for real talks it only warms on a device that used Milo in the
/// last week (or, before its first talk, in the week since Milo was first on), at most once per ``gap``, and not right after a session (the replica is still up).
/// The server applies its own limits too (`POST /milo/warm`).
@MainActor
final class MiloWarmer {
    static let gap: TimeInterval = 10 * 60
    static let recentUse: TimeInterval = 7 * 24 * 60 * 60
    static let stillWarm: TimeInterval = 5 * 60
    static let lastUsedKey = "milo.lastUsedAt"
    /// When this device first could warm Milo without having used him (for example, right after
    /// updating from a build that didn't record ``lastUsedKey``).
    static let firstSeenKey = "milo.warmFirstSeenAt"

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
        if let used = defaults.object(forKey: Self.lastUsedKey) as? Date {
            let sinceUse = now.timeIntervalSince(used)
            guard sinceUse < Self.recentUse, sinceUse >= Self.stillWarm else { return false }
        } else {
            // No talk recorded yet, but Milo is on (callers check): warm for a week from the first try.
            let firstSeen = defaults.object(forKey: Self.firstSeenKey) as? Date ?? {
                defaults.set(now, forKey: Self.firstSeenKey)
                return now
            }()
            guard now.timeIntervalSince(firstSeen) < Self.recentUse else { return false }
        }
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
