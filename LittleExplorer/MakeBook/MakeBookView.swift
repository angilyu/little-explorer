import SwiftUI

/// What each reading level means, for the grown-up choosing one (the server's levels.py has the rules).
struct MakeBookLevel: Identifiable {
    let level: Int
    let ages: String
    let name: String
    let detail: String
    let example: String

    var id: Int { level }
    var title: String { "L\(level) · \(ages) · \(name)" }

    static let all = [
        MakeBookLevel(level: 1, ages: "Ages 4–5", name: "Just starting",
                      detail: "One short sentence per page, easy words that repeat.",
                      example: "Look! A big tower."),
        MakeBookLevel(level: 2, ages: "Ages 5–7", name: "Early reader",
                      detail: "One or two simple sentences, each with a fun place or food.",
                      example: "The Eiffel Tower sparkles every night."),
        MakeBookLevel(level: 3, ages: "Ages 7–9", name: "Reading alone",
                      detail: "Two or three longer sentences, with one new \u{201C}wow\u{201D} word explained.",
                      example: "Workers paint the tower every seven years, so it never gets rusty."),
    ]
}

/// Grown-ups only (behind the parent gate): make a new book for the shelf.
struct MakeBookView: View {
    /// The shelf's sections, in shelf order, to put the new book in.
    let sections: [String]

    static let firstTimeNote = "Your words are sent to OpenAI to write the book. Photos are real, licensed photos. The text is written by AI and checked automatically."
    static let noteSeenKey = "makeBook.noteSeen"

    @Environment(BookMaker.self) private var maker
    @State private var level: Int?
    @State private var section = ""
    @State private var title = ""
    @State private var about = ""
    @State private var sending = false
    @State private var error: MakeError?
    @State private var started: MakeJob?
    @State private var showNote = false
    @State private var pickingSection = false
    @State private var didApplyDemo = false

