import Foundation
import Observation
import os

/// One thing a signed-in family did while reading, as `POST /account/reading` takes it. The server
/// stamps the account and device from the session; `accountId` only keeps queued events with the
/// account they belong to and is never sent. Ids and timings only: never audio or the page's text.
struct ReadingEvent: Codable, Equatable {
    enum Kind: String, Codable {
        case appOpen = "app_open"
        case pageView = "page_view"
        case readAloud = "read_aloud"
    }

    var id: String
    var kind: Kind
    /// ISO 8601 with the device's UTC offset, so the server counts the device's own date.
    var at: String
    var accountId: String
    var book: String?
    var page: String?
    var pageIndex: Int?
    var seconds: Double?
    /// Milo (voice mode) was on while the page was shown.
    var milo: Bool?
    var outcome: String?
    var mode: String?

    /// The JSON the server takes.
    var payload: [String: Any] {
        var body: [String: Any] = ["id": id, "kind": kind.rawValue, "at": at]
        body["book"] = book
        body["page"] = page
        body["page_index"] = pageIndex
        body["seconds"] = seconds
        body["milo"] = milo
        body["outcome"] = outcome
        body["mode"] = mode
        return body
    }
}

/// Sends a batch of reading events (`POST /account/reading`, with the session like stars and hearts).
struct ReadingService {
    var baseURL: URL
    var session: URLSession = .shared

    enum Failure: Error {
        /// Retrying won't help (the server refused the batch's shape); drop it.
        case refused
        /// Offline, signed out, too many or a server problem: keep the events and try later.
        case later
    }

    func send(_ events: [ReadingEvent], token: String) async throws {
        let service = AccountService(baseURL: baseURL, session: session)
        let request = service.request("account/reading", method: "POST", token: token, body: ["events": events.map(\.payload)])
        guard let reply = try? await session.data(for: request) else { throw Failure.later }
        let status = (reply.1 as? HTTPURLResponse)?.statusCode ?? 0
        if (200..<300).contains(status) { return }
        throw [400, 413].contains(status) ? Failure.refused : Failure.later
    }
}

/// Records what a signed-in family reads (page views, read-aloud use and one app open a day per device)
/// and sends it to the server in batches, so the owner can see how the books are used. Events wait in a
/// queue saved on the device (capped, oldest dropped first) until they're sent: when the app comes to
/// the foreground or goes to the background, every minute while it's open, and once a batch fills up.
/// Nothing is recorded while signed out or in a demo launch, and a failure never touches reading.
@MainActor
@Observable
final class ReadingActivity {
    static let maxQueue = 500
    static let batchSize = 100
    /// Send early once this many events are waiting.
    static let sendAt = 50
    /// Longest time counted on one page (the server caps it too), and shortest worth recording.
    static let maxPageSeconds = 600.0
    static let minPageSeconds = 1.0
    nonisolated private static let log = Logger(subsystem: "com.angilyu.littleexplorer", category: "reading")

    private struct OpenPage: Equatable {
        let book: String
        let page: String
        let index: Int
        let milo: Bool
        let since: Date
    }

    @ObservationIgnored private(set) var queue: [ReadingEvent]
    @ObservationIgnored private let enabled: Bool
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let key: String
    @ObservationIgnored private let accountId: () -> String?
    @ObservationIgnored private let token: () -> String?
    @ObservationIgnored private let send: ([ReadingEvent], String) async throws -> Void
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let calendar: Calendar
    @ObservationIgnored private var openPage: OpenPage?
    @ObservationIgnored private var sending: Task<Void, Never>?

    init(
        enabled: Bool = true,
        defaults: UserDefaults = .standard,
        key: String = "reading.queue.v1",
        accountId: @escaping () -> String?,
        token: @escaping () -> String?,
        send: @escaping ([ReadingEvent], String) async throws -> Void,
        now: @escaping () -> Date = Date.init,
        calendar: Calendar = .current
    ) {
        self.enabled = enabled
        self.defaults = defaults
        self.key = key
        self.accountId = accountId
        self.token = token
        self.send = send
        self.now = now
        self.calendar = calendar
        queue = enabled ? (defaults.data(forKey: key).flatMap { try? JSONDecoder().decode([ReadingEvent].self, from: $0) } ?? []) : []
    }

    private var lastOpenKey: String { key + ".lastOpen" }

    // -- recording --------------------------------------------------------------------------------

