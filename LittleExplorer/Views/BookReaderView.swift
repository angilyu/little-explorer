import SwiftUI

struct BookReaderView: View {
    let book: Book

    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(VoiceModeController.self) private var voice
    @Environment(ReadAloudController.self) private var readAloud
    @Environment(StarStore.self) private var stars
    @Environment(CoinStore.self) private var coins
    @Environment(AccountStore.self) private var account

    @State private var pageIndex: Int
    @State private var counterFrame: CGRect = .zero
    @State private var flight: Int?
    @State private var flying = false

    init(book: Book, startPage: Int = 0) {
        self.book = book
        _pageIndex = State(initialValue: min(max(startPage, 0), book.pages.count - 1))
    }

    private var regular: Bool { sizeClass == .regular }
    private var tint: Color { Color(hex: book.coverColor) }

    var body: some View {
        VStack(spacing: 0) {
            topBar
            PageCurlView(pageCount: book.pages.count, currentIndex: $pageIndex) { index in
                AnyView(
                    PageView(
                        book: book,
                        page: book.pages[index],
                        pageNumber: index + 1,
                        readAloud: readAloud
                    )
                )
            }
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay {
                if voice.isConnected {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .strokeBorder(Color.green.opacity(0.8), lineWidth: 5)
                        .allowsHitTesting(false)
                }
            }
            .overlay(alignment: .top) { VoiceNoticeBanner(voice: voice) }
            .animation(.spring(duration: 0.4), value: voice.notice)
            .padding(.horizontal, regular ? 24 : 10)
            bottomBar
        }
        .coordinateSpace(name: StarFlight.space)
        .onPreferenceChange(StarCounterFrameKey.self) { counterFrame = $0 }
        .overlay {
            if flight != nil {
                StarFlight(target: counterFrame, flying: flying)
                    .transition(.opacity)
            }
        }
        .onChange(of: stars.celebration) { _, _ in flyStar() }
        .animation(.spring(duration: 0.35), value: voice.isEnabled)
        .background(tint.opacity(0.18).ignoresSafeArea())
        .task { await voice.pageDidChange(PageContext(book: book, index: pageIndex)) }
        .onChange(of: pageIndex) { _, newIndex in
            readAloud.pageDidChange(to: NarrationLibrary.key(bookId: book.id, pageId: book.pages[newIndex].id))
            Task { await voice.pageDidChange(PageContext(book: book, index: newIndex)) }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                readAloud.stop()
                Task { await voice.turnOff() }
            }
        }
        .sheet(isPresented: Binding(
            get: { voice.needsConsent },
            set: { shown in if !shown { Task { await voice.consentHandled() } } }
        )) {
            MiloPermissionSheet(passedGate: DemoLaunch.current?.milo == .talkConsent)
                .environment(account)
        }
        .onDisappear { readAloud.stop() }
        .onAppear {
            readAloud.onSpeakingChanged = { [voice] reading in
                Task { await voice.appReadAloudDidChange(reading) }
            }
            if DemoLaunch.current?.startsWithVoice == true || DemoLaunch.current?.milo?.talks == true {
                Task { await voice.turnOn() }
            }
            if DemoLaunch.current?.readAloud == true {
                let page = book.pages[pageIndex]
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                    readAloud.toggle(pageId: NarrationLibrary.key(bookId: book.id, pageId: page.id), text: page.text)
                }
            }
        }
    }

    private var topBar: some View {
        HStack(spacing: regular ? 12 : 8) {
            Button {
                readAloud.stop()
                Task { await voice.bookDidClose() }
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 24, weight: .bold))
                    .frame(width: 56, height: 56)
                    .background(Circle().fill(.white))
                    .foregroundStyle(Theme.ink)
            }
            .accessibilityLabel("Close book")

            if regular || !voice.isEnabled {
                FlagBadge(book: book, height: regular ? 34 : 26)
            }
            VStack(alignment: .leading, spacing: 0) {
                Text(book.city)
                    .font(.system(size: regular ? 32 : 24, weight: .heavy, design: .rounded))
                if let theme = book.theme {
                    Text(theme)
                        .font(.system(size: regular ? 18 : 14, weight: .semibold, design: .rounded))
                        .opacity(0.7)
                }
            }
            .foregroundStyle(Theme.ink)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .layoutPriority(1)

            Spacer(minLength: 8)

            VoiceStatusView(voice: voice, compact: !regular)
            if voice.isEnabled {
                StarCounter(total: coins.spendable(fromEarned: stars.total), bounce: stars.celebration, compact: !regular)
            }
            VoiceToggleButton(voice: voice, compact: !regular)
        }
        .padding(.horizontal, regular ? 24 : 12)
        .padding(.vertical, 10)
    }

    /// A big star pops up over the page, then shrinks into the counter.
    private func flyStar() {
        let id = (flight ?? 0) + 1
        flying = false
        withAnimation(.spring(duration: 0.3)) { flight = id }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            guard flight == id else { return }
            withAnimation(.easeIn(duration: 0.7)) { flying = true }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) {
            guard flight == id else { return }
            withAnimation(.easeOut(duration: 0.2)) { flight = nil }
        }
    }

    private var bottomBar: some View {
        HStack {
            pageButton(systemImage: "arrow.left", label: "Previous page", enabled: pageIndex > 0) {
                pageIndex -= 1
            }
            Spacer()
            if pageIndex == book.pages.count - 1 {
                BookStarSummary(bookStars: stars.stars(inBook: book.id), total: coins.spendable(fromEarned: stars.total), regular: regular)
            } else {
                Text("\(pageIndex + 1) / \(book.pages.count)")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundStyle(Theme.ink.opacity(0.7))
                    .accessibilityLabel("Page \(pageIndex + 1) of \(book.pages.count)")
            }
            Spacer()
            pageButton(systemImage: "arrow.right", label: "Next page", enabled: pageIndex < book.pages.count - 1) {
                pageIndex += 1
            }
        }
        .padding(.horizontal, regular ? 32 : 16)
        .padding(.vertical, 10)
    }

    private func pageButton(systemImage: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 28, weight: .heavy))
                .frame(width: 72, height: 60)
                .background(Capsule().fill(enabled ? tint : Color.gray.opacity(0.3)))
                .foregroundStyle(.white)
        }
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}
