import Foundation

/// The Passport badges, worked out from what the app already keeps: his stars (with their kind, time
/// and book), the signed-in account's reading progress, and the cities and books on the shelves. Which
/// badges there are (names, hints, goals, icons, categories and order) comes from the badge catalog
/// (`badges.json`, see ``BadgeCatalog``); each one counts a ``BadgeMetric``. Nothing here can be lost
/// again: goals count things he has done (a streak is his best run ever), and a badge he has seen
/// earned stays earned even if its goal goes up later.
extension StarStore {
    /// A star before this hour is a morning (Early Bird) star; one at or after `bedtimeHour` is a bedtime
    /// (Night Owl) star.
    static let morningHour = 9
    static let bedtimeHour = 19

    var whyHowCount: Int { awards.filter(\.whyHow).count }

    /// Every badge in Passport order: the catalog's categories, and its badges in order within each.
    /// A per-city badge (e.g. Paris Expert) repeats for every city with several books, so new cities and
    /// books just work.
    func badges(
        shelves: [CityShelf], calendar: Calendar = .current, catalog: BadgeCatalog = .bundled,
        reading: ReadingProgress = ReadingProgress()
    ) -> [StarBadge] {
        let facts = BadgeFacts(store: self, shelves: shelves, calendar: calendar, reading: reading)

        func make(_ rule: BadgeRule, id: String, city: String?, progress: Int, cap: Int?) -> StarBadge? {
            let goal: Int
            switch rule.goal {
            case let .count(count): goal = max(min(count, cap ?? count), 1)
            case .all:
                guard let cap else { return nil }
                goal = max(cap, 1)
            }
            func fill(_ text: String) -> String {
                text.replacingOccurrences(of: "{goal}", with: "\(goal)").replacingOccurrences(of: "{city}", with: city ?? "")
            }
            return StarBadge(
                id: id, title: fill(rule.title), detail: fill(rule.hint), doneText: fill(rule.done),
                systemImage: rule.icon, tint: rule.tint, progress: min(progress, goal), goal: goal,
                category: rule.category, kept: seenBadges.contains(id)
            )
        }

        var badges: [StarBadge] = []
        for category in catalog.categories {
            for rule in catalog.badges where rule.category == category.id {
                if rule.metric.perCity {
                    for shelf in shelves where shelf.books.count > 1 {
                        let id = rule.id.replacingOccurrences(of: "{city}", with: shelf.city)
                        let count = facts.perCity(rule.metric, shelf)
                        guard var badge = make(rule, id: id, city: shelf.city, progress: count, cap: shelf.books.count) else { continue }
                        badge.flagBook = shelf.flagBook
                        badges.append(badge)
                    }
                } else if let badge = make(rule, id: rule.id, city: nil, progress: facts.value(rule.metric), cap: facts.cap(rule.metric)) {
                    badges.append(badge)
                }
            }
        }
        return badges
    }

    /// Earned badges he hasn't seen in the Passport yet.
    func newBadgeIds(in badges: [StarBadge]) -> Set<String> {
        Set(badges.filter(\.earned).map(\.id)).subtracting(seenBadges)
    }
}

/// The numbers the badge metrics count, worked out once per list of badges.
@MainActor
private struct BadgeFacts {
    let store: StarStore
    let shelves: [CityShelf]
    let reading: ReadingProgress
    let starDays: StarDays
    let hours: [Int]
    let places: [CityShelf]

    init(store: StarStore, shelves: [CityShelf], calendar: Calendar, reading: ReadingProgress) {
        self.store = store
        self.shelves = shelves
        self.reading = reading
        starDays = StarDays(awards: store.awards, calendar: calendar)
        hours = store.awards.compactMap { award in
            StarDays.date(from: award.awardedAt, timeZone: calendar.timeZone).map { calendar.component(.hour, from: $0) }
        }
        places = shelves.filter { !$0.isTravel }
    }

    private var bookCount: Int { shelves.reduce(0) { $0 + $1.books.count } }

    func value(_ metric: BadgeMetric) -> Int {
        switch metric {
        case .stars: store.total
        case .starAnswers: store.questionCount
        case .longAnswers: store.longQuestionCount
        case .becauseAnswers: store.whyHowCount
        case .starsInADay: starDays.counts.values.max() ?? 0
        case .starDays: starDays.counts.count
        case .morningStars: hours.filter { $0 < StarStore.morningHour }.count
        case .bedtimeStars: hours.filter { $0 >= StarStore.bedtimeHour }.count
        case .booksStarred: Set(store.awards.map(\.book).filter { !$0.isEmpty }).count
        case .citiesStarred: store.stamps(shelves: shelves).filter(\.stamped).count
        case .cityBooksStarred: 0
        case .pagesRead: reading.pages.count
        case .pagesListened: reading.listened.count
        case .miloPages: reading.miloPages.count
        case .booksFinished: shelves.flatMap(\.books).filter(reading.finished).count
        case .readingDays: reading.days.count
        case .readingStreak: reading.longestStreak
        case .citiesRead:
            places.filter { shelf in shelf.books.contains { reading.booksRead.contains($0.id) } }.count
        case .countriesRead:
            Set(places.flatMap(\.books).filter { reading.booksRead.contains($0.id) }.map(\.country)).count
        }
    }

    /// The most there can be with these shelves (a goal is lowered to it), or nil if there's no limit.
    func cap(_ metric: BadgeMetric) -> Int? {
        switch metric {
        case .booksStarred, .booksFinished: bookCount
        case .citiesStarred, .citiesRead: places.count
        case .countriesRead: Set(places.flatMap(\.books).map(\.country)).count
        default: nil
        }
    }

    func perCity(_ metric: BadgeMetric, _ shelf: CityShelf) -> Int {
        switch metric {
        case .cityBooksStarred: shelf.books.filter { store.hasAsked(inBook: $0.id) }.count
        default: 0
        }
    }
}
