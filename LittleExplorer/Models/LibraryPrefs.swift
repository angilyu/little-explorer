import Observation
import SwiftUI

/// How a grown-up arranged the shelf in "Arrange library": hidden books, the order of the sections
/// (a section is a book's `city`, e.g. "Paris" or "Travel") and the order of the books in each.
/// The server keeps one per account (`GET`/`PUT /account/library`, see server/src/little_explorer_agent/library.py)
/// and merges each key on its own, keeping the later change (`t`, milliseconds since 1970).
/// An empty order means the default order and `on: false` means shown; they stay so an older change
/// from another device can't bring the old setting back.
///
/// Applying it never drops or invents a book: ids and sections it doesn't know are skipped, and books
/// and sections it doesn't mention (a new catalog book, a section a grown-up made a book in) keep their
/// default place after the ones it orders. Section names match by ``BookLibrary/sectionKey(_:)``, the
/// same way the shelf groups them.
struct LibraryPrefs: Codable, Equatable {
    struct Order: Codable, Equatable {
        var order: [String]
        var t: Int64
    }

    struct Flag: Codable, Equatable {
        var on: Bool
        var t: Int64
    }

    var v = 0
    var sections: Order?
    var books: [String: Order] = [:]
    var hidden: [String: Flag] = [:]
    var updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case v, sections, books, hidden
        case updatedAt = "updated_at"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        v = (try? c.decodeIfPresent(Int.self, forKey: .v)) ?? 0
        sections = try? c.decodeIfPresent(Order.self, forKey: .sections)
        books = (try? c.decodeIfPresent([String: Order].self, forKey: .books)) ?? [:]
        hidden = (try? c.decodeIfPresent([String: Flag].self, forKey: .hidden)) ?? [:]
        updatedAt = try? c.decodeIfPresent(String.self, forKey: .updatedAt)
    }

    static func millis(_ date: Date) -> Int64 { Int64((date.timeIntervalSince1970 * 1000).rounded(.down)) }

    // MARK: - Applying

    func isHidden(_ bookId: String) -> Bool { hidden[bookId]?.on == true }

    /// Every book, in the arranged order: sections first in the saved order, then the rest in their
    /// default order; inside each, the saved book order, then new books in default order.
    func arranged(_ books: [Book]) -> [Book] {
        arrangedShelves(books).flatMap(\.books)
    }

    func arrangedShelves(_ books: [Book]) -> [CityShelf] {
        let shelves = BookLibrary.shelves(books)
        let bookOrders = bookOrdersBySection
        let sectionOrder = (sections?.order ?? []).map(BookLibrary.sectionKey)
        return Self.ordered(shelves, by: sectionOrder, key: { BookLibrary.sectionKey($0.city) }).map { shelf in
            CityShelf(city: shelf.city, books: Self.ordered(shelf.books, by: bookOrders[BookLibrary.sectionKey(shelf.city)] ?? [], key: \.id))
        }
    }

    /// Each section's saved book order by ``BookLibrary/sectionKey(_:)``; when two saved names mean the
    /// same section, the later change counts.
    private var bookOrdersBySection: [String: [String]] {
        var latest: [String: Order] = [:]
        for (name, order) in books {
            let key = BookLibrary.sectionKey(name)
            if let saved = latest[key], saved.t > order.t { continue }
            latest[key] = order
        }
        return latest.mapValues(\.order)
    }

    /// What the child sees: the arranged books without the hidden ones (a section with none left goes too).
    func visible(_ books: [Book]) -> [Book] {
        arranged(books).filter { !isHidden($0.id) }
    }

    private static func ordered<T>(_ items: [T], by saved: [String], key: (T) -> String) -> [T] {
        guard !saved.isEmpty else { return items }
        let rank = Dictionary(saved.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
        let listed = items.filter { rank[key($0)] != nil }.sorted { rank[key($0)]! < rank[key($1)]! }
        return listed + items.filter { rank[key($0)] == nil }
    }

    // MARK: - Changing

    mutating func setHidden(_ bookId: String, _ on: Bool, at t: Int64) {
        guard isHidden(bookId) != on else { return }
        hidden[bookId] = Flag(on: on, t: t)
    }

    /// Saves `order` (every section on screen, in its new order) as the section order.
    mutating func setSectionOrder(_ order: [String], at t: Int64) {
        sections = Order(order: order, t: t)
    }

    /// Saves `order` (every book in `section`, in its new order) as that section's order.
    mutating func setBookOrder(_ order: [String], in section: String, at t: Int64) {
        books[section] = Order(order: order, t: t)
    }

    /// Back to the catalog order for the sections and every section's books. Hidden books stay hidden.
    mutating func restoreOrder(at t: Int64) {
        if sections.map({ !$0.order.isEmpty }) == true { sections = Order(order: [], t: t) }
        for (name, order) in books where !order.order.isEmpty {
            books[name] = Order(order: [], t: t)
        }
    }

    mutating func showAll(at t: Int64) {
        for (id, flag) in hidden where flag.on {
            hidden[id] = Flag(on: false, t: t)
        }
    }

    var hasCustomOrder: Bool {
        sections.map { !$0.order.isEmpty } == true || books.values.contains { !$0.order.isEmpty }
    }

    var hiddenCount: Int { hidden.values.filter(\.on).count }

    // MARK: - Merging

    /// This merged with `other` key by key, keeping the later change; a tie keeps `other`'s.
    func merging(_ other: LibraryPrefs) -> LibraryPrefs {
        var merged = self
        merged.v = max(v, other.v)
        merged.updatedAt = other.updatedAt ?? updatedAt
        if let incoming = other.sections, incoming.t >= (sections?.t ?? .min) { merged.sections = incoming }
        for (name, incoming) in other.books where incoming.t >= (books[name]?.t ?? .min) {
            merged.books[name] = incoming
        }
        for (id, incoming) in other.hidden where incoming.t >= (hidden[id]?.t ?? .min) {
            merged.hidden[id] = incoming
        }
        return merged
    }

    /// The body of `PUT /account/library`.
    var payload: [String: Any] {
        var body: [String: Any] = [
            "books": books.mapValues { ["order": $0.order, "t": $0.t] as [String: Any] },
            "hidden": hidden.mapValues { ["on": $0.on, "t": $0.t] as [String: Any] },
        ]
        if let sections { body["sections"] = ["order": sections.order, "t": sections.t] as [String: Any] }
        return body
    }
}

