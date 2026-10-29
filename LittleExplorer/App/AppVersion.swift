import Foundation
import StoreKit

/// The app build talking to the server. From 1.1 every request says which build it is in `X-App-*` headers (1.0 sent
/// nothing, and the server treats a request without them as 1.0), so the server and Milo can keep older builds
/// working and the owner can see which builds families run. See "App versions and force update" in
/// docs/operations.md.
struct ClientVersion: Equatable, Sendable {
    enum Channel: String, Sendable { case appstore, testflight, debug }

    static let versionHeader = "X-App-Version"
    static let buildHeader = "X-App-Build"
    static let channelHeader = "X-App-Channel"

    var version: String
    var build: String
    var channel: Channel

    static let current = ClientVersion(bundle: .main)

    init(version: String, build: String, channel: Channel) {
        self.version = version
        self.build = build
        self.channel = channel
    }

    init(bundle: Bundle) {
        let info = bundle.infoDictionary
        version = info?["CFBundleShortVersionString"] as? String ?? ""
        build = info?["CFBundleVersion"] as? String ?? ""
        #if DEBUG
        channel = .debug
        #else
        channel = Self.channel(receiptName: bundle.appStoreReceiptURL?.lastPathComponent)
        #endif
    }

    /// TestFlight (and Xcode) installs have a sandbox receipt; App Store installs have a real one.
    static func channel(receiptName: String?) -> Channel {
        receiptName == "sandboxReceipt" ? .testflight : .appstore
    }

    var headers: [String: String] {
        [Self.versionHeader: version, Self.buildHeader: build, Self.channelHeader: channel.rawValue]
    }
}

extension URLRequest {
    /// Says which app build is asking. Every request to the Milo server should carry it.
    mutating func addClientHeaders(_ client: ClientVersion = .current) {
        for (name, value) in client.headers where !value.isEmpty {
            setValue(value, forHTTPHeaderField: name)
        }
    }
}

/// What `GET /app/version` says about this build.
struct UpdateAdvice: Decodable, Equatable, Sendable {
    enum Status: String, Decodable, Sendable {
        case ok, recommended, required

        init(from decoder: Decoder) throws {
            // A status a later server adds means nothing to this build: carry on.
            self = Status(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .ok
        }
    }

    var status: Status
    var updateURL: URL?
    var minVersion: String?
    var recommendedVersion: String?

    enum CodingKeys: String, CodingKey {
        case status
        case updateURL = "update_url"
        case minVersion = "min_version"
        case recommendedVersion = "recommended_version"
    }

    init(status: Status, updateURL: URL? = nil, minVersion: String? = nil, recommendedVersion: String? = nil) {
        self.status = status
        self.updateURL = updateURL
        self.minVersion = minVersion
        self.recommendedVersion = recommendedVersion
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decode(Status.self, forKey: .status)
        updateURL = (try? c.decodeIfPresent(String.self, forKey: .updateURL)).flatMap { $0.flatMap(URL.init(string:)) }
        minVersion = try? c.decodeIfPresent(String.self, forKey: .minVersion)
        recommendedVersion = try? c.decodeIfPresent(String.self, forKey: .recommendedVersion)
    }

    /// A `426 upgrade_required` answer to any request: the server no longer serves this build.
    static func refusal(status: Int, data: Data) -> UpdateAdvice? {
        guard status == 426 else { return nil }
        let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        return UpdateAdvice(
            status: .required, updateURL: (reply["update_url"] as? String).flatMap(URL.init(string:)),
            minVersion: reply["min_version"] as? String
        )
    }
}

extension Notification.Name {
    /// Posted (with the ``UpdateAdvice`` as the object) when the server refuses a request because this build is
    /// too old; ``UpgradeChecker`` shows the update screen.
    static let appUpdateRequired = Notification.Name("appUpdateRequired")
}

enum UpgradeSignal {
    /// Call with every Milo server answer: a 426 shows the update screen. Returns whether it was one.
    @discardableResult
    static func check(status: Int, data: Data, center: NotificationCenter = .default) -> Bool {
        guard let advice = UpdateAdvice.refusal(status: status, data: data) else { return false }
        center.post(name: .appUpdateRequired, object: advice)
        return true
    }
}

/// Asks the server at launch and whenever the app comes back whether this build must (or should) be updated.
/// It never blocks the child when it can't tell: offline, a server error or an answer it doesn't understand
/// leaves the app as it is.
@MainActor
@Observable
final class UpgradeChecker {
    enum Prompt: Equatable {
        /// This build is no longer supported: the app shows only the update screen.
        case required(URL?)
        /// A newer build is out: a "Maybe later" prompt, at most every ``snooze``.
        case recommended(URL?)
    }

