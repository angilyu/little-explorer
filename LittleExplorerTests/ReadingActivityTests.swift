import XCTest
@testable import LittleExplorer

@MainActor
final class ReadingActivityTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite: String!
    private var clock = Date(timeIntervalSince1970: 1_800_043_200)  // 2027-01-15 12:00 Pacific
    private var accountId: String? = "acct-1"
    private var token: String? = "key-1"
    private var sent: [[ReadingEvent]] = []
    private var failure: Error?
    private var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        return calendar
    }()

    override func setUp() async throws {
        suite = "ReadingActivityTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suite)
    }

    private func makeActivity(enabled: Bool = true) -> ReadingActivity {
        ReadingActivity(
            enabled: enabled,
            defaults: defaults,
            accountId: { [unowned self] in accountId },
            token: { [unowned self] in token },
            send: { [unowned self] events, token in
                XCTAssertEqual(token, "key-1")
                if let failure { throw failure }
                sent.append(events)
            },
            now: { [unowned self] in clock },
            calendar: calendar
        )
    }

    private func readPage(_ activity: ReadingActivity, page: String, index: Int, seconds: TimeInterval, milo: Bool = false) {
        activity.pageShown(book: "paris", page: page, index: index, milo: milo)
        clock += seconds
        activity.pageHidden()
    }

    func testRecordsPageViewsWithTimeOnPage() {
        let activity = makeActivity()
        activity.pageShown(book: "paris", page: "welcome", index: 0, milo: true)
        clock += 12.34
        activity.pageShown(book: "paris", page: "welcome", index: 0, milo: true)  // same page again: still open
        clock += 5
        activity.pageShown(book: "paris", page: "bonjour", index: 1, milo: false)
        clock += 0.4
        activity.pageHidden()  // under a second: not a view

        XCTAssertEqual(activity.queue.count, 1)
        let view = activity.queue[0]
        XCTAssertEqual(view.kind, .pageView)
        XCTAssertEqual(view.book, "paris")
        XCTAssertEqual(view.page, "welcome")
        XCTAssertEqual(view.pageIndex, 0)
        XCTAssertEqual(view.seconds, 17.3)
        XCTAssertEqual(view.milo, true)
        XCTAssertEqual(view.at, "2027-01-15T12:00:00-08:00")
        XCTAssertEqual(view.accountId, "acct-1")
    }

    func testCapsTimeOnPage() {
        let activity = makeActivity()
        readPage(activity, page: "welcome", index: 0, seconds: 3 * 3600)
        XCTAssertEqual(activity.queue.first?.seconds, ReadingActivity.maxPageSeconds)
    }

    func testRecordsReadAloudUse() {
        let activity = makeActivity()
        activity.readAloud(.init(pageId: "paris/bonjour", outcome: .started, mode: .clip, seconds: nil), index: 1)
        activity.readAloud(.init(pageId: "paris/bonjour", outcome: .finished, mode: .clip, seconds: 4.26), index: 1)
        activity.readAloud(.init(pageId: "bad", outcome: .stopped, mode: .system, seconds: 1), index: nil)

        XCTAssertEqual(activity.queue.map(\.outcome), ["started", "finished"])
        XCTAssertEqual(activity.queue.map(\.mode), ["clip", "clip"])
        XCTAssertEqual(activity.queue.map(\.seconds), [nil, 4.3])
        XCTAssertEqual(activity.queue.map(\.page), ["bonjour", "bonjour"])
        XCTAssertEqual(activity.queue.map(\.pageIndex), [1, 1])
        XCTAssertTrue(activity.queue.allSatisfy { $0.kind == .readAloud && $0.book == "paris" })
    }

    func testAppOpenOncePerDayPerAccount() {
        let activity = makeActivity()
        activity.appOpened()
        clock += 3600
        activity.appOpened()
        accountId = "acct-2"
        activity.appOpened()
        accountId = "acct-1"
        clock += 12 * 3600  // the next day in Pacific time
        activity.appOpened()

        XCTAssertEqual(activity.queue.map(\.kind), [.appOpen, .appOpen, .appOpen])
        XCTAssertEqual(activity.queue.map(\.accountId), ["acct-1", "acct-2", "acct-1"])
        XCTAssertEqual(activity.queue.last?.at, "2027-01-16T01:00:00-08:00")
    }

    func testRecordsNothingSignedOutOrInDemo() {
        accountId = nil
        let signedOut = makeActivity()
        signedOut.appOpened()
        readPage(signedOut, page: "welcome", index: 0, seconds: 10)
        signedOut.readAloud(.init(pageId: "paris/welcome", outcome: .started, mode: .system, seconds: nil), index: 0)
        XCTAssertTrue(signedOut.queue.isEmpty)

        accountId = "acct-1"
        let demo = makeActivity(enabled: false)
        demo.appOpened()
        readPage(demo, page: "welcome", index: 0, seconds: 10)
        demo.readAloud(.init(pageId: "paris/welcome", outcome: .started, mode: .system, seconds: nil), index: 0)
        XCTAssertTrue(demo.queue.isEmpty)
        XCTAssertNil(defaults.data(forKey: "reading.queue.v1"))
        XCTAssertNil(defaults.string(forKey: "reading.queue.v1.lastOpen"))
    }

    func testQueueIsSavedAndCapped() {
        failure = ReadingService.Failure.later  // early sends fail, so everything stays queued
        let activity = makeActivity()
        for index in 0..<(ReadingActivity.maxQueue + 20) {
            readPage(activity, page: "p\(index)", index: index % 900, seconds: 2)
        }
        XCTAssertEqual(activity.queue.count, ReadingActivity.maxQueue)
        XCTAssertEqual(activity.queue.first?.page, "p20")

        let reopened = makeActivity()
        XCTAssertEqual(reopened.queue, activity.queue)
    }

    func testFlushSendsBatchesForTheSignedInAccount() async {
        let activity = makeActivity()
        failure = ReadingService.Failure.later
        accountId = "acct-2"
        readPage(activity, page: "other", index: 0, seconds: 2)
        accountId = "acct-1"
        for index in 0..<120 { readPage(activity, page: "p\(index)", index: 0, seconds: 2) }
        await Task.yield()
        sent = []
        failure = nil

        await activity.flush()

        XCTAssertEqual(sent.map(\.count), [100, 20])
        XCTAssertTrue(sent.joined().allSatisfy { $0.accountId == "acct-1" })
        XCTAssertEqual(activity.queue.map(\.page), ["other"])
        XCTAssertEqual(makeActivity().queue.map(\.page), ["other"])
    }

    func testKeepsEventsWhenSendingFailsAndDropsRefusedOnes() async {
        let activity = makeActivity()
        readPage(activity, page: "welcome", index: 0, seconds: 3)

        failure = ReadingService.Failure.later
        await activity.flush()
        XCTAssertEqual(activity.queue.count, 1)

        token = nil
        failure = nil
        await activity.flush()
        XCTAssertEqual(activity.queue.count, 1)
        XCTAssertTrue(sent.isEmpty)

        token = "key-1"
        failure = ReadingService.Failure.refused
        await activity.flush()
        XCTAssertTrue(activity.queue.isEmpty)
        XCTAssertNil(defaults.data(forKey: "reading.queue.v1"))
    }

    func testSignOutDropsThatAccountsEvents() {
        let activity = makeActivity()
        readPage(activity, page: "welcome", index: 0, seconds: 3)
        accountId = "acct-2"
        readPage(activity, page: "bonjour", index: 1, seconds: 3)
        activity.pageShown(book: "paris", page: "eiffel", index: 2, milo: false)

        activity.forgetAccount("acct-1")
        clock += 30
        activity.pageHidden()  // the open page is forgotten too

        XCTAssertEqual(activity.queue.map(\.page), ["bonjour"])
        XCTAssertEqual(makeActivity().queue.map(\.page), ["bonjour"])
    }

    func testProgressKeepsPagesListensMiloPagesAndDaysPerAccount() {
        let activity = makeActivity()
        readPage(activity, page: "welcome", index: 0, seconds: 3, milo: true)
        readPage(activity, page: "welcome", index: 0, seconds: 3)
        readPage(activity, page: "bonjour", index: 1, seconds: 0.5)  // too short: not read
        activity.readAloud(.init(pageId: "paris/welcome", outcome: .finished, mode: .clip, seconds: 9), index: 0)
        activity.readAloud(.init(pageId: "paris/eiffel", outcome: .stopped, mode: .clip, seconds: 2), index: 2)

        XCTAssertEqual(activity.progress.pages, ["paris/welcome"])
        XCTAssertEqual(activity.progress.miloPages, ["paris/welcome"])
        XCTAssertEqual(activity.progress.listened, ["paris/welcome"], "only read-aloud he listened to the end")
        XCTAssertEqual(activity.progress.days, ["2027-01-15"])

        // Another account on the device starts empty; switching back shows the first one's again.
        accountId = "acct-2"
        activity.switchAccount("acct-2")
        XCTAssertTrue(activity.progress.isEmpty)
        activity.switchAccount("acct-1")
        XCTAssertEqual(activity.progress.pages, ["paris/welcome"])

        let relaunched = makeActivity()
        relaunched.switchAccount("acct-1")
        XCTAssertEqual(relaunched.progress.pages, ["paris/welcome"])

        relaunched.forgetAccount("acct-1")
        XCTAssertTrue(relaunched.progress.isEmpty)
        let afterSignOut = makeActivity()
        afterSignOut.switchAccount("acct-1")
        XCTAssertTrue(afterSignOut.progress.isEmpty, "signing out forgets it on this device")
    }

    func testMergeAddsOtherDevicesReadingAndNeverTakesAway() {
        let activity = makeActivity()
        readPage(activity, page: "welcome", index: 0, seconds: 3)
        activity.merge(ReadingProgress(pages: ["london/hello"], days: ["2027-01-10"]), accountId: "acct-1")
        XCTAssertEqual(activity.progress.pages, ["paris/welcome", "london/hello"])
        XCTAssertEqual(activity.progress.days, ["2027-01-10", "2027-01-15"])
        activity.merge(ReadingProgress(), accountId: "acct-1")
        XCTAssertEqual(activity.progress.pages.count, 2)
        activity.merge(ReadingProgress(pages: ["rome/ciao"]), accountId: "someone-else")
        XCTAssertFalse(activity.progress.pages.contains("rome/ciao"), "only the account on screen")
    }

    func testNoProgressSignedOutOrInDemo() {
        accountId = nil
        let signedOut = makeActivity()
        readPage(signedOut, page: "welcome", index: 0, seconds: 3)
        XCTAssertTrue(signedOut.progress.isEmpty)

        accountId = "acct-1"
        let demo = makeActivity(enabled: false)
        readPage(demo, page: "welcome", index: 0, seconds: 3)
        demo.switchAccount("acct-1")
        demo.merge(ReadingProgress(pages: ["paris/welcome"]), accountId: "acct-1")
        XCTAssertTrue(demo.progress.isEmpty)
        demo.seedDemo(ReadingProgress(pages: ["paris/eiffel"]))
        XCTAssertEqual(demo.progress.pages, ["paris/eiffel"], "screenshots only, never saved")
        XCTAssertTrue(defaults.dictionaryRepresentation().keys.allSatisfy { !$0.contains(ReadingActivity.progressKey) })
    }

    func testProgressDecodesTheServerReplyLeniently() throws {
        let json = #"{"progress":{"pages":["paris/welcome"],"milo_pages":["paris/welcome"],"days":["2027-01-15"]}}"#
        let reply = try JSONDecoder().decode(ReadingService.ProgressReply.self, from: Data(json.utf8))
        XCTAssertEqual(reply.progress, ReadingProgress(pages: ["paris/welcome"], miloPages: ["paris/welcome"], days: ["2027-01-15"]))
    }

    func testPayloadUsesServerKeysAndLeavesOutTheAccount() throws {
        let activity = makeActivity()
        readPage(activity, page: "welcome", index: 3, seconds: 2, milo: true)
        let payload = activity.queue[0].payload
        XCTAssertEqual(Set(payload.keys), ["id", "kind", "at", "book", "page", "page_index", "seconds", "milo"])
        XCTAssertEqual(payload["kind"] as? String, "page_view")
        XCTAssertEqual(payload["page_index"] as? Int, 3)
        XCTAssertTrue(JSONSerialization.isValidJSONObject(["events": [payload]]))

        activity.appOpened()
        XCTAssertEqual(Set(activity.queue[1].payload.keys), ["id", "kind", "at"])
    }

    func testServicePostsWithTheSessionAndSortsFailures() async throws {
        let session = URLSession(configuration: {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.protocolClasses = [StubReadingProtocol.self]
            return configuration
        }())
        let service = ReadingService(baseURL: URL(string: "https://example.test")!, session: session)
        let event = ReadingEvent(id: "event-0001", kind: .appOpen, at: "2027-01-15T12:00:00-08:00", accountId: "acct-1")

        StubReadingProtocol.status = 200
        try await service.send([event], token: "key-1")
        let request = try XCTUnwrap(StubReadingProtocol.last)
        XCTAssertEqual(request.url?.path, "/account/reading")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Pip-Session"), "key-1")
        let body = try XCTUnwrap(StubReadingProtocol.body)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: [[String: Any]]])
        XCTAssertEqual(json["events"]?.first?["id"] as? String, "event-0001")
        XCTAssertNil(json["events"]?.first?["accountId"])

        for (status, expected) in [(413, ReadingService.Failure.refused), (400, .refused), (429, .later), (401, .later), (503, .later)] {
            StubReadingProtocol.status = status
            do {
                try await service.send([event], token: "key-1")
                XCTFail("\(status) should fail")
            } catch let failure as ReadingService.Failure {
                XCTAssertEqual(failure, expected, "\(status)")
            }
        }
    }
}

