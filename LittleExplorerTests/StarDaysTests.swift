import XCTest
@testable import LittleExplorer

final class StarDaysTests: XCTestCase {
    private func calendar(_ zone: String) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: zone)!
        calendar.firstWeekday = 1
        return calendar
    }

    private func award(_ id: String, at: String, stars: Int = 1) -> StarAward {
        StarAward(id: id, awardedAt: at, book: "paris", page: "p1", city: "Paris", stars: stars)
    }

    private func date(_ text: String, _ calendar: Calendar) -> Date {
        StarDays.date(from: text, timeZone: calendar.timeZone)!
    }

    func testCountsStarsPerLocalDay() {
        let la = calendar("America/Los_Angeles")
        let days = StarDays(
            awards: [
                award("a", at: "2026-10-18T09:05:00-07:00"),
                award("b", at: "2026-10-18T16:40:00-07:00"),
                award("c", at: "2026-10-17T10:00:00-07:00"),
            ],
            calendar: la
        )
        XCTAssertEqual(days.stars(on: date("2026-10-18T12:00:00-07:00", la)), 2)
        XCTAssertEqual(days.stars(on: date("2026-10-17T12:00:00-07:00", la)), 1)
        XCTAssertEqual(days.stars(on: date("2026-10-16T12:00:00-07:00", la)), 0)
        XCTAssertEqual(days.datedStars, 3)
        XCTAssertEqual(days.undatedStars, 0)
    }

    func testUsesTheDevicesTimeZoneNotTheServers() {
        // 02:30 UTC on the 19th is still the evening of the 18th in California.
        let utcAward = award("late", at: "2026-10-19T02:30:00+00:00")
        let la = StarDays(awards: [utcAward], calendar: calendar("America/Los_Angeles"))
        XCTAssertEqual(la.counts, [StarDays.Day(year: 2026, month: 10, day: 18): 1])
        let tokyo = StarDays(awards: [utcAward], calendar: calendar("Asia/Tokyo"))
        XCTAssertEqual(tokyo.counts, [StarDays.Day(year: 2026, month: 10, day: 19): 1])
    }

    func testReadsAppAndOlderTimeFormats() {
        let la = calendar("America/Los_Angeles")
        XCTAssertNotNil(StarDays.date(from: "2026-10-18T16:05:00Z"))
        XCTAssertNotNil(StarDays.date(from: "2026-10-18T16:05:00.123+00:00"))
        // No offset: read on the device's clock.
        let naive = StarDays(awards: [award("n", at: "2026-01-01T10:00:00")], calendar: la)
        XCTAssertEqual(naive.counts, [StarDays.Day(year: 2026, month: 1, day: 1): 1])
    }

    func testStarsWithoutATimeAreCountedSeparately() {
        let days = StarDays(
            awards: [award("a", at: ""), award("b", at: "not a date"), award("c", at: "2026-10-18T09:00:00Z")],
            calendar: calendar("UTC")
        )
        XCTAssertEqual(days.undatedStars, 2)
        XCTAssertEqual(days.datedStars, 1)
        XCTAssertEqual(days.firstDate, StarDays.date(from: "2026-10-18T09:00:00Z"))
    }

    func testMonthAndRangeTotals() {
        let utc = calendar("UTC")
        let days = StarDays(
            awards: [
                award("sep", at: "2026-09-30T23:59:00Z"),
                award("oct1", at: "2026-10-01T00:00:00Z"),
                award("oct18", at: "2026-10-18T09:00:00Z", stars: 3),
                award("oct24", at: "2026-10-24T09:00:00Z"),
                award("oct25", at: "2026-10-25T09:00:00Z"),
            ],
            calendar: utc
        )
        let oct = date("2026-10-10T00:00:00Z", utc)
        XCTAssertEqual(days.stars(inMonthOf: oct), 6)
        XCTAssertEqual(days.stars(inMonthOf: date("2026-09-10T00:00:00Z", utc)), 1)
        // The 7 days Oct 18 – Oct 24 (Oct 25 is the end, not included).
        XCTAssertEqual(days.stars(from: date("2026-10-18T00:00:00Z", utc), to: date("2026-10-25T00:00:00Z", utc)), 4)
    }

    @MainActor
    func testStoreSpendingStarsOnCoinsDoesNotChangeDays() throws {
        let suite = "StarDaysTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = StarStore(defaults: defaults)
        store.merge([award("a", at: "2026-10-18T09:00:00Z"), award("b", at: "2026-10-18T10:00:00Z")])
        XCTAssertEqual(store.days(calendar: calendar("UTC")).datedStars, 2)
    }

    @MainActor
    func testDemoAwardsSpreadOverRecentDays() throws {
        let la = calendar("America/Los_Angeles")
        let now = date("2026-10-18T17:00:00-07:00", la)
        let shelves = BookLibrary.shelves(try BookLibrary.loadBundled())
        let awards = StarStore.demoAwards(total: 60, shelves: shelves, now: now, calendar: la)
        XCTAssertEqual(awards.count, 60)
        let days = StarDays(awards: awards, calendar: la)
        XCTAssertEqual(days.datedStars, 60)
        XCTAssertEqual(days.stars(on: now), 3)
        XCTAssertGreaterThan(days.counts.count, 14)
        XCTAssertGreaterThan(days.stars(inMonthOf: date("2026-09-20T12:00:00-07:00", la)), 0)
        XCTAssertTrue(awards.allSatisfy { StarDays.date(from: $0.awardedAt)! <= now })
    }

    func testCalendarLaunchArgumentOpensThePassportWithStars() {
        let demo = DemoLaunch.parse(["app", "-demoCalendar"])
        XCTAssertEqual(demo?.showCalendar, true)
        XCTAssertEqual(demo?.openPassport, true)
        XCTAssertEqual(demo?.seedStars, 60)
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoStars", "5", "-demoCalendar"])?.seedStars, 5)
    }
}
