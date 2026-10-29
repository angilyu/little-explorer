import Foundation
import Observation

enum ContentUpdateError: Error, Equatable {
    case server(status: Int)
    case unsupportedFormat(Int)
    case badDownload(String)
    case invalid(String)
}

/// Brings the downloaded books up to the server's catalog: fetch the manifest, download only the files
/// that differ from what the app already has, check every hash, make sure all books (and their photos)
/// load, and only then move the new files into place.
struct ContentUpdater {
    enum Outcome: Equatable {
        case unchanged
        case updated(version: String, downloaded: Int)
    }

    let files: ContentFiles
    /// The server's `/content` URL.
    let baseURL: URL
    var session: URLSession = .shared

    func update(knownVersion: String?) async throws -> Outcome {
        let manifest = try await fetchManifest()
        guard manifest.format == 1 else { throw ContentUpdateError.unsupportedFormat(manifest.format) }
        let plan = files.plan(for: manifest)
        if manifest.version == knownVersion, plan.downloads.isEmpty, plan.stale.isEmpty { return .unchanged }

        let staging = FileManager.default.temporaryDirectory
            .appendingPathComponent("content-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: staging) }

        try await withThrowingTaskGroup(of: Void.self) { group in
            var pending = plan.downloads.makeIterator()
            func next() -> Bool {
                guard let (name, path) = pending.next(), let entry = manifest.files[path] else { return false }
                group.addTask { try await download(path, entry: entry, to: staging.appendingPathComponent(name)) }
                return true
            }
            for _ in 0..<4 where next() {}
            while try await group.next() != nil { _ = next() }
        }

        try validate(manifest, staging: staging)
        try files.install(from: staging, names: plan.downloads.keys, removing: plan.stale)
        return .updated(version: manifest.version, downloaded: plan.downloads.count)
    }

    private func fetchManifest() async throws -> ContentManifest {
        var request = URLRequest(url: baseURL.appendingPathComponent("manifest.json"))
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.addClientHeaders()
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw ContentUpdateError.server(status: status) }
        return try JSONDecoder().decode(ContentManifest.self, from: data)
    }

    private func download(_ path: String, entry: ContentManifest.Entry, to target: URL) async throws {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = 60
        // The server picks the shared files (library.json, narration.json) by version, like the manifest.
        request.addClientHeaders()
        let (data, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw ContentUpdateError.badDownload(path) }
        try data.write(to: target, options: .atomic)
        guard data.count == entry.size, ContentFiles.sha256(of: target) == entry.sha256 else {
            throw ContentUpdateError.badDownload(path)
        }
    }

    /// Every book on the new shelf must decode and have its photos and flag, using the new files where
    /// there are any and the current ones otherwise.
    private func validate(_ manifest: ContentManifest, staging: URL) throws {
        func url(_ name: String) -> URL? {
            let staged = staging.appendingPathComponent(name)
            return FileManager.default.fileExists(atPath: staged.path) ? staged : files.url(name)
        }
        guard !manifest.order.isEmpty else { throw ContentUpdateError.invalid("empty shelf") }
        for id in manifest.order {
            guard let bookURL = url("\(id).json"), let book = try? BookLibrary.decode(Data(contentsOf: bookURL)) else {
                throw ContentUpdateError.invalid("book \(id)")
            }
            let pictures = book.pages.compactMap(\.photo.file) + [book.flagImage].compactMap { $0 }
            for name in pictures where url(name) == nil {
                throw ContentUpdateError.invalid("\(id): missing \(name)")
            }
        }
    }
}

/// The books on the shelf and the Passport badges (`badges.json`). Starts with what's on the device and
/// quietly swaps in new ones from the Milo server when there are any, so content changes don't need an
/// app update.
@MainActor
@Observable
final class ContentStore {
    enum Status: Equatable {
        case idle
        case checking
        case upToDate
        case updated(downloaded: Int)
        case failed(String)
    }

    private(set) var books: [Book]
    private(set) var badges: BadgeCatalog
    private(set) var version: String?
    private(set) var status: Status = .idle
    private(set) var lastChecked: Date?

    @ObservationIgnored private let files: ContentFiles
    @ObservationIgnored private let serverURL: () -> URL
    @ObservationIgnored private let session: URLSession
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let appBuild: String
    /// Don't ask the server again within this long, unless forced.
    @ObservationIgnored var minimumInterval: TimeInterval = 600

    static let versionKey = "content.version"
    static let buildKey = "content.appBuild"

    init(
        files: ContentFiles = .shared,
        serverURL: @escaping () -> URL = { VoiceSettings.tokenServerURL() },
        session: URLSession = .shared,
        defaults: UserDefaults = .standard,
        appBuild: String = ContentStore.currentBuild
    ) {
        self.files = files
        self.serverURL = serverURL
        self.session = session
        self.defaults = defaults
        self.appBuild = appBuild
        // A new app build ships newer books, so downloads made for the old build are dropped.
        if defaults.string(forKey: Self.buildKey) != appBuild {
            files.removeAll()
            defaults.removeObject(forKey: Self.versionKey)
            defaults.set(appBuild, forKey: Self.buildKey)
        }
        version = defaults.string(forKey: Self.versionKey)
        books = Self.load(files)
        badges = BadgeCatalog.load(files)
    }

    nonisolated static var currentBuild: String {
        let info = Bundle.main.infoDictionary
        return "\(info?["CFBundleShortVersionString"] ?? "")-\(info?["CFBundleVersion"] ?? "")"
    }

    private static func load(_ files: ContentFiles) -> [Book] {
        if let books = try? BookLibrary.load(files: files), !books.isEmpty { return books }
        // Something on disk is broken: fall back to the books inside the app.
        files.removeAll()
        return (try? BookLibrary.loadBundled(bundle: files.bundle)) ?? []
    }

    /// Checks the server for new books. Throttled to `minimumInterval` unless `force` is set.
    func refresh(force: Bool = false) async {
        if status == .checking { return }
        if !force, let lastChecked, Date().timeIntervalSince(lastChecked) < minimumInterval { return }
        status = .checking
        let updater = ContentUpdater(
            files: files, baseURL: serverURL().appendingPathComponent("content"), session: session
        )
        do {
            switch try await updater.update(knownVersion: version) {
            case .unchanged:
                status = .upToDate
            case let .updated(newVersion, downloaded):
                version = newVersion
                defaults.set(newVersion, forKey: Self.versionKey)
                books = Self.load(files)
                badges = BadgeCatalog.load(files)
                NarrationLibrary.reload()
                status = downloaded == 0 ? .upToDate : .updated(downloaded: downloaded)
            }
        } catch {
            status = .failed(String(describing: error))
        }
        lastChecked = Date()
    }
}
