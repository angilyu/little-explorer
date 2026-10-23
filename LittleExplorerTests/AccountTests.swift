import XCTest
@testable import LittleExplorer

/// Answers `account.test` requests from a queue (or fails them like a dropped network) and keeps what it was sent.
final class StubAccountServer: URLProtocol {
    struct Sent {
        let method: String
        let path: String
        let query: String?
        let auth: String?
        let session: String?
        let body: [String: Any]
    }

    nonisolated(unsafe) static var replies: [(Int, String)?] = []
    nonisolated(unsafe) static var sent: [Sent] = []

    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "account.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

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
        Self.sent.append(Sent(
            method: request.httpMethod ?? "GET", path: request.url!.path, query: request.url!.query,
            auth: request.value(forHTTPHeaderField: "Authorization"),
            session: request.value(forHTTPHeaderField: AccountService.sessionHeader),
            body: (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        ))
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
final class AccountTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!
    private var tokens: MemorySessionTokenStore!
    private let base = URL(string: "https://account.test")!
    private var urlSession: URLSession!

    private let family = #"{"id":"fam","email":"mom@example.com","all_books":true,"books":[]}"#
    private let device = DeviceInfo(id: "IPAD-1", name: "iPad", model: "iPad13,1")

    override func setUp() {
        super.setUp()
        suiteName = "AccountTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        tokens = MemorySessionTokenStore()
        StubAccountServer.replies = []
        StubAccountServer.sent = []
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubAccountServer.self]
        urlSession = URLSession(configuration: config)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func service() -> AccountService {
        AccountService(baseURL: base, accessKey: "app-key", session: urlSession)
    }

    private func store() -> AccountStore {
        let session = urlSession!
        let base = base
        return AccountStore(tokens: tokens, defaults: defaults, service: {
            AccountService(baseURL: base, accessKey: "app-key", session: session)
        })
    }

    private func award(_ id: String, book: String = "paris") -> StarAward {
        StarAward(id: id, awardedAt: "2026-01-01T10:00:00", book: book, page: "p1", city: "Paris", stars: 1)
    }

    private func signInReply(claimed: Bool, account: String? = nil) -> String {
        #"{"token":"tok-1","account":\#(account ?? family),"claimed_device":\#(claimed)}"#
    }

    // MARK: - Account

    func testDecodesAllBooksAsEveryBookAndAShelfAsItsList() throws {
        let all = try JSONDecoder().decode(Account.self, from: Data(family.utf8))
        XCTAssertNil(all.books)
        let some = try JSONDecoder().decode(
            Account.self, from: Data(#"{"id":"a","email":"x@y.com","all_books":false,"books":["paris","london"]}"#.utf8)
        )
        XCTAssertEqual(some.books, ["paris", "london"])
        let again = try JSONDecoder().decode(Account.self, from: JSONEncoder().encode(some))
        XCTAssertEqual(again, some)
        XCTAssertEqual(try JSONDecoder().decode(Account.self, from: JSONEncoder().encode(all)), all)
    }

    func testShelfKeepsLibraryOrderAndHidesOtherBooks() throws {
        let books = try BookLibrary.loadBundled(ids: ["paris", "paris-food", "paris-fun"])
        let account = Account(id: "a", email: "x@y.com", books: ["paris-fun", "paris"])
        XCTAssertEqual(account.shelf(books).map(\.id), ["paris", "paris-fun"])
        XCTAssertEqual(Account(id: "a", email: "x@y.com", books: nil).shelf(books).count, 3)
    }

    func testDefaultBooksAreFiveCities() throws {
        XCTAssertEqual(Account.defaultBooks.count, 13)
        XCTAssertEqual(Set(Account.defaultBooks).count, 13)
        let perCity = Dictionary(grouping: try BookLibrary.loadBundled(ids: Account.defaultBooks), by: \.city).mapValues(\.count)
        XCTAssertEqual(perCity, ["Paris": 4, "London": 2, "Barcelona": 2, "Beijing": 4, "Shanghai": 1])
    }

    func testEmailChecksAndMask() {
        XCTAssertTrue(Account.looksLikeEmail(" mom@example.com "))
        for bad in ["", "mom", "mom@", "@example.com", "mom@example", "mom@.com", "m om@example.com"] {
            XCTAssertFalse(Account.looksLikeEmail(bad), bad)
        }
        XCTAssertEqual(Account.mask("wenjing@example.com"), "w•••@example.com")
    }

    func testServerErrorsMapToMessages() {
        func error(_ status: Int, _ json: String) -> AccountError { AccountService.error(status: status, data: Data(json.utf8)) }
        XCTAssertEqual(error(400, #"{"error":"bad_email"}"#), .badEmail)
        XCTAssertEqual(error(400, #"{"error":"bad_code","tries_left":2}"#), .badCode(triesLeft: 2))
        XCTAssertEqual(error(400, #"{"error":"expired"}"#), .expired)
        XCTAssertEqual(error(429, #"{"error":"too_many","retry_after_s":42}"#), .tooMany(retryAfter: 42))
        XCTAssertEqual(error(401, #"{"error":"signed_out"}"#), .signedOut)
        XCTAssertEqual(error(500, "oops"), .server(status: 500))
        XCTAssertTrue(AccountError.badCode(triesLeft: 1).message.contains("1 try left"))
        XCTAssertTrue(AccountError.tooMany(retryAfter: 600).message.contains("10 min"))
    }

    // MARK: - AccountStore

    func testSignInSendsEmailThenCodeAndRemembersTheAccount() async throws {
        StubAccountServer.replies = [(200, #"{"ok":true,"resend_after_s":60}"#), (200, signInReply(claimed: false))]
        let account = store()
        XCTAssertFalse(account.isSignedIn)
        let wait = try await account.sendCode(to: " Mom@Example.com ")
        XCTAssertEqual(wait, 60)
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        XCTAssertTrue(account.isSignedIn)
        XCTAssertEqual(account.token, "tok-1")

        let (start, verify) = (StubAccountServer.sent[0], StubAccountServer.sent[1])
        XCTAssertEqual(start.path, "/auth/start")
        XCTAssertEqual(start.body["email"] as? String, "mom@example.com")
        XCTAssertEqual(start.auth, "Bearer app-key")
        XCTAssertEqual(verify.path, "/auth/verify")
        XCTAssertEqual(verify.body["code"] as? String, "123456")
        XCTAssertEqual((verify.body["device"] as? [String: Any])?["id"] as? String, "IPAD-1")

        XCTAssertEqual(store().account?.id, "fam", "the sign-in survives a relaunch")
    }

    // MARK: - Milo consent

    func testMiloConsentDecodesAndSurvivesARelaunch() throws {
        let decode = { (json: String) in try JSONDecoder().decode(Account.self, from: Data(json.utf8)) }
        XCTAssertNil(try decode(family).miloConsent, "never allowed (or \"Not now\") saves nothing")
        XCTAssertFalse(try decode(family).miloAllowed, "Milo waits for a grown-up's OK")
        let allowed = try decode(#"{"id":"fam","email":"mom@example.com","all_books":true,"milo_consent":true}"#)
        XCTAssertTrue(allowed.miloAllowed)
        XCTAssertEqual(try JSONDecoder().decode(Account.self, from: JSONEncoder().encode(allowed)), allowed)
        XCTAssertFalse(try decode(#"{"id":"fam","email":"mom@example.com","milo_consent":false}"#).miloAllowed)
    }

    func testEverySignInAsksAboutMiloUntilTheAccountAllowsIt() async throws {
        for consent in ["null", "false", "true"] {
            StubAccountServer.replies = [(200, signInReply(
                claimed: false, account: #"{"id":"fam","email":"mom@example.com","all_books":true,"milo_consent":\#(consent)}"#
            ))]
            let account = store()
            XCTAssertFalse(account.asksMiloConsent)
            try await account.verify(email: "mom@example.com", code: "123456", device: device)
            XCTAssertEqual(account.asksMiloConsent, consent != "true", "milo_consent \(consent)")
            XCTAssertEqual(account.miloAllowed, consent == "true")
        }
    }

    func testNotNowSavesNothingAndARelaunchDoesNotAsk() async throws {
        StubAccountServer.replies = [(200, signInReply(claimed: false))]
        let account = store()
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        XCTAssertTrue(account.asksMiloConsent)
        let sent = StubAccountServer.sent.count
        account.finishMiloConsent()  // "Not now"
        XCTAssertEqual(StubAccountServer.sent.count, sent, "nothing goes to the server")
        XCTAssertFalse(account.asksMiloConsent)
        XCTAssertNil(account.account?.miloConsent)
        XCTAssertFalse(account.miloAllowed, "Talk to Milo asks a grown-up instead")
        XCTAssertFalse(store().asksMiloConsent, "a relaunch doesn't ask again; the next sign-in does")
    }

    func testAllowingMiloSavesItOnTheAccount() async throws {
        StubAccountServer.replies = [(200, signInReply(claimed: false))]
        let account = store()
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        StubAccountServer.replies = [(200, #"{"account":{"id":"fam","email":"mom@example.com","all_books":true,"milo_consent":true}}"#)]
        try await account.setMiloConsent(true)
        let sent = try XCTUnwrap(StubAccountServer.sent.last)
        XCTAssertEqual(sent.method, "POST")
        XCTAssertEqual(sent.path, "/account/milo-consent")
        XCTAssertEqual(sent.session, "tok-1")
        XCTAssertEqual(sent.body["allow"] as? Bool, true)
        XCTAssertTrue(account.miloAllowed)
        XCTAssertTrue(store().miloAllowed, "remembered across launches")
    }

    func testAllowingMiloOfflineKeepsMiloOffButTurningItOffAlwaysCounts() async throws {
        StubAccountServer.replies = [(200, signInReply(claimed: false, account: #"{"id":"fam","email":"mom@example.com","all_books":true,"milo_consent":true}"#))]
        let account = store()
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        XCTAssertTrue(account.miloAllowed, "a second device sees the account's choice")
        XCTAssertFalse(account.asksMiloConsent, "and isn't asked again")

        StubAccountServer.replies = [nil]
        try await account.setMiloConsent(false)
        XCTAssertFalse(account.miloAllowed, "turning Milo off works even offline")
        XCTAssertEqual(StubAccountServer.sent.last?.body["allow"] as? Bool, false)

        StubAccountServer.replies = [nil]
        do {
            try await account.setMiloConsent(true)
            XCTFail("expected offline")
        } catch {
            XCTAssertEqual(error as? AccountError, .offline)
        }
        XCTAssertFalse(account.miloAllowed)
    }

    func testBadEmailNeverReachesTheServer() async {
        do {
            _ = try await store().sendCode(to: "nope")
            XCTFail("expected badEmail")
        } catch {
            XCTAssertEqual(error as? AccountError, .badEmail)
        }
        XCTAssertTrue(StubAccountServer.sent.isEmpty)
    }

    func testWrongCodeKeepsYouSignedOut() async {
        StubAccountServer.replies = [(400, #"{"error":"bad_code","tries_left":4}"#)]
        let account = store()
        do {
            try await account.verify(email: "mom@example.com", code: "000000", device: device)
            XCTFail("expected badCode")
        } catch {
            XCTAssertEqual(error as? AccountError, .badCode(triesLeft: 4))
        }
        XCTAssertFalse(account.isSignedIn)
        XCTAssertNil(tokens.token)
    }

    func testRefreshPicksUpGrantedBooksAndForgetsAForgottenSession() async throws {
        StubAccountServer.replies = [(200, signInReply(claimed: false))]
        let account = store()
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        StubAccountServer.replies = [(200, #"{"account":{"id":"fam","email":"mom@example.com","all_books":false,"books":["paris"]}}"#)]
        await account.refresh()
        XCTAssertEqual(account.account?.books, ["paris"])
        XCTAssertEqual(StubAccountServer.sent.last?.session, "tok-1")

        StubAccountServer.replies = [nil]
        await account.refresh()
        XCTAssertTrue(account.isSignedIn, "offline keeps the sign-in")

        var signedOut: String?
        account.onSignOut = { signedOut = $0 }
        StubAccountServer.replies = [(401, #"{"error":"signed_out"}"#)]
        await account.refresh()
        XCTAssertFalse(account.isSignedIn)
        XCTAssertNil(tokens.token)
        XCTAssertEqual(signedOut, "fam")
    }

    func testSignOutTellsTheServerEvenWhenOffline() async throws {
        StubAccountServer.replies = [(200, signInReply(claimed: false))]
        let account = store()
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        var order: [String] = []
        account.onSignOut = { _ in order.append("done") }
        await account.signOut()
        XCTAssertEqual(StubAccountServer.sent.last?.path, "/auth/signout")
        XCTAssertEqual(StubAccountServer.sent.last?.session, "tok-1")
        XCTAssertEqual(order, ["done"])
        XCTAssertFalse(account.isSignedIn)
        XCTAssertNil(store().account)
    }

    func testDeleteAsksForACodeThenDeletes() async throws {
        StubAccountServer.replies = [(200, signInReply(claimed: false))]
        let account = store()
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        StubAccountServer.replies = [(200, #"{"ok":true,"resend_after_s":60}"#), (200, #"{"ok":true}"#)]
        _ = try await account.sendDeleteCode()
        XCTAssertEqual(StubAccountServer.sent.last?.body["purpose"] as? String, "delete")
        XCTAssertEqual(StubAccountServer.sent.last?.session, "tok-1")
        try await account.deleteAccount(code: "654321")
        XCTAssertEqual(StubAccountServer.sent.last?.path, "/account/delete")
        XCTAssertFalse(account.isSignedIn)
    }

    // MARK: - Account data requests

    func testSignedInRequestsUseTheAccountEndpoints() {
        let stars = StarBackup(baseURL: base, deviceId: "IPAD-1", accessKey: "k", token: "tok").request()
        XCTAssertEqual(stars.url?.absoluteString, "https://account.test/account/stars")
        XCTAssertEqual(stars.value(forHTTPHeaderField: AccountService.sessionHeader), "tok")
        let oldStars = StarBackup(baseURL: base, deviceId: "IPAD-1", accessKey: "k").request()
        XCTAssertEqual(oldStars.url?.absoluteString, "https://account.test/stars?device=IPAD-1")
        XCTAssertNil(oldStars.value(forHTTPHeaderField: AccountService.sessionHeader))

        let coins = CoinService(baseURL: base, deviceId: "IPAD-1", accessKey: "k", token: "tok")
        XCTAssertEqual(coins.fetchRequest().url?.absoluteString, "https://account.test/account/coins")
        XCTAssertEqual(coins.convertRequest(id: "x", coins: 1, starsPerCoin: 10).url?.path, "/account/coins/convert")
        XCTAssertEqual(coins.fetchRequest().value(forHTTPHeaderField: AccountService.sessionHeader), "tok")
        let oldCoins = CoinService(baseURL: base, deviceId: "IPAD-1", accessKey: "k")
        XCTAssertEqual(oldCoins.fetchRequest().url?.absoluteString, "https://account.test/coins?device=IPAD-1")
    }

    // MARK: - AccountSync

    private func sync(_ account: AccountStore, _ stars: StarStore, _ coins: CoinStore) -> AccountSync {
        let base = base
        return AccountSync(
            account: account, stars: stars, coins: coins, defaults: defaults,
            baseURL: { base }, deviceId: { "IPAD-1" }, session: urlSession
        )
    }

    func testClaimingADeviceKeepsEverythingFromBeforeAccounts() async throws {
        // His iPad before accounts: 2 stars, a seen badge, 3 coins and an old heart (from when the app
        // had them), all under the old keys.
        let stars = StarStore(defaults: defaults)
        stars.merge([award("a"), award("b", book: "london")])
        stars.markBadgesSeen(["first-star"])
        defaults.set(["paris/eiffel"], forKey: "lovedPages.v1")
        let coins = CoinStore(defaults: defaults)
        coins.apply(CoinState(starsEarned: 30, starsSpent: 30, coins: 3))

        let account = store()
        let sync = sync(account, stars, coins)
        sync.start()
        XCTAssertEqual(stars.total, 2, "signed out, the old stars stay where they were")

        // Sign in; the account claims the iPad. Then: import, stars, coins.
        StubAccountServer.replies = [
            (200, signInReply(claimed: true)),
            (200, #"{"account":\#(family)}"#),
            (200, #"{"ok":true,"stars_added":0}"#),
        ]
        try await account.verify(email: "mom@example.com", code: "123456", device: device)

        XCTAssertEqual(stars.total, 2)
        XCTAssertTrue(stars.seenBadges.contains("first-star"), "badges he saw don't come back as NEW")
        let paths = StubAccountServer.sent.map(\.path)
        XCTAssertTrue(paths.contains("/account/import"), "\(paths)")
        let imported = StubAccountServer.sent.first { $0.path == "/account/import" }!
        XCTAssertEqual((imported.body["stars"] as? [[String: Any]])?.compactMap { $0["id"] as? String }, ["a", "b"])
        XCTAssertNil(imported.body["hearts"], "hearts are gone from the app")
        XCTAssertFalse(paths.contains("/account/hearts"), "\(paths)")
        XCTAssertEqual(imported.session, "tok-1")
        XCTAssertFalse(defaults.bool(forKey: "account.fam.importPending"))

        // The old keys are never touched.
        XCTAssertEqual(StarStore(defaults: defaults).total, 2)
        XCTAssertEqual(defaults.stringArray(forKey: "lovedPages.v1"), ["paris/eiffel"])
        XCTAssertEqual(CoinStore(defaults: defaults).coins, 3)
    }

    func testSignInFetchesReadingProgressAndSignOutForgetsIt() async throws {
        let stars = StarStore(defaults: defaults)
        let coins = CoinStore(defaults: defaults)
        let account = store()
        let reading = ReadingActivity(
            defaults: defaults, accountId: { account.account?.id }, token: { account.token }, send: { _, _ in }
        )
        let base = base
        let sync = AccountSync(
            account: account, stars: stars, coins: coins, reading: reading, defaults: defaults,
            baseURL: { base }, deviceId: { "IPAD-1" }, session: urlSession
        )
        sync.start()

        // Sign in, account, stars and coins offline, then the reading progress from his other devices.
        StubAccountServer.replies = [
            (200, signInReply(claimed: false)),
            (200, #"{"account":\#(family)}"#),
            nil, nil,
            (200, #"{"progress":{"pages":["paris/welcome","london/hello"],"listened":["paris/welcome"],"days":["2027-01-15"]}}"#),
        ]
        try await account.verify(email: "mom@example.com", code: "123456", device: device)

        let fetch = try XCTUnwrap(StubAccountServer.sent.last)
        XCTAssertEqual(fetch.method, "GET")
        XCTAssertEqual(fetch.path, "/account/reading")
        XCTAssertEqual(fetch.session, "tok-1")
        XCTAssertEqual(reading.progress.pages, ["paris/welcome", "london/hello"])
        XCTAssertEqual(reading.progress.listened, ["paris/welcome"])

        await account.signOut()
        XCTAssertTrue(reading.progress.isEmpty)
        XCTAssertNil(defaults.data(forKey: "account.fam.readingProgress.v1"))
    }

    func testImportIsRetriedUntilTheServerHasIt() async throws {
        let stars = StarStore(defaults: defaults)
        stars.merge([award("a")])
        let account = store()
        let sync = sync(account, stars, CoinStore(defaults: defaults))
        sync.start()
        StubAccountServer.replies = [(200, signInReply(claimed: true))]  // then offline
        try await account.verify(email: "mom@example.com", code: "123456", device: device)
        XCTAssertTrue(defaults.bool(forKey: "account.fam.importPending"))

        StubAccountServer.replies = [(200, #"{"account":\#(family)}"#), (200, #"{"ok":true}"#)]
        await sync.refresh()
        XCTAssertFalse(defaults.bool(forKey: "account.fam.importPending"))
    }

    func testAnotherAccountOnTheDeviceStartsEmptyAndSignOutForgetsIt() async throws {
        let stars = StarStore(defaults: defaults)
        stars.merge([award("a")])
        let coins = CoinStore(defaults: defaults)
        let account = store()
        let sync = sync(account, stars, coins)
        sync.start()

        let other = #"{"id":"other","email":"aunt@example.com","all_books":false,"books":["paris"]}"#
        StubAccountServer.replies = [(200, signInReply(claimed: false, account: other))]
        try await account.verify(email: "aunt@example.com", code: "123456", device: device)
        XCTAssertEqual(stars.total, 0, "his stars are not hers")
        XCTAssertFalse(StubAccountServer.sent.contains { $0.path == "/account/import" })

        stars.receive(award("z"))
        XCTAssertEqual(stars.total, 1)
        XCTAssertNotNil(defaults.data(forKey: "account.other.starAwards.v1"))
        await account.signOut()
        XCTAssertNil(defaults.data(forKey: "account.other.starAwards.v1"), "signing out forgets her copy here")
        XCTAssertEqual(stars.total, 1, "signed out, the device's own stars show again")
        XCTAssertEqual(stars.awards.map(\.id), ["a"])
    }
}
