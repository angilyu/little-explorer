import SwiftUI

@main
struct LittleExplorerApp: App {
    @State private var hearts = HeartStore()
    @State private var voice = VoiceModeController.live()
    @State private var readAloud = ReadAloudController()
    @State private var content = ContentStore()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            LibraryView(books: content.books)
                .environment(hearts)
                .environment(voice)
                .environment(readAloud)
                .environment(content)
                .task { if DemoLaunch.current == nil { await content.refresh() } }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active, DemoLaunch.current == nil { Task { await content.refresh() } }
                }
        }
    }
}
