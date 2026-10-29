import SwiftUI

struct LibraryView: View {
    /// The books on his shelf: the account's, arranged by a grown-up, without the hidden ones.
    let books: [Book]
    /// Every book of the account in the arranged order, hidden ones too: the Passport and badges count
    /// these (hiding a book doesn't take away what he earned), and the grown-ups corner lists them.
    var allBooks: [Book]? = nil

    @Environment(VoiceModeController.self) private var voice
    @Environment(StarStore.self) private var stars
    @Environment(CoinStore.self) private var coins
    @Environment(AccountStore.self) private var account
    @Environment(ReadingActivity.self) private var reading
    @Environment(ContentStore.self) private var content
    @Environment(BookMaker.self) private var maker
    @Environment(\.horizontalSizeClass) private var sizeClass

    @State private var openBook: OpenBook?
    @State private var showGate = false
    @State private var showParentCorner = false
    @State private var showPassport = false
    @State private var didApplyDemo = false

    private var regular: Bool { sizeClass == .regular }
    private var everyBook: [Book] { allBooks ?? books }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: regular ? 32 : 20) {
                        header
                        ForEach(BookLibrary.rows(BookLibrary.shelves(books), columns: columns), id: \.self) { row in
                            shelfRow(row)
                        }
                        grownUpsButton
                    }
                    .padding(.horizontal, regular ? 40 : 20)
                    .padding(.vertical, regular ? 40 : 8)
                }
                .task {
                    guard let id = DemoLaunch.current?.shelfBook else { return }
                    try? await Task.sleep(for: .milliseconds(600))
                    proxy.scrollTo(id, anchor: .center)
                }
            }
            .background(
                LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
                    .ignoresSafeArea()
            )
            .toolbar(.hidden, for: .navigationBar)
        }
        .fullScreenCover(item: $openBook) { open in
            BookReaderView(book: open.book, startPage: open.startPage)
                .environment(voice)
                .environment(stars)
                .environment(coins)
                .environment(account)
                .environment(reading)
        }
        .sheet(isPresented: $showPassport) {
            PassportView(shelves: BookLibrary.shelves(everyBook), catalog: content.badges)
                .environment(stars)
                .environment(coins)
                .environment(reading)
                .passportSizing()
        }
        .sheet(isPresented: $showGate) {
            ParentGateView {
                showGate = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { showParentCorner = true }
            }
        }
        .sheet(isPresented: $showParentCorner) {
            ParentCornerView(books: everyBook)
                .environment(account)
                .environment(voice)
                .environment(coins)
        }
        .onAppear(perform: applyDemoLaunch)
    }

    private var columns: Int { regular ? 3 : 2 }
    private var gridColumns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: regular ? 24 : 14, alignment: .top), count: columns)
    }

    /// A city with several books: one header, then its covers. One-book cities share a row, each with its own header.
    @ViewBuilder
    private func shelfRow(_ row: [CityShelf]) -> some View {
        if row.count == 1, let shelf = row.first, shelf.books.count > 1 {
            VStack(alignment: .leading, spacing: regular ? 14 : 10) {
                CityHeader(shelf: shelf, regular: regular)
                LazyVGrid(columns: gridColumns, spacing: regular ? 24 : 14) {
                    ForEach(shelf.books) { cover($0) }
                }
            }
        } else {
            LazyVGrid(columns: gridColumns, spacing: regular ? 24 : 14) {
                ForEach(row) { shelf in
                    VStack(alignment: .leading, spacing: regular ? 14 : 10) {
                        CityHeader(shelf: shelf, regular: regular)
                        ForEach(shelf.books) { cover($0) }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func cover(_ book: Book) -> some View {
        if BookMaker.isPlaceholder(book) {
            // A book being made: not openable until it's ready.
            PlaceholderCoverView(book: book, regular: regular)
                .id(book.id)
        } else {
            Button {
                openBook = OpenBook(book: book)
            } label: {
                BookCoverView(book: book, starCount: stars.stars(inBook: book.id), regular: regular,
                              remaking: maker.remaking(book.id)?.progressText)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(book.openLabel)
            .accessibilityValue(BookCoverView.accessibilityValue(level: book.readingLevel, starCount: stars.stars(inBook: book.id), made: book.isMade))
            .id(book.id)
        }
    }

    /// A small "Grown-ups" button after the last shelf, out of the crowded top bar and away from his covers.
    private var grownUpsButton: some View {
        Button {
            showGate = true
        } label: {
            Label("Grown-ups", systemImage: "lock.fill")
                .font(.footnote.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .tint(Theme.ink)
        .frame(maxWidth: .infinity)
        .padding(.top, regular ? 8 : 4)
        .padding(.bottom, regular ? 0 : 16)
        .id("grown-ups")
        .accessibilityHint("For parents only")
    }

    @ViewBuilder
    private var header: some View {
        if regular {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Little Explorer")
                        .font(Theme.titleFont(regular: true))
                        .foregroundStyle(Theme.ink)
                    Text("Pick a book to explore!")
                        .font(.system(size: 28, weight: .semibold, design: .rounded))
                        .foregroundStyle(Theme.ink.opacity(0.75))
                }
                Spacer()
                starBadge
            }
        } else {
            // iPhone: one compact row so every cover fits on the first screen.
            HStack(alignment: .firstTextBaseline) {
                Text("Little Explorer")
                    .font(.system(size: 30, weight: .heavy, design: .rounded))
                    .foregroundStyle(Theme.ink)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .layoutPriority(1)
                Spacer(minLength: 4)
                starBadge
            }
        }
    }

    private var starBadge: some View {
        StarTotalBadge(total: coins.spendable(fromEarned: stars.total), coins: coins.coins, regular: regular) {
            showPassport = true
        }
        .animation(.spring(duration: 0.4), value: coins.spendable(fromEarned: stars.total))
        .animation(.spring(duration: 0.4), value: coins.coins)
    }

    private func applyDemoLaunch() {
        guard !didApplyDemo, let demo = DemoLaunch.current else { return }
        didApplyDemo = true
        if demo.seedStars > 0 {
            stars.merge(StarStore.demoAwards(total: demo.seedStars, shelves: BookLibrary.shelves(everyBook)))
        }
        if demo.showBadges {
            let shelves = BookLibrary.shelves(everyBook)
            stars.merge(StarStore.demoBadgeExtras(shelves: shelves))
            reading.seedDemo(ReadingProgress.demo(shelves: shelves))
            // Everything earned counts as seen but a couple, so the Passport celebrates those.
            let earned = stars.badges(shelves: shelves, catalog: content.badges, reading: reading.progress).filter(\.earned).map(\.id)
            let fresh = demo.badgesSeen ? [] : Array(earned.filter { ["first-book", "on-a-roll"].contains($0) }.prefix(2))
            stars.markBadgesSeen(earned.filter { !fresh.contains($0) })
        }
        if demo.seedCoins > 0 { coins.seedDemo(coins: demo.seedCoins) }
        if demo.openPassport { showPassport = true }
        if demo.openGate { showGate = true }
        if demo.openParent { showParentCorner = true }
        guard let id = demo.bookId, let book = books.first(where: { $0.id == id }) else { return }
        let index = min(demo.pageIndex, book.pages.count - 1)
        openBook = OpenBook(book: book, startPage: index)
    }
}

/// The start page travels with the presented book so the cover never reads a stale value.
private struct OpenBook: Identifiable {
    let book: Book
    var startPage = 0
    var id: String { book.id }
}

struct CityHeader: View {
    let shelf: CityShelf
    let regular: Bool

    var body: some View {
        HStack(spacing: 10) {
            FlagBadge(book: shelf.flagBook, height: regular ? 34 : 24)
            Text(shelf.city)
                .font(.system(size: regular ? 34 : 24, weight: .heavy, design: .rounded))
                .foregroundStyle(Theme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

struct BookCoverView: View {
    let book: Book
    /// Every star he has earned in this book (coins don't take them away). Hidden at 0.
    var starCount = 0
    var regular = true
    /// A page of this made book being made again ("Writing…"), shown on the cover.
    var remaking: String? = nil

    /// What VoiceOver adds after "Open the Paris book", e.g. "Level 2, 7 stars" ("…, made with AI").
    static func accessibilityValue(level: Int, starCount: Int, made: Bool = false) -> String {
        let value = switch starCount {
        case ..<1: "Level \(level)"
        case 1: "Level \(level), 1 star"
        default: "Level \(level), \(starCount) stars"
        }
        return made ? value + ", made with AI" : value
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            PhotoView(photo: book.coverPage.photo, tint: Color(hex: book.coverColor))
                .frame(height: regular ? 210 : 100)
                .frame(maxWidth: .infinity)
                .clipped()
                .overlay(alignment: .topTrailing) {
                    HStack(spacing: regular ? 8 : 5) {
                        if starCount > 0 {
                            HStack(spacing: regular ? 5 : 3) {
                                Image(systemName: "star.fill")
                                    .foregroundStyle(Color.starGold)
                                Text("\(starCount)")
                                    .foregroundStyle(Theme.ink)
                            }
                            .font(.system(size: regular ? 20 : 15, weight: .heavy, design: .rounded))
                            .padding(.horizontal, regular ? 12 : 8)
                            .padding(.vertical, regular ? 6 : 4)
                            .background(Capsule().fill(.white))
                            .shadow(color: .black.opacity(0.15), radius: 3, x: 0, y: 1)
                        }
                    }
                    .lineLimit(1)
                    .fixedSize()
                    .padding(regular ? 12 : 8)
                    .accessibilityHidden(true)
                }
                .coverLevelBadge(level: book.readingLevel, regular: regular)
                .overlay(alignment: .bottomTrailing) {
                    if book.isMade {
                        VStack(alignment: .trailing, spacing: 4) {
                            if let remaking {
                                Label(remaking, systemImage: "arrow.triangle.2.circlepath")
                                    .font(.system(size: regular ? 14 : 10, weight: .bold, design: .rounded))
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, regular ? 10 : 7)
                                    .padding(.vertical, regular ? 5 : 3)
                                    .background(Capsule().fill(Color.orange))
                                    .fixedSize()
                            }
                            MadeWithAILabel(regular: regular)
                        }
                        .padding(regular ? 10 : 6)
                        .accessibilityHidden(true)
                    }
                }
            VStack(alignment: .leading, spacing: 4) {
                Text(book.coverTitle)
                    .font(.system(size: regular ? 30 : 21, weight: .heavy, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                Text(book.tagline)
                    .font(.system(size: regular ? 17 : 13, weight: .medium, design: .rounded))
                    .opacity(0.9)
                    .lineLimit(2, reservesSpace: true)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, regular ? 20 : 12)
            .padding(.vertical, regular ? 16 : 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(hex: book.coverColor))
        }
        .clipShape(RoundedRectangle(cornerRadius: regular ? 28 : 20, style: .continuous))
        .overlay(alignment: .leading) {
            Rectangle().fill(.black.opacity(0.18)).frame(width: regular ? 14 : 9)
                .clipShape(RoundedRectangle(cornerRadius: regular ? 28 : 20, style: .continuous))
        }
        .shadow(color: .black.opacity(0.18), radius: 12, x: 0, y: 8)
        .contentShape(Rectangle())
    }
}
