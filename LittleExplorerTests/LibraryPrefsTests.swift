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
    /// The shelf layout the fake server answers with (none, like a server from before 1.2.6, when nil).
    private var layoutFor: ((LibraryPrefs) -> ShelfLayout?)?

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
                return LibraryReply(library: server, shelf: layoutFor?(server))
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

    // MARK: - The server's shelf layout

    /// A layout as the server sends it: Lake Tahoe first, Paris with paris-fun hidden and a book this device
    /// doesn't have, then "TRAVEL" (the server's name for the Travel section).
    private static let layoutJSON = """
    {"format": 1, "sections": [
      {"key": "tahoe", "name": "Tahoe", "flag": "🇺🇸", "flag_book": "tahoe-heavenly",
       "books": [{"id": "tahoe-heavenly", "hidden": false}, {"id": "tahoe", "hidden": false}]},
      {"key": "paris", "name": "Paris", "flag": "🇫🇷", "flag_book": "paris",
       "books": [{"id": "paris-fun", "hidden": true}, {"id": "not-here-yet", "hidden": false}, {"id": "paris", "hidden": false}]},
      {"key": "travel", "name": "TRAVEL", "books": [{"id": "travel-flight", "hidden": false}, {"id": "making-j1", "hidden": false, "making": true}]}
    ]}
    """

    private func serverLayout() -> ShelfLayout {
        try! JSONDecoder().decode(ShelfLayout.self, from: Data(Self.layoutJSON.utf8))
    }

    func testRendersTheServersLayout() async {
        layoutFor = { [unowned self] _ in serverLayout() }
        let store = makeStore()
        store.switchAccount("acct-1")
        await store.sync()
        XCTAssertEqual(store.layout, serverLayout())
        let shelves = store.arrangedShelves(catalog)
        // The layout's order and names; books it doesn't list (paris-food, london) go where the device puts them.
        XCTAssertEqual(shelves.map(\.city), ["Tahoe", "Paris", "TRAVEL", "London"])
        XCTAssertEqual(shelves[0].books.map(\.id), ["tahoe-heavenly", "tahoe"])
        XCTAssertEqual(shelves[0].flagBook.id, "tahoe-heavenly")
        XCTAssertEqual(shelves[1].books.map(\.id), ["paris-fun", "paris", "paris-food"])
        XCTAssertEqual(shelves[2].books.map(\.city), ["TRAVEL"])
        // Hidden as the layout says (the device's own prefs hide nothing).
        XCTAssertFalse(store.prefs.isHidden("paris-fun"))
        XCTAssertTrue(store.isHidden("paris-fun"))
        XCTAssertEqual(ids(store.visible(catalog)), ["tahoe-heavenly", "tahoe", "paris", "paris-food", "travel-flight", "london"])
        // A book being made goes where the layout puts it.
        var placeholder = book("making-j1", city: "travel")
        placeholder.generated = true
        XCTAssertEqual(store.visibleShelves(catalog + [placeholder])[2].books.map(\.id), ["travel-flight", "making-j1"])
    }

    func testTheCachedLayoutShowsOfflineAndAfterRelaunch() async {
        layoutFor = { [unowned self] _ in serverLayout() }
        let store = makeStore()
        store.switchAccount("acct-1")
        await store.sync()

        failure = LibraryService.Failure.later
        let relaunched = makeStore()
        relaunched.switchAccount("acct-1")
        await relaunched.sync()
        XCTAssertEqual(relaunched.layout, serverLayout())
        XCTAssertEqual(relaunched.arrangedShelves(catalog).map(\.city), ["Tahoe", "Paris", "TRAVEL", "London"])

        // A change the server hasn't seen: the device arranges the shelf itself until it has.
        relaunched.moveSections(["Tahoe", "Paris", "TRAVEL", "London"], from: [3], to: 0)
        XCTAssertTrue(relaunched.dirty)
        XCTAssertEqual(relaunched.arrangedShelves(catalog).map(\.city), ["London", "Tahoe", "Paris", "Travel"])
        XCTAssertFalse(relaunched.isHidden("paris-fun"))

        // Another account on the device has no layout until its own sync.
        relaunched.switchAccount("acct-2")
        XCTAssertNil(relaunched.layout)
        relaunched.forgetAccount("acct-2")
        XCTAssertNil(relaunched.layout)
    }

    func testAServerWithoutLayoutsFallsBackToTheDevicesArrangement() async throws {
        let store = makeStore()
        store.switchAccount("acct-1")
        store.moveSections(["Paris", "London", "Tahoe", "Travel"], from: [2], to: 0)
        await store.sync()
        await settle()
        XCTAssertFalse(store.dirty)
        XCTAssertNil(store.layout)
        XCTAssertEqual(store.arrangedShelves(catalog).map(\.city), ["Tahoe", "Paris", "London", "Travel"])

        // A layout format this app can't read counts as none.
        let newer = #"{"library": {"v": 1}, "shelf": {"format": 2, "sections": "something new"}}"#
        let reply = try JSONDecoder().decode(LibraryReply.self, from: Data(newer.utf8))
        XCTAssertNil(reply.shelf)
        XCTAssertEqual(reply.library.v, 1)
    }

    func testArrangeEditsRoundTripThroughTheServer() async {
        // The fake server lays the shelf out from what it saved (named in capitals, so the test sees whose it is).
        layoutFor = { saved in
            ShelfLayout(format: 1, sections: saved.arrangedShelves(catalog).map { shelf in
                .init(key: shelf.city.lowercased(), name: shelf.city.uppercased(), books: shelf.books.map {
                    .init(id: $0.id, hidden: saved.isHidden($0.id))
                })
            })
        }
        let store = makeStore()
        store.switchAccount("acct-1")
        await store.sync()
        let onScreen = store.arrangedShelves(catalog).map(\.city)
        XCTAssertEqual(onScreen, ["PARIS", "LONDON", "TAHOE", "TRAVEL"])

        store.moveSections(onScreen, from: [2], to: 0)
        store.moveBooks(["paris", "paris-food", "paris-fun"], in: "PARIS", from: [2], to: 0)
        store.setHidden("london", true)
        await store.sync()
        await settle()
        XCTAssertFalse(store.dirty)
        XCTAssertEqual(sent.compactMap { $0 }.last?.sections?.order, ["TAHOE", "PARIS", "LONDON", "TRAVEL"])
        XCTAssertEqual(server.books["PARIS"]?.order, ["paris-fun", "paris", "paris-food"])
        XCTAssertEqual(store.layout, layoutFor?(server))
        XCTAssertEqual(store.visibleShelves(catalog).map(\.city), ["TAHOE", "PARIS", "TRAVEL"])
        XCTAssertEqual(store.arrangedShelves(catalog)[1].books.map(\.id), ["paris-fun", "paris", "paris-food"])
        XCTAssertTrue(store.isHidden("london"))
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

/// Sections a grown-up made books in ("Make a book") show in "Arrange library" just as on the shelf.
@MainActor
final class MadeBookSectionsTests: XCTestCase {
    private func made(_ id: String, _ section: String) -> Book {
        var made = book(id, city: section)
        made.generated = true
        return made
    }

    /// The account's shelf: the catalog, then made books in "Matt’s" (curly apostrophe), Paris and Hawaii,
    /// as on the account that reported the bug.
    private var shelf: [Book] {
        BookLibrary.shelfBooks(catalog: catalog, made: [
            made("dinosaur-2bb188", "Matt’s"), made("what-should-i-do-at-paris-d1a210", "Paris"),
            made("honolulu-046d4f", "Hawaii"), made("honolulu-6e37b6", "Hawaii"),
        ])
    }

    private func sections(_ prefs: LibraryPrefs) -> [String] { prefs.arrangedShelves(shelf).map(\.city) }

    func testAMadeBookSectionShowsInArrangeLikeOnTheShelf() {
        let prefs = LibraryPrefs()
        XCTAssertEqual(sections(prefs), ["Paris", "London", "Tahoe", "Travel", "Matt’s", "Hawaii"])
        XCTAssertEqual(prefs.arrangedShelves(shelf).first { $0.city == "Matt’s" }?.books.map(\.id), ["dinosaur-2bb188"])
        // The shelf groups the arranged books into the same sections.
        XCTAssertEqual(BookLibrary.shelves(prefs.visible(shelf)).map(\.city), sections(prefs))
    }

    func testASavedOrderFromBeforeTheSectionExistedKeepsItWhereTheShelfShowsIt() throws {
        // Saved by an earlier version, before any book was made: no "Matt’s" or "Hawaii".
        let json = """
        {"v": 2, "sections": {"order": ["Tahoe", "Paris"], "t": 1792000000000},
         "books": {"Paris": {"order": ["paris-fun", "paris"], "t": 1792000000000}},
         "hidden": {"london": {"on": true, "t": 1792000000000}}, "updated_at": "2026-10-23T10:00:00-07:00"}
        """
        let prefs = try JSONDecoder().decode(LibraryPrefs.self, from: Data(json.utf8))
        XCTAssertEqual(sections(prefs), ["Tahoe", "Paris", "London", "Travel", "Matt’s", "Hawaii"])
        let paris = prefs.arrangedShelves(shelf)[1]
        XCTAssertEqual(paris.books.map(\.id), ["paris-fun", "paris", "paris-food", "what-should-i-do-at-paris-d1a210"])
        XCTAssertEqual(BookLibrary.shelves(prefs.visible(shelf)).map(\.city), ["Tahoe", "Paris", "Travel", "Matt’s", "Hawaii"])
    }

    func testReorderingAndHidingAMadeSectionPersists() {
        let suite = "MadeBookSectionsTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        func store() -> LibraryStore {
            LibraryStore(defaults: defaults, token: { nil }, send: { _, _ in LibraryReply(library: LibraryPrefs()) })
        }
        let arranging = store()
        arranging.switchAccount("6623a9cd")
        let onScreen = sections(arranging.prefs)
        arranging.moveSections(onScreen, from: [onScreen.firstIndex(of: "Matt’s")!], to: 0)
        let hawaii = arranging.prefs.arrangedShelves(shelf).first { $0.city == "Hawaii" }!
        arranging.moveBooks(hawaii.books.map(\.id), in: hawaii.city, from: [1], to: 0)
        arranging.setHidden("dinosaur-2bb188", true)

        let relaunched = store()
        relaunched.switchAccount("6623a9cd")
        XCTAssertEqual(sections(relaunched.prefs), ["Matt’s", "Paris", "London", "Tahoe", "Travel", "Hawaii"])
        XCTAssertEqual(relaunched.prefs.sections?.order.first, "Matt’s")
        XCTAssertEqual(relaunched.prefs.arrangedShelves(shelf).last?.books.map(\.id), ["honolulu-6e37b6", "honolulu-046d4f"])
        XCTAssertTrue(relaunched.prefs.isHidden("dinosaur-2bb188"))
        // Hidden on the shelf (its section goes with it), still listed in Arrange to show again.
        let visible = BookLibrary.shelves(relaunched.visible(shelf)).map(\.city)
        XCTAssertEqual(visible, ["Paris", "London", "Tahoe", "Travel", "Hawaii"])
        XCTAssertTrue(sections(relaunched.prefs).contains("Matt’s"))
        // What goes to the server keeps the section's name as typed.
        let body = relaunched.prefs.payload
        XCTAssertEqual((body["sections"] as? [String: Any])?["order"] as? [String], sections(relaunched.prefs))
        XCTAssertNotNil((body["books"] as? [String: Any])?["Hawaii"])
    }

    func testASectionNamedLikeACatalogCityMergesIntoIt() {
        let books = BookLibrary.shelfBooks(catalog: catalog, made: [
            made("paris-made", "Paris"), made("paris-typed", " paris "), made("tahoe-typed", "LAKE  tahoe"),
            made("tahoe-made", "TAHOE"),
        ])
        XCTAssertEqual(books.filter(\.isMade).map(\.city), ["Paris", "Paris", "LAKE  tahoe", "Tahoe"])
        let shelves = LibraryPrefs().arrangedShelves(books)
        XCTAssertEqual(shelves.map(\.city), ["Paris", "London", "Tahoe", "Travel", "LAKE  tahoe"])
        XCTAssertEqual(shelves[0].books.map(\.id), ["paris", "paris-food", "paris-fun", "paris-made", "paris-typed"])
        XCTAssertEqual(shelves[2].books.map(\.id), ["tahoe", "tahoe-heavenly", "tahoe-made"])
        // A catalog book already on the shelf isn't listed twice.
        XCTAssertEqual(BookLibrary.shelfBooks(catalog: catalog, made: [made("paris", "Paris")]).count, catalog.count)
    }

    func testCurlyAndStraightApostrophesAreOneSection() {
        XCTAssertEqual(BookLibrary.sectionKey("Matt’s"), BookLibrary.sectionKey("matt's"))
        XCTAssertEqual(BookLibrary.sectionKey(" Matt\u{02BC}s  Trips "), "matt's trips")
        XCTAssertNotEqual(BookLibrary.sectionKey("Matt’s"), BookLibrary.sectionKey("Matts"))

        let books = BookLibrary.shelfBooks(catalog: catalog, made: [made("dino", "Matt’s"), made("rex", "Matt's")])
        XCTAssertEqual(books.suffix(2).map(\.city), ["Matt’s", "Matt’s"])
        // Orders saved under either spelling (say by another device) still apply.
        var prefs = LibraryPrefs()
        prefs.setSectionOrder(["Matt's", "Paris"], at: 1)
        prefs.setBookOrder(["rex", "dino"], in: "matt’s", at: 1)
        let shelves = prefs.arrangedShelves(books)
        XCTAssertEqual(shelves.first?.city, "Matt’s")
        XCTAssertEqual(shelves.first?.books.map(\.id), ["rex", "dino"])
        // The later of two spellings' book orders counts.
        prefs.setBookOrder(["dino", "rex"], in: "Matt’s", at: 2)
        XCTAssertEqual(prefs.arrangedShelves(books).first?.books.map(\.id), ["dino", "rex"])
        XCTAssertEqual(BookLibrary.shelves(books + [made("x", "MATT’S")]).map(\.city), ["Paris", "London", "Tahoe", "Travel", "Matt’s"])
    }
}

