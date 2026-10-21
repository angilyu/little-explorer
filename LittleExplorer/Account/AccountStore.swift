import Foundation
import Observation

enum AccountError: Error, Equatable {
    case badEmail
    /// Wrong code; the server says how many tries the code has left.
    case badCode(triesLeft: Int)
    /// The code is too old or was used up; ask for a new one.
    case expired
    /// Too many codes asked for; try again after this many seconds.
    case tooMany(retryAfter: Int)
    case signedOut
    case server(status: Int)
    case offline

    var message: String {
        switch self {
        case .badEmail: "That doesn't look like an email address."
        case let .badCode(left) where left > 0: "That code isn't right. \(left) \(left == 1 ? "try" : "tries") left."
        case .badCode, .expired: "That code has expired. Tap “Send a new code”."
        case let .tooMany(wait): "Too many codes for now. Try again in \(Self.minutes(wait))."
        case .signedOut: "You're signed out. Sign in again."
        case let .server(status): "Milo's server had a problem (\(status)). Try again."
        case .offline: "Couldn't reach Milo's server. Check the internet and try again."
        }
    }

    private static func minutes(_ seconds: Int) -> String {
        seconds < 90 ? "\(max(seconds, 1)) s" : "\((seconds + 59) / 60) min"
    }
}

/// What `POST /auth/verify` answers.
struct SignInResult: Decodable, Equatable {
    let token: String
    let account: Account
    /// True when this device's stars, coins and hearts from before accounts now belong to this account,
    /// so the app should send what it kept on the device (see ``AccountStore``).
    let claimedDevice: Bool

    enum CodingKeys: String, CodingKey {
        case token, account
        case claimedDevice = "claimed_device"
    }

    init(token: String, account: Account, claimedDevice: Bool = false) {
        self.token = token
        self.account = account
        self.claimedDevice = claimedDevice
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        token = try c.decode(String.self, forKey: .token)
        account = try c.decode(Account.self, forKey: .account)
        claimedDevice = try c.decodeIfPresent(Bool.self, forKey: .claimedDevice) ?? false
    }
}

/// The account endpoints on the Milo token server. Every request carries the app's access key; signed-in
/// ones also carry the session token in `X-Pip-Session`.
struct AccountService {
    var baseURL: URL
    var accessKey: String? = VoiceSettings.accessKey
    var session: URLSession = .shared

    static let sessionHeader = "X-Pip-Session"

    func request(_ path: String, method: String = "GET", token: String? = nil, body: [String: Any]? = nil) -> URLRequest {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 30
        if let accessKey { request.setValue("Bearer " + accessKey, forHTTPHeaderField: "Authorization") }
        if let token { request.setValue(token, forHTTPHeaderField: Self.sessionHeader) }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        return request
    }

    /// Emails a 6-digit code. Signing up and signing in are the same. Returns seconds until a resend is allowed.
    func sendCode(email: String, purpose: String = "sign_in", token: String? = nil) async throws -> Int {
        let data = try await send(request(
            "auth/start", method: "POST", token: token, body: ["email": email, "purpose": purpose]
        ))
        let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return reply?["resend_after_s"] as? Int ?? 60
    }

    func verify(email: String, code: String, device: DeviceInfo) async throws -> SignInResult {
        let body: [String: Any] = [
            "email": email, "code": code,
            "device": ["id": device.id, "name": device.name, "model": device.model],
        ]
        return try JSONDecoder().decode(SignInResult.self, from: await send(request("auth/verify", method: "POST", body: body)))
    }

    func fetchAccount(token: String) async throws -> Account {
        struct Reply: Decodable { let account: Account }
        return try JSONDecoder().decode(Reply.self, from: await send(request("account", token: token))).account
    }

    func signOut(token: String) async throws {
        _ = try await send(request("auth/signout", method: "POST", token: token, body: [:]))
    }

    func deleteAccount(token: String, code: String) async throws {
        _ = try await send(request("account/delete", method: "POST", token: token, body: ["code": code]))
    }

    /// Hands what this device kept from before accounts to the account that claimed it. Safe to repeat.
    func importDevice(token: String, stars: [StarAward], hearts: [String]) async throws {
        let awards = (try? JSONSerialization.jsonObject(with: JSONEncoder().encode(stars))) ?? []
        _ = try await send(request("account/import", method: "POST", token: token, body: ["stars": awards, "hearts": hearts]))
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw AccountError.offline
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard !(200..<300).contains(status) else { return data }
        throw Self.error(status: status, data: data)
    }

