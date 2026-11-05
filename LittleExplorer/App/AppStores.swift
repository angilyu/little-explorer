import SwiftUI

/// The app's shared stores, passed to every sheet and full-screen cover together.
///
/// On a Mac ("Designed for iPad"), when a presented view has a `.sheet` of its own, SwiftUI builds it before
/// the presenter's environment reaches it: only stores injected directly on the presented view are there,
/// and reading any other one crashes the app. 1.0 crashed this way opening a book on a Mac (the book's
/// cover didn't pass `ReadAloudController`). So present with `appSheet` / `appFullScreenCover`, which pass
/// every store, never with a bare `.sheet` / `.fullScreenCover` (PresentationStoresTests checks this).
struct AppStores {
    let account: AccountStore
    let voice: VoiceModeController
    let readAloud: ReadAloudController
    let content: ContentStore
    let stars: StarStore
    let coins: CoinStore
    let reading: ReadingActivity
    let library: LibraryStore
    let made: MadeBookLibrary
    let maker: BookMaker
}

extension View {
    /// Puts every shared store in this view's environment.
    func appStores(_ stores: AppStores) -> some View {
        environment(stores.account)
            .environment(stores.voice)
            .environment(stores.readAloud)
            .environment(stores.content)
            .environment(stores.stars)
            .environment(stores.coins)
            .environment(stores.reading)
            .environment(stores.library)
            .environment(stores.made)
            .environment(stores.maker)
    }

    /// `.sheet(isPresented:)` that passes every shared store from here to the sheet.
    func appSheet<Sheet: View>(isPresented: Binding<Bool>, @ViewBuilder content: @escaping () -> Sheet) -> some View {
        modifier(AppSheet(isPresented: isPresented, sheet: content))
    }

    /// `.fullScreenCover(item:)` that passes every shared store from here to the cover.
    func appFullScreenCover<Item: Identifiable, Cover: View>(
        item: Binding<Item?>,
        @ViewBuilder content: @escaping (Item) -> Cover
    ) -> some View {
        modifier(AppFullScreenCover(item: item, cover: content))
    }
}

/// Reads every shared store from the environment where it's declared (the presenting view's).
private struct AppStoresReader: DynamicProperty {
    @Environment(AccountStore.self) private var account
    @Environment(VoiceModeController.self) private var voice
    @Environment(ReadAloudController.self) private var readAloud
    @Environment(ContentStore.self) private var content
    @Environment(StarStore.self) private var stars
    @Environment(CoinStore.self) private var coins
    @Environment(ReadingActivity.self) private var reading
    @Environment(LibraryStore.self) private var library
    @Environment(MadeBookLibrary.self) private var made
    @Environment(BookMaker.self) private var maker

    var stores: AppStores {
        AppStores(
            account: account, voice: voice, readAloud: readAloud, content: content, stars: stars,
            coins: coins, reading: reading, library: library, made: made, maker: maker
        )
    }
}

private struct AppSheet<Sheet: View>: ViewModifier {
    @Binding var isPresented: Bool
    let sheet: () -> Sheet
    private var reader = AppStoresReader()

    init(isPresented: Binding<Bool>, sheet: @escaping () -> Sheet) {
        _isPresented = isPresented
        self.sheet = sheet
    }

    func body(content: Content) -> some View {
        let stores = reader.stores
        content.sheet(isPresented: $isPresented) { sheet().appStores(stores) }
    }
}

private struct AppFullScreenCover<Item: Identifiable, Cover: View>: ViewModifier {
    @Binding var item: Item?
    let cover: (Item) -> Cover
    private var reader = AppStoresReader()

    init(item: Binding<Item?>, cover: @escaping (Item) -> Cover) {
        _item = item
        self.cover = cover
    }

    func body(content: Content) -> some View {
        let stores = reader.stores
        content.fullScreenCover(item: $item) { cover($0).appStores(stores) }
    }
}
