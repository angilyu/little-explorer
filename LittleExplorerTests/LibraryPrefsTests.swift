import XCTest
@testable import LittleExplorer

/// Builds a book from JSON the way the app reads them, with an optional `level`.
private func book(_ id: String, city: String, level: String? = nil) -> Book {
    let levelField = level.map { ",\"level\":\($0)" } ?? ""
    let json = """
    {"id":"\(id)","city":"\(city)","country":"X","flag":"🏳️","coverColor":"#336699","tagline":"t"\(levelField),
     "pages":[{"id":"p1","text":"Hi.","about":"a","emoji":"🙂","photo":{}}]}
    """
    return try! BookLibrary.decode(Data(json.utf8))
}

/// Catalog order: Paris (3 books), London (1), Tahoe (2), Travel (1).
private let catalog = [
    book("paris", city: "Paris"), book("paris-food", city: "Paris"), book("paris-fun", city: "Paris"),
    book("london", city: "London"),
    book("tahoe", city: "Tahoe"), book("tahoe-heavenly", city: "Tahoe"),
    book("travel-flight", city: "Travel"),
]

final class BookLevelTests: XCTestCase {
    func testDecodesTheLevelAndDefaultsToTwo() {
        XCTAssertEqual(book("a", city: "A", level: "1").readingLevel, 1)
        XCTAssertEqual(book("a", city: "A", level: "3").level, BookLevel(3))
        XCTAssertEqual(book("a", city: "A").readingLevel, 2)
        XCTAssertNil(book("a", city: "A").level)
        XCTAssertEqual(book("a", city: "A", level: "null").readingLevel, 2)
    }

    func testOddLevelsReadAsTwoInsteadOfFailingTheBook() {
        XCTAssertEqual(book("a", city: "A", level: "\"3\"").readingLevel, 3)
        XCTAssertEqual(book("a", city: "A", level: "7").readingLevel, 2)
        XCTAssertEqual(book("a", city: "A", level: "0").readingLevel, 2)
        XCTAssertEqual(book("a", city: "A", level: "\"hard\"").readingLevel, 2)
        XCTAssertEqual(book("a", city: "A", level: "2.5").readingLevel, 2)
        XCTAssertEqual(book("a", city: "A", level: "{}").readingLevel, 2)
    }

    func testEncodesAsANumber() throws {
        let data = try JSONEncoder().encode(book("a", city: "A", level: "3"))
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        XCTAssertEqual(object?["level"] as? Int, 3)
        XCTAssertEqual(try JSONDecoder().decode(Book.self, from: data).readingLevel, 3)
    }

    func testBundledBooksAllHaveALevel() throws {
        let books = try BookLibrary.loadBundled()
        XCTAssertFalse(books.isEmpty)
        for book in books {
            XCTAssertTrue(BookLevel.range.contains(book.readingLevel), book.id)
        }
    }
}

final class LibraryPrefsTests: XCTestCase {
    private func ids(_ books: [Book]) -> [String] { books.map(\.id) }

    func testNoPrefsKeepsTheCatalogOrder() {
        let prefs = LibraryPrefs()
        XCTAssertEqual(ids(prefs.arranged(catalog)), ids(catalog))
        XCTAssertEqual(ids(prefs.visible(catalog)), ids(catalog))
        XCTAssertFalse(prefs.hasCustomOrder)
    }

    func testSectionOrderPutsListedSectionsFirstAndKeepsTheRest() {
        var prefs = LibraryPrefs()
        prefs.setSectionOrder(["Tahoe", "Atlantis", "Paris"], at: 1)
        XCTAssertEqual(prefs.arrangedShelves(catalog).map(\.city), ["Tahoe", "Paris", "London", "Travel"])
        XCTAssertEqual(ids(prefs.arranged(catalog)), ["tahoe", "tahoe-heavenly", "paris", "paris-food", "paris-fun", "london", "travel-flight"])
        XCTAssertTrue(prefs.hasCustomOrder)
    }

