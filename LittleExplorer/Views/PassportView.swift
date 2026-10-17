import SwiftUI

/// His travel passport: a stamp for each city he asked Milo about, and badges for being curious.
struct PassportView: View {
    let shelves: [CityShelf]

    @Environment(StarStore.self) private var stars
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass

    private var regular: Bool { sizeClass == .regular }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: regular ? 28 : 20) {
                    totalCard
                    sectionTitle("City stamps")
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: regular ? 200 : 150), spacing: 14)], spacing: 14) {
                        ForEach(stars.stamps(shelves: shelves)) { StampView(stamp: $0, regular: regular) }
                    }
                    sectionTitle("Badges")
                    VStack(spacing: 12) {
                        ForEach(stars.badges(shelves: shelves)) { BadgeRow(badge: $0, regular: regular) }
                    }
                }
                .padding(regular ? 32 : 18)
            }
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
                Text("\(stars.total) stars")
                    .font(.system(size: regular ? 40 : 30, weight: .heavy, design: .rounded))
                Text("Ask Milo questions to earn more!")
                    .font(.system(size: regular ? 20 : 15, weight: .semibold, design: .rounded))
                    .opacity(0.75)
            }
            .foregroundStyle(Theme.ink)
            Spacer(minLength: 0)
        }
        .padding(regular ? 24 : 16)
        .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(.white))
        .accessibilityElement(children: .combine)
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

private struct BadgeRow: View {
    let badge: StarBadge
    let regular: Bool

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: badge.systemImage)
                .font(.system(size: regular ? 32 : 26, weight: .bold))
                .foregroundStyle(badge.earned ? Color.white : Color.gray)
                .frame(width: regular ? 64 : 54, height: regular ? 64 : 54)
                .background(Circle().fill(badge.earned ? Color.orange : Color.gray.opacity(0.2)))
            VStack(alignment: .leading, spacing: 6) {
                Text(badge.title)
                    .font(.system(size: regular ? 24 : 19, weight: .heavy, design: .rounded))
                Text(badge.earned ? "You did it!" : badge.detail)
                    .font(.system(size: regular ? 17 : 14, weight: .semibold, design: .rounded))
                    .opacity(0.75)
                ProgressView(value: Double(badge.progress), total: Double(badge.goal))
                    .tint(badge.earned ? .orange : Theme.ink.opacity(0.6))
            }
            .foregroundStyle(Theme.ink)
            Text("\(badge.progress)/\(badge.goal)")
                .font(.system(size: regular ? 20 : 16, weight: .bold, design: .rounded))
                .foregroundStyle(Theme.ink.opacity(0.7))
        }
        .padding(regular ? 18 : 14)
        .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(.white))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(badge.title). \(badge.earned ? "Earned" : "\(badge.progress) of \(badge.goal)")")
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
