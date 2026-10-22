import SwiftUI

/// What a signed-out device shows: a fan of real photos from the books, what's inside, and one button that
/// opens email sign-in (signing up is the same steps).
struct WelcomeView: View {
    let books: [Book]

    @Environment(AccountStore.self) private var account
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var showSignIn = false
    @ScaledMetric(relativeTo: .largeTitle) private var headlineSize: CGFloat = 30

    static let headline = "Read your way around the world!"
    static let subline = "Picture books with real photos of real places, read aloud, and Milo to talk with."
    static let features: [(symbol: String, color: Color, title: String, text: String)] = [
        ("book.fill", .accentColor, "Read along", "Every page read aloud"),
        ("bubble.left.and.bubble.right.fill", Color(hex: "#3B6EA5"), "Talk with Milo", "Ask about each place"),
        ("star.fill", .coinGold, "Earn stars", "Fill a passport"),
    ]

    private var regular: Bool { sizeClass == .regular }
    private var cards: [WelcomeCard] { WelcomeCard.cards(from: books, count: regular ? 7 : 5) }

    /// One flag per place a new account starts with, so the screen hints at what's inside.
    private var flags: [Book] {
        let starters = Set(Account.defaultBooks)
        var countries = Set<String>()
        return BookLibrary.shelves(books.filter { starters.contains($0.id) }).map(\.flagBook)
            .filter { countries.insert($0.country).inserted }
    }

    var body: some View {
        GeometryReader { geo in
            if geo.size.width > geo.size.height && geo.size.width >= 640 {
                // Landscape: the photos stay put on the left while the words scroll if they need to.
                HStack(spacing: regular ? 40 : 20) {
                    WelcomeCollage(cards: cards, regular: regular)
                        .frame(height: min(geo.size.height * 0.72, regular ? 460 : 300))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    ScrollView {
                        VStack(spacing: regular ? 22 : 14) {
                            title
                            intro
                            features
                            grownUpsCard
                            credits
                        }
                        .padding(.vertical, regular ? 32 : 16)
                        .frame(maxWidth: .infinity, minHeight: geo.size.height)
                    }
                    .scrollIndicators(.hidden)
                    .frame(maxWidth: regular ? 540 : 430)
                }
                .padding(.horizontal, regular ? 40 : 12)
            } else {
                ScrollView {
                    VStack(spacing: regular ? 24 : 16) {
                        title
                        WelcomeCollage(cards: cards, regular: regular)
                            .frame(height: regular ? 400 : 236)
                        intro
                        features
                        grownUpsCard
                        credits
                    }
                    .padding(.horizontal, regular ? 40 : 20)
                    .padding(.vertical, regular ? 32 : 12)
                    .frame(maxWidth: .infinity, minHeight: geo.size.height)
                }
            }
        }
        .background(background)
        // The sky-and-paper look (like the shelf) needs dark text, even in Dark Mode.
        .environment(\.colorScheme, .light)
        .sheet(isPresented: $showSignIn) {
            SignInView()
                .environment(account)
        }
        .task {
            guard let demo = DemoLaunch.current else { return }
            if demo.accountScreen == .email || demo.accountScreen == .code { showSignIn = true }
        }
    }

    private var title: some View {
        Text("Little Explorer")
            .font(Theme.titleFont(regular: regular))
            .foregroundStyle(Theme.ink)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .accessibilityAddTraits(.isHeader)
    }

