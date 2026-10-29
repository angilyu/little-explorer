import Foundation

struct Book: Codable, Identifiable, Hashable {
    let id: String
    let city: String
    let country: String
    let flag: String
    /// Bundled flag picture shown next to the city name (emoji flags don't render everywhere).
    var flagImage: String? = nil
    let coverColor: String
    let tagline: String
    /// What this book is about when a city has more than one, e.g. "Yummy Paris".
    var theme: String? = nil
    /// "travel" for general travel books (the Travel shelf) that aren't about one place; nil for city books.
    var kind: String? = nil
    /// Reading level 1-3 (see server/src/little_explorer_agent/levels.py); older books have none and read as 2.
    var level: BookLevel? = nil
    let pages: [BookPage]

    var isTravel: Bool { kind == "travel" }
    var readingLevel: Int { level?.value ?? BookLevel.standard }

    var coverPage: BookPage { pages[0] }
    /// "Paris: Yummy Paris" style name for lists; just the city when there is no theme.
    var shelfTitle: String { theme.map { "\(city): \($0)" } ?? city }
    /// Big title on the shelf cover: the theme, or the city for a city's only book.
    var coverTitle: String { theme ?? city }
    var openLabel: String { theme.map { "Open the \($0) book about \(city)" } ?? "Open the \(city) book" }
}

/// A book's reading level. Decoding never fails: a number outside 1-3, a numeric string or anything else
/// becomes the standard level 2, so one odd value can't hide a whole book.
struct BookLevel: Codable, Hashable {
    static let range = 1...3
    static let standard = 2

    let value: Int

    init(_ value: Int) {
        self.value = Self.range.contains(value) ? value : Self.standard
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let number = try? container.decode(Int.self) {
            self.init(number)
        } else if let text = try? container.decode(String.self), let number = Int(text.trimmingCharacters(in: .whitespaces)) {
            self.init(number)
        } else {
            self.init(Self.standard)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(value)
    }
}

struct BookPage: Codable, Identifiable, Hashable {
    let id: String
    /// Short, decodable sentence(s) the child reads aloud.
    let text: String
    /// Richer background notes for the voice buddy. Never shown on screen.
    let about: String
    let emoji: String
    let photo: PhotoInfo
}

struct PhotoInfo: Codable, Hashable {
    /// File name of the bundled, resized copy (e.g. "paris-eiffel.jpg").
    let file: String?
    /// Wikimedia Commons title, e.g. "File:Tour Eiffel Wikimedia Commons.jpg".
    let commonsTitle: String?
    /// Search query used by scripts/fetch_photos.py when no title is pinned.
    let query: String?
    let author: String?
    let license: String?
    let licenseUrl: String?
    let sourceUrl: String?
    /// True once scripts/fetch_photos.py confirmed the license via the Commons API.
    let verified: Bool?

    var isVerified: Bool { verified ?? false }

    /// Commons redirect to a resized rendition; used only when the bundled copy is missing.
    var remoteURL: URL? {
        guard let commonsTitle else { return nil }
        let name = commonsTitle.hasPrefix("File:") ? String(commonsTitle.dropFirst(5)) : commonsTitle
        var components = URLComponents(string: "https://commons.wikimedia.org/wiki/Special:FilePath/")
        components?.path += name.replacingOccurrences(of: " ", with: "_")
        components?.queryItems = [URLQueryItem(name: "width", value: "1200")]
        return components?.url
    }

    var source: URL? {
        if let sourceUrl, let url = URL(string: sourceUrl) { return url }
        guard let commonsTitle else { return nil }
        let path = commonsTitle.replacingOccurrences(of: " ", with: "_")
        return URL(string: "https://commons.wikimedia.org/wiki/" + (path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? path))
    }

    /// One-line credit shown under every photo.
    var creditLine: String {
        let who = author?.isEmpty == false ? author! : "Unknown author"
        let what = license?.isEmpty == false ? license! : "license pending"
        return "Photo: \(who) · \(what) · Wikimedia Commons"
    }
}

struct PageContext: Equatable, Sendable {
    let bookId: String
    let pageId: String
    let pageNumber: Int
    let city: String
    let text: String

    init(book: Book, index: Int) {
        let page = book.pages[index]
        bookId = book.id
        pageId = page.id
        pageNumber = index + 1
        city = book.city
        text = page.text
    }

    init(bookId: String, pageId: String, pageNumber: Int, city: String, text: String) {
        self.bookId = bookId
        self.pageId = pageId
        self.pageNumber = pageNumber
        self.city = city
        self.text = text
    }

    /// Published as LiveKit participant attributes so the agent always knows the current page,
    /// even if it joins after the page was turned. Keys must match server/src/little_explorer_agent/content.py
    /// (`page_text` is what the agent's reading gate compares speech against).
    var attributes: [String: String] {
        [
            "book": bookId,
            "page": pageId,
            "page_number": String(pageNumber),
            "city": city,
            "page_text": text,
        ]
    }
}
