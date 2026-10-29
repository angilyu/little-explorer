import XCTest
@testable import LittleExplorer

/// Answers `upgrade.test` requests from a queue (or fails them like a dropped network) and keeps their headers.
final class StubUpgradeServer: URLProtocol {
    nonisolated(unsafe) static var replies: [(Int, String)?] = []
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "upgrade.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        guard !Self.replies.isEmpty, let (status, json) = Self.replies.removeFirst() else {
            client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@MainActor
final class UpgradeTests: XCTestCase {
    private let base = URL(string: "https://upgrade.test")!
    private let client = ClientVersion(version: "1.1", build: "20261101.0930", channel: .appstore)
    private var suiteName = ""
    private var defaults: UserDefaults!
    private var center: NotificationCenter!
    private var session: URLSession!
    private var now = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() {
        super.setUp()
        suiteName = "UpgradeTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        center = NotificationCenter()
        StubUpgradeServer.replies = []
        StubUpgradeServer.requests = []
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubUpgradeServer.self]
        session = URLSession(configuration: config)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func checker() -> UpgradeChecker {
        UpgradeChecker(baseURL: base, client: client, session: session, defaults: defaults, center: center) { [unowned self] in now }
    }

    // MARK: - Which build is asking

    func testHeadersSayTheBuild() {
        XCTAssertEqual(client.headers, ["X-App-Version": "1.1", "X-App-Build": "20261101.0930", "X-App-Channel": "appstore"])
        var request = URLRequest(url: base)
        request.addClientHeaders(ClientVersion(version: "1.2", build: "", channel: .testflight))
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-App-Version"), "1.2")
        XCTAssertNil(request.value(forHTTPHeaderField: "X-App-Build"))
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-App-Channel"), "testflight")
    }

    func testChannelFromTheReceipt() {
        XCTAssertEqual(ClientVersion.channel(receiptName: "sandboxReceipt"), .testflight)
        XCTAssertEqual(ClientVersion.channel(receiptName: "receipt"), .appstore)
        XCTAssertEqual(ClientVersion.channel(receiptName: nil), .appstore)
    }

    func testThisBuildSaysItsVersion() {
        XCTAssertFalse(ClientVersion.current.version.isEmpty)
        XCTAssertEqual(ClientVersion.current.channel, .debug)
    }

