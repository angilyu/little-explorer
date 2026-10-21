import Foundation
import Observation

/// Remembers which pages the child hearted. Stored locally in UserDefaults only.
@MainActor
@Observable
final class HeartStore {
    private(set) var loved: Set<String>

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let key: String

    init(defaults: UserDefaults = .standard, key: String = "lovedPages.v1") {
        self.defaults = defaults
        self.key = key
        loved = Set(defaults.stringArray(forKey: key) ?? [])
    }

    static func id(bookId: String, pageId: String) -> String { "\(bookId)/\(pageId)" }

    func isLoved(bookId: String, pageId: String) -> Bool {
        loved.contains(Self.id(bookId: bookId, pageId: pageId))
    }

    func toggle(bookId: String, pageId: String) {
        setLoved(!isLoved(bookId: bookId, pageId: pageId), bookId: bookId, pageId: pageId)
    }

    func setLoved(_ isLoved: Bool, bookId: String, pageId: String) {
        let id = Self.id(bookId: bookId, pageId: pageId)
        if isLoved { loved.insert(id) } else { loved.remove(id) }
        defaults.set(loved.sorted(), forKey: key)
    }

    func lovedCount(bookId: String) -> Int {
        loved.filter { $0.hasPrefix(bookId + "/") }.count
    }

    func lovedPages(in book: Book) -> [BookPage] {
        book.pages.filter { isLoved(bookId: book.id, pageId: $0.id) }
    }
}

extension HeartStore {
    /// Screenshot demos keep their hearts in their own store, so they never touch his real ones.
    static func live() -> HeartStore {
        #if DEBUG
        if DemoLaunch.current != nil {
            let suite = "demo.hearts"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            return HeartStore(defaults: defaults)
        }
        #endif
        return HeartStore()
    }
}
