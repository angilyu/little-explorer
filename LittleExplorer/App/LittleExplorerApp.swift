import SwiftUI

@main
struct LittleExplorerApp: App {
    @State private var voice: VoiceModeController
    @State private var readAloud = ReadAloudController()
    @State private var content = ContentStore()
    @State private var stars = StarStore.live()
    @State private var coins = CoinStore.live()
    @State private var account: AccountStore
    @State private var reading: ReadingActivity
    @State private var library: LibraryStore
    @State private var made: MadeBookLibrary
    @State private var maker: BookMaker
    @Environment(\.scenePhase) private var scenePhase

    init() {
        let account = AccountStore.live()
        let voice = VoiceModeController.live()
        // Milo connects only once a grown-up allowed it for the account (see MiloConsentView).
        voice.mayTalk = { account.miloAllowed }
        voice.onMiloOff = { Task { await account.refresh() } }
        _account = State(initialValue: account)
        _reading = State(initialValue: ReadingActivity.live(account: account))
        _library = State(initialValue: LibraryStore.live(account: account))
        let made = MadeBookLibrary.live(account: account)
        let maker = BookMaker.live(account: account)
        maker.onReady = { await made.sync(force: true) }
        _made = State(initialValue: made)
        _maker = State(initialValue: maker)
        _voice = State(initialValue: voice)
    }

    /// The account's catalog books, then its made books (the server already sent only the ones on its shelf).
    private var shelf: [Book] {
        let catalog = account.shelf(content.books)
        let ids = Set(catalog.map(\.id))
        let all = catalog + made.books.filter { !ids.contains($0.id) }
        return DemoLaunch.current?.leveled(all) ?? all
    }

    /// Books being made show on the shelf as placeholder covers, in their section.
    private var placeholders: [Book] { maker.placeholders.map(BookMaker.placeholder(for:)) }

    private var sync: AccountSync {
        AccountSync(account: account, stars: stars, coins: coins, reading: reading, library: library, made: made, maker: maker)
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if account.isSignedIn, account.asksMiloConsent {
                    MiloConsentView {
                        withAnimation { account.finishMiloConsent() }
                    }
                    .background(
                        LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
                            .ignoresSafeArea()
                    )
                } else if account.isSignedIn {
                    LibraryView(books: library.visible(shelf) + placeholders, allBooks: library.arranged(shelf))
                } else {
                    WelcomeView(books: content.books)
                }
            }
                .environment(account)
                .environment(voice)
                .environment(readAloud)
                .environment(content)
                .environment(stars)
                .environment(coins)
                .environment(reading)
                .environment(library)
                .environment(made)
                .environment(maker)
                .task {
                    voice.onStar = { [stars] award in stars.receive(award) }
                    stars.onCelebrate = { StarChime.play() }
                    if let demo = DemoLaunch.current {
                        library.switchAccount(account.account?.id)
                        library.seedDemo(demo.library)
                        if let make = demo.make {
                            if [.placeholder, .made, .menu].contains(make) { made.seedDemo([MakeDemo.book()].compactMap { $0 }) }
                            maker.seedDemo(status: MakeDemo.status(make), ownBooks: [MakeDemo.bookId])
                        }
                        return
                    }
                    let sync = self.sync
                    sync.start()
                    reading.appOpened()
                    async let books: Void = content.refresh()
                    async let saved: Void = sync.refresh()
                    _ = await (books, saved)
                }
                .task {
                    // Reading activity goes out every minute while the app is open (and on the scene changes below).
                    guard DemoLaunch.current == nil else { return }
                    while !Task.isCancelled {
                        try? await Task.sleep(for: .seconds(60))
                        await reading.flush()
                        if library.dirty { await library.sync() }
                    }
                }
                .onChange(of: account.account?.id) { _, id in
                    if id != nil { reading.appOpened() }
                }
                .onChange(of: scenePhase) { _, phase in
                    // Books being made are checked on only while the app is in front.
                    maker.setForeground(phase == .active)
                    if phase == .background { flushInBackground() }
                    guard phase == .active, DemoLaunch.current == nil else { return }
                    reading.appOpened()
                    Task { await reading.flush() }
                    Task { await content.refresh() }
                    Task { await sync.refresh() }
                }
        }
    }

    /// Sends the waiting reading activity while iOS gives the app a few seconds in the background.
    private func flushInBackground() {
        guard DemoLaunch.current == nil else { return }
        reading.pageHidden()
        let app = UIApplication.shared
        var task = UIBackgroundTaskIdentifier.invalid
        task = app.beginBackgroundTask(withName: "reading-activity") {
            app.endBackgroundTask(task)
            task = .invalid
        }
        Task {
            await reading.flush()
            if task != .invalid { app.endBackgroundTask(task) }
            task = .invalid
        }
    }
}
