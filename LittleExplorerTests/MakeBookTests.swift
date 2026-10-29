import CryptoKit
import XCTest
@testable import LittleExplorer

/// Stands in for Milo's server and the storage links for "Make a book" (host make.test and photos.test).
final class StubMakeServer: URLProtocol {
    struct Sent {
        let method: String
        let path: String
        let host: String
        let auth: String?
        let session: String?
        let body: [String: Any]
    }

    /// "METHOD /path" (or just "/path" for any method) → replies, used in order (the last one repeats).
    nonisolated(unsafe) static var routes: [String: [(Int, Data)]] = [:]
    nonisolated(unsafe) static var sent: [Sent] = []

    override class func canInit(with request: URLRequest) -> Bool {
        ["make.test", "photos.test"].contains(request.url?.host)
    }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    static func reply(_ route: String, _ status: Int = 200, json: String) {
        routes[route, default: []].append((status, Data(json.utf8)))
    }

    static func reply(_ route: String, data: Data) {
        routes[route, default: []].append((200, data))
    }

    override func startLoading() {
        var data = Data()
        if let stream = request.httpBodyStream {
            stream.open()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                if n <= 0 { break }
                data.append(buffer, count: n)
            }
            stream.close()
        }
        let method = request.httpMethod ?? "GET"
        let path = request.url!.path
        Self.sent.append(Sent(
            method: method, path: path, host: request.url!.host ?? "",
            auth: request.value(forHTTPHeaderField: "Authorization"),
            session: request.value(forHTTPHeaderField: AccountService.sessionHeader),
            body: (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        ))
        let key = Self.routes["\(method) \(path)"] != nil ? "\(method) \(path)" : path
        guard var queue = Self.routes[key], !queue.isEmpty else {
            let response = HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(#"{"error":"not_found"}"#.utf8))
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        let (status, body) = queue.count > 1 ? queue.removeFirst() : queue[0]
        Self.routes[key] = queue
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@MainActor
final class MakeBookTests: XCTestCase {
    private var urlSession: URLSession!
    private var dir: URL!
    private var defaults: UserDefaults!
    private var suiteName = ""
    private let base = URL(string: "https://make.test")!
    private var token: String? = "tok-1"

    override func setUp() {
        super.setUp()
        StubMakeServer.routes = [:]
        StubMakeServer.sent = []
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubMakeServer.self]
        urlSession = URLSession(configuration: config)
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("made-test-\(UUID().uuidString)")
        suiteName = "MakeBookTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        token = "tok-1"
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func maker(enabled: Bool = true) -> BookMaker {
        let session = urlSession!
        let base = base
        let maker = BookMaker(
            enabled: enabled,
            service: { BookMakerService(baseURL: base, accessKey: "app-key", session: session) },
            token: { [unowned self] in self.token }
        )
        maker.pollInterval = .milliseconds(20)
        return maker
    }

    private func library() -> MadeBookLibrary {
        MadeBookLibrary(
            dir: dir, defaults: defaults, serverURL: { [base] in base }, accessKey: "app-key", session: urlSession,
            token: { [unowned self] in self.token }
        )
    }

    private static func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    private func job(_ status: String, step: String? = nil, progress: String? = nil, kind: String = "book",
                     message: String? = nil) -> String {
        let progress = progress.map { "\"\($0)\"" } ?? "null"
        let message = message.map { "\"\($0)\"" } ?? "null"
        let step = step.map { "\"\($0)\"" } ?? "null"
        return #"{"id":"job-1","book_id":"the-tall-tower-a1b2c3","kind":"\#(kind)","status":"\#(status)","step":\#(step),"progress":\#(progress),"title":"The Tall Tower","section":"Paris","level":1,"page":null,"message":\#(message)}"#
    }

    private let usage = #"{"active":1,"today":1,"this_month":4,"per_day":3,"per_month":10,"left_today":2,"left_this_month":6,"tries_today":1,"tries_per_day":8,"page_redos_today":0,"page_redos_per_day":5,"page_redos_left":5}"#

    // MARK: - Account and replies

    func testAccountDecodesCanGenerateAndDefaultsToOff() throws {
        let on = try JSONDecoder().decode(
            Account.self, from: Data(#"{"id":"a","email":"x@y.com","all_books":true,"books":[],"can_generate":true}"#.utf8)
        )
        XCTAssertTrue(on.canGenerate)
        XCTAssertEqual(try JSONDecoder().decode(Account.self, from: JSONEncoder().encode(on)), on)
        let old = try JSONDecoder().decode(Account.self, from: Data(#"{"id":"a","email":"x@y.com","all_books":true,"books":[]}"#.utf8))
        XCTAssertFalse(old.canGenerate)
        XCTAssertTrue(on.with(miloConsent: true).canGenerate)
    }

    func testDecodesTheStatusAndUsage() throws {
        let json = #"{"can_generate":true,"ready":true,"levels":[],"usage":\#(usage),"fields":{"title_max":40,"section_max":30,"about_max":300},"active":\#(job("running", step: "photos", progress: "Finding photos…")),"jobs":[]}"#
        let status = try JSONDecoder().decode(MakeStatus.self, from: Data(json.utf8))
        XCTAssertTrue(status.canGenerate)
        XCTAssertEqual(status.usage.left, 2)
        XCTAssertEqual(status.usage.summary, "2 books left today · 6 left this month")
        XCTAssertNil(status.usage.limitMessage)
        XCTAssertEqual(status.fields.aboutMax, 300)
        XCTAssertEqual(status.active?.progressText, "Finding photos…")
        XCTAssertEqual(status.active?.bookId, "the-tall-tower-a1b2c3")
    }

    func testUsageExplainsEachLimit() {
        var usage = MakeUsage()
        usage.leftToday = 0
        XCTAssertEqual(usage.left, 0)
        XCTAssertEqual(usage.limitMessage, "You've made 3 books today. You can make more tomorrow.")
        usage.leftThisMonth = 0
        XCTAssertEqual(usage.limitMessage, "You've made 10 books this month. You can make more next month.")
        var tries = MakeUsage()
        tries.triesToday = 8
        XCTAssertEqual(tries.limitMessage, "That's a lot of tries for one day. Try again tomorrow.")
    }

    func testServerRefusalsKeepTheirFriendlyWords() {
        let limit = BookMakerService.error(status: 429, data: Data(#"{"error":"day_limit","message":"You've made 3 books today."}"#.utf8))
        XCTAssertEqual(limit.message, "You've made 3 books today.")
        XCTAssertTrue(limit.isLimit)
        let odd = BookMakerService.error(status: 500, data: Data("oops".utf8))
        XCTAssertEqual(odd.code, "server")
        XCTAssertFalse(odd.isLimit)
    }

    // MARK: - Making a book

    func testMakingABookSendsTheFormAndPollsUntilItIsReady() async throws {
        StubMakeServer.reply("POST /account/books", 202, json: #"{"job":\#(job("queued")),"usage":\#(usage)}"#)
        StubMakeServer.reply("/account/jobs/job-1", json: #"{"job":\#(job("running", step: "photos", progress: "Finding photos…"))}"#)
        StubMakeServer.reply("/account/jobs/job-1", json: #"{"job":\#(job("done"))}"#)
        StubMakeServer.reply("/account/bookgen", json: #"{"can_generate":true,"ready":true,"usage":\#(usage),"jobs":[]}"#)
        let maker = maker()
        maker.setForeground(false)
        var readyCalls = 0
        maker.onReady = { readyCalls += 1 }

        let started = try await maker.make(title: "The Tall Tower", section: "Paris", level: 1, about: "")
        XCTAssertEqual(started.id, "job-1")
        XCTAssertEqual(maker.placeholders.map(\.id), ["job-1"])
        XCTAssertTrue(maker.ownBooks.contains("the-tall-tower-a1b2c3"))
        XCTAssertEqual(maker.usage.leftToday, 2)
        let sent = try XCTUnwrap(StubMakeServer.sent.first)
        XCTAssertEqual(sent.body["title"] as? String, "The Tall Tower")
        XCTAssertEqual(sent.body["level"] as? Int, 1)
        XCTAssertNil(sent.body["about"], "an empty idea isn't sent")
        XCTAssertEqual(sent.session, "tok-1")
        XCTAssertEqual(sent.auth, "Bearer app-key")

        let placeholder = BookMaker.placeholder(for: maker.placeholders[0])
        XCTAssertTrue(BookMaker.isPlaceholder(placeholder))
        XCTAssertEqual(placeholder.city, "Paris")
        XCTAssertTrue(placeholder.isMade)

        await maker.checkActive()
        XCTAssertEqual(maker.active.first?.progressText, "Finding photos…")
        XCTAssertEqual(readyCalls, 0)
        await maker.checkActive()
        XCTAssertTrue(maker.active.isEmpty)
        XCTAssertEqual(readyCalls, 1)
        XCTAssertNil(maker.lastFailure)
        XCTAssertFalse(maker.isPolling)
    }

    func testAFailedBookShowsWhyAndIsNotOwned() async throws {
        StubMakeServer.reply("POST /account/books", 202, json: #"{"job":\#(job("queued")),"usage":\#(usage)}"#)
        StubMakeServer.reply("/account/jobs/job-1", json: #"{"job":\#(job("rejected", message: "This book couldn't be made: the idea isn't right for children. It didn't count toward your limit."))}"#)
        let maker = maker()
        maker.setForeground(false)
        try await maker.make(title: "The Tall Tower", section: "Paris", level: 1, about: "fights")
        await maker.checkActive()
        XCTAssertTrue(maker.active.isEmpty)
        XCTAssertEqual(maker.lastFailure?.status, "rejected")
        XCTAssertTrue(maker.lastFailure?.message?.contains("didn't count") == true)
        XCTAssertFalse(maker.ownBooks.contains("the-tall-tower-a1b2c3"))
    }

    func testABookThatFailedWhileTheAppWasClosedStillShowsWhy() async throws {
        // Made-book jobs retry for ~15 minutes on the server, so the app is often closed when one fails.
        let finished = "2026-10-29T08:00:00-07:00"
        let failed = #"{"id":"job-2","book_id":"dinosaur-79b8ae","kind":"book","status":"failed","title":"Dinosaur","section":"For Matt","level":1,"message":"Something went wrong while making this book. It didn't count toward your limit. Please try again.","finished_at":"\#(finished)"}"#
        let older = #"{"id":"job-1","book_id":"the-tall-tower-a1b2c3","kind":"book","status":"done","title":"The Tall Tower","section":"Paris","level":1,"message":null,"finished_at":"2026-10-20T08:00:00-07:00"}"#
        let maker = maker()
        maker.setForeground(false)
        let then = try XCTUnwrap(ISO8601DateFormatter().date(from: finished))
        maker.now = { then.addingTimeInterval(3600) }

        StubMakeServer.reply("/account/bookgen", json: #"{"can_generate":true,"ready":true,"usage":\#(usage),"jobs":[\#(failed),\#(older)]}"#)
        await maker.refresh()
        XCTAssertTrue(maker.active.isEmpty)
        XCTAssertEqual(maker.lastFailure?.id, "job-2")
        XCTAssertEqual(maker.lastFailure?.finishedAt, then)
        XCTAssertTrue(maker.lastFailure?.message?.contains("didn't count") == true)

        maker.now = { then.addingTimeInterval(BookMaker.failureShownFor + 60) }
        await maker.refresh()
        XCTAssertNil(maker.lastFailure, "an old failure isn't shown forever")

        maker.now = { then.addingTimeInterval(3600) }
        StubMakeServer.routes = [:]
        StubMakeServer.reply("/account/bookgen", json: #"{"can_generate":true,"ready":true,"usage":\#(usage),"jobs":[\#(job("queued")),\#(failed)]}"#)
        await maker.refresh()
        XCTAssertNil(maker.lastFailure, "a newer book replaces the note")
        XCTAssertEqual(maker.placeholders.map(\.id), ["job-1"])
    }

    func testARefusedBookThrowsTheServersWords() async {
        StubMakeServer.reply("POST /account/books", 429, json: #"{"error":"day_limit","message":"You've made 3 books today. You can make more tomorrow."}"#)
        let maker = maker()
        do {
            try await maker.make(title: "Paris by Boat", section: "Paris", level: 2, about: "")
            XCTFail("should refuse")
        } catch let error as MakeError {
            XCTAssertEqual(error.code, "day_limit")
            XCTAssertTrue(error.isLimit)
        } catch {
            XCTFail("unexpected \(error)")
        }
        XCTAssertTrue(maker.active.isEmpty)
        XCTAssertFalse(maker.isPolling)
    }

    func testPollsOnlyInTheForegroundWhileABookIsBeingMade() async throws {
        StubMakeServer.reply("POST /account/books", 202, json: #"{"job":\#(job("queued")),"usage":\#(usage)}"#)
        StubMakeServer.reply("/account/jobs/job-1", json: #"{"job":\#(job("running", step: "writing", progress: "Writing…"))}"#)
        let maker = maker()
        XCTAssertFalse(maker.isPolling, "nothing being made: no polling")
        maker.setForeground(false)
        try await maker.make(title: "The Tall Tower", section: "Paris", level: 1, about: "")
        XCTAssertFalse(maker.isPolling, "in the background: no polling")
        maker.setForeground(true)
        XCTAssertTrue(maker.isPolling)
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertTrue(StubMakeServer.sent.contains { $0.path == "/account/jobs/job-1" })
        maker.setForeground(false)
        XCTAssertFalse(maker.isPolling)
        try await Task.sleep(for: .milliseconds(50)) // a check already on its way may still land
        let checks = StubMakeServer.sent.filter { $0.path == "/account/jobs/job-1" }.count
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(StubMakeServer.sent.filter { $0.path == "/account/jobs/job-1" }.count, checks)
        maker.reset()
        XCTAssertTrue(maker.active.isEmpty)
    }

    func testPollingStopsWhenTheBookIsReady() async throws {
        StubMakeServer.reply("POST /account/books", 202, json: #"{"job":\#(job("queued")),"usage":\#(usage)}"#)
        StubMakeServer.reply("/account/jobs/job-1", json: #"{"job":\#(job("done"))}"#)
        StubMakeServer.reply("/account/bookgen", json: #"{"can_generate":true,"ready":true,"usage":\#(usage),"jobs":[]}"#)
        let maker = maker()
        var ready = false
        maker.onReady = { ready = true }
        try await maker.make(title: "The Tall Tower", section: "Paris", level: 1, about: "")
        XCTAssertTrue(maker.isPolling)
        for _ in 0..<50 where !ready { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertTrue(ready)
        XCTAssertTrue(maker.active.isEmpty)
        XCTAssertFalse(maker.isPolling)
        let checks = StubMakeServer.sent.filter { $0.path == "/account/jobs/job-1" }.count
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(StubMakeServer.sent.filter { $0.path == "/account/jobs/job-1" }.count, checks, "no polling once nothing is being made")
    }

    func testRefreshPicksUpABookStillBeingMadeAndTheOwnBooks() async {
        StubMakeServer.reply("/account/bookgen", json: #"{"can_generate":true,"ready":true,"usage":\#(usage),"active":\#(job("running", progress: "Recording…")),"jobs":[\#(job("running", progress: "Recording…"))]}"#)
        StubMakeServer.reply("GET /account/books", json: #"{"books":[{"id":"the-tall-tower-a1b2c3","title":"The Tall Tower","section":"Paris","level":1,"status":"published","pages":8}]}"#)
        let maker = maker()
        maker.setForeground(false)
        await maker.refresh()
        XCTAssertTrue(maker.canMake)
        XCTAssertEqual(maker.active.count, 1, "the same job twice shows once")
        XCTAssertEqual(maker.ownBooks, ["the-tall-tower-a1b2c3"])
    }

    func testRemakingAPageShowsOnItsBookNotAsAPlaceholder() async throws {
        StubMakeServer.reply(
            "POST /account/books/the-tall-tower-a1b2c3/pages/p03/regenerate", 202,
            json: #"{"job":\#(job("queued", kind: "page")),"usage":\#(usage)}"#
        )
        let maker = maker()
        maker.setForeground(false)
        try await maker.remakePage(bookId: "the-tall-tower-a1b2c3", pageId: "p03")
        XCTAssertTrue(maker.placeholders.isEmpty)
        XCTAssertNotNil(maker.remaking("the-tall-tower-a1b2c3"))
        XCTAssertNil(maker.remaking("paris"))
    }

    func testReportAndDeleteSendTheirRequests() async throws {
        StubMakeServer.reply("POST /account/books/the-tall-tower-a1b2c3/report", 201, json: #"{"ok":true}"#)
        StubMakeServer.reply("DELETE /account/books/the-tall-tower-a1b2c3", json: #"{"ok":true}"#)
        let maker = maker()
        var synced = false
        maker.onReady = { synced = true }
        try await maker.report(bookId: "the-tall-tower-a1b2c3", reason: .scary, note: "the dragon", page: "p02")
        let report = try XCTUnwrap(StubMakeServer.sent.last)
        XCTAssertEqual(report.body["reason"] as? String, "scary")
        XCTAssertEqual(report.body["page"] as? String, "p02")
        try await maker.delete(bookId: "the-tall-tower-a1b2c3")
        XCTAssertEqual(StubMakeServer.sent.last?.method, "DELETE")
        XCTAssertTrue(synced, "the shelf refreshes after a delete")
    }

    func testSignedOutCannotMake() async {
        token = nil
        let maker = maker()
        do {
            try await maker.make(title: "x", section: "y", level: 1, about: "")
            XCTFail("should refuse")
        } catch {
            XCTAssertEqual((error as? MakeError)?.code, "signed_out")
        }
        XCTAssertTrue(StubMakeServer.sent.isEmpty)
    }

    // MARK: - The account's made books

    /// Publishes one made book: its JSON from the account's content, its photo from a storage link.
    private func publishMadeBook(id: String = "the-tall-tower-a1b2c3", version: String = "v1") throws -> Data {
        var json = try XCTUnwrap(JSONSerialization.jsonObject(
            with: Data(contentsOf: XCTUnwrap(ContentFiles.bundleOnly(.main).bundledURL("paris-eiffel.json")))
        ) as? [String: Any])
        json["id"] = id
        json["theme"] = "The Tall Tower"
        json["generated"] = true
        var pages = try XCTUnwrap(json["pages"] as? [[String: Any]])
        pages = Array(pages.prefix(1))
        var photo = try XCTUnwrap(pages[0]["photo"] as? [String: Any])
        photo["file"] = "\(id)-p01.jpg"
        pages[0]["photo"] = photo
        json["pages"] = pages
        let book = try JSONSerialization.data(withJSONObject: json)
        let image = try Data(contentsOf: XCTUnwrap(ContentFiles.bundleOnly(.main).bundledURL("paris-eiffel-welcome.jpg")))
        let manifest = """
        {"format":1,"version":"\(version)","order":["\(id)"],"files":{
          "\(id).json":{"sha256":"\(Self.sha(book))","size":\(book.count)},
          "\(id)-p01.jpg":{"sha256":"\(Self.sha(image))","size":\(image.count),"url":"https://photos.test/signed/\(id)-p01.jpg?sig=abc"}
        }}
        """
        StubMakeServer.reply("/account/content/manifest.json", json: manifest)
        StubMakeServer.reply("/account/content/\(id).json", data: book)
        StubMakeServer.reply("/signed/\(id)-p01.jpg", data: image)
        return book
    }

    func testSyncDownloadsTheAccountsMadeBooksWithTheSignIn() async throws {
        _ = try publishMadeBook()
        let made = library()
        made.switchAccount("fam")
        await made.sync()
        XCTAssertEqual(made.books.map(\.id), ["the-tall-tower-a1b2c3"])
        XCTAssertTrue(made.books[0].isMade)
        XCTAssertTrue(FileManager.default.fileExists(atPath: dir.appendingPathComponent("the-tall-tower-a1b2c3-p01.jpg").path))

        let manifest = try XCTUnwrap(StubMakeServer.sent.first { $0.path == "/account/content/manifest.json" })
        XCTAssertEqual(manifest.session, "tok-1")
        XCTAssertEqual(manifest.auth, "Bearer app-key")
        let photo = try XCTUnwrap(StubMakeServer.sent.first { $0.host == "photos.test" })
        XCTAssertNil(photo.session, "the storage link gets no sign-in")
        XCTAssertNil(photo.auth)

        // A second look within the interval asks nothing; a forced one asks again.
        let asked = StubMakeServer.sent.count
        await made.sync()
        XCTAssertEqual(StubMakeServer.sent.count, asked)
        await made.sync(force: true)
        XCTAssertGreaterThan(StubMakeServer.sent.count, asked)
    }

    func testAnEmptyShelfOrAnOldServerMeansNoMadeBooks() async throws {
        let made = library()
        made.switchAccount("fam")
        await made.sync()
        XCTAssertTrue(made.books.isEmpty, "404 from a server before made books")
        StubMakeServer.reply("/account/content/manifest.json", json: #"{"format":1,"version":"empty","order":[],"files":{}}"#)
        await made.sync(force: true)
        XCTAssertTrue(made.books.isEmpty)
    }

    func testADeletedBookLeavesTheDevice() async throws {
        _ = try publishMadeBook()
        let made = library()
        made.switchAccount("fam")
        await made.sync()
        XCTAssertEqual(made.books.count, 1)
        StubMakeServer.routes["/account/content/manifest.json"] = [(200, Data(#"{"format":1,"version":"v2","order":[],"files":{}}"#.utf8))]
        await made.sync(force: true)
        XCTAssertTrue(made.books.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("the-tall-tower-a1b2c3.json").path))
    }

    func testAnotherAccountOrSigningOutWipesTheMadeBooks() async throws {
        _ = try publishMadeBook()
        let made = library()
        made.switchAccount("fam")
        await made.sync()
        XCTAssertEqual(made.books.count, 1)

        // The same account again (e.g. the next launch) shows them without asking.
        let again = library()
        again.switchAccount("fam")
        XCTAssertEqual(again.books.map(\.id), ["the-tall-tower-a1b2c3"])

        let other = library()
        other.switchAccount("someone-else")
        XCTAssertTrue(other.books.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("the-tall-tower-a1b2c3.json").path))

        _ = try publishMadeBook()
        let mine = library()
        mine.switchAccount("fam")
        await mine.sync(force: true)
        XCTAssertEqual(mine.books.count, 1)
        mine.forget()
        XCTAssertTrue(mine.books.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.path))
    }

    func testMadeBooksAreFoundByTheContentFilesAndTheirNarrationMerges() throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let clip = #"{"clips":{"the-tall-tower-a1b2c3/p01":{"file":"read-the-tall-tower-a1b2c3-p01","text":"Look! A big tower.","duration":1.5,"words":[{"location":0,"length":4,"start":0.1,"end":0.4}]}}}"#
        try Data(clip.utf8).write(to: dir.appendingPathComponent("the-tall-tower-a1b2c3.narration.json"))
        try Data("x".utf8).write(to: dir.appendingPathComponent("the-tall-tower-a1b2c3-p01.jpg"))
        try Data("x".utf8).write(to: dir.appendingPathComponent("read-the-tall-tower-a1b2c3-p01.m4a"))
        let files = ContentFiles(cacheDir: dir.appendingPathComponent("catalog"), bundle: .main, madeDir: dir)
        XCTAssertNotNil(files.cachedURL("the-tall-tower-a1b2c3-p01.jpg"))
        XCTAssertNil(files.cachedURL("nope.jpg"))
        XCTAssertNotNil(files.url("paris.json"), "the catalog still comes from the bundle")

        let narration = NarrationLibrary(files: files)
        let key = NarrationLibrary.key(bookId: "the-tall-tower-a1b2c3", pageId: "p01")
        XCTAssertNotNil(narration.narration(for: key, text: "Look! A big tower."), "the made book reads aloud with its own clip")
        XCTAssertNil(narration.narration(for: key, text: "A different sentence."))
        XCTAssertGreaterThan(narration.clips.count, 1, "the catalog's clips are still there")
    }
}
