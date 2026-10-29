import Foundation
import Observation

/// One book (or one page) being made, as the server tells a grown-up (`Job.parent_json` in bookgen/store.py).
struct MakeJob: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let bookId: String
    /// "book" for a new book, "page" for one page made again.
    let kind: String
    /// queued, running, done, failed or rejected.
    let status: String
    let step: String?
    /// "Writing…", "Finding photos…", "Recording…" or "Almost ready…" while it's being made.
    let progress: String?
    let title: String
    let section: String
    let level: Int?
    let page: String?
    /// Why it wasn't made, for a grown-up.
    let message: String?
    /// When it stopped being made (done, failed or rejected).
    let finishedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, kind, status, step, progress, title, section, level, page, message
        case bookId = "book_id"
        case finishedAt = "finished_at"
    }

    init(id: String, bookId: String, kind: String = "book", status: String, step: String? = nil, progress: String? = nil,
         title: String, section: String, level: Int? = nil, page: String? = nil, message: String? = nil,
         finishedAt: Date? = nil) {
        self.id = id
        self.bookId = bookId
        self.kind = kind
        self.status = status
        self.step = step
        self.progress = progress
        self.title = title
        self.section = section
        self.level = level
        self.page = page
        self.message = message
        self.finishedAt = finishedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        bookId = try c.decodeIfPresent(String.self, forKey: .bookId) ?? ""
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "book"
        status = try c.decode(String.self, forKey: .status)
        step = try c.decodeIfPresent(String.self, forKey: .step)
        progress = try c.decodeIfPresent(String.self, forKey: .progress)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        section = try c.decodeIfPresent(String.self, forKey: .section) ?? ""
        level = try c.decodeIfPresent(Int.self, forKey: .level)
        page = try c.decodeIfPresent(String.self, forKey: .page)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        finishedAt = (try? c.decodeIfPresent(String.self, forKey: .finishedAt)).flatMap { $0 }
            .flatMap { ISO8601DateFormatter().date(from: $0) }
    }

    var isActive: Bool { status == "queued" || status == "running" }
    var didFail: Bool { status == "failed" || status == "rejected" }
    var isPage: Bool { kind == "page" }
    /// What shows under a placeholder cover.
    var progressText: String { progress ?? "Getting ready…" }
}

/// How many books the account may still make (`BookStore.usage` on the server).
struct MakeUsage: Decodable, Equatable, Sendable {
    var perDay = 3
    var perMonth = 10
    var today = 0
    var thisMonth = 0
    var leftToday = 3
    var leftThisMonth = 10
    var triesToday = 0
    var triesPerDay = 8
    var pageRedosToday = 0
    var pageRedosPerDay = 5
    var pageRedosLeft = 5

    enum CodingKeys: String, CodingKey {
        case today
        case perDay = "per_day"
        case perMonth = "per_month"
        case thisMonth = "this_month"
        case leftToday = "left_today"
        case leftThisMonth = "left_this_month"
        case triesToday = "tries_today"
        case triesPerDay = "tries_per_day"
        case pageRedosToday = "page_redos_today"
        case pageRedosPerDay = "page_redos_per_day"
        case pageRedosLeft = "page_redos_left"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func int(_ key: CodingKeys, _ fallback: Int) -> Int { (try? c.decodeIfPresent(Int.self, forKey: key)) ?? fallback }
        perDay = int(.perDay, 3)
        perMonth = int(.perMonth, 10)
        today = int(.today, 0)
        thisMonth = int(.thisMonth, 0)
        leftToday = int(.leftToday, max(0, perDay - today))
        leftThisMonth = int(.leftThisMonth, max(0, perMonth - thisMonth))
        triesToday = int(.triesToday, 0)
        triesPerDay = int(.triesPerDay, 8)
        pageRedosToday = int(.pageRedosToday, 0)
        pageRedosPerDay = int(.pageRedosPerDay, 5)
        pageRedosLeft = int(.pageRedosLeft, max(0, pageRedosPerDay - pageRedosToday))
    }

    /// Books that may still be made now.
    var left: Int { max(0, min(leftToday, leftThisMonth)) }
    var triesLeft: Int { max(0, triesPerDay - triesToday) }

    /// Why no book can be made right now, or nil.
    var limitMessage: String? {
        if leftThisMonth <= 0 { return "You've made \(perMonth) books this month. You can make more next month." }
        if leftToday <= 0 { return "You've made \(perDay) books today. You can make more tomorrow." }
        if triesLeft <= 0 { return "That's a lot of tries for one day. Try again tomorrow." }
        return nil
    }