    private var fields: MakeStatus.Fields { maker.status?.fields ?? MakeStatus.Fields() }
    private var trimmedTitle: String { title.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedSection: String { section.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var busy: MakeJob? { maker.placeholders.first }
    private var limit: String? { maker.usage.limitMessage }
    private var canSend: Bool {
        level != nil && !trimmedTitle.isEmpty && !trimmedSection.isEmpty && !sending && busy == nil && limit == nil
            && maker.status?.ready != false
    }

    var body: some View {
        ScrollViewReader { proxy in
            form
                .onAppear {
                    applyDemo()
                    if [.error, .limit].contains(DemoLaunch.current?.make) {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { withAnimation { proxy.scrollTo("send", anchor: .top) } }
                    }
                }
        }
    }

    private var form: some View {
        Form {
            levelSection
            Section {
                Button {
                    pickingSection = true
                } label: {
                    HStack {
                        Text("Section")
                        Spacer()
                        Text(trimmedSection.isEmpty ? "Choose" : trimmedSection)
                            .foregroundStyle(.secondary)
                        Image(systemName: "chevron.right")
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.tertiary)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityValue(trimmedSection.isEmpty ? "Not chosen" : trimmedSection)
                .accessibilityHint("Pick a section on the shelf or type a new one")
            } header: {
                Text("Where it goes on the shelf")
            }
            Section {
                TextField("For example: The Tall Tower", text: $title)
                    .textInputAutocapitalization(.words)
                    .onChange(of: title) { _, new in if new.count > fields.titleMax { title = String(new.prefix(fields.titleMax)) } }
            } header: {
                Text("Book name")
            } footer: {
                Text("\(title.count)/\(fields.titleMax)")
            }
            Section {
                TextField("For example: how the Eiffel Tower was built, and its lights at night", text: $about, axis: .vertical)
                    .lineLimit(3...6)
                    .onChange(of: about) { _, new in if new.count > fields.aboutMax { about = String(new.prefix(fields.aboutMax)) } }
            } header: {
                Text("What should it be about? (optional)")
            } footer: {
                Text("\(about.count)/\(fields.aboutMax) · Please leave out names, addresses and other personal details.")
            }
            sendSection
        }
        .navigationTitle("Make a book")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(isPresented: $pickingSection) {
            SectionPickerView(sections: sections, section: $section, maxLength: fields.sectionMax)
        }
        .sheet(isPresented: $showNote) {
            FirstTimeNoteView {
                UserDefaults.standard.set(true, forKey: Self.noteSeenKey)
                showNote = false
            }
            .presentationDetents([.medium])
        }
        .task {
            if DemoLaunch.current == nil, !UserDefaults.standard.bool(forKey: Self.noteSeenKey) { showNote = true }
            await maker.refresh()
        }
    }

    // MARK: - Level

    private var levelSection: some View {
        Section {
            ForEach(MakeBookLevel.all) { choice in
                Button {
                    level = choice.level
                } label: {
                    HStack(alignment: .top, spacing: 12) {
                        LevelBadge(level: choice.level)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(choice.title)
                                .font(.headline)
                                .foregroundStyle(.primary)
                            Text(choice.detail)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                            Text("Example: \u{201C}\(choice.example)\u{201D}")
                                .font(.subheadline.italic())
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 4)
                        Image(systemName: level == choice.level ? "checkmark.circle.fill" : "circle")
                            .font(.title2)
                            .foregroundStyle(level == choice.level ? Color.accentColor : Color.secondary.opacity(0.5))
                    }
                    .padding(.vertical, 4)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(level == choice.level ? [.isButton, .isSelected] : .isButton)
            }
        } header: {
            Text("Reading level")
        } footer: {
            Text("Pick the level your child reads at. The level shows on the book's cover.")
        }
    }

    // MARK: - Make

    @ViewBuilder
    private var sendSection: some View {
        Section {
            if let started {
                Label {
                    Text("Making \u{201C}\(started.title)\u{201D}. It shows on the shelf with its progress, and opens when it's ready (usually a few minutes).")
                } icon: {
                    Image(systemName: "sparkles").foregroundStyle(.orange)
                }
            } else if let busy {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Making \u{201C}\(busy.title)\u{201D}: \(busy.progressText) You can make the next book when it's ready.")
                }
            }
            if let error {
                Label(error.message, systemImage: error.isLimit ? "hourglass" : "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
            } else if let limit, started == nil {
                Label(limit, systemImage: "hourglass")
                    .foregroundStyle(.orange)
            } else if maker.status?.ready == false {
                Label("Book making isn't available right now. Please try again later.", systemImage: "moon.zzz")
                    .foregroundStyle(.secondary)
            }
            Button {
                Task { await send() }
            } label: {
                HStack {
                    Text("Make the book").bold()
                    Spacer()
                    if sending { ProgressView() }
                }
            }
            .disabled(!canSend)
            LabeledContent("Left to make", value: maker.usage.summary)
                .font(.subheadline)
                .id("send")
        } footer: {
            Text(Self.firstTimeNote + " A book that can't be made doesn't count toward the limit.")
        }
    }

    private func send() async {
        guard let level else { return }
        sending = true
        error = nil
        defer { sending = false }
        do {
            started = try await maker.make(
                title: trimmedTitle, section: trimmedSection, level: level,
                about: about.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            title = ""
            about = ""
        } catch let refused as MakeError {
            error = refused
        } catch {
            self.error = .offline
        }
    }

    private func applyDemo() {
        guard !didApplyDemo, let make = DemoLaunch.current?.make else { return }
        didApplyDemo = true
        switch make {
        case .note:
            showNote = true
        case .sections:
            section = "Paris"
            pickingSection = true
        case .error:
            level = 2
            section = "Dinosaurs"
            title = "Dino Fight Night"
            about = "Dinosaurs fighting and getting hurt"
            error = MakeError(code: "rejected", message: "This book couldn't be made: the idea isn't right for children. It didn't count toward your limit. Try a different idea.")
        case .limit:
            level = 1
            section = "Paris"
            title = "Paris by Boat"
        default:
            level = 1
            section = "Paris"
            title = "The Tall Tower"
            about = "How the Eiffel Tower was built, and its lights at night"
        }
    }
}

/// Pick one of the shelf's sections, or type a new one.
struct SectionPickerView: View {
    let sections: [String]
    @Binding var section: String
    var maxLength = 30

    @Environment(\.dismiss) private var dismiss
    @State private var newName = ""

    var body: some View {
        List {
            Section {
                TextField("For example: Our Trips", text: $newName)
                    .textInputAutocapitalization(.words)
                    .submitLabel(.done)
                    .onChange(of: newName) { _, new in
                        if new.count > maxLength { newName = String(new.prefix(maxLength)) }
                    }
                    .onSubmit(useNewName)
                if !newName.trimmingCharacters(in: .whitespaces).isEmpty {
                    Button("Use \u{201C}\(newName.trimmingCharacters(in: .whitespaces))\u{201D}", action: useNewName)
                }
            } header: {
                Text("New section")
            } footer: {
                Text("A new section starts its own row on the shelf.")
            }
            Section("Sections on the shelf") {
                ForEach(sections, id: \.self) { name in
                    Button {
                        section = name
                        dismiss()
                    } label: {
                        HStack {
                            Text(name).foregroundStyle(.primary)
                            Spacer()
                            if name == section {
                                Image(systemName: "checkmark").foregroundStyle(Color.accentColor)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(name == section ? .isSelected : [])
                }
            }
        }
        .navigationTitle("Section")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            if !section.isEmpty, !sections.contains(section) { newName = section }
        }
    }

    private func useNewName() {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        section = name
        dismiss()
    }
}

/// Shown the first time a grown-up opens Make a book: what is sent where.
struct FirstTimeNoteView: View {
    let done: () -> Void

    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "sparkles")
                .font(.system(size: 44))
                .foregroundStyle(.orange)
            Text("Before you make a book")
                .font(.title2.bold())
            Text(MakeBookView.firstTimeNote)
                .font(.body)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            Text("Made books show a small \u{201C}Made with AI\u{201D} label and are only on this account's shelf.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            Button(action: done) {
                Text("OK").bold().frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
        .padding(28)
        .frame(maxWidth: 520)
    }
}
