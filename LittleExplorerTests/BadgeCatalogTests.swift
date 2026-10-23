import XCTest
@testable import LittleExplorer

@MainActor
final class BadgeCatalogTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "BadgeCatalogTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func shelves() throws -> [CityShelf] { BookLibrary.shelves(try BookLibrary.loadBundled()) }

    private func catalog(_ badges: String, categories: String? = nil, format: Int = 1) -> BadgeCatalog? {
        let categories = categories ?? ##"[{"id":"reading","title":"Reading","icon":"book.fill","tint":"#2E9E44"}]"##
        return BadgeCatalog.decode(Data(#"{"format":\#(format),"categories":\#(categories),"badges":\#(badges)}"#.utf8))
    }

    private func rule(_ id: String, metric: String = "pages_read", goal: String = "5", extra: String = "") -> String {
        #"{"id":"\#(id)","category":"reading","title":"T \#(id)","hint":"Read {goal} pages","done":"Read {goal} pages!","icon":"book.fill","metric":"\#(metric)","goal":\#(goal)\#(extra)}"#
    }

    private func byId(_ list: [StarBadge]) -> [String: StarBadge] {
        Dictionary(uniqueKeysWithValues: list.map { ($0.id, $0) })
    }

    // MARK: - Catalog

    func testBundledCatalogUsesOnlyKnownMetricsAndKeepsOldIds() {
        let bundled = BadgeCatalog.bundled
        XCTAssertEqual(bundled.categories.map(\.id), ["reading", "days", "milo", "places"])
        let ids = Set(bundled.badges.map(\.id))
        for old in ["curious-cat", "big-talker", "why-wizard", "star-shower", "come-back-kid", "early-bird",
                    "night-owl", "bookworm", "globe-trotter", "super-star", "expert-{city}"] {
            XCTAssertTrue(ids.contains(old), old)
        }
        for new in ["first-book", "page-turner", "good-listener", "book-stack", "super-reader", "story-time",
                    "book-buddy", "on-a-roll", "super-week", "reading-champ", "milo-pal", "city-hopper", "world-traveler"] {
            XCTAssertTrue(ids.contains(new), new)
        }
        XCTAssertEqual(bundled.badges.count, 24)
        for badge in bundled.badges {
            XCTAssertLessThanOrEqual(badge.title.count, 20, badge.id)
            XCTAssertNotNil(UIImage(systemName: badge.icon), badge.id)
        }
    }

    func testBadEntriesAreSkippedNotFatal() throws {
        let list = [
            rule("ok"),
            rule("unknown-metric", metric: "hugs_given"),
            rule("bad-goal", goal: "\"lots\""),
            rule("zero-goal", goal: "0"),
            rule("ok"),  // duplicate id
            #"{"id":"no-title","category":"reading","metric":"pages_read","goal":3}"#,
            rule("wrong-category").replacingOccurrences(of: "\"reading\"", with: "\"cooking\""),
            rule("all-needs-a-city", goal: "\"all\""),
            rule("odd-icon", extra: #","tint":"red""#).replacingOccurrences(of: "book.fill", with: "not.a.real.symbol"),
        ].joined(separator: ",")
        let decoded = try XCTUnwrap(catalog("[\(list)]"))
        XCTAssertEqual(decoded.badges.map(\.id), ["ok", "odd-icon"])
        let odd = try XCTUnwrap(decoded.badges.last)
        XCTAssertEqual(odd.icon, BadgeCatalog.fallbackIcon)
        XCTAssertEqual(odd.tint, "#2E9E44", "a bad tint falls back to its category's")
    }

    func testUnusedCategoriesAreDroppedAndUnreadableCatalogsAreNil() {
        let two = ##"[{"id":"reading","title":"Reading","icon":"book.fill","tint":"#2E9E44"},{"id":"empty","title":"Empty","icon":"star.fill","tint":"#000000"}]"##
        XCTAssertEqual(catalog("[\(rule("a"))]", categories: two)?.categories.map(\.id), ["reading"])
        XCTAssertNil(catalog("[\(rule("a"))]", format: 2), "a newer format waits for an app update")
        XCTAssertNil(catalog(#"[\#(rule("a", metric: "hugs"))]"#), "no usable badges")
        XCTAssertNil(BadgeCatalog.decode(Data("not json".utf8)))
    }

    func testCachedCatalogWinsAndABrokenOneFallsBackToTheBundledCopy() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let files = ContentFiles(cacheDir: folder, bundle: .main)
        XCTAssertEqual(BadgeCatalog.load(files), .bundled)

        let custom = ##"{"format":1,"categories":[{"id":"reading","title":"Reading","icon":"book.fill","tint":"#2E9E44"}],"badges":[\##(rule("only"))]}"##
        try Data(custom.utf8).write(to: folder.appendingPathComponent(BadgeCatalog.fileName))
        XCTAssertEqual(BadgeCatalog.load(files).badges.map(\.id), ["only"])

        try Data("{".utf8).write(to: folder.appendingPathComponent(BadgeCatalog.fileName))
        XCTAssertEqual(BadgeCatalog.load(files), .bundled)
    }

    // MARK: - Reading badges

    func testReadingBadgesStartLocked() throws {
        let badges = byId(StarStore(defaults: defaults).badges(shelves: try shelves()))
        for id in ["first-book", "page-turner", "good-listener", "on-a-roll", "milo-pal", "city-hopper", "world-traveler"] {
            XCTAssertEqual(badges[id]?.earned, false, id)
            XCTAssertEqual(badges[id]?.progress, 0, id)
        }
    }

    func testReadingMetricsCountWhatHeRead() throws {
        let shelves = try shelves()
        let places = shelves.filter { !$0.books.isEmpty && !$0.isTravel }
        let paris = try XCTUnwrap(places.first)
        let book = try XCTUnwrap(paris.books.first)
        var progress = ReadingProgress()
        for page in book.pages { progress.pages.insert("\(book.id)/\(page.id)") }
        for place in places.dropFirst().prefix(2) {
            let other = try XCTUnwrap(place.books.first?.pages.first)
            progress.pages.insert("\(place.books[0].id)/\(other.id)")
        }
        progress.listened = Set(progress.pages.prefix(10))
        progress.miloPages = Set(progress.pages.prefix(3))
        progress.days = ["2027-01-01", "2027-01-02", "2027-01-03", "2027-01-05", "2027-01-09"]

        let badges = byId(StarStore(defaults: defaults).badges(shelves: shelves, reading: progress))
        XCTAssertEqual(badges["first-book"]?.earned, true)
        XCTAssertEqual(badges["book-stack"]?.progress, 1)
        XCTAssertEqual(badges["page-turner"]?.progress, min(progress.pages.count, 25))
        XCTAssertEqual(badges["good-listener"]?.earned, progress.listened.count >= 10)
        XCTAssertEqual(badges["milo-pal"]?.progress, 3)
        XCTAssertEqual(badges["book-buddy"]?.earned, true, "5 different days")
        XCTAssertEqual(badges["on-a-roll"]?.earned, true, "3 days in a row")
        XCTAssertEqual(badges["super-week"]?.progress, 3)
        XCTAssertEqual(badges["city-hopper"]?.earned, true, "read in 3 cities")
        let countries = Set(places.prefix(3).map { $0.books[0].country }).count
        XCTAssertEqual(badges["world-traveler"]?.progress, countries)
    }

    func testStreakIsTheBestRunAndDaysAreNotDoubleCounted() {
        XCTAssertEqual(ReadingProgress(days: ["2027-01-01", "2027-01-02", "2027-01-04"]).longestStreak, 2)
        XCTAssertEqual(ReadingProgress(days: ["2026-12-31", "2027-01-01", "bad"]).longestStreak, 2)
        XCTAssertEqual(ReadingProgress().longestStreak, 0)
    }

    func testGoalsAreCappedToWhatTheShelvesHave() throws {
        let shelves = try shelves()
        let placeCount = shelves.filter { !$0.isTravel }.count
        let tiny = try XCTUnwrap(catalog("[\(rule("all-cities", metric: "cities_read", goal: "500"))]"))
        let badge = try XCTUnwrap(StarStore(defaults: defaults).badges(shelves: shelves, catalog: tiny).first)
        XCTAssertEqual(badge.goal, placeCount)
        XCTAssertEqual(badge.detail, "Read \(placeCount) pages", "{goal} shows the capped goal")
    }

    func testAnEarnedBadgeStaysEarnedWhenItsGoalGoesUp() throws {
        let shelves = try shelves()
        let progress = ReadingProgress(pages: Set((1...5).map { "paris/p\($0)" }))
        let store = StarStore(defaults: defaults)
        let easy = try XCTUnwrap(catalog("[\(rule("pages"))]"))
        XCTAssertEqual(store.badges(shelves: shelves, catalog: easy, reading: progress).first?.earned, true)
        store.markBadgesSeen(["pages"])

        let harder = try XCTUnwrap(catalog("[\(rule("pages", goal: "50"))]"))
        let badge = try XCTUnwrap(store.badges(shelves: shelves, catalog: harder, reading: progress).first)
        XCTAssertTrue(badge.earned)
        XCTAssertEqual(badge.progress, 5)
        XCTAssertTrue(store.newBadgeIds(in: [badge]).isEmpty)
    }

    func testCatalogOrderAndCategoriesComeFromTheServer() throws {
        let categories = ##"[{"id":"b","title":"B","icon":"star.fill","tint":"#000000"},{"id":"a","title":"A","icon":"star.fill","tint":"#000000"}]"##
        let list = [
            rule("one").replacingOccurrences(of: "\"reading\"", with: "\"a\""),
            rule("two").replacingOccurrences(of: "\"reading\"", with: "\"b\""),
        ].joined(separator: ",")
        let custom = try XCTUnwrap(catalog("[\(list)]", categories: categories))
        let badges = StarStore(defaults: defaults).badges(shelves: try shelves(), catalog: custom)
        XCTAssertEqual(badges.map(\.id), ["two", "one"])
        XCTAssertEqual(badges.map(\.category), ["b", "a"])
    }

    func testDemoProgressEarnsSomeReadingBadgesAndLeavesSomeToGo() throws {
        let shelves = try shelves()
        let demo = ReadingProgress.demo(shelves: shelves)
        let badges = StarStore(defaults: defaults).badges(shelves: shelves, reading: demo)
        let reading = badges.filter { ["reading", "days", "places"].contains($0.category) }
        XCTAssertTrue(reading.contains(where: \.earned))
        XCTAssertTrue(reading.contains { !$0.earned })
    }
}
