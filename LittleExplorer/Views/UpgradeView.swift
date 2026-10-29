import SwiftUI

/// The update screen. `required`: this build is no longer supported, so it is the only thing the app shows (no way
/// to close it). `recommended`: a newer build is out, with "Maybe later". Update goes through the grown-ups gate
/// first, since it leaves the app for the App Store (or TestFlight).
struct UpgradeView: View {
    let prompt: UpgradeChecker.Prompt
    /// "Maybe later" on the recommended screen.
    var onLater: () -> Void = {}

    @Environment(\.openURL) private var openURL
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var gate = false

    static let requiredTitle = "Time for an update!"
    static let requiredText = "Little Explorer has a new version. Ask a grown-up to update the app so you can keep exploring."
    static let recommendedTitle = "A new version is ready"
    static let recommendedText = "Little Explorer has an update with new things to explore. Ask a grown-up to update it when it's a good time."
    static let grownUps = "For grown-ups: tap Update to get the newest Little Explorer. Your books, stars and coins stay just as they are."
    static let gateNote = "Updating opens the App Store."

    private var regular: Bool { sizeClass == .regular }
    private var required: Bool { if case .required = prompt { true } else { false } }
    private var url: URL? {
        switch prompt {
        case .required(let url), .recommended(let url): url
        }
    }

    var body: some View {
        ScrollView {
            VStack(spacing: regular ? 22 : 18) {
                Image(systemName: required ? "sparkles" : "gift.fill")
                    .font(.system(size: regular ? 64 : 52))
                    .foregroundStyle(.tint)
                    .symbolRenderingMode(.hierarchical)
                    .accessibilityHidden(true)
                Text(required ? Self.requiredTitle : Self.recommendedTitle)
                    .font(.system(size: regular ? 36 : 30, weight: .bold, design: .rounded))
                    .multilineTextAlignment(.center)
                Text(required ? Self.requiredText : Self.recommendedText)
                    .font(.system(size: regular ? 22 : 19, weight: .medium, design: .rounded))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Text(Self.grownUps)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(regular ? 40 : 24)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
        .safeAreaInset(edge: .bottom) { buttons }
        .foregroundStyle(Theme.ink)
        .background(
            LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom).ignoresSafeArea()
        )
        .interactiveDismissDisabled(required)
        .sheet(isPresented: $gate) {
            ParentGateView(onPass: {
                gate = false
                Task { openURL(await UpgradeChecker.destination(url)) }
            }, note: Self.gateNote)
        }
    }

    private var buttons: some View {
        VStack(spacing: 10) {
            Button { gate = true } label: {
                Label("Update", systemImage: "arrow.down.app.fill")
                    .font(.headline)
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            if !required {
                Button("Maybe later") { onLater() }
                    .font(.headline)
                    .padding(.vertical, 4)
            }
        }
        .padding(.horizontal, regular ? 40 : 24)
        .padding(.vertical, 12)
        .frame(maxWidth: 480)
    }
}
