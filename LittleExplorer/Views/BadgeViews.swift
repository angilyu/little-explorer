import SwiftUI

/// A round badge medal: the badge's color and symbol once earned, gray before. City Expert medals carry
/// the city's flag.
struct BadgeMedal: View {
    let badge: StarBadge
    let size: CGFloat
    var wiggle = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var tint: Color { Color(hex: badge.tint) }

    var body: some View {
        ZStack {
            Circle()
                .fill(badge.earned ? tint : Color.gray.opacity(0.15))
            Circle()
                .strokeBorder(.white.opacity(badge.earned ? 0.7 : 0), lineWidth: max(2, size * 0.045))
                .padding(size * 0.06)
            Image(systemName: badge.systemImage)
                .font(.system(size: size * 0.42, weight: .bold))
                .foregroundStyle(badge.earned ? Color.white : Color.gray.opacity(0.45))
            if let flag = badge.flagBook {
                FlagBadge(book: flag, height: size * 0.24)
                    .saturation(badge.earned ? 1 : 0)
                    .opacity(badge.earned ? 1 : 0.5)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                    .accessibilityHidden(true)
            }
        }
        .frame(width: size, height: size)
        .shadow(color: badge.earned ? tint.opacity(0.45) : .clear, radius: size * 0.08, y: size * 0.03)
        .modifier(Wiggle(active: wiggle && !reduceMotion))
        .accessibilityHidden(true)
    }
}

/// One group of badges on the Passport: how many he has, the medals he earned, and the badge he is
/// closest to next. Tapping it opens the whole group, so the Passport stays short however many
/// badges there are.
struct BadgeCategoryCard: View {
    let category: BadgeCategory
    let badges: [StarBadge]
    let newBadges: Set<String>
    let regular: Bool

    private var tint: Color { Color(hex: category.tint) }
    private var earned: [StarBadge] { badges.filter(\.earned) }
    private var freshCount: Int { badges.filter { newBadges.contains($0.id) }.count }
    private var maxMedals: Int { regular ? 6 : 5 }
    private var next: StarBadge? { BadgeOrder.next(in: badges) }

