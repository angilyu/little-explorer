import SwiftUI

/// Parent-only screen: turning his stars into coins, what he loved, voice server settings, and
/// photo credits with links.
struct ParentCornerView: View {
    let books: [Book]

    @Environment(HeartStore.self) private var hearts
    @Environment(CoinStore.self) private var coins
    @Environment(\.dismiss) private var dismiss
    @Environment(ContentStore.self) private var content
    @Environment(AccountStore.self) private var account
    @State private var confirmSignOut = false
    @State private var showDelete = false
    @AppStorage(VoiceSettings.tokenServerKey) private var tokenServer = VoiceSettings.defaultTokenServer
    @State private var device: DeviceInfo?
    @State private var coinsLoading = false
    @State private var coinsReached = true
    @State private var coinsToMake = 1
    @State private var confirmConvert = false
    @State private var converting = false
    @State private var coinMessage: String?

    var body: some View {
        NavigationStack {
            List {
                accountSection
                coinSection

                Section("Loved pages") {
                    let total = books.reduce(0) { $0 + hearts.lovedCount(bookId: $1.id) }
                    if total == 0 {
                        Text("No hearts yet. When he taps the heart on a page, it shows up here.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(books) { book in
                        let loved = hearts.lovedPages(in: book)
                        if !loved.isEmpty {
                            DisclosureGroup("\(book.shelfTitle) · \(loved.count)") {
                                ForEach(loved) { page in
                                    HStack(alignment: .top, spacing: 12) {
                                        PhotoView(photo: page.photo, tint: Color(hex: book.coverColor))
                                            .frame(width: 56, height: 56)
                                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                                        Text(page.text)
                                    }
                                    .padding(.vertical, 4)
                                }
                            }
                        }
                    }
                }

                Section {
                    TextField("Token server URL", text: $tokenServer)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button("Reset to default") { tokenServer = VoiceSettings.defaultTokenServer }
                    if let device {
                        LabeledContent("This device") {
                            Text(device.id)
                                .font(.caption.monospaced())
                                .textSelection(.enabled)
                        }
                        Button("Copy device id") { UIPasteboard.general.string = device.id }
                    }
                } header: {
                    Text("Voice buddy (Milo)")
                } footer: {
                    Text("Milo's server hands out voice tokens and new books. The default is the hosted server; for a server on your Mac use http://127.0.0.1:8787 (simulator) or the Mac's address, e.g. http://192.168.1.20:8787. Audio is not recorded. Milo's minutes are counted per device: approve this device with `tools/devices.py approve <id>` for the longer daily limit.")
                }

                Section {
                    LabeledContent("Books version", value: content.version.map { String($0.prefix(8)) } ?? "Built in")
                    LabeledContent("Last check", value: statusText)
                    Button("Check for new books") { Task { await content.refresh(force: true) } }
                        .disabled(content.status == .checking)
                } header: {
                    Text("Books")
                } footer: {
                    Text("New and updated books download from Milo's server when the app opens, so they don't need an app update.")
                }

                Section("Photo credits") {
                    ForEach(books) { book in
                        DisclosureGroup(book.shelfTitle) {
                            ForEach(book.pages) { page in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(page.photo.author ?? "Unknown author").font(.subheadline.bold())
                                    Text(page.photo.license ?? "License pending").font(.caption)
                                    if let source = page.photo.source {
                                        Link(source.absoluteString, destination: source)
                                            .font(.caption)
                                            .lineLimit(1)
                                            .truncationMode(.middle)
                                    }
                                }
                                .padding(.vertical, 2)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Grown-ups corner")
            .task {
                device = DeviceIdentity.current()
                await loadCoins()
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }

    // MARK: - Account

    @ViewBuilder
    private var accountSection: some View {
        if let signedIn = account.account {
            Section {
                LabeledContent("Signed in as", value: signedIn.maskedEmail)
                LabeledContent("Books", value: signedIn.books == nil ? "All books" : "\(signedIn.shelf(content.books).count) books")
                Button("Sign out", role: .destructive) { confirmSignOut = true }
                    .confirmationDialog("Sign out of this device?", isPresented: $confirmSignOut, titleVisibility: .visible) {
                        Button("Sign out", role: .destructive) {
                            Task {
                                await account.signOut()
                                dismiss()
                            }
                        }
                    } message: {
                        Text("Stars, coins and hearts stay in the account. Sign in again with the same email to get them back.")
                    }
                Button("Delete account…", role: .destructive) { showDelete = true }
                    .foregroundStyle(.secondary)
            } header: {
                Text("Account")
            } footer: {
                Text("Every iPad and iPhone signed in with this email shares the same books, stars, coins and hearts. The owner can add more books to the account.")
            }
            .sheet(isPresented: $showDelete) {
                DeleteAccountView().environment(account)
            }
        }
    }

    // MARK: - Stars and coins

    @ViewBuilder
    private var coinSection: some View {
        Section {
            if let state = coins.state {
                LabeledContent("Stars to spend") {
                    HStack(spacing: 6) {
                        Image(systemName: "star.fill").foregroundStyle(Color.starGold)
                        Text("\(state.stars)")
                    }
                }
                LabeledContent("Coins") {
                    HStack(spacing: 6) {
                        CoinIcon(size: 18)
                        Text("\(state.coins)")
                    }
                }
                if !state.enabled {
                    Text("Converting is turned off on Milo's server right now.")
                        .foregroundStyle(.secondary)
                } else if state.canConvert == 0 {
                    Text(notEnoughText(state))
                        .foregroundStyle(.secondary)
                } else {
                    let making = min(max(coinsToMake, state.minCoins), state.canConvert)
                    LabeledContent("Can make", value: "up to \(state.canConvert) \(state.canConvert == 1 ? "coin" : "coins")")
                    if state.canConvert > state.minCoins {
                        Stepper(value: $coinsToMake, in: state.minCoins...state.canConvert) {
                            Text("Make \(making) \(making == 1 ? "coin" : "coins")")
                        }
                        HStack(spacing: 10) {
                            ForEach(Self.quickChoices(min: state.minCoins, max: state.canConvert), id: \.self) { amount in
                                quickChoice(amount, all: amount == state.canConvert, selected: amount == making)
                            }
                        }
                    }
                    LabeledContent("Uses", value: "\(making * state.starsPerCoin) stars")
                    LabeledContent("Stars left after", value: "\(state.stars - making * state.starsPerCoin)")
                    Button {
                        confirmConvert = true
                    } label: {
                        HStack {
                            Text("Convert \(making * state.starsPerCoin) stars to \(making) \(making == 1 ? "coin" : "coins")")
                            Spacer()
                            if converting { ProgressView() }
                        }
                    }
                    .disabled(converting)
                    .confirmationDialog(
                        "Turn \(making * state.starsPerCoin) stars into \(making) \(making == 1 ? "coin" : "coins")?",
                        isPresented: $confirmConvert, titleVisibility: .visible
                    ) {
                        Button("Convert") { Task { await convert(making, rate: state.starsPerCoin) } }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("He keeps his badges. His star count goes down by \(making * state.starsPerCoin) and he gets \(making) \(making == 1 ? "coin" : "coins").")
                    }
                }
            } else if coinsLoading {
                HStack {
                    ProgressView()
                    Text("Asking Milo's server…").foregroundStyle(.secondary)
                }
            } else if !coinsReached {
                Text("Couldn't reach Milo's server.")
                    .foregroundStyle(.secondary)
                Button("Try again") { Task { await loadCoins() } }
            }
            if let coinMessage {
                Text(coinMessage)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Stars and coins")
        } footer: {
            Text(coinFooter)
        }
    }

    /// Shortcuts next to the stepper: a few round amounts and "All", only ones his stars cover.
    static func quickChoices(min: Int, max: Int) -> [Int] {
        guard max > min else { return [] }
        let round = [min, 5, 10, 25, 50, 100].filter { $0 >= min && $0 < max }
        return Array(Set(round)).sorted().prefix(3) + [max]
    }

    @ViewBuilder
    private func quickChoice(_ amount: Int, all: Bool, selected: Bool) -> some View {
        let title = all ? "All \(amount)" : "\(amount)"
        if selected {
            Button(title) { coinsToMake = amount }
                .buttonStyle(.borderedProminent)
                .accessibilityLabel(all ? "All \(amount) coins, selected" : "\(amount) coins, selected")
        } else {
            Button(title) { coinsToMake = amount }
                .buttonStyle(.bordered)
                .accessibilityLabel(all ? "All \(amount) coins" : "\(amount) coins")
        }
    }

    private var coinFooter: String {
        let rate = coins.state?.starsPerCoin ?? 10
        var text = "Every \(rate) stars he earns can become 1 coin. Convert as many as his stars cover, in one go or a few at a time. Only grown-ups can convert them, here."
        if let state = coins.state, state.maxCoins > 0 {
            text += " Up to \(state.maxCoins) \(state.maxCoins == 1 ? "coin" : "coins") at a time."
        }
        return text + " Milo's server sets the rate and keeps his coins, so they survive a reinstall. Badges keep counting every star he ever earned."
    }

    private func notEnoughText(_ state: CoinState) -> String {
        let need = state.minCoins * state.starsPerCoin
        let what = state.minCoins == 1 ? "1 coin" : "\(state.minCoins) coins"
        return "\(need) stars make \(what). \(state.starsToGo) more \(state.starsToGo == 1 ? "star" : "stars") to go."
    }

    private func loadCoins() async {
        coinsLoading = true
        coinsReached = await coins.refresh(using: CoinStore.service(token: account.token))
        coinsLoading = false
        if let state = coins.state { coinsToMake = state.canConvert }
    }

    private func convert(_ amount: Int, rate: Int) async {
        converting = true
        defer { converting = false }
        do {
            let made = try await coins.convert(coins: amount, starsPerCoin: rate, using: CoinStore.service(token: account.token))
            let count = made?.coins ?? amount
            coinMessage = "Done! \(count * rate) stars became \(count) \(count == 1 ? "coin" : "coins")."
        } catch let error as CoinError {
            coinMessage = error.message
        } catch {
            coinMessage = "Couldn't reach Milo's server. Try again; it won't convert twice."
        }
        if let state = coins.state { coinsToMake = state.canConvert }
    }

    private var statusText: String {
        switch content.status {
        case .idle: "Not yet"
        case .checking: "Checking…"
        case .upToDate: "Up to date"
        case let .updated(count): "Updated (\(count) new files)"
        case .failed: "Couldn't reach the server"
        }
    }
}
