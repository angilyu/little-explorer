import SwiftUI

/// The small "Made with AI" label on a made book's cover.
struct MadeWithAILabel: View {
    var regular = true

    var body: some View {
        Label("Made with AI", systemImage: "sparkles")
            .font(.system(size: regular ? 14 : 10, weight: .bold, design: .rounded))
            .labelStyle(.titleAndIcon)
            .foregroundStyle(.white)
            .padding(.horizontal, regular ? 10 : 7)
            .padding(.vertical, regular ? 5 : 3)
            .background(Capsule().fill(.black.opacity(0.6)))
            .lineLimit(1)
            .fixedSize()
    }
}

/// The cover a book being made shows on the shelf until it's ready: its name and how far along it is.
struct PlaceholderCoverView: View {
    let book: Book
    var regular = true

    /// The server's progress words, in order (bookgen/store.py `STEP_WORDS`); the tagline is the current one.
    static let steps = ["Writing…", "Finding photos…", "Recording…"]
    private var step: Int { Self.steps.firstIndex(of: book.tagline) ?? (book.tagline == "Almost ready…" ? Self.steps.count : 0) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack {
                LinearGradient(colors: [Color(hex: "#B8C4D6"), Color(hex: "#8E9AAF")], startPoint: .topLeading, endPoint: .bottomTrailing)
                VStack(spacing: regular ? 12 : 6) {
                    Image(systemName: "sparkles")
                        .font(.system(size: regular ? 44 : 26, weight: .bold))
                        .foregroundStyle(.white)
                        .symbolEffect(.pulse, options: .repeating)
                    HStack(spacing: regular ? 6 : 4) {
                        ForEach(0..<Self.steps.count, id: \.self) { index in
                            Capsule()
                                .fill(.white.opacity(index <= step ? 0.95 : 0.35))
                                .frame(width: regular ? 34 : 20, height: regular ? 8 : 5)
                        }
                    }
                }
            }
            .frame(height: regular ? 210 : 100)
            .frame(maxWidth: .infinity)
            .overlay(alignment: .topLeading) {
                LevelBadge(level: book.readingLevel, regular: regular)
                    .padding(.leading, regular ? 24 : 15)
                    .padding(.top, regular ? 12 : 8)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(book.coverTitle)
                    .font(.system(size: regular ? 30 : 21, weight: .heavy, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                Text(book.tagline)
                    .font(.system(size: regular ? 17 : 13, weight: .semibold, design: .rounded))
                    .opacity(0.95)
                    .lineLimit(2, reservesSpace: true)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, regular ? 20 : 12)
            .padding(.vertical, regular ? 16 : 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(hex: book.coverColor))
        }
        .clipShape(RoundedRectangle(cornerRadius: regular ? 28 : 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: regular ? 28 : 20, style: .continuous)
                .strokeBorder(.white.opacity(0.8), style: StrokeStyle(lineWidth: 3, dash: [10, 8]))
        )
        .shadow(color: .black.opacity(0.12), radius: 8, x: 0, y: 6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Making the book \(book.coverTitle)")
        .accessibilityValue(book.tagline)
    }
}

/// The Grown-ups corner's part for made books: "Make a book", books being made, and each made book's menu.
struct MadeBooksSection: View {
    /// Every book on the shelf, for the sections a new book can go in.
    let books: [Book]

    @Environment(BookMaker.self) private var maker
    @Environment(MadeBookLibrary.self) private var made
    @Environment(LibraryStore.self) private var library
    @Environment(AccountStore.self) private var account

    private var canMake: Bool { account.account?.canGenerate == true || maker.canMake }

    var body: some View {
        if canMake || !made.books.isEmpty {
            Section {
                if canMake {
                    NavigationLink {
                        MakeBookView(sections: Self.sections(books))
                    } label: {
                        LabeledContent {
                            Text(maker.status.map { "\($0.usage.left) left today" } ?? "")
                        } label: {
                            Label("Make a book", systemImage: "sparkles")
                        }
                    }
                }
                ForEach(maker.active) { job in
                    HStack(spacing: 10) {
                        ProgressView()
                        VStack(alignment: .leading, spacing: 2) {
                            Text(job.isPage ? "Making a page of \u{201C}\(job.title)\u{201D} again" : "Making \u{201C}\(job.title)\u{201D}")
                            Text(job.progressText).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
                if let failed = maker.lastFailure, let message = failed.message {
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\u{201C}\(failed.title)\u{201D}").bold()
                            Text(message)
                        }
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                    }
                    .foregroundStyle(.red)
                }
                ForEach(made.books) { book in
                    NavigationLink {
                        MadeBookMenuView(book: book)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(book.coverTitle)
                            Text(subtitle(book))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                Text("Made books")
            } footer: {
                Text("Books you make are written by AI, checked automatically and illustrated with real, licensed photos. They're on this account's shelf only, with a \u{201C}Made with AI\u{201D} label.")
            }
        }
    }

    private func subtitle(_ book: Book) -> String {
        var parts = ["Level \(book.readingLevel)", book.city]
        if library.prefs.isHidden(book.id) { parts.append("Hidden") }
        if !maker.ownBooks.contains(book.id) { parts.append("Shared with this account") }
        return parts.joined(separator: " · ")
    }

    /// The shelf's sections in order, without repeats.
    static func sections(_ books: [Book]) -> [String] {
        var seen = Set<String>()
        return books.map(\.city).filter { seen.insert($0).inserted }
    }
}

/// The grown-ups' menu for one made book: make a page again, hide it, report it, delete it.
struct MadeBookMenuView: View {
    let book: Book

    @Environment(BookMaker.self) private var maker
    @Environment(LibraryStore.self) private var library
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDelete = false
    @State private var deleting = false
    @State private var message: String?

    private var own: Bool { maker.ownBooks.contains(book.id) }

    var body: some View {
        List {
            Section {
                HStack {
                    Spacer()
                    BookCoverView(book: book, regular: false)
                        .frame(width: 220)
                        .allowsHitTesting(false)
                    Spacer()
                }
                .listRowBackground(Color.clear)
                LabeledContent("Section", value: book.city)
                LabeledContent("Level", value: MakeBookLevel.all.first { $0.level == book.readingLevel }?.title ?? "Level \(book.readingLevel)")
                LabeledContent("Pages", value: "\(book.pages.count)")
            }
            Section {
                if let job = maker.remaking(book.id) {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Making a page again: \(job.progressText)")
                    }
                } else if own {
                    NavigationLink {
                        RemakePageView(book: book)
                    } label: {
                        Label("Make a page again…", systemImage: "arrow.triangle.2.circlepath")
                    }
                }
                Toggle(isOn: Binding(
                    get: { !library.prefs.isHidden(book.id) },
                    set: { library.setHidden(book.id, !$0) }
                )) {
                    Label("Show on the shelf", systemImage: "eye")
                }
                NavigationLink {
                    ReportBookView(book: book)
                } label: {
                    Label("Report a problem…", systemImage: "flag")
                }
                if own {
                    Button(role: .destructive) {
                        confirmDelete = true
                    } label: {
                        HStack {
                            Label("Delete book…", systemImage: "trash")
                            Spacer()
                            if deleting { ProgressView() }
                        }
                    }
                    .disabled(deleting)
                }
                if let message {
                    Text(message).foregroundStyle(.red)
                }
            } footer: {
                Text(own
                     ? "Hiding takes it off the shelf on this account's devices; you can show it again. Deleting removes it from every shelf it's on and still counts toward this month's limit."
                     : "The owner of Little Explorer shared this book with your account. You can hide it or tell the owner about a problem.")
            }
        }
        .navigationTitle(book.coverTitle)
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("Delete \u{201C}\(book.coverTitle)\u{201D}?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete book", role: .destructive) { Task { await delete() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It leaves every shelf it's on, with its stars kept. It still counts toward this month's limit.")
        }
    }

    private func delete() async {
        deleting = true
        message = nil
        defer { deleting = false }
        do {
            try await maker.delete(bookId: book.id)
            dismiss()
        } catch let error as MakeError {
            message = error.message
        } catch {
            message = MakeError.offline.message
        }
    }
}

/// Pick one page of a made book to write, illustrate and record again.
struct RemakePageView: View {
    let book: Book

    @Environment(BookMaker.self) private var maker
    @Environment(\.dismiss) private var dismiss
    @State private var picked: BookPage?
    @State private var sending = false
    @State private var message: String?

    private var left: Int { maker.usage.pageRedosLeft }

    var body: some View {
        List {
            if let message {
                Section { Label(message, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            }
            Section {
                ForEach(Array(book.pages.enumerated()), id: \.element.id) { index, page in
                    Button {
                        picked = page
                    } label: {
                        HStack(spacing: 12) {
                            PhotoView(photo: page.photo, tint: Color(hex: book.coverColor))
                                .frame(width: 64, height: 48)
                                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Page \(index + 1)").font(.caption).foregroundStyle(.secondary)
                                Text(page.text).foregroundStyle(.primary).lineLimit(3)
                            }
                        }
                    }
                    .disabled(sending || left == 0)
                }
            } footer: {
                Text("The page gets new words, a new photo and a new reading, checked like a new book. It doesn't use up a book. \(left) of \(maker.usage.pageRedosPerDay) page remakes left today.")
            }
        }
        .navigationTitle("Make a page again")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog(
            "Make this page again?", isPresented: Binding(get: { picked != nil }, set: { if !$0 { picked = nil } }),
            titleVisibility: .visible, presenting: picked
        ) { page in
            Button("Make it again") { Task { await remake(page) } }
            Button("Cancel", role: .cancel) {}
        } message: { page in
            Text(page.text)
        }
    }

    private func remake(_ page: BookPage) async {
        sending = true
        message = nil
        defer { sending = false }
        do {
            try await maker.remakePage(bookId: book.id, pageId: page.id)
            dismiss()
        } catch let error as MakeError {
            message = error.message
        } catch {
            message = MakeError.offline.message
        }
    }
}

/// Tell the owner something is wrong with a made book.
struct ReportBookView: View {
    let book: Book

    @Environment(BookMaker.self) private var maker
    @State private var reason: ReportReason = .wrong
    @State private var page = ""
    @State private var note = ""
    @State private var sending = false
    @State private var sent = false
    @State private var message: String?

    var body: some View {
        Form {
            Section("What's wrong?") {
                Picker("What's wrong?", selection: $reason) {
                    ForEach(ReportReason.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.inline)
                .labelsHidden()
            }
            Section {
                Picker("Page", selection: $page) {
                    Text("The whole book").tag("")
                    ForEach(Array(book.pages.enumerated()), id: \.element.id) { index, page in
                        Text("Page \(index + 1)").tag(page.id)
                    }
                }
                TextField("Anything else? (optional)", text: $note, axis: .vertical)
                    .lineLimit(2...5)
                    .onChange(of: note) { _, new in if new.count > 300 { note = String(new.prefix(300)) } }
            }
            Section {
                if sent {
                    Label("Thanks. The owner of Little Explorer will look at it. You can hide the book meanwhile.", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                } else {
                    Button {
                        Task { await send() }
                    } label: {
                        HStack {
                            Text("Send report").bold()
                            Spacer()
                            if sending { ProgressView() }
                        }
                    }
                    .disabled(sending)
                }
                if let message {
                    Text(message).foregroundStyle(.red)
                }
            } footer: {
                Text("The report goes to the owner of Little Explorer with this book's name.")
            }
        }
        .navigationTitle("Report a problem")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func send() async {
        sending = true
        message = nil
        defer { sending = false }
        do {
            try await maker.report(bookId: book.id, reason: reason, note: note.trimmingCharacters(in: .whitespacesAndNewlines), page: page.isEmpty ? nil : page)
            sent = true
        } catch let error as MakeError {
            message = error.message
        } catch {
            message = MakeError.offline.message
        }
    }
}