    /// "2 books left today · 8 left this month"
    var summary: String {
        let day = "\(leftToday) \(leftToday == 1 ? "book" : "books") left today"
        return "\(day) · \(leftThisMonth) left this month"
    }
}

/// `GET /account/bookgen`: what the Make a book screen needs.
struct MakeStatus: Decodable, Equatable, Sendable {
    struct Fields: Decodable, Equatable, Sendable {
        var titleMax = 40
        var sectionMax = 30
        var aboutMax = 300

        enum CodingKeys: String, CodingKey {
            case titleMax = "title_max"
            case sectionMax = "section_max"
            case aboutMax = "about_max"
        }
    }

    var canGenerate: Bool
    var ready = true
    var usage = MakeUsage()
    var fields = Fields()
    var active: MakeJob?
    var jobs: [MakeJob] = []

    enum CodingKeys: String, CodingKey {
        case ready, usage, fields, active, jobs
        case canGenerate = "can_generate"
    }

    init(canGenerate: Bool, ready: Bool = true, usage: MakeUsage = MakeUsage(), active: MakeJob? = nil, jobs: [MakeJob] = []) {
        self.canGenerate = canGenerate
        self.ready = ready
        self.usage = usage
        self.active = active
        self.jobs = jobs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        canGenerate = try c.decodeIfPresent(Bool.self, forKey: .canGenerate) ?? false
        ready = try c.decodeIfPresent(Bool.self, forKey: .ready) ?? false
        usage = try c.decodeIfPresent(MakeUsage.self, forKey: .usage) ?? MakeUsage()
        fields = try c.decodeIfPresent(Fields.self, forKey: .fields) ?? Fields()
        active = try c.decodeIfPresent(MakeJob.self, forKey: .active)
        jobs = try c.decodeIfPresent([MakeJob].self, forKey: .jobs) ?? []
    }
}

/// A book this account made, from `GET /account/books`.
struct MadeBookInfo: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let title: String
    let section: String
    let level: Int?
    let status: String
    let pages: Int?
}

/// What a grown-up can tell the owner about a made book (`REPORT_REASONS` on the server).
enum ReportReason: String, CaseIterable, Identifiable, Sendable {
    case wrong, scary, photo, other

    var id: String { rawValue }

    var label: String {
        switch self {
        case .wrong: "Something isn't true"
        case .scary: "Not right for children"
        case .photo: "A photo is wrong"
        case .other: "Something else"
        }
    }
}

/// A refusal from the server, with words for a grown-up.
struct MakeError: Error, Equatable {
    /// The server's `error` code: bad_request, not_allowed, not_ready, busy, day_limit, month_limit,
    /// tries_limit, page_limit, paused, not_found, signed_out, offline…
    let code: String
    let message: String

    static let offline = MakeError(code: "offline", message: "Couldn't reach Milo's server. Check the internet and try again.")

    var isLimit: Bool { ["day_limit", "month_limit", "tries_limit", "page_limit", "paused"].contains(code) }
}

/// The grown-ups' book-making endpoints on the Milo token server (signed in, like the other account calls).
struct BookMakerService: Sendable {
    var baseURL: URL
    var accessKey: String? = VoiceSettings.accessKey
    var session: URLSession = .shared

    private var account: AccountService { AccountService(baseURL: baseURL, accessKey: accessKey, session: session) }

    func status(token: String) async throws -> MakeStatus {
        try decode(MakeStatus.self, await send(account.request("account/bookgen", token: token)))
    }

    func make(token: String, title: String, section: String, level: Int, about: String) async throws -> (MakeJob, MakeUsage) {
        var body: [String: Any] = ["title": title, "section": section, "level": level]
        if !about.isEmpty { body["about"] = about }
        let reply = try decode(JobReply.self, await send(account.request("account/books", method: "POST", token: token, body: body)))
        return (reply.job, reply.usage ?? MakeUsage())
    }

    func job(token: String, id: String) async throws -> MakeJob {
        try decode(JobReply.self, await send(account.request("account/jobs/\(id)", token: token))).job
    }

    func books(token: String) async throws -> [MadeBookInfo] {
        struct Reply: Decodable { let books: [MadeBookInfo] }
        return try decode(Reply.self, await send(account.request("account/books", token: token))).books
    }

    func delete(token: String, bookId: String) async throws {
        _ = try await send(account.request("account/books/\(bookId)", method: "DELETE", token: token))
    }

