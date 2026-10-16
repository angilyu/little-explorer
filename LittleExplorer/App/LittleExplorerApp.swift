import SwiftUI

@main
struct LittleExplorerApp: App {
    @State private var hearts = HeartStore()
    @State private var voice = VoiceModeController.live()
    @State private var readAloud = ReadAloudController()
    private let books: [Book] = (try? BookLibrary.loadBundled()) ?? []

    var body: some Scene {
        WindowGroup {
            LibraryView(books: books)
                .environment(hearts)
                .environment(voice)
                .environment(readAloud)
        }
    }
}
