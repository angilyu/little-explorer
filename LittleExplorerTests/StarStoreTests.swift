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

    private func award(
        _ id: String, book: String = "paris", city: String = "Paris", stars: Int = 1, long: Bool = false,
        whyHow: Bool = false, at: String = "2026-01-01T10:00:00"
    ) -> StarAward {
        StarAward(id: id, awardedAt: at, book: book, page: "p1", city: city, long: long, whyHow: whyHow, stars: stars)
    }

    private var pacific: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        return calendar
    }

    private func badges(_ store: StarStore) throws -> [String: StarBadge] {
        let list = store.badges(shelves: try shelves(), calendar: pacific)
        return Dictionary(uniqueKeysWithValues: list.map { ($0.id, $0) })
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
        XCTAssertEqual(stamps.map(\.city), ["Paris", "London", "Barcelona", "Lake Tahoe", "Hawaii", "Chengdu", "Longyan", "Beijing", "Shanghai", "Israel"])
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

    func testNewBadgesStartLockedWithAHint() throws {
        let all = try badges(StarStore(defaults: defaults))
        for id in ["why-wizard", "star-shower", "come-back-kid", "early-bird", "night-owl", "bookworm", "globe-trotter", "super-star"] {
            let badge = try XCTUnwrap(all[id], id)
            XCTAssertFalse(badge.earned, id)
            XCTAssertFalse(badge.detail.isEmpty, id)
            XCTAssertTrue(badge.doneText.hasSuffix("!"), id)
        }
        XCTAssertEqual(all["night-owl"]?.detail, "Earn a star at bedtime")
    }

    func testWhyWizardCountsBecauseAnswers() throws {
        let store = StarStore(defaults: defaults)
        store.merge((0..<2).map { award("w\($0)", whyHow: true) } + [award("plain")])
        XCTAssertEqual(try badges(store)["why-wizard"]?.progress, 2)
        store.merge([award("w2", whyHow: true)])
        let wizard = try XCTUnwrap(try badges(store)["why-wizard"])
        XCTAssertTrue(wizard.earned)
        XCTAssertEqual(wizard.doneText, "You answered with \u{201C}because\u{201D} 3 times!")
    }

    func testStarShowerNeedsFiveStarsOnOneDay() throws {
        let store = StarStore(defaults: defaults)
        store.merge((0..<4).map { award("a\($0)", at: "2026-03-01T1\($0):00:00-08:00") })
        store.merge((0..<4).map { award("b\($0)", at: "2026-03-02T1\($0):00:00-08:00") })
        XCTAssertEqual(try badges(store)["star-shower"]?.progress, 4)
        store.merge([award("a4", at: "2026-03-01T18:00:00-08:00")])
        XCTAssertTrue(try XCTUnwrap(try badges(store)["star-shower"]).earned)
    }

    func testComeBackKidCountsDifferentDaysNotAStreak() throws {
        let store = StarStore(defaults: defaults)
        let days = ["2026-03-01", "2026-03-04", "2026-03-04", "2026-03-10", "2026-04-02"]
        store.merge(days.enumerated().map { award("d\($0.offset)", at: "\($0.element)T10:00:00-08:00") })
        XCTAssertEqual(try badges(store)["come-back-kid"]?.progress, 4)
        store.merge([award("late", at: "2026-06-30T10:00:00-07:00")])
        XCTAssertTrue(try XCTUnwrap(try badges(store)["come-back-kid"]).earned, "gaps between days never matter")
    }

    func testEarlyBirdAndNightOwlUseHisLocalTime() throws {
        let store = StarStore(defaults: defaults)
        store.merge([award("noon", at: "2026-03-01T12:00:00-08:00"), award("evening", at: "2026-03-01T18:59:00-08:00")])
        var all = try badges(store)
        XCTAssertFalse(all["early-bird"]!.earned)
        XCTAssertFalse(all["night-owl"]!.earned)
        // 16:30 UTC is 8:30 am in California; 03:15 UTC the next day is 7:15 pm.
        store.merge([award("early", at: "2026-03-02T16:30:00Z"), award("bedtime", at: "2026-03-03T03:15:00Z")])
        all = try badges(store)
        XCTAssertTrue(all["early-bird"]!.earned)
        XCTAssertEqual(all["early-bird"]!.doneText, "A star before 9 am!")
        XCTAssertTrue(all["night-owl"]!.earned)
    }

    func testBookwormGlobeTrotterAndSuperStar() throws {
        let shelves = try shelves()
        let books = shelves.flatMap(\.books)
        let store = StarStore(defaults: defaults)
        store.merge(books.prefix(9).map { award("b-\($0.id)", book: $0.id, city: $0.city) })
        var all = try badges(store)
        XCTAssertEqual(all["bookworm"]?.progress, 9)
        XCTAssertEqual(all["bookworm"]?.goal, 10)
        XCTAssertEqual(all["globe-trotter"]?.progress, Set(books.prefix(9).map(\.city)).count)
        store.merge([award("b10", book: books[9].id, city: books[9].city)])
        store.merge(["London", "Barcelona"].map { award("c-\($0)", book: "x", city: $0) })
        all = try badges(store)
        XCTAssertTrue(all["bookworm"]!.earned)
        XCTAssertTrue(all["globe-trotter"]!.earned)
        XCTAssertEqual(all["globe-trotter"]!.doneText, "Stamps from 3 cities!")
        XCTAssertEqual(all["super-star"]?.progress, store.total)
        store.merge((0..<50).map { award("s\($0)") })
        XCTAssertTrue(try XCTUnwrap(try badges(store)["super-star"]).earned)
        XCTAssertEqual(try badges(store)["super-star"]?.progress, 50, "progress stops at the goal")
    }

    func testExpertBadgesFollowTheShelves() throws {
        var shelves = try shelves()
        let extra = shelves[0].books.prefix(2).map { $0 }
        shelves.append(CityShelf(city: "Tel Aviv", books: extra))
        let store = StarStore(defaults: defaults)
        let expert = try XCTUnwrap(store.badges(shelves: shelves).first { $0.id == "expert-Tel Aviv" })
        XCTAssertEqual(expert.title, "Tel Aviv Expert")
        XCTAssertEqual(expert.doneText, "A star in every Tel Aviv book!")
        XCTAssertEqual(expert.flagBook?.id, extra[0].id)
    }

    func testBadgeVoiceOverLabelsSayWhatHeDid() {
        let wizard = StarBadge(
            id: "why-wizard", title: "Why Wizard", detail: "Answer with because 3 times",
            doneText: "You answered with because 3 times!", systemImage: "wand.and.stars", tint: "#8E44AD", progress: 3, goal: 3
        )
        XCTAssertEqual(wizard.accessibilityLabel(), "Why Wizard badge, earned. You answered with because 3 times!")
        XCTAssertEqual(wizard.accessibilityLabel(isNew: true), "Why Wizard badge, earned. You answered with because 3 times! New badge!")
        let owl = StarBadge(
            id: "night-owl", title: "Night Owl", detail: "Earn a star at bedtime", doneText: "A star at bedtime!",
            systemImage: "moon.stars.fill", tint: "#2B2D6E", progress: 0, goal: 1
        )
        XCTAssertEqual(owl.accessibilityLabel(), "Night Owl badge, not yet. Earn a star at bedtime.")
        let worm = StarBadge(
            id: "bookworm", title: "Bookworm", detail: "Earn stars in 10 different books", doneText: "Stars in 10 books!",
            systemImage: "books.vertical.fill", tint: "#2E9E44", progress: 9, goal: 10
        )
        XCTAssertEqual(worm.accessibilityLabel(), "Bookworm badge, not yet. Earn stars in 10 different books. 9 of 10.")
    }

    func testNewBadgesShowUntilSeen() throws {
        let store = StarStore(defaults: defaults)
        store.merge((0..<10).map { award("q\($0)") })
        let list = store.badges(shelves: try shelves())
        XCTAssertEqual(store.newBadgeIds(in: list), ["curious-cat", "star-shower"])
        store.markBadgesSeen(list.filter(\.earned).map(\.id))
        XCTAssertTrue(store.newBadgeIds(in: list).isEmpty)
        XCTAssertTrue(StarStore(defaults: defaults).newBadgeIds(in: list).isEmpty, "seen badges survive a relaunch")
        store.merge((0..<3).map { award("w\($0)", whyHow: true) })
        XCTAssertEqual(store.newBadgeIds(in: store.badges(shelves: try shelves())), ["why-wizard"])
    }

    func testRemovedBigHeartBadgeIsGoneAndItsSeenMarkIsHarmless() throws {
        let store = StarStore(defaults: defaults)
        store.markBadgesSeen(["big-heart"])
        store.merge((0..<10).map { award("q\($0)") })
        let list = store.badges(shelves: try shelves())
        XCTAssertFalse(list.contains { $0.id == "big-heart" })
        XCTAssertEqual(store.newBadgeIds(in: list), ["curious-cat", "star-shower"])
        XCTAssertEqual(list.filter(\.earned).count, 2)
    }

    func testDemoBadgeExtrasGiveAnEarlyMorningStar() throws {
        let extras = StarStore.demoBadgeExtras(shelves: try shelves(), calendar: pacific)
        XCTAssertEqual(extras.count, 1)
        let store = StarStore(defaults: defaults)
        store.merge(extras)
        XCTAssertTrue(try XCTUnwrap(try badges(store)["early-bird"]).earned)
    }

    func testDemoAwardsAddUpToTheAskedTotal() throws {
        let awards = StarStore.demoAwards(total: 23, shelves: try shelves())
        XCTAssertEqual(awards.reduce(0) { $0 + $1.stars }, 23)
        XCTAssertTrue(awards.allSatisfy { $0.stars == 1 }, "one star per award")
        XCTAssertEqual(Set(awards.map(\.id)).count, awards.count)
        let perPage = Dictionary(grouping: awards) { "\($0.book)/\($0.page)" }.mapValues(\.count)
        XCTAssertLessThanOrEqual(perPage.values.max() ?? 0, 5, "at most 5 stars per page")
    }

    func testBookCoverReadsItsStars() {
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 0), "")
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 1), "1 star")
        XCTAssertEqual(BookCoverView.accessibilityValue(starCount: 7), "7 stars")
    }
}
