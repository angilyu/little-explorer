import SwiftUI

@main
struct LittleExplorerApp: App {
    @State private var voice = VoiceModeController.live()
    @State private var readAloud = ReadAloudController()
    @State private var content = ContentStore()
    @State private var stars = StarStore.live()
    @State private var coins = CoinStore.live()
    @State private var account = AccountStore.live()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            Group {
                if account.isSignedIn {
                    LibraryView(books: account.shelf(content.books))
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
                .task {
                    voice.onStar = { [stars] award in stars.receive(award) }
                    stars.onCelebrate = { StarChime.play() }
                    guard DemoLaunch.current == nil else { return }
                    let sync = AccountSync(account: account, stars: stars, coins: coins)
                    sync.start()
                    async let books: Void = content.refresh()
                    async let saved: Void = sync.refresh()
                    _ = await (books, saved)
                }
                .onChange(of: scenePhase) { _, phase in
                    guard phase == .active, DemoLaunch.current == nil else { return }
                    Task { await content.refresh() }
                    Task { await AccountSync(account: account, stars: stars, coins: coins).refresh() }
                }
        }
    }
}
