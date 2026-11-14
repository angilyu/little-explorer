import Foundation

enum ContentError: Error, Equatable {
    case missingBook(String)
    case emptyBook(String)
}

enum BookLibrary {
    /// Reading order of the books on the shelf when library.json is missing.
    static let order = ["paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "barcelona-park-guell", "barcelona-sagrada", "barcelona-casa-batllo", "barcelona-tibidabo", "barcelona-montjuic", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu", "chengdu", "longyan", "beijing", "beijing-great-wall", "beijing-forbidden-city", "beijing-summer-palace", "beijing-temple-of-heaven", "shanghai", "israel-tel-aviv", "israel-dead-sea", "israel-hebrew", "travel-flight", "travel-packing", "travel-passport", "travel-hotel", "travel-jetlag"]

    private struct Shelf: Decodable { let order: [String] }

    /// The shelf order from library.json (downloaded or bundled).
    static func order(files: ContentFiles) -> [String] {
        guard let data = files.data("library.json"), let shelf = try? JSONDecoder().decode(Shelf.self, from: data),
              !shelf.order.isEmpty else { return order }
        return shelf.order
    }

    /// The books on the shelf, preferring downloaded copies over bundled ones.
    static func load(files: ContentFiles = .shared) throws -> [Book] {
        try order(files: files).map { id in
            guard let data = files.data("\(id).json") else { throw ContentError.missingBook(id) }
            return try decode(data)
        }
    }

    static func decode(_ data: Data) throws -> Book {
        let book = try JSONDecoder().decode(Book.self, from: data)
        guard !book.pages.isEmpty else { throw ContentError.emptyBook(book.id) }
        return book
    }

    static func loadBundled(bundle: Bundle = .main, ids: [String] = order) throws -> [Book] {
        try ids.map { id in
            guard let url = bundle.url(forResource: id, withExtension: "json") else {
                throw ContentError.missingBook(id)
            }
            return try decode(Data(contentsOf: url))
        }
    }

    /// Books grouped by section for the shelf, in order of each section's first book. Names with the
    /// same ``sectionKey(_:)`` are one section, named after its first book.
    static func shelves(_ books: [Book]) -> [CityShelf] {
        var shelves: [CityShelf] = []
        var index: [String: Int] = [:]
        for book in books {
            let key = sectionKey(book.city)
            if let i = index[key] {
                shelves[i].books.append(book)
            } else {
                index[key] = shelves.count
                shelves.append(CityShelf(city: book.city, books: [book]))
            }
        }
        return shelves
    }

    /// Which section a name means: names that differ only in case, spacing or the kind of apostrophe
    /// ("Matt’s" and "matt's") are the same section.
    static func sectionKey(_ name: String) -> String {
        let text = String(name.precomposedStringWithCompatibilityMapping.map { apostrophes.contains($0) ? "'" : $0 })
        return text.split(whereSeparator: \.isWhitespace).joined(separator: " ").lowercased()
    }

    private static let apostrophes: Set<Character> = ["\u{2019}", "\u{2018}", "\u{02BC}", "\u{2032}", "\u{00B4}", "`"]

    /// Every book on an account's shelf: its catalog books, then its made books (the server already sent
    /// only the ones on its shelf). A made book in a section the catalog has ("paris", "Matt's") takes the
    /// catalog's name for it, so the shelf, "Arrange library" and the saved arrangement all agree.
    static func shelfBooks(catalog: [Book], made: [Book]) -> [Book] {
        let ids = Set(catalog.map(\.id))
        var names: [String: String] = [:]
        for book in catalog where names[sectionKey(book.city)] == nil {
            names[sectionKey(book.city)] = book.city
        }
        let added = made.filter { !ids.contains($0.id) }.map { book in
            var book = book
            let key = sectionKey(book.city)
            if let name = names[key] { book.city = name } else { names[key] = book.city }
            return book
        }
        return catalog + added
    }

    /// Lays shelves out in rows of `columns` covers: a city with several books gets its own row(s),
    /// while neighbouring one-book cities share a row so the whole shelf fits on screen.
    static func rows(_ shelves: [CityShelf], columns: Int) -> [[CityShelf]] {
        let columns = max(1, columns)
        var rows: [[CityShelf]] = []
        var pending: [CityShelf] = []
        for shelf in shelves {
            if shelf.books.count == 1 {
                pending.append(shelf)
                if pending.count == columns { rows.append(pending); pending = [] }
            } else {
                if !pending.isEmpty { rows.append(pending); pending = [] }
                rows.append([shelf])
            }
        }
        if !pending.isEmpty { rows.append(pending) }
        return rows
    }
}

struct CityShelf: Identifiable, Hashable {
    let city: String
    var books: [Book]
    /// Whose flag the section shows (the server's layout says); its first book otherwise.
    var flagId: String? = nil

    var id: String { city }
    var flagBook: Book { books.first { $0.id == flagId } ?? books[0] }
    /// The general Travel shelf (flights, packing, hotels…) rather than a place.
    var isTravel: Bool { books.allSatisfy(\.isTravel) }
}
