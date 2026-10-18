import Foundation
import Observation

/// This device's stars and coins on the Milo server, and the rules for turning stars into coins
/// (see server/src/little_explorer_agent/coins.py). The server owns the ledger and the rules.
struct CoinState: Codable, Equatable, Sendable {
    var enabled = true
    var starsPerCoin = 10
    var minCoins = 1
    /// 0 means no limit.
    var maxCoins = 0
    var starsEarned = 0
    var starsSpent = 0
    /// Stars he can still spend: earned minus spent.
    var stars = 0
    var coins = 0
    /// Most coins one conversion may make now (0 when off or not enough stars).
    var canConvert = 0
    var leftoverStars = 0

    enum CodingKeys: String, CodingKey {
        case enabled, stars, coins
        case starsPerCoin = "stars_per_coin"
        case minCoins = "min_coins"
        case maxCoins = "max_coins"
        case starsEarned = "stars_earned"
        case starsSpent = "stars_spent"
        case canConvert = "can_convert"
        case leftoverStars = "leftover_stars"
    }

    init(
        enabled: Bool = true, starsPerCoin: Int = 10, minCoins: Int = 1, maxCoins: Int = 0, starsEarned: Int = 0,
        starsSpent: Int = 0, coins: Int = 0
    ) {
        self.enabled = enabled
        self.starsPerCoin = starsPerCoin
        self.minCoins = minCoins
        self.maxCoins = maxCoins
        self.starsEarned = starsEarned
        self.starsSpent = starsSpent
        self.coins = coins
        stars = max(starsEarned - starsSpent, 0)
        var most = enabled ? stars / max(starsPerCoin, 1) : 0
        if maxCoins > 0 { most = min(most, maxCoins) }
        canConvert = most >= minCoins ? most : 0
        leftoverStars = stars - canConvert * starsPerCoin
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        starsPerCoin = max(try c.decodeIfPresent(Int.self, forKey: .starsPerCoin) ?? 10, 1)
        minCoins = max(try c.decodeIfPresent(Int.self, forKey: .minCoins) ?? 1, 1)
        maxCoins = max(try c.decodeIfPresent(Int.self, forKey: .maxCoins) ?? 0, 0)
        starsEarned = try c.decodeIfPresent(Int.self, forKey: .starsEarned) ?? 0
        starsSpent = try c.decodeIfPresent(Int.self, forKey: .starsSpent) ?? 0
        stars = try c.decodeIfPresent(Int.self, forKey: .stars) ?? max(starsEarned - starsSpent, 0)
        coins = try c.decodeIfPresent(Int.self, forKey: .coins) ?? 0
        canConvert = try c.decodeIfPresent(Int.self, forKey: .canConvert) ?? 0
        leftoverStars = try c.decodeIfPresent(Int.self, forKey: .leftoverStars) ?? stars
    }

    /// Stars still needed before the smallest conversion is allowed.
    var starsToGo: Int { max(minCoins * starsPerCoin - stars, 0) }
}

/// One conversion the server made (or replayed, when the same id is sent again).
struct CoinConversion: Decodable, Equatable, Sendable {
    let id: String
    let coins: Int
    let starsSpent: Int
    let starsPerCoin: Int
    let replayed: Bool

    enum CodingKeys: String, CodingKey {
        case id, coins, replayed
        case starsSpent = "stars_spent"
        case starsPerCoin = "stars_per_coin"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        coins = try c.decode(Int.self, forKey: .coins)
        starsSpent = try c.decodeIfPresent(Int.self, forKey: .starsSpent) ?? 0
        starsPerCoin = try c.decodeIfPresent(Int.self, forKey: .starsPerCoin) ?? 0
        replayed = try c.decodeIfPresent(Bool.self, forKey: .replayed) ?? false
    }
}

enum CoinError: Error, Equatable {
    /// The server said no, e.g. `disabled`, `rate_changed`, `not_enough_stars`, `too_many`.
    case refused(code: String, message: String, state: CoinState?)
    case server(status: Int)

    var message: String {
        switch self {
        case let .refused(_, message, _): message
        case let .server(status) where status == 401: "Milo's server didn't accept this app's key."
        case let .server(status): "Milo's server had a problem (\(status)). Try again."
        }
    }
}

/// `GET /coins?device=<id>` and `POST /coins/convert` on the Milo token server.
struct CoinService {
    var baseURL: URL
    var deviceId: String
    var accessKey: String? = VoiceSettings.accessKey
    var session: URLSession = .shared

    private struct ConvertResponse: Decodable {
        let conversion: CoinConversion?
    }

    private struct Refusal: Decodable {
        let error: String
        let message: String?
    }

    private func request(_ path: String) -> URLRequest {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = 20
        if let accessKey { request.setValue("Bearer " + accessKey, forHTTPHeaderField: "Authorization") }
        return request
    }