/// `GET` and `PUT /account/shelf`: the arrangement and the shelf the server lays out with it. A server from
/// before 1.2.6 answers 404 there, so the app falls back to `/account/library` (the arrangement only).
struct LibraryService {
    var baseURL: URL
    var session: URLSession = .shared

    enum Failure: Error {
        /// The server refused the settings (bad shape or too many); sending them again won't help.
        case refused
        /// Offline, signed out or a server problem: try later.
        case later
    }

    /// Sends `prefs` when given (the server merges them in) or just fetches; returns the account's settings and,
    /// from a 1.2.6 server, its shelf layout.
    func sync(_ prefs: LibraryPrefs?, token: String) async throws -> LibraryReply {
        if let reply = try await send("account/shelf", prefs, token: token) { return reply }
        guard let reply = try await send("account/library", prefs, token: token) else { throw Failure.later }
        return reply
    }

    /// nil when the server doesn't have `path`.
    private func send(_ path: String, _ prefs: LibraryPrefs?, token: String) async throws -> LibraryReply? {
        let service = AccountService(baseURL: baseURL, session: session)
        let request = prefs.map { service.request(path, method: "PUT", token: token, body: $0.payload) }
            ?? service.request(path, method: "GET", token: token)
        guard let (data, response) = try? await session.data(for: request) else { throw Failure.later }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 404 { return nil }
        if [400, 413].contains(status) { throw Failure.refused }
        guard status == 200, let reply = try? JSONDecoder().decode(LibraryReply.self, from: data) else { throw Failure.later }
        return reply
    }
}

/// The signed-in account's ``LibraryPrefs`` and the ``ShelfLayout`` the server made with them: a copy saved on
/// the device per account (so the shelf shows offline and at launch), changes saved at once and sent to the
/// server, and the server's copy fetched when the app opens or comes back. Forgotten at sign-out. In a demo
/// launch nothing is saved or sent.
///
/// The shelf and "Arrange library" draw the server's layout. Without one (a server from before 1.2.6, or never
/// reached), or while a change hasn't reached the server yet, the device arranges the shelf itself from the prefs.
@MainActor
@Observable
final class LibraryStore {
    private(set) var prefs = LibraryPrefs()
    /// The server's layout of the shelf, as of the last sync.
    private(set) var layout: ShelfLayout?
    private(set) var accountId: String?
    /// Changed here and not yet sent.
    private(set) var dirty = false

    @ObservationIgnored private let enabled: Bool
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let token: () -> String?
    @ObservationIgnored private let send: (LibraryPrefs?, String) async throws -> LibraryReply
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var syncing = false
    @ObservationIgnored private var syncAgain = false

    init(
        enabled: Bool = true,
        defaults: UserDefaults = .standard,
        token: @escaping () -> String?,
        send: @escaping (LibraryPrefs?, String) async throws -> LibraryReply,
        now: @escaping () -> Date = Date.init
    ) {
        self.enabled = enabled
        self.defaults = defaults
        self.token = token
        self.send = send
        self.now = now
    }

    private struct Saved: Codable {
        var prefs: LibraryPrefs
        var dirty: Bool
        var layout: ShelfLayout?
    }

    private func key(_ accountId: String) -> String { AccountKeys.key("library.v1", accountId) }