final class StubReadingProtocol: URLProtocol {
    nonisolated(unsafe) static var status = 200
    nonisolated(unsafe) static var last: URLRequest?
    nonisolated(unsafe) static var body: Data?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.last = request
        Self.body = request.httpBody ?? request.httpBodyStream.map { stream in
            stream.open()
            defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                guard count > 0 else { break }
                data.append(buffer, count: count)
            }
            return data
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data("{}".utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@MainActor
final class ReadAloudUseTests: XCTestCase {
    private var clock = Date(timeIntervalSince1970: 1_800_043_200)
    private var uses: [ReadAloudController.Use] = []

    private func makeReader(player: FakeNarrationPlayer, engine: FakeSpeechEngine, clip: Bool) -> ReadAloudController {
        let narration = Narration(file: "read-paris-welcome", text: "This is Paris.", duration: 2, words: [])
        let reader = ReadAloudController(
            engine: engine, player: player, narrations: { _, _ in clip ? narration : nil },
            audioSession: FakeReadingAudioSession(), now: { [unowned self] in clock }
        )
        reader.onUse = { [unowned self] in uses.append($0) }
        return reader
    }

    func testReportsStartAndFinishWithTheClip() {
        let player = FakeNarrationPlayer()
        let reader = makeReader(player: player, engine: FakeSpeechEngine(), clip: true)
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        clock += 2.5
        player.finish()
        XCTAssertEqual(uses, [
            .init(pageId: "paris/welcome", outcome: .started, mode: .clip, seconds: nil),
            .init(pageId: "paris/welcome", outcome: .finished, mode: .clip, seconds: 2.5),
        ])
    }

    func testReportsEarlyStopsWithTheSystemVoice() {
        let engine = FakeSpeechEngine()
        let reader = makeReader(player: FakeNarrationPlayer(), engine: engine, clip: false)
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        clock += 1
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")  // tap again
        reader.toggle(pageId: "paris/welcome", text: "This is Paris.")
        clock += 2
        reader.toggle(pageId: "paris/bonjour", text: "Bonjour!")  // another page's button
        clock += 3
        reader.pageDidChange(to: "paris/eiffel")  // page turned
        reader.stop()  // already stopped: nothing more

        XCTAssertEqual(uses.map(\.outcome), [.started, .stopped, .started, .stopped, .started, .stopped])
        XCTAssertEqual(uses.map(\.pageId), ["paris/welcome", "paris/welcome", "paris/welcome", "paris/welcome", "paris/bonjour", "paris/bonjour"])
        XCTAssertEqual(uses.map(\.seconds), [nil, 1, nil, 2, nil, 3])
        XCTAssertTrue(uses.allSatisfy { $0.mode == .system })
    }
}