    func testBookOrderSkipsUnknownBooksAndAddsNewOnesAtTheEnd() {
        var prefs = LibraryPrefs()
        // "paris-old" was removed from the catalog; "paris-food" is new since the order was saved.
        prefs.setBookOrder(["paris-fun", "paris-old", "paris"], in: "Paris", at: 1)
        prefs.setBookOrder(["nope"], in: "Atlantis", at: 1)
        XCTAssertEqual(ids(prefs.arranged(catalog)).prefix(3), ["paris-fun", "paris", "paris-food"])
        XCTAssertEqual(prefs.arranged(catalog).count, catalog.count)
    }

    func testHiddenBooksLeaveTheShelfAndEmptySectionsGo() {
        var prefs = LibraryPrefs()
        prefs.setHidden("london", true, at: 1)
        prefs.setHidden("paris-food", true, at: 1)
        prefs.setHidden("gone-book", true, at: 1)
        let visible = prefs.visible(catalog)
        XCTAssertEqual(ids(visible), ["paris", "paris-fun", "tahoe", "tahoe-heavenly", "travel-flight"])
        XCTAssertFalse(BookLibrary.shelves(visible).map(\.city).contains("London"))
        // Every book is still arranged (for the Passport, badges and the grown-ups corner).
        XCTAssertEqual(prefs.arranged(catalog).count, catalog.count)
        XCTAssertEqual(prefs.hiddenCount, 3)
        prefs.setHidden("london", false, at: 2)
        XCTAssertTrue(ids(prefs.visible(catalog)).contains("london"))
        XCTAssertEqual(prefs.hidden["london"], .init(on: false, t: 2))
    }

    func testRestoreOrderAndShowAllLeaveTombstones() {
        var prefs = LibraryPrefs()
        prefs.setSectionOrder(["Tahoe"], at: 1)
        prefs.setBookOrder(["paris-fun"], in: "Paris", at: 1)
        prefs.setHidden("london", true, at: 1)
        prefs.restoreOrder(at: 5)
        XCTAssertEqual(ids(prefs.visible(catalog)), ids(catalog).filter { $0 != "london" })
        XCTAssertEqual(prefs.sections, .init(order: [], t: 5))
        XCTAssertEqual(prefs.books["Paris"], .init(order: [], t: 5))
        XCTAssertFalse(prefs.hasCustomOrder)
        prefs.showAll(at: 6)
        XCTAssertEqual(ids(prefs.visible(catalog)), ids(catalog))
        XCTAssertEqual(prefs.hidden["london"], .init(on: false, t: 6))
    }

    func testMergeKeepsTheLaterChangePerKey() {
        var mine = LibraryPrefs()
        mine.setHidden("london", true, at: 10)
        mine.setHidden("paris", true, at: 30)
        mine.setSectionOrder(["Tahoe"], at: 30)
        var theirs = LibraryPrefs()
        theirs.v = 4
        theirs.hidden["london"] = .init(on: false, t: 20)
        theirs.hidden["paris"] = .init(on: false, t: 5)
        theirs.setSectionOrder(["Paris"], at: 30)
        theirs.setBookOrder(["paris-fun"], in: "Paris", at: 1)
        let merged = mine.merging(theirs)
        XCTAssertEqual(merged.hidden["london"]?.on, false)
        XCTAssertEqual(merged.hidden["paris"]?.on, true)
        XCTAssertEqual(merged.sections?.order, ["Paris"], "a tie keeps the other side's")
        XCTAssertEqual(merged.books["Paris"]?.order, ["paris-fun"])
        XCTAssertEqual(merged.v, 4)
    }

