import Foundation
import UIKit

/// What a badge counts. The app works each one out on the device (see `StarStore.badges`); the catalog
/// only picks a metric and a goal, so a new kind of metric is the one badge change that needs an app
/// update. Keep in step with `METRICS` in server/src/little_explorer_agent/badges.py.
enum BadgeMetric: String, CaseIterable {
    case stars
    case starAnswers = "star_answers"
    case longAnswers = "long_answers"
    case becauseAnswers = "because_answers"
    case starsInADay = "stars_in_a_day"
    case starDays = "star_days"
    case morningStars = "morning_stars"
    case bedtimeStars = "bedtime_stars"
    case booksStarred = "books_starred"
    case citiesStarred = "cities_starred"
    case cityBooksStarred = "city_books_starred"
    case pagesRead = "pages_read"
    case pagesListened = "pages_listened"
    case miloPages = "milo_pages"
    case booksFinished = "books_finished"
    case readingDays = "reading_days"
    case readingStreak = "reading_streak"
    case citiesRead = "cities_read"
    case countriesRead = "countries_read"

    /// Counted for one city at a time: the badge repeats for every city with several books.
    var perCity: Bool { self == .cityBooksStarred }

    /// Has a most-there-can-be from the shelves (so the goal can be "all", and a bigger goal is lowered to it).
    var capped: Bool {
        switch self {
        case .cityBooksStarred, .booksStarred, .citiesStarred, .booksFinished, .citiesRead, .countriesRead: true
        default: false
        }
    }
}

/// A group of badges in the Passport, e.g. Reading or Places.
struct BadgeCategory: Equatable, Identifiable {
    let id: String
    let title: String
    let icon: String
    let tint: String
}

/// One badge as the catalog describes it. `{goal}` and `{city}` in its texts (and `{city}` in a per-city
/// badge's id) are filled in when the badges are worked out.
struct BadgeRule: Equatable, Identifiable {
    enum Goal: Equatable {
        case count(Int)
        /// Every one there is, e.g. every book of a city.
        case all
    }

    let id: String
    let category: String
    let title: String
    let icon: String
    let tint: String
    let metric: BadgeMetric
    let goal: Goal
    let hint: String
    let done: String
}

/// The Passport badges: `badges.json`, bundled in the app and updated from the Milo server with the
/// books (see ContentStore), so names, hints, goals, categories, icons and order change without an app
/// update. Decoding is forgiving: an entry the app can't use (an unknown metric or category, a missing
/// text, a bad goal) is skipped, an unknown icon or color gets a plain one, and a file it can't read at
/// all falls back to the bundled copy.
struct BadgeCatalog: Equatable {
    static let fileName = "badges.json"
    static let format = 1
    static let fallbackIcon = "star.fill"
    static let fallbackTint = "#F4B400"

    var categories: [BadgeCategory]
    var badges: [BadgeRule]

    static let empty = BadgeCatalog(categories: [], badges: [])

    /// The copy inside the app.
    static let bundled: BadgeCatalog = load(.bundleOnly(.main))

    /// The downloaded copy if there is a usable one, else the bundled one.
    static func load(_ files: ContentFiles) -> BadgeCatalog {
        if let catalog = files.data(fileName).flatMap(decode) { return catalog }
        return files.bundledURL(fileName).flatMap { try? Data(contentsOf: $0) }.flatMap(decode) ?? .empty
    }

    /// The usable part of a `badges.json`, or nil if there is none (not JSON, another format, no badges).
    static func decode(_ data: Data) -> BadgeCatalog? {
        guard
            let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
            root["format"] as? Int == format,
            let rawCategories = root["categories"] as? [[String: Any]],
            let rawBadges = root["badges"] as? [[String: Any]]
        else { return nil }

        var categories: [BadgeCategory] = []
        for raw in rawCategories {
            guard let id = text(raw["id"]), let title = text(raw["title"]),
                  !categories.contains(where: { $0.id == id }) else { continue }
            categories.append(BadgeCategory(id: id, title: title, icon: icon(raw["icon"]), tint: tint(raw["tint"]) ?? fallbackTint))
        }
        var badges: [BadgeRule] = []
        for raw in rawBadges {
            guard
                let id = text(raw["id"]), !badges.contains(where: { $0.id == id }),
                let category = categories.first(where: { $0.id == text(raw["category"]) }),
                let metric = text(raw["metric"]).flatMap(BadgeMetric.init(rawValue:)),
                let goal = goal(raw["goal"]),
                let title = text(raw["title"]), let hint = text(raw["hint"]), let done = text(raw["done"])
            else { continue }
            let eachCity = text(raw["each"]) == "city"
            guard eachCity == metric.perCity, eachCity == id.contains("{city}"), raw["each"] == nil || eachCity,
                  goal != .all || metric.capped else { continue }
            badges.append(BadgeRule(
                id: id, category: category.id, title: title, icon: icon(raw["icon"]),
                tint: tint(raw["tint"]) ?? category.tint, metric: metric, goal: goal, hint: hint, done: done
            ))
        }
        let used = Set(badges.map(\.category))
        categories.removeAll { !used.contains($0.id) }
        return badges.isEmpty ? nil : BadgeCatalog(categories: categories, badges: badges)
    }

    private static func text(_ value: Any?) -> String? {
        guard let string = value as? String else { return nil }
        let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func goal(_ value: Any?) -> BadgeRule.Goal? {
        if text(value) == "all" { return .all }
        // JSON booleans also come through as NSNumber; they aren't goals.
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let count = number.intValue
        return count >= 1 && Double(count) == number.doubleValue ? .count(count) : nil
    }

    /// An SF Symbol this device has, else a plain star (a newer symbol on an older iOS).
    private static func icon(_ value: Any?) -> String {
        guard let name = text(value), UIImage(systemName: name) != nil else { return fallbackIcon }
        return name
    }

    private static func tint(_ value: Any?) -> String? {
        guard let hex = text(value), hex.count == 7, hex.first == "#",
              hex.dropFirst().allSatisfy(\.isHexDigit) else { return nil }
        return hex
    }
}
