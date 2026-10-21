import Foundation
import Observation

/// One star award from Milo for answering his question (see server/src/little_explorer_agent/stars.py).
/// Counts and labels only; never what he said.
struct StarAward: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let awardedAt: String
    let book: String
    let page: String
    let city: String
    let kind: String
    let long: Bool
    let whyHow: Bool
    let followup: Bool
    let stars: Int
    let words: Int
    let reason: String

    enum CodingKeys: String, CodingKey {
        case id, book, page, city, kind, long, followup, stars, words, reason
        case awardedAt = "awarded_at"
        case whyHow = "why_how"
    }

    init(
        id: String = UUID().uuidString, awardedAt: String = ISO8601DateFormatter().string(from: Date()),
        book: String, page: String, city: String, kind: String = "question", long: Bool = false,
        whyHow: Bool = false, followup: Bool = false, stars: Int, words: Int = 0, reason: String = ""
    ) {
        self.id = id
        self.awardedAt = awardedAt
        self.book = book
        self.page = page
        self.city = city
        self.kind = kind
        self.long = long
        self.whyHow = whyHow
        self.followup = followup
        self.stars = stars
        self.words = words
        self.reason = reason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        stars = try c.decode(Int.self, forKey: .stars)
        book = try c.decodeIfPresent(String.self, forKey: .book) ?? ""
        page = try c.decodeIfPresent(String.self, forKey: .page) ?? ""
        city = try c.decodeIfPresent(String.self, forKey: .city) ?? ""
        awardedAt = try c.decodeIfPresent(String.self, forKey: .awardedAt) ?? ""
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "question"
        long = try c.decodeIfPresent(Bool.self, forKey: .long) ?? false
        whyHow = try c.decodeIfPresent(Bool.self, forKey: .whyHow) ?? false
        followup = try c.decodeIfPresent(Bool.self, forKey: .followup) ?? false
        words = try c.decodeIfPresent(Int.self, forKey: .words) ?? 0
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
    }
}

/// A Passport badge and how close he is to it. Locked badges show `detail`, a hint he can follow;
/// earned ones show `doneText`, what he did to earn it.
struct StarBadge: Identifiable, Equatable {
    let id: String
    let title: String
    let detail: String
    let doneText: String
    let systemImage: String
    /// The badge's circle color once earned (hex, like book cover colors).
    let tint: String
    let progress: Int
    let goal: Int
    /// City Expert badges show that city's flag on the medal.
    var flagBook: Book? = nil

    var earned: Bool { progress >= goal }

    /// What VoiceOver reads for the badge card.
    func accessibilityLabel(isNew: Bool = false) -> String {
        if earned { return "\(title) badge, earned. \(doneText)\(isNew ? " New badge!" : "")" }
        let progress = goal > 1 ? " \(self.progress) of \(goal)." : ""
        return "\(title) badge, not yet. \(detail).\(progress)"
    }
}

/// A Passport stamp: a city he has asked Milo about.
struct PassportStamp: Identifiable, Equatable {
    let city: String
    let flagBook: Book
    let stamped: Bool
    var id: String { city }
}

/// The stars he has earned, saved on this device and backed up on the Milo server by device id
/// (so a reinstall can restore them with ``merge(_:)``).
@MainActor
@Observable
final class StarStore {
    private(set) var awards: [StarAward]
    /// Goes up with every star that arrives live, so views can play the flying star.
    private(set) var celebration = 0
    private(set) var lastAward: StarAward?

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let key: String
    @ObservationIgnored var onCelebrate: (() -> Void)?

    init(defaults: UserDefaults = .standard, key: String = "starAwards.v1") {
        self.defaults = defaults
        self.key = key
        let saved = defaults.data(forKey: key).flatMap { try? JSONDecoder().decode([StarAward].self, from: $0) }
        awards = saved ?? []
        seenBadges = Set(defaults.stringArray(forKey: key + ".seenBadges") ?? [])
    }

    /// Earned badges he has already seen in the Passport; the rest show as NEW there. Local only.
    private(set) var seenBadges: Set<String>

    func markBadgesSeen(_ ids: some Sequence<String>) {
        let next = seenBadges.union(ids)
        guard next != seenBadges else { return }
        seenBadges = next
        defaults.set(next.sorted(), forKey: key + ".seenBadges")
    }

    var total: Int { awards.reduce(0) { $0 + $1.stars } }
    var questionCount: Int { awards.count }
    var longQuestionCount: Int { awards.filter(\.long).count }

    func stars(inBook bookId: String) -> Int {
        awards.filter { $0.book == bookId }.reduce(0) { $0 + $1.stars }
    }

    func hasAsked(inCity city: String) -> Bool { awards.contains { $0.city == city } }
    func hasAsked(inBook bookId: String) -> Bool { awards.contains { $0.book == bookId } }

    /// A star Milo just gave: save it and celebrate.
    func receive(_ award: StarAward) {
        guard add([award]) > 0 else { return }
        lastAward = award
        celebration += 1
        onCelebrate?()
    }