    func fetchRequest() -> URLRequest {
        var request = request("coins")
        var components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "device", value: deviceId)]
        request.url = components.url
        return request
    }

    func convertRequest(id: String, coins: Int, starsPerCoin: Int) -> URLRequest {
        var request = request("coins/convert")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["device": deviceId, "id": id, "coins": coins, "stars_per_coin": starsPerCoin]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        return request
    }

    func fetch() async throws -> CoinState {
        let (data, status) = try await send(fetchRequest())
        guard (200..<300).contains(status) else { throw Self.error(status: status, data: data) }
        return try JSONDecoder().decode(CoinState.self, from: data)
    }

    func convert(id: String, coins: Int, starsPerCoin: Int) async throws -> (CoinState, CoinConversion?) {
        let (data, status) = try await send(convertRequest(id: id, coins: coins, starsPerCoin: starsPerCoin))
        guard (200..<300).contains(status) else { throw Self.error(status: status, data: data) }
        let decoder = JSONDecoder()
        return (try decoder.decode(CoinState.self, from: data), try decoder.decode(ConvertResponse.self, from: data).conversion)
    }

    private func send(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await session.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    private static func error(status: Int, data: Data) -> CoinError {
        guard [400, 403, 409].contains(status), let refusal = try? JSONDecoder().decode(Refusal.self, from: data) else {
            return .server(status: status)
        }
        let state = try? JSONDecoder().decode(CoinState.self, from: data)
        return .refused(code: refusal.error, message: refusal.message ?? refusal.error, state: state)
    }
}

/// His coins and how many stars have been spent on them, as last heard from the server and saved
/// here so the badge is right offline. Badges and the Passport keep using every star he earned.
@MainActor
@Observable
final class CoinStore {
    private(set) var coins: Int
    private(set) var starsSpent: Int
    /// The server's full answer, for the grown-ups corner; nil until it has been reached.
    private(set) var state: CoinState?

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let prefix: String

    init(defaults: UserDefaults = .standard, prefix: String = "coins.v1") {
        self.defaults = defaults
        self.prefix = prefix
        coins = defaults.integer(forKey: prefix + ".coins")
        starsSpent = defaults.integer(forKey: prefix + ".starsSpent")
    }

    /// Stars he can still spend, out of the `earned` stars this device knows about.
    func spendable(fromEarned earned: Int) -> Int { max(earned - starsSpent, 0) }

    func apply(_ state: CoinState) {
        self.state = state
        coins = state.coins
        starsSpent = state.starsSpent
        defaults.set(coins, forKey: prefix + ".coins")
        defaults.set(starsSpent, forKey: prefix + ".starsSpent")
    }

    /// Asks the server for the latest balance and rules; keeps the saved numbers if it can't.
    @discardableResult
    func refresh(using service: CoinService) async -> Bool {
        guard let fresh = try? await service.fetch() else { return false }
        apply(fresh)
        return true
    }

    /// A conversion the grown-up asked for. If the network drops, the same id is sent again on the
    /// next try (for the same amount), so the server can't make the coins twice.
    func convert(coins: Int, starsPerCoin: Int, using service: CoinService) async throws -> CoinConversion? {
        let pending = Pending(coins: coins, starsPerCoin: starsPerCoin)
        let id = pendingId(for: pending) ?? UUID().uuidString
        savePending(id: id, pending)
        do {
            let (fresh, conversion) = try await service.convert(id: id, coins: coins, starsPerCoin: starsPerCoin)
            clearPending()
            apply(fresh)
            return conversion
        } catch let error as CoinError {
            clearPending()
            if case let .refused(_, _, state?) = error { apply(state) }
            throw error
        }
    }

    // MARK: - Retry id

    private struct Pending: Codable, Equatable {
        let coins: Int
        let starsPerCoin: Int
    }

    private var pendingKey: String { prefix + ".pending" }

    func pendingId(forCoins coins: Int, starsPerCoin: Int) -> String? {
        pendingId(for: Pending(coins: coins, starsPerCoin: starsPerCoin))
    }

    private func pendingId(for pending: Pending) -> String? {
        guard let saved = defaults.dictionary(forKey: pendingKey), let id = saved["id"] as? String,
              saved["coins"] as? Int == pending.coins, saved["starsPerCoin"] as? Int == pending.starsPerCoin
        else { return nil }
        return id
    }

    private func savePending(id: String, _ pending: Pending) {
        defaults.set(["id": id, "coins": pending.coins, "starsPerCoin": pending.starsPerCoin], forKey: pendingKey)
    }

    private func clearPending() { defaults.removeObject(forKey: pendingKey) }
}

extension CoinStore {
    static func live() -> CoinStore {
        #if DEBUG
        if DemoLaunch.current != nil {
            let suite = "demo.coins"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            return CoinStore(defaults: defaults)
        }
        #endif
        return CoinStore()
    }

    /// Screenshot demo: `coins` coins already made from `coins × starsPerCoin` stars.
    func seedDemo(coins: Int, starsPerCoin: Int = 10) {
        apply(CoinState(starsEarned: coins * starsPerCoin, starsSpent: coins * starsPerCoin, coins: coins))
        state = nil
    }

    static func service() -> CoinService {
        CoinService(baseURL: VoiceSettings.tokenServerURL(), deviceId: DeviceIdentity.current().id)
    }
}
