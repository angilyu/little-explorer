import Foundation

/// The Passport badges, worked out from what the app already keeps: his stars (with their kind, time
/// and book) and the cities and books on the shelves. Nothing here can be lost again: every goal counts
/// things he has done, never a streak.
extension StarStore {
    static let curiousCatGoal = 10
    /// Every answer of 5+ words earns a star (up to 5 per page); 8+ words also counts as long.
    static let bigTalkerGoal = 3
    static let whyWizardGoal = 3
    static let starShowerGoal = 5
    static let comeBackGoal = 5
    static let bookwormGoal = 10
    static let globeTrotterGoal = 3
    static let superStarGoal = 50
    /// A star before this hour is an Early Bird star; one at or after `nightOwlHour` is a Night Owl star.
    static let earlyBirdHour = 9
    static let nightOwlHour = 19

    var whyHowCount: Int { awards.filter(\.whyHow).count }

    /// Every badge in Passport order: the fixed ones, then an Expert badge for each city with several
    /// books (a star in every one of them), e.g. Paris Expert. New cities and books just work.
    func badges(shelves: [CityShelf], calendar: Calendar = .current) -> [StarBadge] {
        let days = StarDays(awards: awards, calendar: calendar)
        let hours = awards.compactMap { award in
            StarDays.date(from: award.awardedAt, timeZone: calendar.timeZone).map { calendar.component(.hour, from: $0) }
        }
        let bookCount = shelves.reduce(0) { $0 + $1.books.count }
        let starredBooks = Set(awards.map(\.book).filter { !$0.isEmpty }).count
        let places = stamps(shelves: shelves)

        func badge(
            _ id: String, _ title: String, _ symbol: String, _ tint: String, hint: String, done: String,
            progress: Int, goal: Int
        ) -> StarBadge {
            let goal = max(goal, 1)
            return StarBadge(id: id, title: title, detail: hint, doneText: done, systemImage: symbol, tint: tint, progress: min(progress, goal), goal: goal)
        }

        let bookwormGoal = min(Self.bookwormGoal, bookCount)
        let globeGoal = min(Self.globeTrotterGoal, places.count)
        var badges = [
            badge(
                "curious-cat", "Curious Cat", "cat.fill", "#F28C28", hint: "Earn \(Self.curiousCatGoal) stars",
                done: "You earned \(Self.curiousCatGoal) stars!",
                progress: questionCount, goal: Self.curiousCatGoal
            ),
            badge(
                "big-talker", "Big Talker", "bubble.left.and.bubble.right.fill", "#3A86FF", hint: "Give \(Self.bigTalkerGoal) long answers",
                done: "\(Self.bigTalkerGoal) long answers!",
                progress: longQuestionCount, goal: Self.bigTalkerGoal
            ),
            badge(
                "why-wizard", "Why Wizard", "wand.and.stars", "#8E44AD",
                hint: "Answer with \u{201C}because\u{201D} \(Self.whyWizardGoal) times",
                done: "You answered with \u{201C}because\u{201D} \(Self.whyWizardGoal) times!",
                progress: whyHowCount, goal: Self.whyWizardGoal
            ),
            badge(
                "star-shower", "Star Shower", "sparkles", "#4361EE", hint: "Earn \(Self.starShowerGoal) stars in one day",
                done: "\(Self.starShowerGoal) stars in one day!",
                progress: days.counts.values.max() ?? 0, goal: Self.starShowerGoal
            ),
            badge(
                "come-back-kid", "Come-Back Kid", "hand.wave.fill", "#14A098", hint: "Earn stars on \(Self.comeBackGoal) different days",
                done: "Stars on \(Self.comeBackGoal) different days!",
                progress: days.counts.count, goal: Self.comeBackGoal
            ),
            badge(
                "early-bird", "Early Bird", "bird.fill", "#F4B400", hint: "Earn a star early in the morning",
                done: "A star before \(Self.earlyBirdHour) am!",
                progress: hours.contains { $0 < Self.earlyBirdHour } ? 1 : 0, goal: 1
            ),
            badge(
                "night-owl", "Night Owl", "moon.stars.fill", "#2B2D6E", hint: "Earn a star at bedtime",
                done: "A star at bedtime!",
                progress: hours.contains { $0 >= Self.nightOwlHour } ? 1 : 0, goal: 1
            ),
            badge(
                "bookworm", "Bookworm", "books.vertical.fill", "#2E9E44", hint: "Earn stars in \(bookwormGoal) different books",
                done: "Stars in \(bookwormGoal) books!",
                progress: starredBooks, goal: bookwormGoal
            ),
            badge(
                "globe-trotter", "Globe Trotter", "globe.americas.fill", "#1E88E5", hint: "Get stamps from \(globeGoal) cities",
                done: "Stamps from \(globeGoal) cities!",
                progress: places.filter(\.stamped).count, goal: globeGoal
            ),
            badge(
                "super-star", "Super Star", "trophy.fill", "#E63946", hint: "Earn \(Self.superStarGoal) stars",
                done: "\(Self.superStarGoal) stars!",
                progress: total, goal: Self.superStarGoal
            ),
        ]
        for shelf in shelves where shelf.books.count > 1 {
            var expert = badge(
                "expert-\(shelf.city)", "\(shelf.city) Expert", "medal.fill", "#D4A017",
                hint: "Earn a star in every \(shelf.city) book",
                done: "A star in every \(shelf.city) book!",
                progress: shelf.books.filter { hasAsked(inBook: $0.id) }.count, goal: shelf.books.count
            )
            expert.flagBook = shelf.flagBook
            badges.append(expert)
        }
        return badges
    }

    /// Earned badges he hasn't seen in the Passport yet.
    func newBadgeIds(in badges: [StarBadge]) -> Set<String> {
        Set(badges.filter(\.earned).map(\.id)).subtracting(seenBadges)
    }
}