    static func error(status: Int, data: Data) -> AccountError {
        let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        switch reply["error"] as? String {
        case "bad_email": return .badEmail
        case "bad_code": return .badCode(triesLeft: reply["tries_left"] as? Int ?? 0)
        case "expired": return .expired
        case "too_many": return .tooMany(retryAfter: reply["retry_after_s"] as? Int ?? 60)
        case "signed_out": return .signedOut
        default: return .server(status: status)
        }
    }
}

/// Who is signed in on this device. Signed out, the app shows the grown-ups' welcome screen; signed in, the
/// shelf shows the account's books.
@MainActor
@Observable
final class AccountStore {
    private(set) var account: Account?

    @ObservationIgnored private let tokens: SessionTokenStore
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let key: String
    @ObservationIgnored var service: () -> AccountService
    /// Called just before the account shows, so the stores switch to the account's saved data first.
    @ObservationIgnored var willSignIn: ((SignInResult) -> Void)?
    /// Called after a sign-in with the result, so the stores can load the account's stars, coins and hearts.
    @ObservationIgnored var onSignIn: ((SignInResult) async -> Void)?
    /// Called before a grown-up signs out, while the sign-in still works (to send hearts not sent yet).
    @ObservationIgnored var willSignOut: (() async -> Void)?
    /// Called with the account id after signing out or deleting the account, so the stores forget its data.
    @ObservationIgnored var onSignOut: ((String) -> Void)?

    init(
        tokens: SessionTokenStore = KeychainSessionTokenStore(), defaults: UserDefaults = .standard,
        key: String = "account.v1", service: @escaping () -> AccountService = AccountStore.liveService
    ) {
        self.tokens = tokens
        self.defaults = defaults
        self.key = key
        self.service = service
        let saved = defaults.data(forKey: key).flatMap { try? JSONDecoder().decode(Account.self, from: $0) }
        account = tokens.read() == nil ? nil : saved
    }

    nonisolated static func liveService() -> AccountService { AccountService(baseURL: VoiceSettings.tokenServerURL()) }

    var isSignedIn: Bool { account != nil }
    var token: String? { account == nil ? nil : tokens.read() }

    func shelf(_ all: [Book]) -> [Book] { account?.shelf(all) ?? [] }

    func sendCode(to email: String) async throws -> Int {
        let email = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard Account.looksLikeEmail(email) else { throw AccountError.badEmail }
        return try await service().sendCode(email: email)
    }

    func verify(email: String, code: String, device: DeviceInfo) async throws {
        let email = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let result = try await service().verify(email: email, code: code, device: device)
        tokens.write(result.token)
        willSignIn?(result)
        save(result.account)
        await onSignIn?(result)
    }

    /// Picks up books the owner granted since; signs out if the server forgot this session.
    func refresh() async {
        guard let token else { return }
        do {
            save(try await service().fetchAccount(token: token))
        } catch AccountError.signedOut {
            forget()
        } catch {}
    }

    /// Signs out here; the account and everything in it stay on the server.
    func signOut() async {
        await willSignOut?()
        if let token { try? await service().signOut(token: token) }
        forget()
    }

    func sendDeleteCode() async throws -> Int {
        guard let account, let token else { throw AccountError.signedOut }
        return try await service().sendCode(email: account.email, purpose: "delete", token: token)
    }

    /// Deletes the account and everything in it on the server, then signs out here.
    func deleteAccount(code: String) async throws {
        guard let token else { throw AccountError.signedOut }
        try await service().deleteAccount(token: token, code: code)
        forget()
    }

    private func save(_ account: Account) {
        self.account = account
        if let data = try? JSONEncoder().encode(account) { defaults.set(data, forKey: key) }
    }

    private func forget() {
        let id = account?.id
        tokens.write(nil)
        defaults.removeObject(forKey: key)
        account = nil
        if let id { onSignOut?(id) }
    }
}

extension AccountStore {
    static func live() -> AccountStore {
        #if DEBUG
        if let demo = DemoLaunch.current {
            let suite = "demo.account"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            let store = AccountStore(tokens: MemorySessionTokenStore(), defaults: defaults)
            if let account = demo.account { store.signInForDemo(account) }
            return store
        }
        #endif
        return AccountStore()
    }

    #if DEBUG
    func signInForDemo(_ account: Account) {
        tokens.write("demo")
        save(account)
    }
    #endif
}
