import SwiftUI

struct PageView: View {
    let book: Book
    let page: BookPage
    let pageNumber: Int
    let readAloud: ReadAloudController

    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var showCredit: Bool

    init(book: Book, page: BookPage, pageNumber: Int, readAloud: ReadAloudController) {
        self.book = book
        self.page = page
        self.pageNumber = pageNumber
        self.readAloud = readAloud
        let demo = DemoLaunch.current
        _showCredit = State(initialValue: demo?.showCredit == true && book.isMade && demo?.pageIndex == pageNumber - 1)
    }

    private var regular: Bool { sizeClass == .regular }
    private var tint: Color { Color(hex: book.coverColor) }
    private var readingId: String { NarrationLibrary.key(bookId: book.id, pageId: page.id) }

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height * 1.15
            ZStack {
                Theme.paper
                if wide {
                    HStack(spacing: regular ? 36 : 20) {
                        photoBlock
                            .frame(width: geo.size.width * 0.52)
                        textBlock
                    }
                    .padding(regular ? 36 : 18)
                } else {
                    VStack(spacing: regular ? 28 : 16) {
                        photoBlock
                            .frame(height: geo.size.height * (regular ? 0.58 : 0.52))
                        textBlock
                    }
                    .padding(regular ? 32 : 16)
                }
            }
        }
        // A plain `.sheet`: this page lives in PageCurlView's own UIHostingController, outside the app's
        // environment, so `appSheet` couldn't read the stores here, and the credit sheet needs none.
        .sheet(isPresented: $showCredit) {
            PhotoCreditSheet(photo: page.photo)
        }
    }

    private var photoBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            PhotoView(photo: page.photo, tint: tint)
                .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .strokeBorder(.white, lineWidth: 6)
                )
                .overlay(alignment: .bottomTrailing) {
                    // Made books keep the page clean: the credit is one tap away (and listed in the Grown-ups corner).
                    if book.isMade, page.photo.hasCredit { creditButton }
                }
                .shadow(color: .black.opacity(0.12), radius: 8, x: 0, y: 4)
            if !book.isMade {
                Button {
                    showCredit = true
                } label: {
                    Text(page.photo.creditLine)
                        .font(.system(size: regular ? 13 : 11, weight: .medium))
                        .foregroundStyle(Theme.ink.opacity(0.55))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Photo credit. \(page.photo.creditLine)")
            }
        }
    }

    /// A small "i" in the photo's corner (where the photo is usually its blurred fill) that opens the credit.
    private var creditButton: some View {
        Button {
            showCredit = true
        } label: {
            Image(systemName: "info.circle")
                .font(.system(size: regular ? 20 : 17, weight: .semibold))
                .foregroundStyle(Theme.ink.opacity(0.7))
                .frame(width: regular ? 32 : 28, height: regular ? 32 : 28)
                .background(Circle().fill(.white.opacity(0.75)))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(regular ? 8 : 6)
        .accessibilityLabel("Photo credit")
        .accessibilityHint(page.photo.creditLine)
    }

    private var textBlock: some View {
        let reading = readAloud.isSpeaking(pageId: readingId)
        return VStack(spacing: 12) {
            Spacer(minLength: 0)
            Text(Self.highlighted(page.text, range: reading ? readAloud.spokenRange : nil, tint: tint))
                .font(Theme.readingFont(regular: regular))
                .foregroundStyle(Theme.ink)
                .multilineTextAlignment(.center)
                .lineSpacing(regular ? 10 : 6)
                .minimumScaleFactor(0.5)
                .frame(maxWidth: .infinity)
                .accessibilityAddTraits(.isHeader)
                .animation(.easeOut(duration: 0.12), value: readAloud.spokenRange)
            Spacer(minLength: 0)
            HStack {
                Spacer()
                ReadAloudButton(isReading: reading, tint: tint, size: regular ? 84 : 68) {
                    readAloud.toggle(pageId: readingId, text: page.text)
                }
            }
        }
    }

    /// The sentence with the word being spoken picked out, so he can follow along.
    static func highlighted(_ text: String, range: NSRange?, tint: Color) -> AttributedString {
        var result = AttributedString(text)
        guard let range, let swiftRange = Range(range, in: text),
              let lower = AttributedString.Index(swiftRange.lowerBound, within: result),
              let upper = AttributedString.Index(swiftRange.upperBound, within: result) else { return result }
        result[lower..<upper].backgroundColor = Color.yellow.opacity(0.75)
        result[lower..<upper].foregroundColor = tint
        return result
    }
}

