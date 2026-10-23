import Foundation
import Observation

/// The books a grown-up made in the app ("Make a book"), and made books the owner put on this account's
/// shelf. They come from the account's own manifest (`GET /account/content/manifest.json`), never from the
/// catalog every install downloads, so one family's books never reach another's device. Kept in their own
/// folder next to the catalog's downloads and forgotten at sign-out.
@MainActor
@Observable
final class MadeBookLibrary {
    private(set) var books: [Book] = []
    private(set) var accountId: String?
    private(set) var lastSynced: Date?

    @ObservationIgnored let dir: URL
    @ObservationIgnored private let enabled: Bool
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let serverURL: () -> URL
    @ObservationIgnored private let accessKey: String?
    @ObservationIgnored private let session: URLSession
    @ObservationIgnored private let token: () -> String?
    @ObservationIgnored private var syncing = false
    @ObservationIgnored private var syncAgain = false
    /// Don't ask the server again within this long, unless forced (a book just finished).
    @ObservationIgnored var minimumInterval: TimeInterval = 300

    nonisolated static var defaultDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MadeBooks", isDirectory: true)
    }

    static let savedKey = "made.v1"

    private struct Saved: Codable {
        var account: String
        var version: String?
        var order: [String]
    }

    init(
        dir: URL = MadeBookLibrary.defaultDir,
        enabled: Bool = true,
        defaults: UserDefaults = .standard,
        serverURL: @escaping () -> URL = { VoiceSettings.tokenServerURL() },
        accessKey: String? = VoiceSettings.accessKey,
        session: URLSession = .shared,
        token: @escaping () -> String?
    ) {
        self.dir = dir
        self.enabled = enabled
        self.defaults = defaults
        self.serverURL = serverURL
        self.accessKey = accessKey
        self.session = session
        self.token = token
    }

    private var files: ContentFiles { ContentFiles(cacheDir: dir, bundle: .main) }

    private var saved: Saved? {
        defaults.data(forKey: Self.savedKey).flatMap { try? JSONDecoder().decode(Saved.self, from: $0) }
    }

    /// Shows the account's made books from the device; another account's are deleted first.
    func switchAccount(_ accountId: String?) {
        guard enabled, accountId != self.accountId else { return }
        self.accountId = accountId
        lastSynced = nil
        guard let accountId else {
            books = []
            return
        }
        if let saved, saved.account != accountId { wipe() }
        books = load()
    }

    /// At sign-out: the made books leave the device with the account.
    func forget() {
        guard enabled else { return }
        accountId = nil
        lastSynced = nil
        wipe()
    }

    /// Brings the device's made books up to the account's manifest (downloads only what changed).
    func sync(force: Bool = false) async {
        guard enabled, let accountId, let token = token() else { return }
        if syncing { syncAgain = syncAgain || force; return }
        if !force, let lastSynced, Date().timeIntervalSince(lastSynced) < minimumInterval { return }
        syncing = true
        defer { syncing = false }
        repeat {
            syncAgain = false
            await syncOnce(accountId: accountId, token: token)
        } while syncAgain && self.accountId == accountId
    }

    private func syncOnce(accountId: String, token: String) async {
        let access = accessKey
        let updater = ContentUpdater(
            files: files, baseURL: serverURL().appendingPathComponent("account/content"), session: session,
            authorize: { request in
                var request = request
                if let access { request.setValue("Bearer " + access, forHTTPHeaderField: "Authorization") }
                request.setValue(token, forHTTPHeaderField: AccountService.sessionHeader)
                return request
            },
            allowsEmpty: true
        )
        let manifest: ContentManifest
        do {
            manifest = try await updater.fetchManifest()
        } catch ContentUpdateError.server(status: 404) {
            // A server from before made books: nothing made on this account.
            manifest = ContentManifest(format: 1, version: "none", order: [], files: [:])
        } catch {
            return
        }
        guard self.accountId == accountId else { return }
        do {
            _ = try await updater.apply(manifest, knownVersion: saved?.account == accountId ? saved?.version : nil)
        } catch {
            return
        }
        guard self.accountId == accountId else { return }
        save(Saved(account: accountId, version: manifest.version, order: manifest.order))
        books = load()
        lastSynced = Date()
        NarrationLibrary.reload()
    }

    private func save(_ saved: Saved) {
        if let data = try? JSONEncoder().encode(saved) { defaults.set(data, forKey: Self.savedKey) }
    }

    /// The saved books that load, each marked as made (the server marks them too).
    private func load() -> [Book] {
        guard let saved, saved.account == accountId else { return [] }
        return saved.order.compactMap { id in
            guard let data = try? Data(contentsOf: dir.appendingPathComponent("\(id).json")),
                  var book = try? BookLibrary.decode(data) else { return nil }
            book.generated = true
            return book
        }
    }

    private func wipe() {
        defaults.removeObject(forKey: Self.savedKey)
        try? FileManager.default.removeItem(at: dir)
        ContentFiles.clearImageCache()
        books = []
        NarrationLibrary.reload()
    }

    /// Demo launches show these (nothing is saved or fetched).
    func seedDemo(_ books: [Book]) {
        guard !enabled else { return }
        self.books = books
    }
}

extension MadeBookLibrary {
    static func live(account: AccountStore) -> MadeBookLibrary {
        MadeBookLibrary(enabled: DemoLaunch.current == nil, token: { account.token })
    }
}
