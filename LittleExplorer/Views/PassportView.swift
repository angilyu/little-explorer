import SwiftUI

/// His travel passport: a stamp for each city he asked Milo about, badges for reading and being curious,
/// and a calendar of the stars he earned each day. Badges come in groups (from the badge catalog), each
/// a card that opens the whole group, so the Passport stays short however many badges there are.
struct PassportView: View {
    let shelves: [CityShelf]
    var catalog: BadgeCatalog = .bundled

    @Environment(StarStore.self) private var stars
    @Environment(CoinStore.self) private var coins
    @Environment(ReadingActivity.self) private var reading
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass

    /// Badges earned since he last looked, worked out once when the Passport opens so they stay NEW
    /// while he looks; they count as seen when he closes it.
    @State private var newBadges: Set<String> = []
    @State private var celebrating: [StarBadge] = []
    @State private var didCheckNew = false
    @State private var path: [String] = []

    private var regular: Bool { sizeClass == .regular }
    private var badges: [StarBadge] { stars.badges(shelves: shelves, catalog: catalog, reading: reading.progress) }
    private static let calendarAnchor = "star-calendar"
    private static let badgesAnchor = "badges"

    var body: some View {
        NavigationStack(path: $path) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: regular ? 28 : 20) {
                        totalCard
                        sectionTitle("City stamps")
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: regular ? 200 : 150), spacing: 14)], spacing: 14) {
                            ForEach(stars.stamps(shelves: shelves)) { StampView(stamp: $0, regular: regular) }
                        }
                        badgesSection
                            .id(Self.badgesAnchor)
                        sectionTitle("My star calendar")
                            .id(Self.calendarAnchor)
                        StarCalendarView(days: stars.days(), regular: regular)
                    }
                    .padding(regular ? 32 : 18)
                }
                .task {
                    guard let demo = DemoLaunch.current, demo.showCalendar || demo.showBadges else { return }
                    try? await Task.sleep(for: .milliseconds(700))
                    proxy.scrollTo(demo.showCalendar ? Self.calendarAnchor : Self.badgesAnchor, anchor: .top)
                    if let id = demo.badgeCategory, catalog.categories.contains(where: { $0.id == id }) {
                        try? await Task.sleep(for: .milliseconds(500))
                        path = [id]
                    }
                }
            }
            .background(
                LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
                    .ignoresSafeArea()
            )
            .navigationTitle("My Passport")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: String.self) { id in
                if let category = catalog.categories.first(where: { $0.id == id }) {
                    BadgeCategoryView(
                        category: category, badges: badges.filter { $0.category == id }, newBadges: newBadges, regular: regular
                    )
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                }
            }
        }
        .overlay {
            if !celebrating.isEmpty {
                BadgeCelebration(badges: celebrating, regular: regular) {
                    withAnimation(.easeOut(duration: 0.2)) { celebrating = [] }
                }
                .transition(.opacity)
            }
        }
        .onAppear {
            guard !didCheckNew else { return }
            didCheckNew = true
            let all = badges
            newBadges = stars.newBadgeIds(in: all)
            celebrating = all.filter { newBadges.contains($0.id) }
            if !newBadges.isEmpty { StarChime.play() }
        }
        .onDisappear { stars.markBadgesSeen(badges.filter(\.earned).map(\.id)) }
    }

    private var totalCard: some View {
        HStack(spacing: 14) {
            Image(systemName: "star.fill")
                .font(.system(size: regular ? 56 : 44, weight: .heavy))
                .foregroundStyle(Color.starGold)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(coins.spendable(fromEarned: stars.total)) stars")
                    .font(.system(size: regular ? 40 : 30, weight: .heavy, design: .rounded))
                Text(coins.starsSpent > 0 ? "You earned \(stars.total) stars in all!" : "Answer Milo's questions to earn more!")
                    .font(.system(size: regular ? 20 : 15, weight: .semibold, design: .rounded))
                    .opacity(0.75)
            }
            .foregroundStyle(Theme.ink)
            Spacer(minLength: 0)
            VStack(spacing: 4) {
                CoinIcon(size: regular ? 56 : 44)
                Text(coins.coins == 1 ? "1 coin" : "\(coins.coins) coins")
                    .font(.system(size: regular ? 24 : 18, weight: .heavy, design: .rounded))
                    .foregroundStyle(Theme.ink)
            }
        }
        .padding(regular ? 24 : 16)
        .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(.white))
        .accessibilityElement(children: .combine)
    }

    private var badgesSection: some View {
        let all = badges
        let earned = all.filter(\.earned).count
        let columns = regular ? [GridItem(.flexible(), spacing: 16), GridItem(.flexible())] : [GridItem(.flexible())]
        return VStack(alignment: .leading, spacing: regular ? 16 : 12) {
            HStack(alignment: .firstTextBaseline) {
                sectionTitle("Badges")
                Spacer(minLength: 8)
                Text("\(earned) of \(all.count)")
                    .font(.system(size: regular ? 20 : 16, weight: .heavy, design: .rounded))
                    .foregroundStyle(Theme.ink.opacity(0.7))
                    .accessibilityLabel("\(earned) of \(all.count) badges earned")
            }
            LazyVGrid(columns: columns, alignment: .leading, spacing: 14) {
                ForEach(catalog.categories) { category in
                    let group = all.filter { $0.category == category.id }
                    if !group.isEmpty {
                        NavigationLink(value: category.id) {
                            BadgeCategoryCard(category: category, badges: group, newBadges: newBadges, regular: regular)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text)
            .font(.system(size: regular ? 30 : 24, weight: .heavy, design: .rounded))
            .foregroundStyle(Theme.ink)
            .accessibilityAddTraits(.isHeader)
    }
}

private struct StampView: View {
    let stamp: PassportStamp
    let regular: Bool

    private var ink: Color { stamp.stamped ? Color(hex: stamp.flagBook.coverColor) : .gray }

    var body: some View {
        VStack(spacing: 8) {
            FlagBadge(book: stamp.flagBook, height: regular ? 40 : 30)
                .saturation(stamp.stamped ? 1 : 0)
                .opacity(stamp.stamped ? 1 : 0.4)
            Text(stamp.city.uppercased())
                .font(.system(size: regular ? 22 : 18, weight: .black, design: .rounded))
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(stamp.stamped ? "Visited!" : "Ask Milo here")
                .font(.system(size: regular ? 15 : 13, weight: .bold, design: .rounded))
                .opacity(0.8)
        }
        .foregroundStyle(ink)
        .frame(maxWidth: .infinity)
        .padding(.vertical, regular ? 22 : 16)
        .padding(.horizontal, 10)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(.white.opacity(stamp.stamped ? 1 : 0.55))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(ink.opacity(stamp.stamped ? 0.9 : 0.4), style: StrokeStyle(lineWidth: 4, dash: [10, 6]))
        )
        .rotationEffect(.degrees(stamp.stamped ? -4 : 0))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(stamp.stamped ? "\(stamp.city) stamp" : "\(stamp.city), no stamp yet")
    }
}

extension View {
    /// A roomy page-sized sheet on iPad (iOS 18+); the default sheet elsewhere.
    @ViewBuilder
    func passportSizing() -> some View {
        if #available(iOS 18.0, *) {
            presentationSizing(.page)
        } else {
            self
        }
    }
}
