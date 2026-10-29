import SwiftUI

/// Grown-ups corner → "Arrange library": hide or show books and drag the shelf's sections, and the
/// books in each, into a new order. Changes save at once on this device and go to the account, so every
/// device signed in with the same email shows the same shelf. Hidden books just leave his shelf: the
/// account still has them, and his stars and badges for them stay.
struct ArrangeLibraryView: View {
    @Environment(LibraryStore.self) private var library
    @Environment(AccountStore.self) private var account
    @Environment(ContentStore.self) private var content
    @State private var confirmShowAll = false
    @State private var confirmRestore = false

    private var shelves: [CityShelf] {
        let books = account.shelf(content.books)
        return library.prefs.arrangedShelves(DemoLaunch.current?.leveled(books) ?? books)
    }

    var body: some View {
        let shelves = shelves
        ScrollViewReader { proxy in
            List {
                Section {
                    ForEach(shelves) { shelf in
                        sectionRow(shelf, in: shelves)
                    }
                    .onMove { from, to in library.moveSections(shelves.map(\.city), from: from, to: to) }
                } header: {
                    Text("Sections")
                } footer: {
                    Text("Drag the sections and books into the order you like. Hidden books leave the shelf but keep their stars and badges.")
                }

                ForEach(shelves) { shelf in
                    Section {
                        ForEach(shelf.books) { book in
                            bookRow(book, in: shelf).id(book.id)
                        }
                        .onMove { from, to in library.moveBooks(shelf.books.map(\.id), in: shelf.city, from: from, to: to) }
                    } header: {
                        Text(shelf.city)
                    }
                }

                Section {
                    Button("Show all books") { confirmShowAll = true }
                        .disabled(library.prefs.hiddenCount == 0)
                        .confirmationDialog("Show every hidden book again?", isPresented: $confirmShowAll, titleVisibility: .visible) {
                            Button("Show all books") { library.showAll() }
                        }
                    Button("Restore default order") { confirmRestore = true }
                        .disabled(!library.prefs.hasCustomOrder)
                        .confirmationDialog("Put the sections and books back in their usual order?", isPresented: $confirmRestore, titleVisibility: .visible) {
                            Button("Restore default order") { library.restoreOrder() }
                        } message: {
                            Text("Hidden books stay hidden.")
                        }
                } footer: {
                    Text("Changes count on every iPad and iPhone signed in with this email. New books show up in their usual place.")
                }
            }
            .environment(\.editMode, .constant(.active))
            .task {
                // Screenshots: `-demoArrange -demoShelf paris-food` scrolls to that book.
                guard let id = DemoLaunch.current?.shelfBook else { return }
                try? await Task.sleep(for: .milliseconds(600))
                proxy.scrollTo(id, anchor: .center)
            }
        }
        .navigationTitle("Arrange library")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func sectionRow(_ shelf: CityShelf, in shelves: [CityShelf]) -> some View {
        let hidden = shelf.books.filter { library.prefs.isHidden($0.id) }.count
        let index = shelves.firstIndex(of: shelf) ?? 0
        let detail = hidden == 0 ? Self.count(shelf.books.count) : "\(Self.count(shelf.books.count)), \(hidden) hidden"
        return HStack(spacing: 12) {
            FlagBadge(book: shelf.flagBook, height: 22)
                .frame(width: 40)
            Text(shelf.city).font(.body.weight(.semibold))
            Spacer()
            Text(detail).font(.subheadline).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(shelf.city) section")
        .accessibilityValue(detail)
        .accessibilityActions {
            if index > 0 {
                Button("Move up") { library.moveSections(shelves.map(\.city), from: [index], to: index - 1) }
            }
            if index < shelves.count - 1 {
                Button("Move down") { library.moveSections(shelves.map(\.city), from: [index], to: index + 2) }
            }
        }
    }

    private func bookRow(_ book: Book, in shelf: CityShelf) -> some View {
        let hidden = library.prefs.isHidden(book.id)
        let index = shelf.books.firstIndex(of: book) ?? 0
        let ids = shelf.books.map(\.id)
        let toggle = hidden ? "Show \(book.coverTitle)" : "Hide \(book.coverTitle)"
        return HStack(spacing: 12) {
            PhotoView(photo: book.coverPage.photo, tint: Color(hex: book.coverColor))
                .frame(width: 52, height: 40)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .saturation(hidden ? 0 : 1)
                .opacity(hidden ? 0.45 : 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(book.coverTitle)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(hidden ? .secondary : .primary)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    LevelBadge(level: book.readingLevel)
                        .saturation(hidden ? 0 : 1)
                        .opacity(hidden ? 0.6 : 1)
                    if hidden {
                        Text("Hidden")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer(minLength: 8)
            Button {
                withAnimation { library.setHidden(book.id, !hidden) }
            } label: {
                Image(systemName: hidden ? "eye.slash" : "eye")
                    .font(.title3)
                    .foregroundStyle(hidden ? Color.secondary : Color.accentColor)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(toggle)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(book.coverTitle)
        .accessibilityValue(hidden ? "Level \(book.readingLevel), hidden" : "Level \(book.readingLevel), shown")
        .accessibilityActions {
            Button(toggle) { library.setHidden(book.id, !hidden) }
            if index > 0 {
                Button("Move up") { library.moveBooks(ids, in: shelf.city, from: [index], to: index - 1) }
            }
            if index < ids.count - 1 {
                Button("Move down") { library.moveBooks(ids, in: shelf.city, from: [index], to: index + 2) }
            }
        }
    }

    private static func count(_ books: Int) -> String { books == 1 ? "1 book" : "\(books) books" }
}
