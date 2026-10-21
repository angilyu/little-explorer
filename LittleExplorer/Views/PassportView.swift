import SwiftUI

/// His travel passport: a stamp for each city he asked Milo about, badges for being curious, and a
/// calendar of the stars he earned each day.
struct PassportView: View {
    let shelves: [CityShelf]

    @Environment(StarStore.self) private var stars
    @Environment(CoinStore.self) private var coins
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass

    /// Badges earned since he last looked, worked out once when the Passport opens so they stay NEW
    /// while he looks; they count as seen when he closes it.
    @State private var newBadges: Set<String> = []
    @State private var didCheckNew = false

    private var regular: Bool { sizeClass == .regular }
    private var badges: [StarBadge] { stars.badges(shelves: shelves) }
    private static let calendarAnchor = "star-calendar"
    private static let badgesAnchor = "badges"

    var body: some View {
        NavigationStack {
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
                }
            }
            .onAppear {
                guard !didCheckNew else { return }
                didCheckNew = true
                newBadges = stars.newBadgeIds(in: badges)
                if !newBadges.isEmpty { StarChime.play() }
            }
            .onDisappear { stars.markBadgesSeen(badges.filter(\.earned).map(\.id)) }
            .background(
                LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
                    .ignoresSafeArea()
            )
            .navigationTitle("My Passport")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                }
            }
        }
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
        let fresh = all.filter { newBadges.contains($0.id) }
        return VStack(alignment: .leading, spacing: regular ? 16 : 12) {
            HStack(alignment: .firstTextBaseline) {
                sectionTitle("Badges")
                Spacer(minLength: 8)
                Text("\(earned) of \(all.count)")
                    .font(.system(size: regular ? 20 : 16, weight: .heavy, design: .rounded))
                    .foregroundStyle(Theme.ink.opacity(0.7))
                    .accessibilityLabel("\(earned) of \(all.count) badges earned")
            }
            if !fresh.isEmpty { NewBadgeBanner(badges: fresh, regular: regular) }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: regular ? 190 : 150), spacing: 14)], spacing: 14) {
                ForEach(all) { BadgeTile(badge: $0, isNew: newBadges.contains($0.id), regular: regular) }
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

/// "You got a new badge!" at the top of the badges, when he earned some since he last looked.
private struct NewBadgeBanner: View {
    let badges: [StarBadge]
    let regular: Bool

    private var message: String {
        badges.count == 1 ? "New badge: \(badges[0].title)!" : "\(badges.count) new badges!"
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "party.popper.fill")
                .font(.system(size: regular ? 40 : 30, weight: .bold))
                .foregroundStyle(.white)
                .accessibilityHidden(true)
            Text(message)
                .font(.system(size: regular ? 26 : 20, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)
                .lineLimit(2)
                .minimumScaleFactor(0.7)
            Spacer(minLength: 0)
        }
        .padding(regular ? 18 : 14)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(LinearGradient(colors: [Color(hex: "#FF6FA5"), Color(hex: "#FF9F1C")], startPoint: .leading, endPoint: .trailing))
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel(badges.count == 1 ? "New badge: \(badges[0].title)" : "\(badges.count) new badges")
    }
}

/// One badge: a big round medal, its name, and either what he did to earn it or a hint and how close he is.
private struct BadgeTile: View {
    let badge: StarBadge
    let isNew: Bool
    let regular: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var tint: Color { Color(hex: badge.tint) }
    private var medal: CGFloat { regular ? 104 : 84 }

    var body: some View {
        VStack(spacing: 8) {
            medalView
                .padding(.top, 4)
            Text(badge.title)
                .font(.system(size: regular ? 21 : 17, weight: .heavy, design: .rounded))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.75)
            Text(badge.earned ? badge.doneText : badge.detail)
                .font(.system(size: regular ? 15 : 13, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .opacity(0.75)
            Spacer(minLength: 0)
            if !badge.earned && badge.goal > 1 {
                HStack(spacing: 6) {
                    ProgressView(value: Double(badge.progress), total: Double(badge.goal))
                        .tint(tint)
                    Text("\(badge.progress)/\(badge.goal)")
                        .font(.system(size: regular ? 15 : 13, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .opacity(0.7)
                }
            }
        }
        .foregroundStyle(Theme.ink)
        .frame(maxWidth: .infinity)
        .padding(regular ? 16 : 12)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(.white.opacity(badge.earned ? 1 : 0.7))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .strokeBorder(badge.earned ? tint.opacity(0.85) : .clear, lineWidth: 3)
        )
        .overlay(alignment: .topTrailing) {
            if isNew { newRibbon }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(badge.accessibilityLabel(isNew: isNew))
    }

    private var medalView: some View {
        ZStack {
            Circle()
                .fill(badge.earned ? tint : Color.gray.opacity(0.15))
            Circle()
                .strokeBorder(.white.opacity(badge.earned ? 0.7 : 0), lineWidth: 4)
                .padding(5)
            Image(systemName: badge.systemImage)
                .font(.system(size: medal * 0.42, weight: .bold))
                .foregroundStyle(badge.earned ? Color.white : Color.gray.opacity(0.45))
            if let flag = badge.flagBook {
                FlagBadge(book: flag, height: medal * 0.24)
                    .saturation(badge.earned ? 1 : 0)
                    .opacity(badge.earned ? 1 : 0.5)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                    .accessibilityHidden(true)
            }
        }
        .frame(width: medal, height: medal)
        .shadow(color: badge.earned ? tint.opacity(0.45) : .clear, radius: 8, y: 3)
        .modifier(Wiggle(active: isNew && !reduceMotion))
    }

    private var newRibbon: some View {
        Text("NEW!")
            .font(.system(size: regular ? 15 : 13, weight: .black, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(Color(hex: "#FF3B6B")))
            .rotationEffect(.degrees(12))
            .offset(x: 6, y: -8)
            .accessibilityHidden(true)
    }
}

/// A happy little wiggle for a badge he just earned.
private struct Wiggle: ViewModifier {
    let active: Bool

    func body(content: Content) -> some View {
        if active {
            content.phaseAnimator([0.0, 1.0, -1.0, 0.0]) { view, phase in
                view
                    .rotationEffect(.degrees(phase * 10))
                    .scaleEffect(1 + abs(phase) * 0.08)
            } animation: { _ in .easeInOut(duration: 0.25) }
        } else {
            content
        }
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