    var body: some View {
        VStack(alignment: .leading, spacing: regular ? 14 : 12) {
            header
            medals
            if let next { nextRow(next) } else { allDone }
        }
        .foregroundStyle(Theme.ink)
        .padding(regular ? 20 : 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(.white))
        .overlay(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .strokeBorder(tint.opacity(freshCount > 0 ? 0.9 : 0.25), lineWidth: freshCount > 0 ? 3 : 2)
        )
        .contentShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityHint("Shows all \(category.title) badges")
        .accessibilityAddTraits(.isButton)
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: category.icon)
                .font(.system(size: regular ? 24 : 20, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: regular ? 48 : 42, height: regular ? 48 : 42)
                .background(Circle().fill(tint))
            VStack(alignment: .leading, spacing: 0) {
                Text(category.title)
                    .font(.system(size: regular ? 24 : 21, weight: .heavy, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text("\(earned.count) of \(badges.count) badges")
                    .font(.system(size: regular ? 16 : 14, weight: .bold, design: .rounded))
                    .opacity(0.65)
            }
            Spacer(minLength: 4)
            if freshCount > 0 { NewPill(count: freshCount, regular: regular) }
            Image(systemName: "chevron.right")
                .font(.system(size: regular ? 18 : 16, weight: .heavy))
                .foregroundStyle(tint)
        }
    }

    @ViewBuilder
    private var medals: some View {
        if earned.isEmpty {
            Text("No badges here yet. You can do it!")
                .font(.system(size: regular ? 17 : 15, weight: .semibold, design: .rounded))
                .opacity(0.7)
        } else {
            let size: CGFloat = regular ? 52 : 46
            let shown = earned.count > maxMedals ? maxMedals - 1 : earned.count
            HStack(spacing: regular ? 10 : 8) {
                ForEach(earned.prefix(shown)) { badge in
                    BadgeMedal(badge: badge, size: size, wiggle: newBadges.contains(badge.id))
                }
                if earned.count > shown {
                    Text("+\(earned.count - shown)")
                        .font(.system(size: size * 0.36, weight: .heavy, design: .rounded))
                        .foregroundStyle(tint)
                        .frame(width: size, height: size)
                        .background(Circle().fill(tint.opacity(0.14)))
                }
            }
        }
    }

    private func nextRow(_ badge: StarBadge) -> some View {
        HStack(spacing: 10) {
            BadgeMedal(badge: badge, size: regular ? 40 : 36)
            VStack(alignment: .leading, spacing: 4) {
                Text("Next: \(badge.title)")
                    .font(.system(size: regular ? 17 : 15, weight: .heavy, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Text(badge.detail)
                    .font(.system(size: regular ? 15 : 13, weight: .semibold, design: .rounded))
                    .lineLimit(2)
                    .opacity(0.7)
                if badge.goal > 1 { BadgeProgress(badge: badge, regular: regular) }
            }
        }
        .padding(regular ? 12 : 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(tint.opacity(0.08)))
    }

    private var allDone: some View {
        Label("You got them all!", systemImage: "checkmark.seal.fill")
            .font(.system(size: regular ? 18 : 16, weight: .heavy, design: .rounded))
            .foregroundStyle(tint)
    }

    private var accessibilityText: String {
        var text = "\(category.title) badges. \(earned.count) of \(badges.count) earned."
        if freshCount > 0 { text += freshCount == 1 ? " 1 new badge!" : " \(freshCount) new badges!" }
        if let next {
            text += " Next: \(next.title). \(next.detail)."
            if next.goal > 1 { text += " \(next.progress) of \(next.goal)." }
        } else {
            text += " You got them all!"
        }
        return text
    }
}

/// All the badges of one group: the ones he earned as medals, then the ones still to find (closest
/// first) with a hint and how far he has got.
struct BadgeCategoryView: View {
    let category: BadgeCategory
    let badges: [StarBadge]
    let newBadges: Set<String>
    let regular: Bool

    private var tint: Color { Color(hex: category.tint) }

    var body: some View {
        let earned = badges.filter(\.earned)
        let locked = BadgeOrder.closestFirst(badges.filter { !$0.earned })
        ScrollView {
            VStack(alignment: .leading, spacing: regular ? 24 : 18) {
                HStack(spacing: 14) {
                    Image(systemName: category.icon)
                        .font(.system(size: regular ? 34 : 28, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: regular ? 68 : 56, height: regular ? 68 : 56)
                        .background(Circle().fill(tint))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(category.title)
                            .font(.system(size: regular ? 34 : 28, weight: .heavy, design: .rounded))
                        Text("\(earned.count) of \(badges.count) badges")
                            .font(.system(size: regular ? 20 : 16, weight: .bold, design: .rounded))
                            .opacity(0.7)
                    }
                    .foregroundStyle(Theme.ink)
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                }
                if !earned.isEmpty {
                    title("You earned")
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: regular ? 170 : 140), spacing: 14)], spacing: 14) {
                        ForEach(earned) { BadgeTile(badge: $0, isNew: newBadges.contains($0.id), regular: regular) }
                    }
                }
                if !locked.isEmpty {
                    title("Still to find")
                    LazyVGrid(columns: regular ? [GridItem(.flexible(), spacing: 14), GridItem(.flexible())] : [GridItem(.flexible())], spacing: 12) {
                        ForEach(locked) { BadgeRow(badge: $0, regular: regular) }
                    }
                }
            }
            .padding(regular ? 32 : 18)
        }
        .background(
            LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
        )
        .navigationTitle(category.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func title(_ text: String) -> some View {
        Text(text)
            .font(.system(size: regular ? 26 : 22, weight: .heavy, design: .rounded))
            .foregroundStyle(Theme.ink)
            .accessibilityAddTraits(.isHeader)
    }
}

/// An earned badge: its medal, name and what he did.
struct BadgeTile: View {
    let badge: StarBadge
    let isNew: Bool
    let regular: Bool

    private var tint: Color { Color(hex: badge.tint) }

