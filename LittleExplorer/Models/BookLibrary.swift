import Foundation

enum ContentError: Error, Equatable {
    case missingBook(String)
    case emptyBook(String)
}

enum BookLibrary {
    /// Reading order of the books on the shelf when library.json is missing.
    static let order = ["paris", "paris-food", "paris-fun", "london", "barcelona"]

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

    /// Books grouped by city for the shelf, in order of each city's first book.
    static func shelves(_ books: [Book]) -> [CityShelf] {
        var shelves: [CityShelf] = []
        for book in books {
            if let i = shelves.firstIndex(where: { $0.city == book.city }) {
                shelves[i].books.append(book)
            } else {
                shelves.append(CityShelf(city: book.city, books: [book]))
            }
        }
        return shelves
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

    var id: String { city }
    var flagBook: Book { books[0] }
}