    func remakePage(token: String, bookId: String, pageId: String) async throws -> (MakeJob, MakeUsage) {
        let request = account.request("account/books/\(bookId)/pages/\(pageId)/regenerate", method: "POST", token: token, body: [:])
        let reply = try decode(JobReply.self, await send(request))
        return (reply.job, reply.usage ?? MakeUsage())
    }

    func report(token: String, bookId: String, reason: ReportReason, note: String, page: String?) async throws {
        var body: [String: Any] = ["reason": reason.rawValue, "note": note]
        if let page { body["page"] = page }
        _ = try await send(account.request("account/books/\(bookId)/report", method: "POST", token: token, body: body))
    }

    private struct JobReply: Decodable {
        let job: MakeJob
        let usage: MakeUsage?
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw MakeError(code: "bad_reply", message: "Milo's server sent something unexpected. Try again.")
        }
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw MakeError.offline
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard !(200..<300).contains(status) else { return data }
        throw Self.error(status: status, data: data)
    }

    static func error(status: Int, data: Data) -> MakeError {
        let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let code = reply["error"] as? String ?? "server"
        if let message = reply["message"] as? String, !message.isEmpty { return MakeError(code: code, message: message) }
        switch code {
        case "signed_out": return MakeError(code: code, message: AccountError.signedOut.message)
        default: return MakeError(code: code, message: "Milo's server had a problem (\(status)). Try again.")
        }
    }
}

/// Books being made for this account: starts them, follows them while the app is open (only then, and
/// only while one is being made), and tells the made-books library to fetch a book once it's ready.
@MainActor
@Observable
final class BookMaker {
    private(set) var status: MakeStatus?
    /// Jobs being made right now (books show as placeholder covers, pages as a note on their book).
    private(set) var active: [MakeJob] = []
    /// The latest job that ended without a book, so the grown-up sees why.
    private(set) var lastFailure: MakeJob?
    /// Book ids this account made itself (it may delete them and make their pages again).
    private(set) var ownBooks: Set<String> = []

    @ObservationIgnored private let enabled: Bool
    @ObservationIgnored private let service: () -> BookMakerService
    @ObservationIgnored private let token: () -> String?
    @ObservationIgnored private var poller: Task<Void, Never>?
    @ObservationIgnored private(set) var foreground = true
    /// How long to wait between checks on a book being made.
    @ObservationIgnored var pollInterval: Duration = .seconds(4)
    /// Called when a book or page is ready, to download it.
    @ObservationIgnored var onReady: (() async -> Void)?
    @ObservationIgnored var now: () -> Date = Date.init
    /// How long a book that couldn't be made keeps its note, when the app wasn't open to see it end.
    static let failureShownFor: TimeInterval = 3 * 24 * 3600

    init(enabled: Bool = true, service: @escaping () -> BookMakerService, token: @escaping () -> String?) {
        self.enabled = enabled
        self.service = service
        self.token = token
    }

    var usage: MakeUsage { status?.usage ?? MakeUsage() }
    var canMake: Bool { status?.canGenerate == true }
    var isPolling: Bool { poller != nil }
    var placeholders: [MakeJob] { active.filter { !$0.isPage } }

    /// What's happening to this made book's page, if one is being made again.
    func remaking(_ bookId: String) -> MakeJob? { active.first { $0.isPage && $0.bookId == bookId } }

    /// Asks the server whether this account may make books, how many are left and what's being made.
    func refresh() async {
        guard enabled, let token = token() else { return }
        guard let status = try? await service().status(token: token) else { return }
        self.status = status
        let running = status.jobs.filter(\.isActive) + [status.active].compactMap { $0 }
        var seen = Set<String>()
        active = running.filter { seen.insert($0.id).inserted }
        lastFailure = recentFailure(in: status.jobs)
        if let books = try? await service().books(token: token) { ownBooks = Set(books.map(\.id)) }
        updatePolling()
    }

    /// The account's newest job, if it ended without a book not long ago. Books are made on the server,
    /// so one can fail while the app is closed; without this its placeholder would just disappear.
    private func recentFailure(in jobs: [MakeJob]) -> MakeJob? {
        guard let newest = jobs.first, newest.didFail, newest.message != nil else { return nil }
        if let finished = newest.finishedAt, now().timeIntervalSince(finished) > Self.failureShownFor { return nil }
        return newest
    }