    var body: some View {
        VStack(spacing: 8) {
            BadgeMedal(badge: badge, size: regular ? 96 : 80, wiggle: isNew)
                .padding(.top, 4)
            Text(badge.title)
                .font(.system(size: regular ? 20 : 17, weight: .heavy, design: .rounded))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.75)
            Text(badge.doneText)
                .font(.system(size: regular ? 15 : 13, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .opacity(0.75)
            Spacer(minLength: 0)
        }
        .foregroundStyle(Theme.ink)
        .frame(maxWidth: .infinity)
        .padding(regular ? 16 : 12)
        .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(.white))
        .overlay(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .strokeBorder(tint.opacity(0.85), lineWidth: 3)
        )
        .overlay(alignment: .topTrailing) {
            if isNew { NewPill(count: nil, regular: regular).rotationEffect(.degrees(12)).offset(x: 6, y: -8) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(badge.accessibilityLabel(isNew: isNew))
    }
}

/// A badge still to find: a gray medal, its hint, and a progress bar.
struct BadgeRow: View {
    let badge: StarBadge
    let regular: Bool

    var body: some View {
        HStack(spacing: 12) {
            BadgeMedal(badge: badge, size: regular ? 60 : 52)
            VStack(alignment: .leading, spacing: 4) {
                Text(badge.title)
                    .font(.system(size: regular ? 19 : 17, weight: .heavy, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Text(badge.detail)
                    .font(.system(size: regular ? 15 : 14, weight: .semibold, design: .rounded))
                    .fixedSize(horizontal: false, vertical: true)
                    .opacity(0.75)
                if badge.goal > 1 { BadgeProgress(badge: badge, regular: regular) }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(Theme.ink)
        .padding(regular ? 14 : 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(.white.opacity(0.75)))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(badge.accessibilityLabel())
    }
}

/// How far along a badge he is, e.g. a bar and "18/25".
private struct BadgeProgress: View {
    let badge: StarBadge
    let regular: Bool

    var body: some View {
        HStack(spacing: 6) {
            ProgressView(value: Double(badge.progress), total: Double(badge.goal))
                .tint(Color(hex: badge.tint))
            Text("\(badge.progress)/\(badge.goal)")
                .font(.system(size: regular ? 15 : 13, weight: .bold, design: .rounded))
                .monospacedDigit()
                .opacity(0.7)
        }
    }
}

private struct NewPill: View {
    /// How many are new, or nil for just "NEW!".
    let count: Int?
    let regular: Bool

    var body: some View {
        Text(count.map { $0 > 1 ? "\($0) NEW!" : "NEW!" } ?? "NEW!")
            .font(.system(size: regular ? 15 : 13, weight: .black, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(Color(hex: "#FF3B6B")))
            .fixedSize()
            .accessibilityHidden(true)
    }
}

/// Which locked badge to suggest next: the one he is closest to (by share of its goal), then the first
/// in the catalog.
enum BadgeOrder {
    static func closestFirst(_ badges: [StarBadge]) -> [StarBadge] {
        badges.enumerated().sorted { a, b in
            let x = share(a.element), y = share(b.element)
            return x != y ? x > y : a.offset < b.offset
        }.map(\.element)
    }

    static func next(in badges: [StarBadge]) -> StarBadge? {
        closestFirst(badges.filter { !$0.earned }).first
    }

    private static func share(_ badge: StarBadge) -> Double {
        Double(badge.progress) / Double(max(badge.goal, 1))
    }
}

/// "New badge!" when he opens the Passport with badges he earned since he last looked: the medals
/// wiggle, confetti falls (not with Reduce Motion), and "Yay!" closes it.
struct BadgeCelebration: View {
    let badges: [StarBadge]
    let regular: Bool
    let onClose: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    private var title: String { badges.count == 1 ? "New badge!" : "\(badges.count) new badges!" }
    private var medals: [StarBadge] { Array(badges.prefix(3)) }

    var body: some View {
        ZStack {
            Color.black.opacity(0.45)
                .ignoresSafeArea()
                .onTapGesture(perform: onClose)
                .accessibilityHidden(true)
            if !reduceMotion {
                Confetti()
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
            card
                .scaleEffect(shown || reduceMotion ? 1 : 0.6)
                .opacity(shown || reduceMotion ? 1 : 0)
        }
        .onAppear {
            withAnimation(.spring(response: 0.45, dampingFraction: 0.6)) { shown = true }
        }
        .accessibilityAddTraits(.isModal)
    }

    private var card: some View {
        VStack(spacing: regular ? 18 : 14) {
            Text(title)
                .font(.system(size: regular ? 38 : 30, weight: .black, design: .rounded))
                .foregroundStyle(Color(hex: "#FF3B6B"))
                .accessibilityAddTraits(.isHeader)
            HStack(spacing: regular ? 18 : 12) {
                ForEach(medals) { BadgeMedal(badge: $0, size: medalSize, wiggle: true) }
            }
            .padding(.vertical, 6)
            VStack(spacing: 6) {
                if badges.count == 1, let badge = badges.first {
                    Text(badge.title)
                        .font(.system(size: regular ? 30 : 24, weight: .heavy, design: .rounded))
                    Text(badge.doneText)
                        .font(.system(size: regular ? 20 : 17, weight: .semibold, design: .rounded))
                        .opacity(0.8)
                } else {
                    ForEach(badges.prefix(4)) { badge in
                        Text(badge.title)
                            .font(.system(size: regular ? 24 : 20, weight: .heavy, design: .rounded))
                    }
                    if badges.count > 4 {
                        Text("and \(badges.count - 4) more!")
                            .font(.system(size: regular ? 20 : 17, weight: .semibold, design: .rounded))
                            .opacity(0.8)
                    }
                }
            }
            .multilineTextAlignment(.center)
            .foregroundStyle(Theme.ink)
            .accessibilityElement(children: .combine)
            Button(action: onClose) {
                Text("Yay!")
                    .font(.system(size: regular ? 26 : 22, weight: .heavy, design: .rounded))
                    .foregroundStyle(.white)
                    .frame(maxWidth: regular ? 260 : 200)
                    .padding(.vertical, regular ? 16 : 12)
                    .background(
                        Capsule().fill(LinearGradient(
                            colors: [Color(hex: "#FF6FA5"), Color(hex: "#FF9F1C")], startPoint: .leading, endPoint: .trailing
                        ))
                    )
            }
            .buttonStyle(.plain)
            .padding(.top, 4)
        }
        .padding(regular ? 36 : 24)
        .frame(maxWidth: regular ? 520 : 340)
        .background(RoundedRectangle(cornerRadius: 32, style: .continuous).fill(.white))
        .shadow(color: .black.opacity(0.2), radius: 20, y: 8)
        .padding(24)
    }

    private var medalSize: CGFloat {
        let base: CGFloat = regular ? 130 : 104
        return medals.count == 1 ? base : medals.count == 2 ? base * 0.8 : base * 0.66
    }
}

/// Bright paper bits drifting down.
private struct Confetti: View {
    private static let colors: [Color] = ["#FF3B6B", "#FF9F1C", "#F4B400", "#2E9E44", "#3A86FF", "#9B5DE5"].map { Color(hex: $0) }
    @State private var start = Date()

    var body: some View {
        TimelineView(.animation) { context in
            Canvas { canvas, size in
                let t = context.date.timeIntervalSince(start)
                for i in 0..<60 {
                    let seed = Double(i)
                    let speed = 70 + (seed * 37).truncatingRemainder(dividingBy: 90)
                    let x = (seed * 0.618034).truncatingRemainder(dividingBy: 1) * size.width
                    let y = ((t * speed) + seed * 53).truncatingRemainder(dividingBy: size.height + 40) - 20
                    let sway = sin(t * 2 + seed) * 14
                    let angle = Angle.degrees(t * 120 + seed * 30)
                    var piece = canvas
                    piece.translateBy(x: x + sway, y: y)
                    piece.rotate(by: angle)
                    piece.fill(
                        Path(roundedRect: CGRect(x: -5, y: -3, width: 10, height: 6), cornerRadius: 1.5),
                        with: .color(Self.colors[i % Self.colors.count])
                    )
                }
            }
        }
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
