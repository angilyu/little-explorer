import Foundation
import Observation

/// Remembers which pages the child hearted, in UserDefaults. Signed in, each account has its own copy here
/// and the account's hearts on the Milo server are the ones that count (``sync(using:)``); the hearts from
/// before accounts stay under the old key.
@MainActor
@Observable
final class HeartStore {
    private(set) var loved: Set<String>

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let baseKey: String
    @ObservationIgnored private var key: String
    /// Hearts changed here that the server hasn't heard about yet (page id: loved), only when signed in.
    @ObservationIgnored private var pending: [String: Bool]
    @ObservationIgnored private var syncing = false
    /// Called after a heart changes while signed in, so the app can send it.
    @ObservationIgnored var onChange: (() -> Void)?

    init(defaults: UserDefaults = .standard, key: String = "lovedPages.v1") {
        self.defaults = defaults
        baseKey = key
        self.key = key
        loved = Set(defaults.stringArray(forKey: key) ?? [])
        pending = [:]
    }

    /// Shows the hearts saved for this account (nil: the ones from before accounts).
    func switchAccount(_ accountId: String?) {
        let next = AccountKeys.key(baseKey, accountId)
        guard next != key else { return }
        key = next
        syncing = accountId != nil
        loved = Set(defaults.stringArray(forKey: key) ?? [])
        pending = defaults.dictionary(forKey: key + ".pending") as? [String: Bool] ?? [:]
    }

    /// What this device saved before accounts (never deleted), for the account that claims it.
    var legacyLoved: [String] { (defaults.stringArray(forKey: baseKey) ?? []).sorted() }

    /// Forgets an account's saved copy here (after signing out; the server keeps the hearts).
    func forgetAccount(_ accountId: String) {
        let gone = AccountKeys.key(baseKey, accountId)
        defaults.removeObject(forKey: gone)
        defaults.removeObject(forKey: gone + ".pending")
    }

    var hasPending: Bool { !pending.isEmpty }

    /// Sends the hearts changed here, then shows the account's hearts as the server has them (so hearts
    /// from another device appear). Offline, it keeps what it has and tries again next time.
    func sync(using service: HeartService) async {
        let sent = pending
        do {
            let hearts = try await service.update(
                loved: sent.filter(\.value).keys.sorted(), unloved: sent.filter { !$0.value }.keys.sorted()
            )
            for (id, value) in sent where pending[id] == value { pending[id] = nil }
            // A heart changed while the request was out stays as he set it.
            var next = Set(hearts)
            for (id, value) in pending { if value { next.insert(id) } else { next.remove(id) } }
            loved = next
            save()
        } catch {}
    }

    /// Adds hearts from before accounts to this account (they go up with the next ``sync(using:)``).
    func adopt(_ ids: [String]) {
        for id in ids where !loved.contains(id) {
            loved.insert(id)
            pending[id] = true
        }
        save()
    }

    private func save() {
        defaults.set(loved.sorted(), forKey: key)
        if syncing { defaults.set(pending, forKey: key + ".pending") }
    }

    static func id(bookId: String, pageId: String) -> String { "\(bookId)/\(pageId)" }

    func isLoved(bookId: String, pageId: String) -> Bool {
        loved.contains(Self.id(bookId: bookId, pageId: pageId))
    }

    func toggle(bookId: String, pageId: String) {
        setLoved(!isLoved(bookId: bookId, pageId: pageId), bookId: bookId, pageId: pageId)
    }

    func setLoved(_ isLoved: Bool, bookId: String, pageId: String) {
        let id = Self.id(bookId: bookId, pageId: pageId)
        if isLoved { loved.insert(id) } else { loved.remove(id) }
        if syncing { pending[id] = isLoved }
        save()
        if syncing { onChange?() }
    }

    func lovedCount(bookId: String) -> Int {
        loved.filter { $0.hasPrefix(bookId + "/") }.count
    }

    func lovedPages(in book: Book) -> [BookPage] {
        book.pages.filter { isLoved(bookId: book.id, pageId: $0.id) }
    }
}

extension HeartStore {
    /// Screenshot demos keep their hearts in their own store, so they never touch his real ones.
    static func live() -> HeartStore {
        #if DEBUG
        if DemoLaunch.current != nil {
            let suite = "demo.hearts"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            return HeartStore(defaults: defaults)
        }
        #endif
        return HeartStore()
    }
}

/// `GET`/`POST /account/hearts` on the Milo token server: the account's hearted pages ("book/page").
struct HeartService {
    var baseURL: URL
    var token: String
    var accessKey: String? = VoiceSettings.accessKey
    var session: URLSession = .shared

    private struct Reply: Decodable { let hearts: [String] }

    func request(loved: [String] = [], unloved: [String] = []) -> URLRequest {
        var request = URLRequest(url: baseURL.appendingPathComponent("account/hearts"))
        request.timeoutInterval = 20
        if let accessKey { request.setValue("Bearer " + accessKey, forHTTPHeaderField: "Authorization") }
        request.setValue(token, forHTTPHeaderField: AccountService.sessionHeader)
        if !loved.isEmpty || !unloved.isEmpty {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: ["loved": loved, "unloved": unloved])
        }
        return request
    }

    /// Sends the changes (if any) and returns every hearted page on the account.
    func update(loved: [String], unloved: [String]) async throws -> [String] {
        let (data, response) = try await session.data(for: request(loved: loved, unloved: unloved))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw AccountService.error(status: status, data: data) }
        return try JSONDecoder().decode(Reply.self, from: data).hearts
    }
}