    func testDecodesTheServerReplyAndBuildsThePayload() throws {
        let json = """
        {"v":3,"sections":{"order":["Tahoe"],"t":1792000000000},"books":{"Paris":{"order":["paris-food"],"t":1}},
         "hidden":{"london":{"on":true,"t":2}},"updated_at":"2026-10-23T10:00:00-07:00","future":1}
        """
        let prefs = try JSONDecoder().decode(LibraryPrefs.self, from: Data(json.utf8))
        XCTAssertEqual(prefs.v, 3)
        XCTAssertEqual(prefs.sections, .init(order: ["Tahoe"], t: 1_792_000_000_000))
        XCTAssertTrue(prefs.isHidden("london"))
        XCTAssertEqual(prefs.updatedAt, "2026-10-23T10:00:00-07:00")
        let empty = try JSONDecoder().decode(LibraryPrefs.self, from: Data(#"{"v":0,"sections":null,"books":{},"hidden":{},"updated_at":null}"#.utf8))
        XCTAssertEqual(empty, LibraryPrefs())

        let body = prefs.payload
        XCTAssertNil(body["v"])
        XCTAssertEqual((body["sections"] as? [String: Any])?["order"] as? [String], ["Tahoe"])
        XCTAssertEqual(((body["hidden"] as? [String: Any])?["london"] as? [String: Any])?["on"] as? Bool, true)
        XCTAssertNoThrow(try JSONSerialization.data(withJSONObject: body))
        XCTAssertNil(LibraryPrefs().payload["sections"])
    }
}

@MainActor
final class LibraryStoreTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite: String!
    private var clock = Date(timeIntervalSince1970: 1_800_043_200)
    private var token: String? = "key-1"
    private var server = LibraryPrefs()
    private var sent: [LibraryPrefs?] = []
    private var failure: Error?
    private var onSend: (() -> Void)?

    override func setUp() async throws {
        suite = "LibraryStoreTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suite)
    }

    private func makeStore(enabled: Bool = true) -> LibraryStore {
        LibraryStore(
            enabled: enabled,
            defaults: defaults,
            token: { [unowned self] in token },
            send: { [unowned self] prefs, token in
                XCTAssertEqual(token, "key-1")
                sent.append(prefs)
                onSend?()
                if let failure { throw failure }
                if let prefs {
                    server = server.merging(prefs)
                    server.v += 1
                }
                return server
            },
            now: { [unowned self] in clock }
        )
    }

    private func settle() async {
        for _ in 0..<20 { await Task.yield() }
    }

    func testEditsSaveForTheAccountAndSync() async {
        let store = makeStore()
        store.switchAccount("acct-1")
        store.setHidden("london", true)
        XCTAssertTrue(store.prefs.isHidden("london"))
        XCTAssertEqual(store.prefs.hidden["london"]?.t, LibraryPrefs.millis(clock))
        await store.sync()
        await settle()
        XCTAssertFalse(store.dirty)
        XCTAssertTrue(server.isHidden("london"))
        XCTAssertEqual(store.prefs, server)

        // Another account on the device starts with the default shelf; switching back restores the copy.
        store.switchAccount("acct-2")
        XCTAssertEqual(store.prefs, LibraryPrefs())
        let copy = makeStore()
        copy.switchAccount("acct-1")
        XCTAssertTrue(copy.prefs.isHidden("london"))
    }

    func testOfflineEditsStayDirtyUntilSent() async {
        failure = LibraryService.Failure.later
        let store = makeStore()
        store.switchAccount("acct-1")
        store.moveSections(["Paris", "London", "Tahoe"], from: [2], to: 0)
        await store.sync()
        XCTAssertTrue(store.dirty)
        XCTAssertEqual(store.prefs.sections?.order, ["Tahoe", "Paris", "London"])

        // Relaunched on the same device: still dirty, so it's sent once online.
        let relaunched = makeStore()
        relaunched.switchAccount("acct-1")
        XCTAssertTrue(relaunched.dirty)
        failure = nil
        sent = []
        await relaunched.sync()
        XCTAssertEqual(sent.count, 1)
        XCTAssertEqual(sent.first??.sections?.order, ["Tahoe", "Paris", "London"])
        XCTAssertFalse(relaunched.dirty)
    }

    func testAFetchBringsOtherDevicesChanges() async {
        server.setHidden("paris", true, at: 5)
        let store = makeStore()
        store.switchAccount("acct-1")
        await store.sync()
        XCTAssertEqual(sent.count, 1)
        XCTAssertNil(sent[0], "nothing to send, so it only fetches")
        XCTAssertTrue(store.prefs.isHidden("paris"))
    }

    func testAChangeDuringTheRequestIsKeptAndSentNext() async {
        let store = makeStore()
        store.switchAccount("acct-1")
        store.setHidden("london", true)
        await settle()
        sent = []
        onSend = { [unowned self] in
            if sent.count == 1 {
                clock += 1
                store.setHidden("tahoe", true)
            }
        }
        store.setHidden("paris", true)
        await store.sync()
        await settle()
        XCTAssertTrue(store.prefs.isHidden("tahoe"))
        XCTAssertTrue(server.isHidden("tahoe"))
        XCTAssertFalse(store.dirty)
    }

    func testRefusedChangesFallBackToTheServersCopy() async {
        server.setHidden("paris", true, at: 5)
        let store = makeStore()
        store.switchAccount("acct-1")
        failure = LibraryService.Failure.refused
        store.setHidden("london", true)
        // The PUT is refused; the GET after it works.
        onSend = { [unowned self] in if sent.count > 1 { failure = nil } }
        await store.sync()
        await settle()
        XCTAssertFalse(store.dirty)
        XCTAssertEqual(store.prefs, server)
    }

    func testSignOutForgetsTheAccountsCopy() async {
        let store = makeStore()
        store.switchAccount("acct-1")
        store.setHidden("london", true)
        store.forgetAccount("acct-1")
        XCTAssertNil(store.accountId)
        XCTAssertEqual(store.prefs, LibraryPrefs())
        store.switchAccount("acct-1")
        XCTAssertFalse(store.prefs.isHidden("london"))
        XCTAssertNil(defaults.data(forKey: "account.acct-1.library.v1"))
    }

    func testSignedOutOrDemoNeverSavesOrSends() async {
        let signedOut = makeStore()
        signedOut.setHidden("london", true)
        XCTAssertFalse(signedOut.prefs.isHidden("london"))

        let demo = makeStore(enabled: false)
        demo.switchAccount("demo-family")
        var seeded = LibraryPrefs()
        seeded.setHidden("paris", true, at: 1)
        demo.seedDemo(seeded)
        demo.setHidden("london", true)
        await demo.sync()
        await settle()
        XCTAssertTrue(demo.prefs.isHidden("paris") && demo.prefs.isHidden("london"))
        XCTAssertTrue(sent.isEmpty)
        XCTAssertNil(defaults.data(forKey: "account.demo-family.library.v1"))
    }

    func testMovesUseTheOrderOnScreen() {
        let store = makeStore(enabled: false)
        store.switchAccount("acct-1")
        store.moveBooks(["paris", "paris-food", "paris-fun"], in: "Paris", from: [0], to: 3)
        XCTAssertEqual(store.prefs.books["Paris"]?.order, ["paris-food", "paris-fun", "paris"])
        store.moveBooks(["paris-food", "paris-fun", "paris"], in: "Paris", from: [1], to: 1)
        XCTAssertEqual(store.prefs.books["Paris"]?.order, ["paris-food", "paris-fun", "paris"], "no move, no change")
        XCTAssertEqual(ids(store.visible(catalog)).prefix(3), ["paris-food", "paris-fun", "paris"])
        store.restoreOrder()
        XCTAssertEqual(ids(store.visible(catalog)), ids(catalog))
    }

    private func ids(_ books: [Book]) -> [String] { books.map(\.id) }
}

final class LibraryDemoLaunchTests: XCTestCase {
    func testDemoFlagsArrangeTheShelf() {
        let demo = DemoLaunch.parse(["app", "-demoHide", "london, paris-food", "-demoSections", "Tahoe,Paris",
                                     "-demoOrder", "Paris:paris-fun,paris", "-demoArrange"])
        XCTAssertEqual(demo?.openArrange, true)
        XCTAssertEqual(demo?.openParent, true)
        let prefs = demo?.library ?? LibraryPrefs()
        XCTAssertEqual(prefs.visible(catalog).map(\.id), ["tahoe", "tahoe-heavenly", "paris-fun", "paris", "travel-flight"])
    }

    func testDemoLevels() {
        let demo = DemoLaunch.parse(["app", "-demoLevels", "paris:1,london:3,tahoe:x"])
        let books = demo?.leveled(catalog) ?? []
        XCTAssertEqual(books.map(\.readingLevel), [1, 2, 2, 3, 2, 2, 2])
    }
}
