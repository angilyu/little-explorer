import XCTest
@testable import LittleExplorer

@MainActor
final class HeartStoreTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "HeartStoreTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testStartsEmpty() {
        let store = HeartStore(defaults: defaults)
        XCTAssertFalse(store.isLoved(bookId: "paris", pageId: "eiffel"))
        XCTAssertEqual(store.lovedCount(bookId: "paris"), 0)
    }

    func testToggleLovesAndUnloves() {
        let store = HeartStore(defaults: defaults)
        store.toggle(bookId: "paris", pageId: "eiffel")
        XCTAssertTrue(store.isLoved(bookId: "paris", pageId: "eiffel"))
        store.toggle(bookId: "paris", pageId: "eiffel")
        XCTAssertFalse(store.isLoved(bookId: "paris", pageId: "eiffel"))
    }

    func testPersistsAcrossInstances() {
        let first = HeartStore(defaults: defaults)
        first.setLoved(true, bookId: "london", pageId: "bus")
        first.setLoved(true, bookId: "paris", pageId: "eiffel")

        let second = HeartStore(defaults: defaults)
        XCTAssertTrue(second.isLoved(bookId: "london", pageId: "bus"))
        XCTAssertTrue(second.isLoved(bookId: "paris", pageId: "eiffel"))
        XCTAssertEqual(second.loved.count, 2)
    }

    func testCountsArePerBookAndDoNotLeakAcrossPrefixes() {
        let store = HeartStore(defaults: defaults)
        store.setLoved(true, bookId: "paris", pageId: "eiffel")
        store.setLoved(true, bookId: "paris", pageId: "louvre")
        store.setLoved(true, bookId: "parisian", pageId: "x")
        XCTAssertEqual(store.lovedCount(bookId: "paris"), 2)
        XCTAssertEqual(store.lovedCount(bookId: "london"), 0)
    }

    func testLovedPagesKeepsBookOrder() throws {
        let paris = try XCTUnwrap(BookLibrary.loadBundled(ids: ["paris"]).first)
        let store = HeartStore(defaults: defaults)
        store.setLoved(true, bookId: "paris", pageId: paris.pages[5].id)
        store.setLoved(true, bookId: "paris", pageId: paris.pages[1].id)
        XCTAssertEqual(store.lovedPages(in: paris).map(\.id), [paris.pages[1].id, paris.pages[5].id])
    }

    func testSettingSameValueTwiceIsIdempotent() {
        let store = HeartStore(defaults: defaults)
        store.setLoved(true, bookId: "barcelona", pageId: "churros")
        store.setLoved(true, bookId: "barcelona", pageId: "churros")
        XCTAssertEqual(store.lovedCount(bookId: "barcelona"), 1)
        store.setLoved(false, bookId: "barcelona", pageId: "churros")
        XCTAssertEqual(HeartStore(defaults: defaults).lovedCount(bookId: "barcelona"), 0)
    }

    func testThemedBooksWithSamePageIdsKeepSeparateHearts() throws {
        let books = try BookLibrary.loadBundled(ids: ["paris", "paris-food", "paris-fun"])
        let store = HeartStore(defaults: defaults)
        store.setLoved(true, bookId: "paris", pageId: "welcome")
        store.setLoved(true, bookId: "paris-food", pageId: "macarons")
        XCTAssertTrue(store.isLoved(bookId: "paris", pageId: "welcome"))
        XCTAssertFalse(store.isLoved(bookId: "paris-food", pageId: "welcome"))
        XCTAssertFalse(store.isLoved(bookId: "paris-fun", pageId: "welcome"))
        XCTAssertEqual(books.map { store.lovedCount(bookId: $0.id) }, [1, 1, 0])
        XCTAssertEqual(store.lovedPages(in: books[1]).map(\.id), ["macarons"])
    }

    func testExistingParisHeartKeysStillLoad() {
        // Hearts saved before the themed books existed used "paris/<page>"; the Famous Places book keeps id "paris".
        let legacy = HeartStore(defaults: defaults)
        legacy.setLoved(true, bookId: "paris", pageId: "eiffel")
        XCTAssertEqual(HeartStore.id(bookId: "paris", pageId: "eiffel"), "paris/eiffel")
        XCTAssertTrue(HeartStore(defaults: defaults).isLoved(bookId: "paris", pageId: "eiffel"))
    }
}
