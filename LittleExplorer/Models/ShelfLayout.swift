import Foundation

/// The shelf as the server lays it out (`GET`/`PUT /account/shelf`, see server/src/little_explorer_agent/shelf.py):
/// the sections in order, each with its books in order and whether each is hidden. The shelf and "Arrange
/// library" draw this, so the server decides the sections and their order (made books, merging section names,
/// saved arrangements). The app only matches it to the books it has: ids it doesn't have are skipped, and books
/// it has that the layout doesn't list (say, a catalog book newer than the layout) go where the device's own
/// arrangement (``LibraryPrefs/arrangedShelves(_:)``) puts them.
struct ShelfLayout: Codable, Equatable {
    struct Entry: Codable, Equatable {
        let id: String
        var hidden: Bool?
        /// A book still being made (its placeholder cover is `making-<job id>`).
        var making: Bool?
    }

    struct Section: Codable, Equatable {
        /// The server's section identity; the app only shows `name`.
        let key: String
        let name: String
        var flag: String?
        /// Whose flag the section shows.
        var flagBook: String?
        let books: [Entry]

        enum CodingKeys: String, CodingKey {
            case key, name, flag, books
            case flagBook = "flag_book"
        }
    }

    /// The layout format this app reads; a server sending a newer one is treated as having none.
    static let readable = 1

    var format: Int
    var sections: [Section]

    /// The hidden flag the layout gives `bookId`, or nil if it doesn't list the book.
    func isHidden(_ bookId: String) -> Bool? {
        for section in sections {
            if let entry = section.books.first(where: { $0.id == bookId }) { return entry.hidden == true }
        }
        return nil
    }

    /// `books` (hidden ones too) in the layout's sections and order, named as the layout names them; books it
    /// doesn't list are arranged by `fallback` and join the section with the same name, or come after.
    func shelves(_ books: [Book], fallback: LibraryPrefs) -> [CityShelf] {
        var byId: [String: Book] = [:]
        for book in books where byId[book.id] == nil { byId[book.id] = book }
        var used = Set<String>()
        var shelves: [CityShelf] = []
        for section in sections {
            var list: [Book] = []
            for entry in section.books {
                guard var book = byId[entry.id], used.insert(entry.id).inserted else { continue }
                book.city = section.name
                list.append(book)
            }
            shelves.append(CityShelf(city: section.name, books: list, flagId: section.flagBook))
        }
        for shelf in fallback.arrangedShelves(books.filter { !used.contains($0.id) }) {
            let key = BookLibrary.sectionKey(shelf.city)
            if let i = shelves.firstIndex(where: { BookLibrary.sectionKey($0.city) == key }) {
                shelves[i].books += shelf.books.map { var book = $0; book.city = shelves[i].city; return book }
            } else {
                shelves.append(shelf)
            }
        }
        return shelves.filter { !$0.books.isEmpty }
    }
}

/// What `GET`/`PUT /account/shelf` (or, on a server from before 1.2.6, `/account/library`) answers.
struct LibraryReply: Decodable, Equatable {
    var library: LibraryPrefs
    var shelf: ShelfLayout?

    init(library: LibraryPrefs, shelf: ShelfLayout? = nil) {
        self.library = library
        self.shelf = shelf
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        library = try c.decode(LibraryPrefs.self, forKey: .library)
        // A layout this app can't read (a newer format, or a bad one) counts as none: it lays the shelf out itself.
        shelf = (try? c.decodeIfPresent(ShelfLayout.self, forKey: .shelf))
            .flatMap { $0.format == ShelfLayout.readable ? $0 : nil }
    }

    enum CodingKeys: String, CodingKey { case library, shelf }
}