    func testEveryServerRequestCarriesTheBuild() {
        let requests = [
            AccountService(baseURL: base).request("account", token: "t"),
            CoinService(baseURL: base, deviceId: "dev-1").fetchRequest(),
            CoinService(baseURL: base, deviceId: "dev-1", token: "t").convertRequest(id: "c", coins: 1, starsPerCoin: 10),
            StarBackup(baseURL: base, deviceId: "dev-1").request(),
        ]
        for request in requests {
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-App-Version"), ClientVersion.current.version, "\(request)")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-App-Channel"), "debug", "\(request)")
        }
        XCTAssertEqual(checker().request().allHTTPHeaderFields?.filter { $0.key.hasPrefix("X-App-") }, client.headers)
    }

    func testTokenRequestCarriesTheBuildAndA426ShowsTheUpdateScreen() async {
        StubUpgradeServer.replies = [(426, #"{"error":"upgrade_required","message":"Update","update_url":"https://apps.apple.com/app/id1","min_version":"1.2"}"#)]
        let watcher = UpgradeChecker(baseURL: base, client: client, session: session, defaults: defaults)  // the default center
        let tokens = TokenService(baseURL: base, accessKey: "app-key", session: session)
        do {
            _ = try await tokens.fetchCredentials(context: nil)
            XCTFail("expected an error")
        } catch {
            guard case VoiceError.tokenServer(status: 426) = error else { return XCTFail("\(error)") }
        }
        XCTAssertEqual(StubUpgradeServer.requests.first?.value(forHTTPHeaderField: "X-App-Version"), ClientVersion.current.version)
        await waitFor { watcher.isRequired }
        XCTAssertEqual(watcher.prompt, .required(URL(string: "https://apps.apple.com/app/id1")))
    }

    // MARK: - What the server says

    func testAdviceDecoding() throws {
        let advice = try JSONDecoder().decode(UpdateAdvice.self, from: Data(#"""
        {"status":"required","update_url":"itms-apps://apps.apple.com/app/id1","min_version":"1.2","recommended_version":null,
         "client":{"version":"1.1","build":null,"channel":"appstore","legacy":false},"new_field":1}
        """#.utf8))
        XCTAssertEqual(advice, UpdateAdvice(status: .required, updateURL: URL(string: "itms-apps://apps.apple.com/app/id1"), minVersion: "1.2"))
        let later = try JSONDecoder().decode(UpdateAdvice.self, from: Data(#"{"status":"someday","update_url":null}"#.utf8))
        XCTAssertEqual(later.status, .ok)
        XCTAssertNil(later.updateURL)
        XCTAssertNil(UpdateAdvice.refusal(status: 403, data: Data()))
        XCTAssertEqual(UpdateAdvice.refusal(status: 426, data: Data("oops".utf8)), UpdateAdvice(status: .required))
    }

    func testRequiredStaysWhenTheServerCantBeReached() async {
        let checker = checker()
        StubUpgradeServer.replies = [(200, #"{"status":"required","update_url":null}"#), nil, (500, "{}"), (200, "not json")]
        await checker.check()
        XCTAssertEqual(checker.prompt, .required(nil))
        let request = StubUpgradeServer.requests[0]
        XCTAssertEqual(request.url?.path, "/app/version")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-App-Build"), "20261101.0930")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        for _ in 0..<3 { await checker.check() }
        XCTAssertTrue(checker.isRequired)
        // The owner lowered the minimum again.
        StubUpgradeServer.replies = [(200, #"{"status":"ok"}"#)]
        await checker.check()
        XCTAssertNil(checker.prompt)
    }

    func testNeverBlocksWhenOfflineOrTheServerIsOld() async {
        let checker = checker()
        StubUpgradeServer.replies = [nil, (404, #"{"error":"not_found"}"#), (503, "")]
        for _ in 0..<3 { await checker.check() }
        XCTAssertNil(checker.prompt)
    }

    func testMaybeLaterWaitsAFewDays() async {
        let checker = checker()
        let recommended = #"{"status":"recommended","update_url":"https://apps.apple.com/app/id1"}"#
        StubUpgradeServer.replies = [(200, recommended), (200, recommended), (200, recommended)]
        await checker.check()
        XCTAssertEqual(checker.prompt, .recommended(URL(string: "https://apps.apple.com/app/id1")))
        checker.snoozeRecommended()
        XCTAssertNil(checker.prompt)
        now += 60 * 60
        await checker.check()
        XCTAssertNil(checker.prompt)
        now += UpgradeChecker.snooze
        await checker.check()
        XCTAssertNotNil(checker.prompt)
    }

    func testRequiredCantBeSnoozed() async {
        let checker = checker()
        checker.apply(UpdateAdvice(status: .required))
        checker.snoozeRecommended()
        XCTAssertTrue(checker.isRequired)
        // Snoozing a recommendation doesn't hide a later requirement.
        checker.apply(UpdateAdvice(status: .recommended))
        checker.snoozeRecommended()
        checker.apply(UpdateAdvice(status: .required))
        XCTAssertTrue(checker.isRequired)
    }

    func testARefusedRequestShowsTheUpdateScreen() async {
        let checker = checker()
        XCTAssertFalse(UpgradeSignal.check(status: 200, data: Data(), center: center))
        XCTAssertTrue(UpgradeSignal.check(status: 426, data: Data(#"{"error":"upgrade_required"}"#.utf8), center: center))
        await waitFor { checker.isRequired }
    }

    func testUpdateGoesToTheServersLinkElseTestFlightElseTheStore() async {
        let link = URL(string: "https://apps.apple.com/app/id1")!
        let given = await UpgradeChecker.destination(link, channel: .appstore) { nil }
        XCTAssertEqual(given, link)
        let beta = await UpgradeChecker.destination(nil, channel: .testflight) { nil }
        XCTAssertEqual(beta.scheme, "itms-beta")
        let page = await UpgradeChecker.destination(nil, channel: .appstore) { 1234567890 }
        XCTAssertEqual(page.absoluteString, "https://apps.apple.com/app/id1234567890")
        let search = await UpgradeChecker.destination(nil, channel: .appstore) { nil }
        XCTAssertEqual(search.host, "apps.apple.com")
    }

    func testDemoArguments() {
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoUpgrade", "required"])?.upgrade, .required)
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoUpgrade", "recommended"])?.upgrade, .recommended)
        XCTAssertNil(DemoLaunch.parse(["app", "-demoUpgrade", "soon"]))
    }

    private func waitFor(_ condition: @escaping () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<100 where !condition() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(condition(), file: file, line: line)
    }
}
