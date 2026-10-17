import SwiftUI

extension Color {
    static let starGold = Color(red: 1.0, green: 0.76, blue: 0.1)
}

/// Library corner badge: his star total. Tapping it opens the Passport.
struct StarTotalBadge: View {
    let total: Int
    var regular = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: "star.fill")
                    .foregroundStyle(Color.starGold)
                Text("\(total)")
                    .contentTransition(.numericText())
                    .foregroundStyle(Theme.ink)
            }
            .font(.system(size: regular ? 28 : 22, weight: .heavy, design: .rounded))
            .padding(.horizontal, regular ? 18 : 14)
            .frame(minHeight: regular ? 56 : 46)
            .background(Capsule().fill(.white))
            .shadow(color: .black.opacity(0.12), radius: 4, x: 0, y: 2)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(total) stars. Open my passport")
    }
}

/// Where a flying star lands: the in-book counter's frame in the reader's coordinate space.
struct StarCounterFrameKey: PreferenceKey {
    static let defaultValue: CGRect = .zero
    static func reduce(value: inout CGRect, nextValue: () -> CGRect) {
        let next = nextValue()
        if next != .zero { value = next }
    }
}

/// In-book counter next to the Milo switch; shown only while Milo is on.
struct StarCounter: View {
    let total: Int
    let bounce: Int
    var compact = false

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "star.fill")
                .foregroundStyle(Color.starGold)
                .symbolEffect(.bounce, value: bounce)
            Text("\(total)")
                .contentTransition(.numericText())
                .foregroundStyle(Theme.ink)
        }
        .font(.system(size: compact ? 20 : 24, weight: .heavy, design: .rounded))
        .padding(.horizontal, compact ? 10 : 14)
        .frame(minHeight: 56)
        .background(Capsule().fill(.white))
        .fixedSize()
        .background(
            GeometryReader { geo in
                Color.clear.preference(key: StarCounterFrameKey.self, value: geo.frame(in: .named(StarFlight.space)))
            }
        )
        .animation(.spring(duration: 0.4), value: total)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(total) stars")
        .transition(.scale.combined(with: .opacity))
    }
}

/// A big star that pops up over the page and flies into the counter.
struct StarFlight: View {
    static let space = "reader"

    let target: CGRect
    let flying: Bool

    var body: some View {
        GeometryReader { geo in
            Image(systemName: "star.fill")
                .font(.system(size: 110, weight: .heavy))
                .foregroundStyle(Color.starGold)
                .shadow(color: .orange.opacity(0.6), radius: 12)
                .scaleEffect(flying ? 0.22 : 1)
                .rotationEffect(.degrees(flying ? 200 : 0))
                .position(
                    flying && target != .zero
                        ? CGPoint(x: target.midX, y: target.midY)
                        : CGPoint(x: geo.size.width / 2, y: geo.size.height * 0.45)
                )
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Last page: how many stars this book gave, plus his new total.
struct BookStarSummary: View {
    let bookStars: Int
    let total: Int
    var regular = true

    var body: some View {
        VStack(spacing: 2) {
            HStack(spacing: 6) {
                Image(systemName: "star.fill")
                    .foregroundStyle(Color.starGold)
                Text(headline)
                    .lineLimit(2)
                    .minimumScaleFactor(0.7)
                    .multilineTextAlignment(.center)
            }
            .font(.system(size: regular ? 22 : 16, weight: .heavy, design: .rounded))
            if bookStars > 0 {
                Text("You have \(total) stars in all!")
                    .font(.system(size: regular ? 17 : 13, weight: .semibold, design: .rounded))
                    .opacity(0.75)
            }
        }
        .foregroundStyle(Theme.ink)
        .padding(.horizontal, 10)
        .accessibilityElement(children: .combine)
    }

    private var headline: String {
        switch bookStars {
        case 0: "Ask Milo a question to earn a star!"
        case 1: "You got 1 star in this book!"
        default: "You got \(bookStars) stars in this book!"
        }
    }
}
