import SwiftUI

@main
struct LittleExplorerApp: App {
    @State private var hearts = HeartStore()
    @State private var voice = VoiceModeController.live()
    @State private var readAloud = ReadAloudController()
    @State private var content = ContentStore()
    @State private var stars = StarStore.live()
    @State private var coins = CoinStore.live()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            LibraryView(books: content.books)
                .environment(hearts)
                .environment(voice)
                .environment(readAloud)
                .environment(content)
                .environment(stars)
                .environment(coins)
                .task {
                    voice.onStar = { [stars] award in stars.receive(award) }
                    stars.onCelebrate = { StarChime.play() }
                    guard DemoLaunch.current == nil else { return }
                    async let books: Void = content.refresh()
                    async let saved: Void = stars.restore(
                        from: StarBackup(baseURL: VoiceSettings.tokenServerURL(), deviceId: DeviceIdentity.current().id)
                    )
                    async let balance: Bool = coins.refresh(using: CoinStore.service())
                    _ = await (books, saved, balance)
                }
                .onChange(of: scenePhase) { _, phase in
                    guard phase == .active, DemoLaunch.current == nil else { return }
                    Task { await content.refresh() }
                    Task { await coins.refresh(using: CoinStore.service()) }
                }
        }
    }
}
