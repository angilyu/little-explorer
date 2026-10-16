import CryptoKit
import Foundation
import UIKit

/// Where book files come from: books, photos and narration downloaded from the Milo server win over the
/// copies bundled in the app. Files are looked up by name (e.g. "paris.json", "paris-eiffel.jpg"),
/// because the app bundle is flat and the server's catalog keeps names unique.
struct ContentFiles: @unchecked Sendable {
    let cacheDir: URL
    let bundle: Bundle

    static let shared = ContentFiles(cacheDir: defaultCacheDir, bundle: .main)

    /// Files inside the app only, ignoring downloads.
    static func bundleOnly(_ bundle: Bundle) -> ContentFiles {
        ContentFiles(cacheDir: URL(fileURLWithPath: "/dev/null/no-content-cache"), bundle: bundle)
    }

    static var defaultCacheDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Content", isDirectory: true)
    }

    func cachedURL(_ name: String) -> URL? {
        let url = cacheDir.appendingPathComponent(name)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    func bundledURL(_ name: String) -> URL? {
        let ns = name as NSString
        return bundle.url(forResource: ns.deletingPathExtension, withExtension: ns.pathExtension)
    }

    func url(_ name: String) -> URL? { cachedURL(name) ?? bundledURL(name) }

    func data(_ name: String) -> Data? { url(name).flatMap { try? Data(contentsOf: $0) } }

    /// Downloaded photos are decoded once and kept; bundled ones go through `UIImage(named:)`'s cache.
    func image(named name: String) -> UIImage? {
        guard let url = cachedURL(name) else { return UIImage(named: name, in: bundle, with: nil) }
        let key = url.path as NSString
        if let image = Self.images.object(forKey: key) { return image }
        guard let image = UIImage(contentsOfFile: url.path) else { return nil }
        Self.images.setObject(image, forKey: key)
        return image
    }

    private static let images = NSCache<NSString, UIImage>()

    static func clearImageCache() { images.removeAllObjects() }

    // MARK: Updating

    static func sha256(of url: URL) -> String? {
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    struct Plan: Equatable {
        /// name → path on the server, for files whose current copy differs from the manifest.
        var downloads: [String: String] = [:]
        /// Downloaded files that are no longer needed (gone from the catalog, or the bundle has caught up).
        var stale: Set<String> = []
    }

    /// What to fetch so every file in `manifest` resolves to the server's version.
    func plan(for manifest: ContentManifest) -> Plan {
        var plan = Plan()
        var names = Set<String>()
        for (path, entry) in manifest.files {
            let name = (path as NSString).lastPathComponent
            names.insert(name)
            let bundled = bundledURL(name).flatMap(Self.sha256(of:))
            if let cached = cachedURL(name) {
                if bundled == entry.sha256 {
                    plan.stale.insert(name)
                } else if Self.sha256(of: cached) != entry.sha256 {
                    plan.downloads[name] = path
                }
            } else if bundled != entry.sha256 {
                plan.downloads[name] = path
            }
        }
        plan.stale.formUnion(cachedNames().subtracting(names))
        return plan
    }

    func cachedNames() -> Set<String> {
        Set((try? FileManager.default.contentsOfDirectory(atPath: cacheDir.path)) ?? [])
    }

    /// Moves verified downloads from `staging` into the cache and removes stale files.
    func install(from staging: URL, names: some Sequence<String>, removing stale: Set<String>) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: cacheDir, withIntermediateDirectories: true)
        for name in names {
            let target = cacheDir.appendingPathComponent(name)
            if fm.fileExists(atPath: target.path) {
                _ = try fm.replaceItemAt(target, withItemAt: staging.appendingPathComponent(name))
            } else {
                try fm.moveItem(at: staging.appendingPathComponent(name), to: target)
            }
        }
        for name in stale {
            try? fm.removeItem(at: cacheDir.appendingPathComponent(name))
        }
        Self.clearImageCache()
    }

    func removeAll() {
        try? FileManager.default.removeItem(at: cacheDir)
        Self.clearImageCache()
    }
}

/// `GET /content/manifest.json` from the Milo server (see server/src/little_explorer_agent/catalog.py).
struct ContentManifest: Codable, Equatable {
    struct Entry: Codable, Equatable {
        let sha256: String
        let size: Int
    }

    let format: Int
    let version: String
    let order: [String]
    let files: [String: Entry]
}
