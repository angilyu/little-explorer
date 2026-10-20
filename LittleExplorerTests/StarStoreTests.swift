import XCTest
@testable import LittleExplorer

@MainActor
final class StarStoreTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "StarStoreTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func award(_ id: String, book: String = "paris", city: String = "Paris", stars: Int = 1, long: Bool = false, at: String = "2026-01-01T10:00:00") -> StarAward {
        StarAward(id: id, awardedAt: at, book: book, page: "p1", city: city, long: long, stars: stars)
    }

    private func shelves() throws -> [CityShelf] { BookLibrary.shelves(try BookLibrary.loadBundled()) }

    func testDecodesAgentPayload() throws {
        let json = """
        {"id":"abc123","device_id":"dev","awarded_at":"2026-05-01T10:00:00","book":"paris","page":"eiffel",
         "city":"Paris","kind":"follow_up","long":true,"why_how":false,"followup":true,"stars":3,"words":10,
         "reason":"asked why it is tall","session_total":5}
        """
        let award = try JSONDecoder().decode(StarAward.self, from: Data(json.utf8))
        XCTAssertEqual(award.id, "abc123")
        XCTAssertEqual(award.stars, 3)
        XCTAssertEqual(award.kind, "follow_up")
        XCTAssertTrue(award.long)
        XCTAssertTrue(award.followup)
        XCTAssertFalse(award.whyHow)
        XCTAssertEqual(award.page, "eiffel")
    }

    func testDecodesBackupResponse() throws {
        let json = #"{"stars":[{"id":"a","stars":1,"book":"london","city":"London"}],"total":1}"#
        let saved = try JSONDecoder().decode(StarBackup.Response.self, from: Data(json.utf8))
        XCTAssertEqual(saved.stars.map(\.id), ["a"])
        XCTAssertEqual(saved.stars[0].kind, "question")
        XCTAssertFalse(saved.stars[0].long)
    }

    func testBackupRequestAsksForThisDevice() throws {
        let backup = StarBackup(baseURL: URL(string: "https://milo.example")!, deviceId: "dev 1", accessKey: "k")
        let request = backup.request()
        XCTAssertEqual(request.url?.absoluteString, "https://milo.example/stars?device=dev%201")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer k")
        XCTAssertEqual(request.httpMethod, "GET")
    }

    func testReceiveSavesCountsAndCelebrates() {
        let store = StarStore(defaults: defaults)
        var chimes = 0
        store.onCelebrate = { chimes += 1 }
        store.receive(award("a", stars: 2))
        store.receive(award("b", book: "paris-food"))
        XCTAssertEqual(store.total, 3)
        XCTAssertEqual(store.stars(inBook: "paris"), 2)
        XCTAssertEqual(store.celebration, 2)
        XCTAssertEqual(chimes, 2)
        XCTAssertEqual(store.lastAward?.id, "b")
        XCTAssertEqual(StarStore(defaults: defaults).total, 3, "stars survive a relaunch")
    }

    func testDuplicateStarIsIgnored() {
        let store = StarStore(defaults: defaults)
        store.receive(award("a"))
        store.receive(award("a"))
        XCTAssertEqual(store.total, 1)
        XCTAssertEqual(store.celebration, 1)
    }

    func testMergeRestoresWithoutDuplicatesOrCelebration() {
        let store = StarStore(defaults: defaults)
        store.receive(award("a", at: "2026-01-02T10:00:00"))
        let added = store.merge([award("a"), award("b", at: "2026-01-01T09:00:00"), award("zero", stars: 0)])
        XCTAssertEqual(added, 1)
        XCTAssertEqual(store.total, 2)
        XCTAssertEqual(store.awards.map(\.id), ["b", "a"])
        XCTAssertEqual(store.celebration, 1)
    }

    func testStampsOnlyForCitiesWithQuestions() throws {
        let store = StarStore(defaults: defaults)
        store.merge([award("a", book: "paris", city: "Paris")])
        let stamps = store.stamps(shelves: try shelves())
        XCTAssertEqual(stamps.filter(\.stamped).map(\.city), ["Paris"])
        XCTAssertTrue(stamps.contains { $0.city == "London" && !$0.stamped })
    }

    func testTravelShelfGetsNoCityStamp() throws {
        let store = StarStore(defaults: defaults)
        store.merge([award("t", book: "travel-flight", city: "Travel")])
        let stamps = store.stamps(shelves: try shelves())
        XCTAssertFalse(stamps.contains { $0.city == "Travel" })
        XCTAssertEqual(stamps.map(\.city), ["Paris", "London", "Barcelona", "Lake Tahoe", "Hawaii", "Chengdu", "Longyan", "Beijing", "Shanghai"])
        XCTAssertEqual(store.total, 1)
    }

    func testCuriousCatAndBigTalkerBadges() throws {
        let store = StarStore(defaults: defaults)
        store.merge((0..<10).map { award("q\($0)", long: $0 < 2) })
        var badges = Dictionary(uniqueKeysWithValues: store.badges(shelves: try shelves()).map { ($0.id, $0) })
        XCTAssertTrue(badges["curious-cat"]!.earned)
        XCTAssertFalse(badges["big-talker"]!.earned)
        XCTAssertEqual(badges["big-talker"]!.progress, 2)
        XCTAssertEqual(badges["big-talker"]!.goal, 3)
        store.merge([award("long", long: true)])
        badges = Dictionary(uniqueKeysWithValues: store.badges(shelves: try shelves()).map { ($0.id, $0) })
        XCTAssertTrue(badges["big-talker"]!.earned)
    }

    func testParisExpertNeedsEveryParisBook() throws {
        let shelves = try shelves()
        let paris = try XCTUnwrap(shelves.first { $0.city == "Paris" })
        let store = StarStore(defaults: defaults)
        store.merge(paris.books.dropLast().map { award("q-\($0.id)", book: $0.id) })
        var expert = try XCTUnwrap(store.badges(shelves: shelves).first { $0.id == "expert-Paris" })
        XCTAssertEqual(expert.title, "Paris Expert")
        XCTAssertFalse(expert.earned)
        store.merge([award("last", book: paris.books.last!.id)])
        expert = try XCTUnwrap(store.badges(shelves: shelves).first { $0.id == "expert-Paris" })
        XCTAssertTrue(expert.earned)
        XCTAssertEqual(expert.goal, paris.books.count)
    }

    func testDemoAwardsAddUpToTheAskedTotal() throws {
        let awards = StarStore.demoAwards(total: 23, shelves: try shelves())
        XCTAssertEqual(awards.reduce(0) { $0 + $1.stars }, 23)
        XCTAssertTrue(awards.allSatisfy { $0.stars == 1 }, "one star per award")
        XCTAssertEqual(Set(awards.map(\.id)).count, awards.count)
        let perPage = Dictionary(grouping: awards) { "\($0.book)/\($0.page)" }.mapValues(\.count)
        XCTAssertLessThanOrEqual(perPage.values.max() ?? 0, 5, "at most 5 stars per page")
    }

    func testBookCoverReadsItsStarsAndHearts() {
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 0, lovedCount: 0), "")
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 1, lovedCount: 0), "1 star")
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 7, lovedCount: 1), "7 stars, 1 loved page")
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 0, lovedCount: 3), "3 loved pages")
    }
}