    func switchAccount(_ accountId: String?) {
        guard accountId != self.accountId else { return }
        self.accountId = accountId
        let saved = enabled ? accountId.flatMap { defaults.data(forKey: key($0)) }.flatMap { try? JSONDecoder().decode(Saved.self, from: $0) } : nil
        prefs = saved?.prefs ?? LibraryPrefs()
        dirty = saved?.dirty ?? false
        layout = saved?.layout
    }

    func forgetAccount(_ accountId: String) {
        defaults.removeObject(forKey: key(accountId))
        if self.accountId == accountId {
            self.accountId = nil
            prefs = LibraryPrefs()
            layout = nil
            dirty = false
        }
    }

    /// Demo launches start from these settings (nothing is saved).
    func seedDemo(_ prefs: LibraryPrefs, layout: ShelfLayout? = nil) {
        guard !enabled else { return }
        self.prefs = prefs
        self.layout = layout
    }

    /// The layout the shelf follows: the server's, unless this device changed something it hasn't seen yet.
    private var currentLayout: ShelfLayout? { dirty ? nil : layout }

    /// Every book in its section, in order, hidden ones too (what "Arrange library" lists).
    func arrangedShelves(_ books: [Book]) -> [CityShelf] {
        currentLayout?.shelves(books, fallback: prefs) ?? prefs.arrangedShelves(books)
    }

    func isHidden(_ bookId: String) -> Bool { currentLayout?.isHidden(bookId) ?? prefs.isHidden(bookId) }

    /// What the child sees: the arranged sections without hidden books (a section with none left goes too).
    func visibleShelves(_ books: [Book]) -> [CityShelf] {
        arrangedShelves(books).compactMap { shelf in
            var shelf = shelf
            shelf.books.removeAll { isHidden($0.id) }
            return shelf.books.isEmpty ? nil : shelf
        }
    }

    func arranged(_ books: [Book]) -> [Book] { arrangedShelves(books).flatMap(\.books) }
    func visible(_ books: [Book]) -> [Book] { visibleShelves(books).flatMap(\.books) }

    // MARK: - Changes

    func setHidden(_ bookId: String, _ on: Bool) { change { $0.setHidden(bookId, on, at: $1) } }

    func moveSections(_ current: [String], from source: IndexSet, to destination: Int) {
        var order = current
        order.move(fromOffsets: source, toOffset: destination)
        guard order != current else { return }
        change { $0.setSectionOrder(order, at: $1) }
    }

    func moveBooks(_ current: [String], in section: String, from source: IndexSet, to destination: Int) {
        var order = current
        order.move(fromOffsets: source, toOffset: destination)
        guard order != current else { return }
        change { $0.setBookOrder(order, in: section, at: $1) }
    }

    func restoreOrder() { change { $0.restoreOrder(at: $1) } }
    func showAll() { change { $0.showAll(at: $1) } }

    private func change(_ edit: (inout LibraryPrefs, Int64) -> Void) {
        guard accountId != nil else { return }
        var next = prefs
        edit(&next, LibraryPrefs.millis(now()))
        guard next != prefs else { return }
        prefs = next
        dirty = true
        save()
        guard enabled else { return }
        Task { await sync() }
    }

    private func save() {
        guard enabled, let accountId, let data = try? JSONEncoder().encode(Saved(prefs: prefs, dirty: dirty, layout: layout)) else { return }
        defaults.set(data, forKey: key(accountId))
    }

    // MARK: - Sync

    /// Sends this device's changes (or just fetches the account's settings) and keeps the result.
    /// Changes made while the request was out are merged on top and sent next time.
    func sync() async {
        guard enabled, let accountId, let token = token() else { return }
        if syncing { syncAgain = true; return }
        syncing = true
        defer { syncing = false }
        repeat {
            syncAgain = false
            let sent = prefs
            let sending = dirty
            do {
                let reply = try await send(sending ? sent : nil, token)
                guard self.accountId == accountId else { return }
                layout = reply.shelf
                if prefs == sent {
                    prefs = reply.library
                    dirty = false
                } else {
                    prefs = reply.library.merging(prefs)
                    syncAgain = true
                }
            } catch LibraryService.Failure.refused where sending {
                // The server won't take these; keep its copy so the device stops retrying them.
                guard self.accountId == accountId, prefs == sent else { return }
                if let reply = try? await send(nil, token), self.accountId == accountId, prefs == sent {
                    prefs = reply.library
                    layout = reply.shelf
                    dirty = false
                }
            } catch {
                return
            }
            save()
        } while syncAgain
    }

    static func live(account: AccountStore) -> LibraryStore {
        LibraryStore(
            enabled: DemoLaunch.current == nil,
            token: { [weak account] in account?.token },
            send: { prefs, token in try await LibraryService(baseURL: VoiceSettings.tokenServerURL()).sync(prefs, token: token) }
        )
    }
}