    private var intro: some View {
        VStack(spacing: regular ? 14 : 10) {
            Text(Self.headline)
                .font(.system(size: headlineSize * (regular ? 1.3 : 1), weight: .heavy, design: .rounded))
                .foregroundStyle(Color.accentColor)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(Self.subline)
                .font(.system(regular ? .title3 : .body, design: .rounded).weight(.semibold))
                .foregroundStyle(Theme.ink.opacity(0.75))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: regular ? 12 : 9) {
                ForEach(flags) { FlagBadge(book: $0, height: regular ? 34 : 24) }
            }
            .shadow(color: .black.opacity(0.12), radius: 2, x: 0, y: 1)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Books about " + ListFormatter.localizedString(byJoining: flags.map(\.country)))
        }
    }

    @ViewBuilder
    private var features: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 10))
            : AnyLayout(HStackLayout(alignment: .top, spacing: regular ? 14 : 8))
        layout {
            ForEach(Self.features, id: \.title) { feature in
                VStack(spacing: 6) {
                    Image(systemName: feature.symbol)
                        .font(.system(size: regular ? 24 : 19, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: regular ? 52 : 42, height: regular ? 52 : 42)
                        .background(Circle().fill(feature.color.gradient))
                        .accessibilityHidden(true)
                    Text(feature.title)
                        .font(.system(regular ? .headline : .subheadline, design: .rounded).weight(.heavy))
                        .foregroundStyle(Theme.ink)
                    Text(feature.text)
                        .font(.system(regular ? .subheadline : .caption, design: .rounded).weight(.medium))
                        .foregroundStyle(Theme.ink.opacity(0.65))
                }
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(.vertical, regular ? 14 : 10)
                .padding(.horizontal, 6)
                .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(.white.opacity(0.7)))
                .accessibilityElement(children: .combine)
            }
        }
        .frame(maxWidth: 560)
    }

    private var grownUpsCard: some View {
        VStack(spacing: 12) {
            Text("Hello, grown-ups!")
                .font(.system(.title2, design: .rounded).weight(.heavy))
                .foregroundStyle(Theme.ink)
                .accessibilityAddTraits(.isHeader)
            Text("Sign in with your email to open the bookshelf. New here? The same steps make your free account.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button {
                showSignIn = true
            } label: {
                Label("Sign in or sign up", systemImage: "envelope.fill")
                    .font(.system(.headline, design: .rounded))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .accessibilityHint("Opens email sign-in")
            Text("Just an email and a 6-digit code. No passwords.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(regular ? 26 : 18)
        .frame(maxWidth: 480)
        .background(
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(.white)
                .shadow(color: Theme.ink.opacity(0.12), radius: 18, x: 0, y: 8)
        )
    }

    private var credits: some View {
        Text(WelcomeCard.credits(cards))
            .font(.caption2)
            .foregroundStyle(Theme.ink.opacity(0.5))
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 640)
    }

    private var background: some View {
        ZStack {
            LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
            RadialGradient(colors: [.white.opacity(0.9), .white.opacity(0)], center: UnitPoint(x: 0.5, y: 0.25),
                           startRadius: 10, endRadius: regular ? 520 : 300)
        }
        .ignoresSafeArea()
    }
}

/// One photo from a book for the welcome fan, like a postcard: the photo, the place's flag and a short name.
struct WelcomeCard: Identifiable, Equatable {
    let id: String
    let title: String
    let photo: PhotoInfo
    let flagBook: Book
    let tint: Color

    /// Photos from the books a new account starts with, center card first-in-the-middle (Eiffel Tower).
    static let picks: [(book: String, page: String, title: String)] = [
        ("paris", "croissant", "Croissants"),
        ("london-big-ben", "welcome", "Big Ben"),
        ("barcelona-park-guell", "dragon", "Mosaic dragon"),
        ("paris-eiffel", "welcome", "Eiffel Tower"),
        ("beijing", "panda", "Giant panda"),
        ("beijing-great-wall", "welcome", "Great Wall"),
        ("barcelona-sagrada", "welcome", "Sagrada Família"),
    ]

    /// The middle `count` picks that the books (downloaded or bundled) still have.
    static func cards(from books: [Book], count: Int = picks.count) -> [WelcomeCard] {
        let all: [WelcomeCard] = picks.compactMap { pick in
            guard let book = books.first(where: { $0.id == pick.book }),
                  let page = book.pages.first(where: { $0.id == pick.page }) else { return nil }
            return WelcomeCard(id: "\(book.id)/\(page.id)", title: pick.title, photo: page.photo, flagBook: book,
                               tint: Color(hex: book.coverColor))
        }
        let drop = max(0, all.count - max(0, count))
        return Array(all.dropFirst(drop / 2).dropLast(drop - drop / 2))
    }

    /// Who took the photos on the fan, and their licenses (the books show the same credit under each photo).
    static func credits(_ cards: [WelcomeCard]) -> String {
        let people = cards.map { card in
            let who = card.photo.author?.isEmpty == false ? card.photo.author! : "Unknown author"
            let what = card.photo.license?.isEmpty == false ? card.photo.license! : "license pending"
            return "\(card.title): \(who), \(what)"
        }
        return "Photos from Wikimedia Commons. " + people.joined(separator: " · ")
    }

    static func == (lhs: WelcomeCard, rhs: WelcomeCard) -> Bool { lhs.id == rhs.id }
}

/// The book photos fanned out like postcards, gently bobbing (still when Reduce Motion is on).
struct WelcomeCollage: View {
    let cards: [WelcomeCard]
    let regular: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var fanned = false

    var body: some View {
        GeometryReader { geo in
            let cardWidth = min(geo.size.height * 0.84 * 0.74, geo.size.width * 0.5)
            let cardHeight = cardWidth / 0.74
            let mid = Double(cards.count - 1) / 2
            let step = mid > 0 ? min(cardWidth * 0.66, (geo.size.width - cardWidth) / (2 * mid)) : 0
            TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion || !fanned)) { context in
                let time = context.date.timeIntervalSinceReferenceDate
                ZStack {
                    ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                        let offset = Double(index) - mid
                        let bob = reduceMotion ? 0 : sin(time * 1.1 + Double(index) * 1.4) * (regular ? 5 : 3.5)
                        WelcomePostcard(card: card, regular: regular, showsTitle: abs(offset) < 0.5)
                            .frame(width: cardWidth, height: cardHeight)
                            .scaleEffect(fanned ? 1 - 0.09 * abs(offset) : 0.9)
                            .rotationEffect(.degrees(fanned ? offset * 7 + bob * 0.25 : 0))
                            .offset(x: fanned ? offset * step : 0,
                                    y: fanned ? offset * offset * cardHeight * 0.025 + bob : 0)
                            .zIndex(-abs(offset))
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
            }
        }
        .onAppear {
            if reduceMotion {
                fanned = true
            } else {
                withAnimation(.spring(duration: 0.9, bounce: 0.3).delay(0.15)) { fanned = true }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Photos from the books: " + ListFormatter.localizedString(byJoining: cards.map(\.title)))
        .accessibilityAddTraits(.isImage)
    }
}

/// A white-framed photo with the place's flag underneath, and its name on the front card (the others overlap).
struct WelcomePostcard: View {
    let card: WelcomeCard
    let regular: Bool
    var showsTitle = true

    var body: some View {
        VStack(spacing: regular ? 8 : 5) {
            photo
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: regular ? 14 : 10, style: .continuous))
            HStack(spacing: regular ? 7 : 5) {
                FlagBadge(book: card.flagBook, height: regular ? 18 : 12)
                if showsTitle {
                    Text(card.title)
                        .font(.system(size: regular ? 17 : 12, weight: .heavy, design: .rounded))
                        .foregroundStyle(Theme.ink)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                }
            }
            .frame(height: regular ? 22 : 15)
            .padding(.bottom, regular ? 4 : 2)
        }
        .padding(regular ? 9 : 6)
        .background(RoundedRectangle(cornerRadius: regular ? 20 : 14, style: .continuous).fill(.white))
        .shadow(color: .black.opacity(0.2), radius: regular ? 14 : 9, x: 0, y: regular ? 8 : 5)
    }

    @ViewBuilder
    private var photo: some View {
        if let file = card.photo.file, let image = ContentFiles.shared.image(named: file) {
            Color.clear.overlay {
                Image(uiImage: image).resizable().scaledToFill()
            }
            .clipped()
        } else {
            LinearGradient(colors: [card.tint, card.tint.opacity(0.6)], startPoint: .top, endPoint: .bottom)
        }
    }
}