    /// The app is open for the signed-in account: recorded once a day per device and account.
    func appOpened() {
        guard let account = signedIn() else { return }
        let mark = "\(account) \(day(now()))"
        guard defaults.string(forKey: lastOpenKey) != mark else { return }
        defaults.set(mark, forKey: lastOpenKey)
        add(ReadingEvent(id: UUID().uuidString, kind: .appOpen, at: stamp(now()), accountId: account))
    }

    /// A page is on screen. Ends the page shown before (if it was a different one).
    func pageShown(book: String, page: String, index: Int, milo: Bool) {
        if let openPage, openPage.book == book, openPage.page == page { return }
        pageHidden()
        guard enabled else { return }
        openPage = OpenPage(book: book, page: page, index: index, milo: milo, since: now())
    }

    /// The page on screen went away (another page, the book closed, or the app went to the background).
    func pageHidden() {
        guard let open = openPage else { return }
        openPage = nil
        let seconds = now().timeIntervalSince(open.since)
        guard seconds >= Self.minPageSeconds, let account = signedIn() else { return }
        add(ReadingEvent(
            id: UUID().uuidString, kind: .pageView, at: stamp(open.since), accountId: account,
            book: open.book, page: open.page, pageIndex: open.index,
            seconds: (min(seconds, Self.maxPageSeconds) * 10).rounded() / 10, milo: open.milo
        ))
    }

    /// Read-aloud started, finished or was stopped early on a page (`index` is its place in the book).
    func readAloud(_ use: ReadAloudController.Use, index: Int?) {
        guard let account = signedIn() else { return }
        let parts = use.pageId.split(separator: "/", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return }
        add(ReadingEvent(
            id: UUID().uuidString, kind: .readAloud, at: stamp(now()), accountId: account,
            book: parts[0], page: parts[1], pageIndex: index,
            seconds: use.seconds.map { (min($0, Self.maxPageSeconds) * 10).rounded() / 10 },
            outcome: use.outcome.rawValue, mode: use.mode.rawValue
        ))
    }

    /// The account signed out or was deleted: its unsent events are dropped.
    func forgetAccount(_ accountId: String) {
        openPage = nil
        queue.removeAll { $0.accountId == accountId }
        save()
    }

    // -- sending ----------------------------------------------------------------------------------

    /// Sends the signed-in account's waiting events, a batch at a time (after any send already under
    /// way). Never throws; what can't be sent now stays queued.
    func flush() async {
        while let sending { await sending.value }
        guard enabled, let account = signedIn(), let token = token() else { return }
        let task = Task {
            await sendQueued(account: account, token: token)
            sending = nil  // before anyone waiting on it resumes
        }
        sending = task
        await task.value
    }

    private func sendQueued(account: String, token: String) async {
        while true {
            let batch = Array(queue.filter { $0.accountId == account }.prefix(Self.batchSize))
            guard !batch.isEmpty else { return }
            do {
                try await send(batch, token)
            } catch ReadingService.Failure.refused {
                Self.log.error("server refused \(batch.count) reading events; dropping them")
            } catch {
                return
            }
            let sent = Set(batch.map(\.id))
            queue.removeAll { sent.contains($0.id) }
            save()
        }
    }

    // -- helpers ----------------------------------------------------------------------------------

    private func signedIn() -> String? {
        guard enabled, let account = accountId() else { return nil }
        return account
    }

    private func add(_ event: ReadingEvent) {
        queue.append(event)
        if queue.count > Self.maxQueue { queue.removeFirst(queue.count - Self.maxQueue) }
        save()
        if queue.count >= Self.sendAt { Task { await flush() } }
    }

    private func save() {
        guard enabled else { return }
        if queue.isEmpty {
            defaults.removeObject(forKey: key)
        } else if let data = try? JSONEncoder().encode(queue) {
            defaults.set(data, forKey: key)
        }
    }

    private func day(_ date: Date) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    private func stamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = calendar.timeZone
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}

extension ReadingActivity {
    /// The app's recorder: the signed-in account's session, off in demo launches.
    static func live(account: AccountStore) -> ReadingActivity {
        ReadingActivity(
            enabled: DemoLaunch.current == nil,
            accountId: { [weak account] in account?.account?.id },
            token: { [weak account] in account?.token },
            send: { events, token in try await ReadingService(baseURL: VoiceSettings.tokenServerURL()).send(events, token: token) }
        )
    }
}