/// `LibraryService` asks `/account/shelf` and falls back to `/account/library` on a server from before 1.2.6.
final class LibraryServiceTests: XCTestCase {
    private func service() -> LibraryService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubAccountServer.self]
        return LibraryService(baseURL: URL(string: "https://account.test")!, session: URLSession(configuration: config))
    }

    override func setUp() {
        StubAccountServer.replies = []
        StubAccountServer.sent = []
    }

    func testAsksForTheShelfLayout() async throws {
        let shelf = #"{"format": 1, "sections": [{"key": "matt's", "name": "Matt’s", "flag": "", "flag_book": "dino", "books": [{"id": "dino", "hidden": false}]}]}"#
        StubAccountServer.replies = [(200, #"{"library": {"v": 2, "books": {}, "hidden": {}}, "shelf": \#(shelf)}"#)]
        var prefs = LibraryPrefs()
        prefs.setSectionOrder(["Matt’s"], at: 5)
        let reply = try await service().sync(prefs, token: "tok")
        XCTAssertEqual(StubAccountServer.sent.map(\.path), ["/account/shelf"])
        XCTAssertEqual(StubAccountServer.sent[0].method, "PUT")
        XCTAssertEqual((StubAccountServer.sent[0].body["sections"] as? [String: Any])?["order"] as? [String], ["Matt’s"])
        XCTAssertEqual(reply.library.v, 2)
        XCTAssertEqual(reply.shelf?.sections.first?.name, "Matt’s")
        XCTAssertEqual(reply.shelf?.sections.first?.flagBook, "dino")
    }

    func testAnOlderServerGetsTheLibraryEndpoint() async throws {
        StubAccountServer.replies = [(404, #"{"error": "not found"}"#), (200, #"{"library": {"v": 3}}"#)]
        let reply = try await service().sync(nil, token: "tok")
        XCTAssertEqual(StubAccountServer.sent.map(\.path), ["/account/shelf", "/account/library"])
        XCTAssertEqual(StubAccountServer.sent.map(\.method), ["GET", "GET"])
        XCTAssertEqual(reply.library.v, 3)
        XCTAssertNil(reply.shelf)
    }

    func testRefusedChangesAreNotRetriedElsewhere() async {
        StubAccountServer.replies = [(400, #"{"error": "bad_library"}"#)]
        do {
            _ = try await service().sync(LibraryPrefs(), token: "tok")
            XCTFail("refused")
        } catch {
            XCTAssertEqual(error as? LibraryService.Failure, .refused)
        }
        XCTAssertEqual(StubAccountServer.sent.count, 1)
    }
}
