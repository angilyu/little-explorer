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
    @State private var upgrade = UpgradeChecker()
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
        _voice = State(initialValue: voice)
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if case .required(let url) = upgrade.prompt {
                    // This build is no longer supported: nothing else shows until it's updated.
                    UpgradeView(prompt: .required(url))
                } else if account.isSignedIn, account.asksMiloConsent {
                    MiloConsentView {
                        withAnimation { account.finishMiloConsent() }
                    }
                    .background(
                        LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom)
                            .ignoresSafeArea()
                    )
                } else if account.isSignedIn {
                    let shelf = DemoLaunch.current?.leveled(account.shelf(content.books)) ?? account.shelf(content.books)
                    LibraryView(books: library.visible(shelf), allBooks: library.arranged(shelf))
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
                .sheet(isPresented: recommendsUpdate) {
                    if let prompt = upgrade.prompt {
                        UpgradeView(prompt: prompt) { upgrade.snoozeRecommended() }
                    }
                }
                .onChange(of: upgrade.isRequired) { _, required in
                    guard required else { return }
                    readAloud.stop()
                    Task { await voice.turnOff() }
                }
                .task {
                    voice.onStar = { [stars] award in stars.receive(award) }
                    stars.onCelebrate = { StarChime.play() }
                    if let demo = DemoLaunch.current?.upgrade { upgrade.show(demo == .required ? .required(nil) : .recommended(nil)) }
                    if let demo = DemoLaunch.current {
                        library.switchAccount(account.account?.id)
                        library.seedDemo(demo.library)
                        return
                    }
                    Task { await upgrade.check() }
                    let sync = AccountSync(account: account, stars: stars, coins: coins, reading: reading, library: library)
                    sync.start()
                    reading.appOpened()
                    voice.warmUp()
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
                    if phase == .background { flushInBackground() }
                    guard phase == .active, DemoLaunch.current == nil else { return }
                    Task { await upgrade.check() }
                    reading.appOpened()
                    voice.warmUp()
                    Task { await reading.flush() }
                    Task { await content.refresh() }
                    Task { await AccountSync(account: account, stars: stars, coins: coins, reading: reading, library: library).refresh() }
                }
        }
    }

    /// The "Maybe later" update prompt; closing it any way counts as "Maybe later".
    private var recommendsUpdate: Binding<Bool> {
        Binding(
            get: { if case .recommended = upgrade.prompt { true } else { false } },
            set: { if !$0 { upgrade.snoozeRecommended() } }
        )
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
