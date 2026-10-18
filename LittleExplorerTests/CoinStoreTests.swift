import XCTest
@testable import LittleExplorer

/// Answers `coins.test` requests from a queue (or fails them like a dropped network) and keeps what it was sent.
final class StubCoinServer: URLProtocol {
    nonisolated(unsafe) static var replies: [(Int, String)?] = []
    nonisolated(unsafe) static var requests: [(path: String, query: String?, auth: String?, body: [String: Any])] = []

    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "coins.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var body: [String: Any] = [:]
        if let stream = request.httpBodyStream {
            stream.open()
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                if n <= 0 { break }
                data.append(buffer, count: n)
            }
            stream.close()
            body = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        }
        Self.requests.append((request.url!.path, request.url!.query, request.value(forHTTPHeaderField: "Authorization"), body))
        guard !Self.replies.isEmpty, let (status, json) = Self.replies.removeFirst() else {
            client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
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
final class CoinStoreTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!
    private var service: CoinService!

    override func setUp() {
        super.setUp()
        suiteName = "CoinStoreTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        StubCoinServer.replies = []
        StubCoinServer.requests = []
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubCoinServer.self]
        service = CoinService(
            baseURL: URL(string: "https://coins.test")!, deviceId: "dev-1", accessKey: "app-key",
            session: URLSession(configuration: config)
        )
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private let balance23 = """
    {"enabled":true,"stars_per_coin":10,"min_coins":1,"max_coins":0,"stars_earned":23,"stars_spent":0,
     "stars":23,"coins":0,"can_convert":2,"leftover_stars":3}
    """
    private let converted = """
    {"enabled":true,"stars_per_coin":10,"min_coins":1,"max_coins":0,"stars_earned":23,"stars_spent":20,
     "stars":3,"coins":2,"can_convert":0,"leftover_stars":3,
     "conversion":{"id":"x","device_id":"dev-1","converted_at":"2026-05-01T10:00:00","stars_spent":20,"coins":2,
                   "stars_per_coin":10,"replayed":false}}
    """

    func testDecodesServerBalanceAndToleratesMissingFields() throws {
        let state = try JSONDecoder().decode(CoinState.self, from: Data(balance23.utf8))
        XCTAssertEqual(state, CoinState(starsEarned: 23))
        XCTAssertEqual(state.canConvert, 2)
        XCTAssertEqual(state.leftoverStars, 3)
        let sparse = try JSONDecoder().decode(CoinState.self, from: Data(#"{"coins":4,"stars_spent":40}"#.utf8))
        XCTAssertTrue(sparse.enabled)
        XCTAssertEqual(sparse.starsPerCoin, 10)
        XCTAssertEqual(sparse.coins, 4)
    }

    func testLocalStateMatchesServerRules() {
        XCTAssertEqual(CoinState(starsPerCoin: 10, starsEarned: 9).canConvert, 0)
        XCTAssertEqual(CoinState(starsPerCoin: 10, starsEarned: 9).starsToGo, 1)
        XCTAssertEqual(CoinState(starsPerCoin: 10, maxCoins: 3, starsEarned: 75).canConvert, 3)
        XCTAssertEqual(CoinState(starsPerCoin: 10, maxCoins: 3, starsEarned: 75).leftoverStars, 45)
        XCTAssertEqual(CoinState(starsPerCoin: 10, minCoins: 2, starsEarned: 15).canConvert, 0)
        XCTAssertEqual(CoinState(enabled: false, starsEarned: 50).canConvert, 0)
    }

    func testRequestsCarryDeviceRateAndKey() throws {
        let fetch = service.fetchRequest()
        XCTAssertEqual(fetch.url?.absoluteString, "https://coins.test/coins?device=dev-1")
        XCTAssertEqual(fetch.value(forHTTPHeaderField: "Authorization"), "Bearer app-key")
        let convert = service.convertRequest(id: "abc12345", coins: 2, starsPerCoin: 10)
        XCTAssertEqual(convert.httpMethod, "POST")
        XCTAssertEqual(convert.url?.path, "/coins/convert")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(convert.httpBody)) as? [String: Any])
        XCTAssertEqual(body["device"] as? String, "dev-1")
        XCTAssertEqual(body["id"] as? String, "abc12345")
        XCTAssertEqual(body["coins"] as? Int, 2)
        XCTAssertEqual(body["stars_per_coin"] as? Int, 10)
    }

    func testRefreshSavesBalanceAndSpendableStars() async {
        StubCoinServer.replies = [(200, converted)]
        let store = CoinStore(defaults: defaults)
        XCTAssertEqual(store.spendable(fromEarned: 23), 23)
        let reached = await store.refresh(using: service)
        XCTAssertTrue(reached)
        XCTAssertEqual(store.coins, 2)
        XCTAssertEqual(store.spendable(fromEarned: 23), 3)
        XCTAssertEqual(store.spendable(fromEarned: 5), 0)
        XCTAssertEqual(StubCoinServer.requests.first?.query, "device=dev-1")

        // Saved for next launch, even when the server can't be reached then.
        let again = CoinStore(defaults: defaults)
        let offline = await again.refresh(using: service)
        XCTAssertFalse(offline)
        XCTAssertEqual(again.coins, 2)
        XCTAssertEqual(again.starsSpent, 20)
        XCTAssertNil(again.state)
    }

    func testConvertUpdatesCoins() async throws {
        StubCoinServer.replies = [(200, converted)]
        let store = CoinStore(defaults: defaults)
        let made = try await store.convert(coins: 2, starsPerCoin: 10, using: service)
        XCTAssertEqual(made?.coins, 2)
        XCTAssertEqual(store.coins, 2)
        XCTAssertEqual(store.state?.stars, 3)
        XCTAssertNil(store.pendingId(forCoins: 2, starsPerCoin: 10))
        let sent = try XCTUnwrap(StubCoinServer.requests.first)
        XCTAssertEqual(sent.path, "/coins/convert")
        XCTAssertEqual(sent.auth, "Bearer app-key")
        XCTAssertEqual(sent.body["coins"] as? Int, 2)
        XCTAssertGreaterThanOrEqual((sent.body["id"] as? String)?.count ?? 0, 8)
    }

    func testRetryAfterDroppedNetworkReusesTheSameId() async throws {
        StubCoinServer.replies = [nil, (200, converted)]
        let store = CoinStore(defaults: defaults)
        do {
            _ = try await store.convert(coins: 2, starsPerCoin: 10, using: service)
            XCTFail("expected a network error")
        } catch is CoinError {
            XCTFail("a dropped network is not a refusal")
        } catch {}
        XCTAssertNotNil(store.pendingId(forCoins: 2, starsPerCoin: 10))
        XCTAssertNil(store.pendingId(forCoins: 1, starsPerCoin: 10))
        _ = try await store.convert(coins: 2, starsPerCoin: 10, using: service)
        let ids = StubCoinServer.requests.map { $0.body["id"] as? String }
        XCTAssertEqual(ids.count, 2)
        XCTAssertEqual(ids[0], ids[1])
        XCTAssertNil(store.pendingId(forCoins: 2, starsPerCoin: 10))
    }

    func testRefusalShowsServerMessageAndNewRules() async {
        let refusal = """
        {"error":"rate_changed","message":"The rate changed: it is now 20 stars for 1 coin.","enabled":true,
         "stars_per_coin":20,"min_coins":1,"max_coins":0,"stars_earned":23,"stars_spent":0,"stars":23,"coins":0,
         "can_convert":1,"leftover_stars":3}
        """
        StubCoinServer.replies = [(409, refusal), (401, #"{"error":"unauthorized"}"#)]
        let store = CoinStore(defaults: defaults)
        do {
            _ = try await store.convert(coins: 2, starsPerCoin: 10, using: service)
            XCTFail("expected a refusal")
        } catch let error as CoinError {
            guard case let .refused(code, message, state) = error else { return XCTFail("\(error)") }
            XCTAssertEqual(code, "rate_changed")
            XCTAssertTrue(message.contains("20 stars"))
            XCTAssertEqual(state?.starsPerCoin, 20)
        } catch {
            XCTFail("\(error)")
        }
        XCTAssertEqual(store.state?.starsPerCoin, 20)
        XCTAssertNil(store.pendingId(forCoins: 2, starsPerCoin: 10))
        do {
            _ = try await store.convert(coins: 1, starsPerCoin: 20, using: service)
            XCTFail("expected an error")
        } catch let error as CoinError {
            XCTAssertEqual(error, .server(status: 401))
        } catch {
            XCTFail("\(error)")
        }
    }

    func testDemoSeedTakesStarsOffTheCount() {
        let store = CoinStore(defaults: defaults)
        store.seedDemo(coins: 2)
        XCTAssertEqual(store.coins, 2)
        XCTAssertEqual(store.spendable(fromEarned: 23), 3)
        XCTAssertNil(store.state)
    }
}

final class ParentGateTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "ParentGateTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testQuestionsAreHardForKidsAndTyped() {
        var generator = SystemRandomNumberGenerator()
        for _ in 0..<200 {
            let q = ParentGateQuestion.random(using: &generator)
            XCTAssertTrue(ParentGateQuestion.aRange.contains(q.a))
            XCTAssertTrue(ParentGateQuestion.bRange.contains(q.b))
            XCTAssertTrue(ParentGateQuestion.cRange.contains(q.c))
            XCTAssertGreaterThanOrEqual(q.answer, 12 * 3 + 11)
            XCTAssertLessThanOrEqual(String(q.answer).count, ParentGateQuestion.maxDigits)
        }
        let q = ParentGateQuestion(a: 23, b: 7, c: 38)
        XCTAssertEqual(q.answer, 199)
        XCTAssertEqual(q.text, "23 × 7 + 38")
        XCTAssertTrue(q.isAnswer("199"))
        XCTAssertTrue(q.isAnswer("0199"))
        XCTAssertFalse(q.isAnswer("161"))
        XCTAssertFalse(q.isAnswer(""))
    }

    func testThreeWrongAnswersRestTheGateAndItRemembers() {
        let lock = ParentGateLock(defaults: defaults)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        XCTAssertEqual(lock.triesLeft, 3)
        XCTAssertFalse(lock.miss(now: now))
        XCTAssertFalse(lock.miss(now: now))
        XCTAssertEqual(lock.triesLeft, 1)
        XCTAssertEqual(lock.secondsLeft(now: now), 0)
        XCTAssertTrue(lock.miss(now: now))
        XCTAssertEqual(lock.secondsLeft(now: now), Int(ParentGateLock.cooldown))
        XCTAssertEqual(lock.secondsLeft(now: now.addingTimeInterval(10.5)), 20)

        // Closing and reopening the gate doesn't start over.
        let reopened = ParentGateLock(defaults: defaults)
        XCTAssertNotNil(reopened.lockedUntil(now: now.addingTimeInterval(29)))
        XCTAssertNil(reopened.lockedUntil(now: now.addingTimeInterval(30)))
        XCTAssertEqual(reopened.triesLeft, 3)
        XCTAssertFalse(reopened.miss(now: now.addingTimeInterval(31)))
        reopened.pass()
        XCTAssertEqual(reopened.triesLeft, 3)
    }

    func testDemoLaunchOpensGateCornerAndSeedsCoins() {
        let demo = DemoLaunch.parse(["app", "-demoCoins", "2", "-demoGate", "-demoParent"])
        XCTAssertEqual(demo?.seedCoins, 2)
        XCTAssertEqual(demo?.openGate, true)
        XCTAssertEqual(demo?.openParent, true)
        XCTAssertNil(DemoLaunch.parse(["app"]))
    }
}