    /// Adds awards it doesn't have yet (by id); returns how many were new.
    @discardableResult
    func merge(_ incoming: [StarAward]) -> Int { add(incoming) }

    private func add(_ incoming: [StarAward]) -> Int {
        var known = Set(awards.map(\.id))
        let fresh = incoming.filter { $0.stars > 0 && known.insert($0.id).inserted }
        guard !fresh.isEmpty else { return 0 }
        awards = (awards + fresh).sorted { $0.awardedAt < $1.awardedAt }
        if let data = try? JSONEncoder().encode(awards) { defaults.set(data, forKey: key) }
        return fresh.count
    }

    /// One stamp per place; the Travel shelf isn't a place, so it gets no stamp.
    func stamps(shelves: [CityShelf]) -> [PassportStamp] {
        shelves.filter { !$0.isTravel }.map { PassportStamp(city: $0.city, flagBook: $0.flagBook, stamped: hasAsked(inCity: $0.city)) }
    }
}

extension StarStore {
    static func live() -> StarStore {
        #if DEBUG
        if DemoLaunch.current != nil {
            let suite = "demo.stars"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            return StarStore(defaults: defaults)
        }
        #endif
        return StarStore()
    }

    /// Screenshot demo: about `total` stars spread over most of the first city's books and one more city,
    /// and over the last few weeks (some days off) so the Passport calendar has something to show.
    static func demoAwards(
        total: Int, shelves: [CityShelf], now: Date = Date(), calendar: Calendar = .current
    ) -> [StarAward] {
        let first = shelves.first.map { Array($0.books.dropLast(2)) } ?? []
        let books = first + shelves.dropFirst().prefix(1).compactMap(\.books.first)
        guard !books.isEmpty else { return [] }
        // Stars per day, going back from today; the pattern repeats for bigger totals.
        let perDay = [3, 0, 2, 5, 1, 0, 0, 4, 2, 3, 0, 6, 1, 2, 0, 0, 3, 1, 4, 2, 0, 7, 2, 0, 1, 3, 0, 2]
        let today = calendar.startOfDay(for: now)
        var slots: [Date] = []
        var back = 0
        while slots.count < total {
            let n = perDay[back % perDay.count]
            let day = calendar.date(byAdding: .day, value: -back, to: today) ?? today
            for k in 0..<n where slots.count < total {
                // Mornings and afternoons, never later than now on today.
                let minutes = 9 * 60 + k * 47
                let time = calendar.date(byAdding: .minute, value: minutes, to: day) ?? day
                slots.append(back == 0 ? min(time, now) : time)
            }
            back += 1
        }
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = calendar.timeZone
        return slots.reversed().enumerated().map { i, date in
            let book = books[i % books.count]
            return StarAward(
                id: "demo-\(i)", awardedAt: formatter.string(from: date), book: book.id,
                page: book.pages[i % book.pages.count].id, city: book.city, long: i % 2 == 0,
                whyHow: i % 3 == 0, stars: 1, words: i % 2 == 0 ? 9 : 5  // one star per award
            )
        }
    }

    /// Screenshot demo for the badges: one star early in the morning, so Early Bird shows as earned.
    static func demoBadgeExtras(shelves: [CityShelf], now: Date = Date(), calendar: Calendar = .current) -> [StarAward] {
        guard let book = shelves.first?.books.first, let page = book.pages.first else { return [] }
        let day = calendar.date(byAdding: .day, value: -2, to: calendar.startOfDay(for: now)) ?? now
        let morning = calendar.date(byAdding: .minute, value: 7 * 60 + 40, to: day) ?? day
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = calendar.timeZone
        return [
            StarAward(
                id: "demo-early", awardedAt: formatter.string(from: morning), book: book.id, page: page.id,
                city: book.city, whyHow: true, stars: 1, words: 6
            ),
        ]
    }

    /// Pulls this device's stars back from the Milo server (after a reinstall) and keeps any it lacks.
    func restore(from backup: StarBackup) async {
        guard let saved = try? await backup.fetch() else { return }
        merge(saved)
    }
}

/// Reads the server's copy of this device's stars (`GET /stars?device=<id>`). The agent writes it.
struct StarBackup {
    var baseURL: URL
    var deviceId: String
    var accessKey: String? = VoiceSettings.accessKey
    var session: URLSession = .shared

    struct Response: Decodable {
        let stars: [StarAward]
    }

    func request() -> URLRequest {
        var components = URLComponents(url: baseURL.appendingPathComponent("stars"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "device", value: deviceId)]
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 20
        if let accessKey { request.setValue("Bearer \(accessKey)", forHTTPHeaderField: "Authorization") }
        return request
    }

    func fetch() async throws -> [StarAward] {
        let (data, response) = try await session.data(for: request())
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw VoiceError.tokenServer(status: status) }
        return try JSONDecoder().decode(Response.self, from: data).stars
    }
}