/// Big speaker button that reads the page sentence aloud; tap again to stop.
struct ReadAloudButton: View {
    let isReading: Bool
    let tint: Color
    var size: CGFloat = 72
    let action: () -> Void

    var body: some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            action()
        } label: {
            Image(systemName: isReading ? "stop.fill" : "speaker.wave.2.fill")
                .font(.system(size: size * (isReading ? 0.38 : 0.44), weight: .bold))
                .foregroundStyle(isReading ? Color.white : tint)
                .contentTransition(.symbolEffect(.replace))
                .frame(width: size, height: size)
                .background(Circle().fill(isReading ? tint : Color.white).shadow(color: .black.opacity(0.15), radius: 6, x: 0, y: 3))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isReading ? "Stop reading" : "Read this page to me")
    }
}

struct PhotoView: View {
    let photo: PhotoInfo
    let tint: Color

    var body: some View {
        Color.clear
            .overlay { content }
            .clipped()
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var content: some View {
        if let file = photo.file, let image = ContentFiles.shared.image(named: file) {
            framed(Image(uiImage: image))
        } else if let url = photo.remoteURL {
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    framed(image)
                } else {
                    placeholder
                }
            }
        } else {
            placeholder
        }
    }

    /// Shows the whole photo (tall towers stay uncropped) over a soft blurred fill of itself.
    private func framed(_ image: Image) -> some View {
        ZStack {
            image.resizable().scaledToFill().blur(radius: 24).opacity(0.7)
            image.resizable().scaledToFit()
        }
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [tint.opacity(0.85), tint.opacity(0.45)], startPoint: .topLeading, endPoint: .bottomTrailing)
            Image(systemName: "photo")
                .font(.system(size: 80, weight: .semibold))
                .foregroundStyle(.white.opacity(0.8))
        }
    }
}

struct PhotoCreditSheet: View {
    let photo: PhotoInfo
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Photo") {
                    PhotoCreditRows(photo: photo, opensLinks: false)
                }
                Section {
                    Text("Photos come from Wikimedia Commons, Flickr and other sites that share photos under free licenses. Links open in the grown-ups corner.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Photo credit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

/// One photo's full credit: its title, who took it, the license (with its link) and where it's from.
/// Links are tappable only behind the parent gate (`opensLinks`); on a child's page they're just shown.
struct PhotoCreditRows: View {
    let photo: PhotoInfo
    let opensLinks: Bool

    var body: some View {
        if let title = photo.displayTitle {
            LabeledContent("Title", value: title)
        }
        LabeledContent("By", value: photo.author?.isEmpty == false ? photo.author! : "Unknown author")
        LabeledContent("License", value: photo.license?.isEmpty == false ? photo.license! : "License pending")
        if let license = photo.licenseLink {
            link("License", license)
        }
        if let source = photo.source {
            link(photo.sourceLabel, source)
        }
    }

    @ViewBuilder
    private func link(_ label: String, _ url: URL) -> some View {
        if opensLinks {
            Link(destination: url) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(label)
                    Text(url.absoluteString).font(.caption).lineLimit(1).truncationMode(.middle)
                }
            }
            .accessibilityHint("Opens \(url.host() ?? "the page") in Safari")
        } else {
            VStack(alignment: .leading, spacing: 2) {
                Text(label).foregroundStyle(.secondary)
                Text(url.absoluteString)
                    .font(.footnote)
                    .textSelection(.enabled)
            }
            .accessibilityElement(children: .combine)
        }
    }
}
