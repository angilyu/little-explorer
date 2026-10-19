import Foundation

/// How many stars he earned on each day, in the device's time zone, for the Passport calendar.
/// Counts awards as Milo gave them, so turning stars into coins never takes any off a day.
struct StarDays: Equatable {
    struct Day: Hashable, Comparable {
        let year: Int
        let month: Int
        let day: Int

        static func < (a: Day, b: Day) -> Bool { (a.year, a.month, a.day) < (b.year, b.month, b.day) }
    }

    let calendar: Calendar
    private(set) var counts: [Day: Int] = [:]
    /// Stars whose award has no readable time (none expected, but an old or odd backup could have one).
    private(set) var undatedStars = 0
    private(set) var firstDate: Date?

    init(awards: [StarAward], calendar: Calendar = .current) {
        self.calendar = calendar
        for award in awards where award.stars > 0 {
            guard let date = Self.date(from: award.awardedAt, timeZone: calendar.timeZone) else {
                undatedStars += award.stars
                continue
            }
            counts[day(of: date), default: 0] += award.stars
            if firstDate.map({ date < $0 }) ?? true { firstDate = date }
        }
    }

    func day(of date: Date) -> Day {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return Day(year: c.year ?? 0, month: c.month ?? 0, day: c.day ?? 0)
    }

    func stars(on date: Date) -> Int { counts[day(of: date)] ?? 0 }

    /// Stars from `start` up to (not including) `end`.
    func stars(from start: Date, to end: Date) -> Int {
        let first = day(of: start)
        let last = day(of: end)
        return counts.reduce(0) { $1.key >= first && $1.key < last ? $0 + $1.value : $0 }
    }

    func stars(inMonthOf date: Date) -> Int {
        guard let month = calendar.dateInterval(of: .month, for: date) else { return 0 }
        return stars(from: month.start, to: month.end)
    }

    var datedStars: Int { counts.values.reduce(0, +) }

    /// The server sends ISO 8601 with an offset (`2026-10-18T09:05:00-07:00`); the app writes `...Z`.
    /// A time without an offset is read in `timeZone`.
    static func date(from text: String, timeZone: TimeZone = .current) -> Date? {
        let text = text.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }
        for options: ISO8601DateFormatter.Options in [
            [.withInternetDateTime], [.withInternetDateTime, .withFractionalSeconds],
        ] {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = options
            if let date = formatter.date(from: text) { return date }
        }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.timeZone = timeZone
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd"] {
            local.dateFormat = format
            if let date = local.date(from: text) { return date }
        }
        return nil
    }
}

extension StarStore {
    func days(calendar: Calendar = .current) -> StarDays { StarDays(awards: awards, calendar: calendar) }
}
