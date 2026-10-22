import Foundation
import Security

/// A family's Little Explorer account: a grown-up's email and the books on the child's shelf.
/// Stars and coins belong to the account on Milo's server, so every signed-in device shares them.
struct Account: Codable, Equatable, Sendable {
    let id: String
    let email: String
    /// Book ids on this account's shelf; nil means every book (the server's `all_books`).
    let books: [String]?
    /// A grown-up's choice about Milo for the whole account: true once they allowed Milo, false if they
    /// turned Milo off, nil if never asked (accounts from before the consent screen).
    let miloConsent: Bool?

    enum CodingKeys: String, CodingKey {
        case id, email, books
        case allBooks = "all_books"
        case miloConsent = "milo_consent"
    }

    init(id: String, email: String, books: [String]?, miloConsent: Bool? = nil) {
        self.id = id
        self.email = email
        self.books = books
        self.miloConsent = miloConsent
    }

    /// Milo may connect only after a grown-up allowed it.
    var miloAllowed: Bool { miloConsent == true }

    func with(miloConsent: Bool?) -> Account {
        Account(id: id, email: email, books: books, miloConsent: miloConsent)
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        email = try c.decode(String.self, forKey: .email)
        let all = try c.decodeIfPresent(Bool.self, forKey: .allBooks) ?? false
        books = all ? nil : (try c.decodeIfPresent([String].self, forKey: .books) ?? [])
        miloConsent = try c.decodeIfPresent(Bool.self, forKey: .miloConsent)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(email, forKey: .email)
        try c.encode(books == nil, forKey: .allBooks)
        try c.encode(books ?? [], forKey: .books)
        try c.encodeIfPresent(miloConsent, forKey: .miloConsent)
    }

    /// The books a new account starts with. The server's list (`PIP_DEFAULT_BOOKS`) is the one that counts;
    /// the app uses this for the welcome screen and demos.
    static let defaultBooks = [
        "paris", "paris-food", "paris-eiffel", "paris-louvre", "london", "london-big-ben",
        "barcelona", "barcelona-sagrada",
        "beijing", "beijing-great-wall", "beijing-forbidden-city", "beijing-temple-of-heaven", "shanghai",
    ]

    /// The books this account may read, in shelf order.
    func shelf(_ all: [Book]) -> [Book] {
        guard let books else { return all }
        let allowed = Set(books)
        return all.filter { allowed.contains($0.id) }
    }

    /// "w•••@example.com": enough for a grown-up to recognise, on a screen a child may see.
    var maskedEmail: String { Self.mask(email) }

    static func mask(_ email: String) -> String {
        let parts = email.split(separator: "@", maxSplits: 1)
        guard parts.count == 2, let first = parts[0].first else { return email }
        return "\(first)•••@\(parts[1])"
    }

    /// A quick check before asking the server, which checks properly.
    static func looksLikeEmail(_ text: String) -> Bool {
        let email = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard email.count <= 254, !email.contains(" ") else { return false }
        let parts = email.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty else { return false }
        let domain = parts[1]
        return domain.contains(".") && !domain.hasPrefix(".") && !domain.hasSuffix(".")
    }
}

/// Where the sign-in token lives.
protocol SessionTokenStore {
    func read() -> String?
    func write(_ token: String?)
}

/// The sign-in token in the Keychain, on this device only.
struct KeychainSessionTokenStore: SessionTokenStore {
    var service = "com.angilyu.littleexplorer.account"
    var account = "session-token"

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    func read() -> String? {
        var item: CFTypeRef?
        var lookup = query
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        guard SecItemCopyMatching(lookup as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func write(_ token: String?) {
        SecItemDelete(query as CFDictionary)
        guard let token else { return }
        var add = query
        add[kSecValueData as String] = Data(token.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}

/// For tests and screenshot demos.
final class MemorySessionTokenStore: SessionTokenStore {
    var token: String?
    init(_ token: String? = nil) { self.token = token }
    func read() -> String? { token }
    func write(_ token: String?) { self.token = token }
}
