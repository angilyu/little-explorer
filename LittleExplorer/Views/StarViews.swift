import SwiftUI

extension Color {
    static let starGold = Color(red: 1.0, green: 0.76, blue: 0.1)
    static let coinGold = Color(red: 0.93, green: 0.6, blue: 0.05)
}

/// How a coin is drawn. Preview only: three looks to choose from (`-demoCoinIcon crown|dollar|stack`).
enum CoinStyle: String, CaseIterable, Identifiable {
    case crown, dollar, stack

    var id: String { rawValue }

    var title: String {
        switch self {
        case .crown: "A: Gold coin"
        case .dollar: "B: Dollar coin"
        case .stack: "C: Coin stack"
        }
    }

    static var current: CoinStyle { DemoLaunch.current?.coinStyle ?? .crown }
}

private struct CoinStyleKey: EnvironmentKey {
    static var defaultValue: CoinStyle { CoinStyle.current }
}

extension EnvironmentValues {
    var coinStyle: CoinStyle {
        get { self[CoinStyleKey.self] }
        set { self[CoinStyleKey.self] = newValue }
    }
}

/// A gold coin: what a grown-up turns his stars into. Drawn so it can't be mistaken for a star.
struct CoinIcon: View {
    var size: CGFloat = 28
    @Environment(\.coinStyle) private var style

    var body: some View {
        Group {
            switch style {
            case .crown: GoldCoin(size: size)
            case .dollar:
                Image(systemName: "dollarsign.circle.fill")
                    .resizable()
                    .scaledToFit()
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, Color.coinGold)
                    .fontWeight(.bold)
            case .stack: CoinStack(size: size)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

extension Color {
    static let coinRim = Color(red: 0.78, green: 0.5, blue: 0.05)
    static let coinLight = Color(red: 1.0, green: 0.86, blue: 0.35)
}

/// A round gold coin with a raised rim and a crown, like a pirate's gold piece.
private struct GoldCoin: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle().fill(Color.coinRim)
            Circle()
                .fill(RadialGradient(colors: [Color.coinLight, Color.coinGold], center: .init(x: 0.35, y: 0.3),
                                     startRadius: 0, endRadius: size * 0.55))
                .padding(size * 0.09)
            Circle()
                .strokeBorder(Color.coinRim.opacity(0.55), lineWidth: max(size * 0.035, 0.8))
                .padding(size * 0.17)
            Image(systemName: "crown.fill")
                .font(.system(size: size * 0.36, weight: .black))
                .foregroundStyle(Color.coinRim)
                .offset(y: -size * 0.01)
        }
    }
}

/// Three gold coins stacked up, seen from the side.
private struct CoinStack: View {
    let size: CGFloat

    var body: some View {
        let width = size * 0.86
        let face = size * 0.3
        let thick = size * 0.13
        ZStack(alignment: .bottom) {
            ForEach(0..<3) { i in
                coin(width: width, face: face, thick: thick)
                    .offset(x: [0, size * 0.05, -size * 0.03][i], y: -CGFloat(i) * (thick + size * 0.02))
            }
        }
        .frame(width: size, height: size, alignment: .bottom)
        .padding(.bottom, size * 0.08)
    }

    private func coin(width: CGFloat, face: CGFloat, thick: CGFloat) -> some View {
        ZStack(alignment: .top) {
            Ellipse().fill(Color.coinRim).frame(width: width, height: face).offset(y: thick)
            Rectangle().fill(Color.coinRim).frame(width: width, height: thick).offset(y: face / 2)
            Ellipse()
                .fill(LinearGradient(colors: [Color.coinLight, Color.coinGold], startPoint: .leading, endPoint: .trailing))
                .frame(width: width, height: face)
            Ellipse()
                .strokeBorder(Color.coinRim.opacity(0.6), lineWidth: max(face * 0.08, 0.7))
                .frame(width: width * 0.66, height: face * 0.58)
                .offset(y: face * 0.21)
        }
        .frame(width: width, height: face + thick, alignment: .top)
    }
}

/// Library corner badge: the stars he can spend and the coins he has. Tapping it opens the Passport.
struct StarTotalBadge: View {
    let total: Int
    var coins = 0
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
                CoinIcon(size: regular ? 30 : 24)
                    .padding(.leading, regular ? 8 : 4)
                Text("\(coins)")
                    .contentTransition(.numericText())
                    .foregroundStyle(Theme.ink)
            }
            .font(.system(size: regular ? 28 : 22, weight: .heavy, design: .rounded))
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, regular ? 18 : 12)
            .frame(minHeight: regular ? 56 : 46)
            .background(Capsule().fill(.white))
            .shadow(color: .black.opacity(0.12), radius: 4, x: 0, y: 2)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(total) stars and \(coins) \(coins == 1 ? "coin" : "coins"). Open my passport")
    }
}

#if DEBUG
/// Preview only (`-demoCoinIcons`): the coin looks side by side, big and in the library badge.
struct CoinIconGallery: View {
    var regular = true

    var body: some View {
        ScrollView {
            VStack(spacing: regular ? 28 : 20) {
                Text("Pick a coin")
                    .font(.system(size: regular ? 34 : 26, weight: .heavy, design: .rounded))
                HStack(alignment: .top, spacing: regular ? 40 : 16) {
                    ForEach(CoinStyle.allCases) { style in
                        VStack(spacing: 10) {
                            CoinIcon(size: regular ? 110 : 80)
                            Text(style.title)
                                .font(.system(size: regular ? 20 : 15, weight: .bold, design: .rounded))
                        }
                        .environment(\.coinStyle, style)
                    }
                }
                ForEach(CoinStyle.allCases) { style in
                    HStack(spacing: 14) {
                        Text(String(style.title.prefix(1)))
                            .font(.system(size: regular ? 24 : 18, weight: .heavy, design: .rounded))
                            .frame(width: 24)
                        StarTotalBadge(total: 41, coins: 3, regular: regular) {}
                        Spacer(minLength: 0)
                        HStack(spacing: 6) {
                            CoinIcon(size: 18)
                            Text("3").font(.body)
                        }
                    }
                    .environment(\.coinStyle, style)
                    .padding(.horizontal, regular ? 40 : 16)
                }
            }
            .foregroundStyle(Theme.ink)
            .padding(.vertical, regular ? 40 : 24)
        }
        .background(LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom).ignoresSafeArea())
    }
}
#endif

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