    private(set) var prompt: Prompt?

    var isRequired: Bool { if case .required = prompt { true } else { false } }

    @ObservationIgnored var baseURL: URL
    @ObservationIgnored var client: ClientVersion
    @ObservationIgnored var session: URLSession
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var observer: NSObjectProtocol?

    static let snooze: TimeInterval = 3 * 24 * 60 * 60
    static let snoozeKey = "upgrade.snoozedUntil"

    init(
        baseURL: URL = VoiceSettings.tokenServerURL(), client: ClientVersion = .current, session: URLSession = .shared,
        defaults: UserDefaults = .standard,
        center: NotificationCenter = .default, now: @escaping () -> Date = Date.init
    ) {
        self.baseURL = baseURL
        self.client = client
        self.session = session
        self.defaults = defaults
        self.now = now
        observer = center.addObserver(forName: .appUpdateRequired, object: nil, queue: .main) { [weak self] note in
            guard let advice = note.object as? UpdateAdvice else { return }
            MainActor.assumeIsolated { self?.apply(advice) }
        }
    }

    /// No access key: even a build whose key was retired must still hear it has to update.
    func request() -> URLRequest {
        var request = URLRequest(url: baseURL.appendingPathComponent("app/version"))
        request.timeoutInterval = 10
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.addClientHeaders(client)
        return request
    }

    /// Asks the server; keeps the current prompt if it can't get an answer.
    func check() async {
        guard let (data, response) = try? await session.data(for: request()),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let advice = try? JSONDecoder().decode(UpdateAdvice.self, from: data)
        else { return }
        apply(advice)
    }

    func apply(_ advice: UpdateAdvice) {
        switch advice.status {
        case .required:
            prompt = .required(advice.updateURL)
        case .recommended:
            if isRequired { prompt = nil }
            if prompt == nil, !snoozed { prompt = .recommended(advice.updateURL) }
        case .ok:
            prompt = nil
        }
    }

    /// "Maybe later" on the recommended prompt: ask again in a few days.
    func snoozeRecommended() {
        guard case .recommended = prompt else { return }
        defaults.set(now().addingTimeInterval(Self.snooze), forKey: Self.snoozeKey)
        prompt = nil
    }

    private var snoozed: Bool {
        guard let until = defaults.object(forKey: Self.snoozeKey) as? Date else { return false }
        return until > now()
    }

    /// For screenshots (`-demoUpgrade required|recommended`).
    func show(_ prompt: Prompt) { self.prompt = prompt }

    /// Where Update goes: the server's link, else this app's App Store page (which App Store builds know), else a
    /// search for the app. TestFlight builds open TestFlight.
    static func destination(
        _ url: URL?, channel: ClientVersion.Channel = ClientVersion.current.channel,
        appStoreID: () async -> UInt64? = appStoreID
    ) async -> URL {
        if let url { return url }
        if channel == .testflight { return URL(string: "itms-beta://")! }
        if let id = await appStoreID() { return URL(string: "https://apps.apple.com/app/id\(id)")! }
        return URL(string: "https://apps.apple.com/search?term=Little%20Explorer%20City%20Books")!
    }

    static func appStoreID() async -> UInt64? {
        guard case .verified(let transaction)? = try? await AppTransaction.shared else { return nil }
        return transaction.appID
    }
}
