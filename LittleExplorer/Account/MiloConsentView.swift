import SwiftUI

/// What a grown-up reads before Milo may talk: Milo is an AI, what goes to OpenAI through LiveKit while
/// Milo is on, and what Little Explorer keeps. Shown once after signing in (the grown-up who typed the code
/// is holding the device), behind the grown-up check the first time "Talk to Milo" is tapped on a device that
/// was signed in before, and from the Grown-ups corner. The choice is the account's, so it counts on every
/// device signed in with the same email.
struct MiloConsentView: View {
    /// The account already allowed Milo (on another device): the grown-up confirms instead of allowing.
    var confirming = false
    /// Called with whether Milo is now allowed.
    let onDone: (Bool) -> Void

    @Environment(AccountStore.self) private var account
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var busy = false
    @State private var message: String?

    static let privacyURL = URL(string: "https://github.com/angilyu/little-explorer/blob/main/PRIVACY.md")!

    static let title = "Meet Milo, the AI voice buddy"
    static let intro = "Milo is an AI voice buddy your child can talk with about the book. Milo needs a grown-up's OK first."
    static let alreadyOn = "Milo is already on for this account. Here's a reminder of how Milo works."
    static let points: [(symbol: String, title: String, text: String)] = [
        ("waveform", "What Milo sends",
         "While Milo is on, your child's voice and the page being read are sent to OpenAI (the AI that understands and answers) through LiveKit (the voice connection), so Milo can talk back. When Milo is off, nothing is sent."),
        ("tray", "What Little Explorer keeps",
         "We don't record or keep your child's voice or the conversation. We keep how many minutes Milo was used each day, the stars Milo gives, and a short note with each star (a few words). The voice service may keep short text of what was said in its logs for a little while to fix problems. OpenAI doesn't use it to train its models by default."),
        ("switch.2", "You're in charge",
         "Reading and read-aloud work without Milo. You can turn Milo off any time in the Grown-ups corner. Your choice counts on every iPad and iPhone signed in with this email."),
    ]

    private var regular: Bool { sizeClass == .regular }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: regular ? 22 : 18) {
                VStack(spacing: 10) {
                    Image(systemName: "bubble.left.and.text.bubble.right.fill")
                        .font(.system(size: regular ? 52 : 44))
                        .foregroundStyle(.tint)
                        .accessibilityHidden(true)
                    Text(Self.title)
                        .font(.title.bold())
                        .multilineTextAlignment(.center)
                    Text(confirming ? Self.alreadyOn : Self.intro)
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)

                ForEach(Self.points, id: \.title) { point in
                    HStack(alignment: .top, spacing: 14) {
                        Image(systemName: point.symbol)
                            .font(.title2)
                            .foregroundStyle(.tint)
                            .frame(width: 32)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(point.title).font(.headline)
                            Text(point.text)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }

                Link(destination: Self.privacyURL) {
                    Label("Read the privacy policy", systemImage: "hand.raised.fill")
                }
                .frame(maxWidth: .infinity)
            }
            .padding(regular ? 32 : 22)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .safeAreaInset(edge: .bottom) { choices }
        .interactiveDismissDisabled(busy)
    }

    /// Always on screen, however long the text: a grown-up shouldn't have to hunt for the choice.
    private var choices: some View {
        VStack(spacing: 10) {
            if let message {
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }
            Button {
                Task { await choose(true) }
            } label: {
                HStack {
                    if busy { ProgressView() }
                    Text(confirming ? "Keep Milo on" : "Allow Milo")
                }
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            Button {
                Task { await choose(false) }
            } label: {
                Text(confirming ? "Turn Milo off" : "Not now")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.bordered)
        }
        .disabled(busy)
        .padding(.horizontal, regular ? 32 : 22)
        .padding(.vertical, 12)
        .frame(maxWidth: 560)
        .frame(maxWidth: .infinity)
        .background(.bar)
    }

    /// Allowing saves the choice on the account first; "Not now" changes nothing, so Milo stays off until a
    /// grown-up allows it. Turning Milo off (when it was on) saves that for the account.
    private func choose(_ allow: Bool) async {
        if allow == account.miloAllowed || (!allow && !confirming) {
            onDone(account.miloAllowed)
            return
        }
        busy = true
        message = nil
        defer { busy = false }
        do {
            try await account.setMiloConsent(allow)
            onDone(account.miloAllowed)
        } catch let error as AccountError {
            message = error.message
        } catch {
            message = AccountError.offline.message
        }
    }
}

/// The child tapped "Talk to Milo" before a grown-up allowed Milo: the grown-up check, then ``MiloConsentView``.
struct MiloPermissionSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var passedGate = false

    init(passedGate: Bool = false) {
        _passedGate = State(initialValue: passedGate)
    }

    var body: some View {
        Group {
            if passedGate {
                MiloConsentView { _ in dismiss() }
            } else {
                ParentGateView(onPass: { withAnimation { passedGate = true } }, note: "Milo needs a grown-up's OK before talking.")
            }
        }
        .presentationDetents([.large])
        .passportSizing()
    }
}