    /// Starts making a book. Throws a ``MakeError`` with words for the grown-up when the server refuses.
    @discardableResult
    func make(title: String, section: String, level: Int, about: String) async throws -> MakeJob {
        guard let token = token() else { throw MakeError(code: "signed_out", message: AccountError.signedOut.message) }
        let (job, usage) = try await service().make(token: token, title: title, section: section, level: level, about: about)
        start(job, usage: usage)
        return job
    }

    /// Makes one page of a made book again (its own small daily limit; not a book from the quota).
    @discardableResult
    func remakePage(bookId: String, pageId: String) async throws -> MakeJob {
        guard let token = token() else { throw MakeError(code: "signed_out", message: AccountError.signedOut.message) }
        let (job, usage) = try await service().remakePage(token: token, bookId: bookId, pageId: pageId)
        start(job, usage: usage)
        return job
    }

    func delete(bookId: String) async throws {
        guard let token = token() else { throw MakeError(code: "signed_out", message: AccountError.signedOut.message) }
        try await service().delete(token: token, bookId: bookId)
        ownBooks.remove(bookId)
        await onReady?()
    }

    func report(bookId: String, reason: ReportReason, note: String, page: String?) async throws {
        guard let token = token() else { throw MakeError(code: "signed_out", message: AccountError.signedOut.message) }
        try await service().report(token: token, bookId: bookId, reason: reason, note: note, page: page)
    }

    private func start(_ job: MakeJob, usage: MakeUsage) {
        lastFailure = nil
        active.removeAll { $0.id == job.id }
        if job.isActive { active.append(job) }
        if status == nil { status = MakeStatus(canGenerate: true) }
        status?.usage = usage
        if !job.isPage { ownBooks.insert(job.bookId) }
        updatePolling()
    }

    /// The app came to the front or went away: checks on books being made only while it's in front.
    func setForeground(_ on: Bool) {
        foreground = on
        updatePolling()
    }

    /// At sign-out.
    func reset() {
        poller?.cancel()
        poller = nil
        status = nil
        active = []
        lastFailure = nil
        ownBooks = []
    }

    private func updatePolling() {
        let wanted = enabled && foreground && !active.isEmpty
        if !wanted {
            poller?.cancel()
            poller = nil
        } else if poller == nil {
            poller = Task { [weak self] in
                await self?.pollLoop()
            }
        }
    }

    private func pollLoop() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: pollInterval)
            guard !Task.isCancelled else { break }
            await checkActive()
            if active.isEmpty || !foreground { break }
        }
        if !Task.isCancelled { poller = nil }
    }

    /// One look at each job being made; a finished one brings its book.
    func checkActive() async {
        guard let token = token() else { return }
        var finished = false
        for job in active {
            guard let latest = try? await service().job(token: token, id: job.id) else { continue }
            guard let index = active.firstIndex(where: { $0.id == job.id }) else { continue }
            if latest.isActive {
                active[index] = latest
            } else {
                active.remove(at: index)
                finished = true
                if latest.status != "done" {
                    lastFailure = latest
                    if !latest.isPage { ownBooks.remove(latest.bookId) }
                }
            }
        }
        guard finished else { return }
        updatePolling()
        await onReady?()
        if let status = try? await service().status(token: token) { self.status = status }
    }

    // MARK: - Demo

    /// Demo launches show this state (nothing is fetched).
    func seedDemo(status: MakeStatus, ownBooks: Set<String>, failure: MakeJob? = nil) {
        guard !enabled else { return }
        self.status = status
        self.active = status.active.map { [$0] } ?? []
        self.ownBooks = ownBooks
        self.lastFailure = failure
    }
}

extension BookMaker {
    static func live(account: AccountStore) -> BookMaker {
        BookMaker(
            enabled: DemoLaunch.current == nil,
            service: { BookMakerService(baseURL: VoiceSettings.tokenServerURL()) },
            token: { account.token }
        )
    }

    /// A placeholder cover for a book being made: it sits in its section with the progress words.
    static func placeholder(for job: MakeJob) -> Book {
        Book(
            id: "making-\(job.id)", city: job.section, country: "", flag: "", coverColor: "#8E9AAF",
            tagline: job.progressText, theme: job.title, level: job.level.map { BookLevel($0) }, generated: true,
            pages: [BookPage(
                id: "p01", text: "", about: "", emoji: "✨",
                photo: PhotoInfo(file: nil, commonsTitle: nil, query: nil, author: nil, license: nil,
                                 licenseUrl: nil, sourceUrl: nil, verified: nil)
            )]
        )
    }

    static func isPlaceholder(_ book: Book) -> Bool { book.id.hasPrefix("making-") }
}
