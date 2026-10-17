import Foundation
import Observation

/// One star award from Milo for a curious question (see server/src/little_explorer_agent/stars.py).
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

/// A Passport badge and how close he is to it.
struct StarBadge: Identifiable, Equatable {
    let id: String
    let title: String
    let detail: String
    let systemImage: String
    let progress: Int
    let goal: Int

    var earned: Bool { progress >= goal }
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

    static let curiousCatGoal = 10
    static let bigTalkerGoal = 5

    init(defaults: UserDefaults = .standard, key: String = "starAwards.v1") {
        self.defaults = defaults
        self.key = key
        let saved = defaults.data(forKey: key).flatMap { try? JSONDecoder().decode([StarAward].self, from: $0) }
        awards = saved ?? []
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

    func stamps(shelves: [CityShelf]) -> [PassportStamp] {
        shelves.map { PassportStamp(city: $0.city, flagBook: $0.flagBook, stamped: hasAsked(inCity: $0.city)) }
    }

    /// Curious Cat (10 questions), Big Talker (5 long questions), and an Expert badge for each city
    /// with several books (asked in every one of them), e.g. Paris Expert.
    func badges(shelves: [CityShelf]) -> [StarBadge] {
        var badges = [
            StarBadge(
                id: "curious-cat", title: "Curious Cat", detail: "Ask \(Self.curiousCatGoal) questions",
                systemImage: "cat.fill", progress: min(questionCount, Self.curiousCatGoal), goal: Self.curiousCatGoal
            ),
            StarBadge(
                id: "big-talker", title: "Big Talker", detail: "Ask \(Self.bigTalkerGoal) long questions",
                systemImage: "bubble.left.and.bubble.right.fill",
                progress: min(longQuestionCount, Self.bigTalkerGoal), goal: Self.bigTalkerGoal
            ),
        ]
        for shelf in shelves where shelf.books.count > 1 {
            let asked = shelf.books.filter { hasAsked(inBook: $0.id) }.count
            badges.append(
                StarBadge(
                    id: "expert-\(shelf.city)", title: "\(shelf.city) Expert",
                    detail: "Ask in every \(shelf.city) book", systemImage: "medal.fill",
                    progress: asked, goal: shelf.books.count
                )
            )
        }
        return badges
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

    /// Screenshot demo: about `total` stars spread over most of the first city's books and one more city.
    static func demoAwards(total: Int, shelves: [CityShelf]) -> [StarAward] {
        let first = shelves.first.map { Array($0.books.dropLast(2)) } ?? []
        let books = first + shelves.dropFirst().prefix(1).compactMap(\.books.first)
        guard !books.isEmpty else { return [] }
        var awards: [StarAward] = []
        var sum = 0
        var i = 0
        while sum < total {
            let book = books[i % books.count]
            let stars = min(i % 3 == 0 ? 2 : 1, total - sum)
            let date = Date(timeIntervalSince1970: 1_750_000_000 + Double(i) * 600)
            awards.append(
                StarAward(
                    id: "demo-\(i)", awardedAt: ISO8601DateFormatter().string(from: date), book: book.id,
                    page: book.pages[i % book.pages.count].id, city: book.city, long: i % 2 == 0,
                    whyHow: i % 3 == 0, stars: stars, words: i % 2 == 0 ? 9 : 5
                )
            )
            sum += stars
            i += 1
        }
        return awards
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
