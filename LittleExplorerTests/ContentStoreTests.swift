import CryptoKit
import XCTest
@testable import LittleExplorer

/// Serves canned responses for `https://milo.test/content/...`.
final class StubContentServer: URLProtocol {
    nonisolated(unsafe) static var responses: [String: (Int, Data)] = [:]
    nonisolated(unsafe) static var requested: [String] = []

    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "milo.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let path = request.url!.path
        Self.requested.append(path)
        let (status, body) = Self.responses[path] ?? (404, Data())
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@MainActor
final class ContentStoreTests: XCTestCase {
    private var cacheDir: URL!
    private var defaults: UserDefaults!
    private var session: URLSession!

    override func setUp() {
        cacheDir = FileManager.default.temporaryDirectory.appendingPathComponent("content-test-\(UUID().uuidString)")
        defaults = UserDefaults(suiteName: "content-test-\(UUID().uuidString)")
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubContentServer.self]
        session = URLSession(configuration: config)
        StubContentServer.responses = [:]
        StubContentServer.requested = []
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: cacheDir)
    }

    private var files: ContentFiles { ContentFiles(cacheDir: cacheDir, bundle: .main) }

    private func makeStore() -> ContentStore {
        ContentStore(
            files: files, serverURL: { URL(string: "https://milo.test")! }, session: session,
            defaults: defaults, appBuild: "test"
        )
    }

    private static func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    private func bundled(_ name: String) throws -> Data {
        try Data(contentsOf: XCTUnwrap(files.bundledURL(name)))
    }

    /// Publishes `files` (path → contents) with a manifest, like the server's catalog does.
    private func publish(_ published: [String: Data], version: String, order: [String]) throws {
        var entries: [String: ContentManifest.Entry] = [:]
        for (path, data) in published {
            entries[path] = .init(sha256: Self.sha(data), size: data.count)
            StubContentServer.responses["/content/\(path)"] = (200, data)
        }
        let manifest = ContentManifest(format: 1, version: version, order: order, files: entries)
        StubContentServer.responses["/content/manifest.json"] = (200, try JSONEncoder().encode(manifest))
    }

    private func edited(_ id: String, title: String) throws -> Data {
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: bundled("\(id).json")) as? [String: Any])
        json["tagline"] = title
        return try JSONSerialization.data(withJSONObject: json)
    }

    func testStartsWithTheBundledShelfInLibraryOrder() {
        let store = makeStore()
        XCTAssertEqual(store.books.map(\.id), ["paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu", "chengdu", "longyan", "travel-flight", "travel-packing", "travel-passport", "travel-hotel", "travel-jetlag"])
        XCTAssertEqual(BookLibrary.order(files: files), BookLibrary.order)
        XCTAssertNil(store.version)
    }

    func testDownloadsOnlyChangedFilesAndSwapsInTheNewShelf() async throws {
        let london = try edited("london", title: "Hello London")
        try publish([
            "library.json": Data(#"{"order": ["london", "paris"]}"#.utf8),
            "Books/london.json": london,
            "Books/paris.json": try bundled("paris.json"),
            "Photos/paris-eiffel.jpg": try bundled("paris-eiffel.jpg"),
        ], version: "v2", order: ["london", "paris"])

        let store = makeStore()
        await store.refresh()

        XCTAssertEqual(store.status, .updated(downloaded: 2))
        XCTAssertEqual(store.version, "v2")
        XCTAssertEqual(store.books.map(\.id), ["london", "paris"])
        XCTAssertEqual(store.books.first?.tagline, "Hello London")
        XCTAssertEqual(files.cachedNames(), ["library.json", "london.json"])
        XCTAssertFalse(StubContentServer.requested.contains("/content/Books/paris.json"), "unchanged files aren't fetched")

        // Same version again: only the manifest is fetched.
        StubContentServer.requested = []
        await store.refresh(force: true)
        XCTAssertEqual(store.status, .upToDate)
        XCTAssertEqual(StubContentServer.requested, ["/content/manifest.json"])

        // A new store (next launch) starts from the downloaded books.
        XCTAssertEqual(makeStore().books.first?.tagline, "Hello London")
    }

    func testABadDownloadChangesNothing() async throws {
        try publish(["Books/london.json": try edited("london", title: "Broken")], version: "v3", order: BookLibrary.order)
        StubContentServer.responses["/content/Books/london.json"] = (200, Data("tampered".utf8))

        let store = makeStore()
        await store.refresh()

        guard case .failed = store.status else { return XCTFail("expected failure, got \(store.status)") }
        XCTAssertNil(store.version)
        XCTAssertEqual(store.books.first { $0.id == "london" }?.tagline, try BookLibrary.loadBundled(ids: ["london"]).first?.tagline)
        XCTAssertTrue(files.cachedNames().isEmpty)
    }

    func testABookWithAMissingPhotoIsRejected() async throws {
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: bundled("london.json")) as? [String: Any])
        var pages = try XCTUnwrap(json["pages"] as? [[String: Any]])
        var photo = try XCTUnwrap(pages[0]["photo"] as? [String: Any])
        photo["file"] = "nowhere.jpg"
        pages[0]["photo"] = photo
        json["pages"] = pages
        try publish(["Books/london.json": try JSONSerialization.data(withJSONObject: json)], version: "v4", order: ["london"])

        let store = makeStore()
        await store.refresh()

        XCTAssertEqual(store.status, .failed(String(describing: ContentUpdateError.invalid("london: missing nowhere.jpg"))))
        XCTAssertTrue(files.cachedNames().isEmpty)
    }

    func testDownloadsAreDroppedWhenTheBundleCatchesUpOrTheAppIsUpdated() async throws {
        try publish(["Books/london.json": try edited("london", title: "Hello London")], version: "v5", order: ["london"])
        let store = makeStore()
        await store.refresh()
        XCTAssertEqual(files.cachedNames(), ["london.json"])

        // The server goes back to what the app ships with: the download is no longer needed.
        try publish(["Books/london.json": try bundled("london.json")], version: "v6", order: ["london"])
        await store.refresh(force: true)
        XCTAssertTrue(files.cachedNames().isEmpty)

        // Any download is dropped when a new app build starts.
        try publish(["Books/london.json": try edited("london", title: "Hello again")], version: "v7", order: ["london"])
        await store.refresh(force: true)
        XCTAssertEqual(files.cachedNames(), ["london.json"])
        let updated = ContentStore(
            files: files, serverURL: { URL(string: "https://milo.test")! }, session: session,
            defaults: defaults, appBuild: "next"
        )
        XCTAssertTrue(files.cachedNames().isEmpty)
        XCTAssertNil(updated.version)
    }

    func testRefreshIsThrottledUnlessForced() async throws {
        try publish(["Books/london.json": try bundled("london.json")], version: "v8", order: ["london"])
        let store = makeStore()
        await store.refresh()
        await store.refresh()
        XCTAssertEqual(StubContentServer.requested.filter { $0.hasSuffix("manifest.json") }.count, 1)
        await store.refresh(force: true)
        XCTAssertEqual(StubContentServer.requested.filter { $0.hasSuffix("manifest.json") }.count, 2)
    }

    func testDownloadedPhotosWinOverBundledOnes() throws {
        try FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
        let flag = try bundled("london-flag.png")
        try flag.write(to: cacheDir.appendingPathComponent("paris-eiffel.jpg"))
        let image = try XCTUnwrap(files.image(named: "paris-eiffel.jpg"))
        XCTAssertEqual(image.size, try XCTUnwrap(UIImage(data: flag)).size)
        XCTAssertNotNil(ContentFiles.bundleOnly(.main).image(named: "paris-eiffel.jpg"))
        ContentFiles.clearImageCache()
    }

    func testTokenRequestsCarryTheAccessKey() async throws {
        final class Capture: URLProtocol {
            nonisolated(unsafe) static var authorization: String?
            override class func canInit(with request: URLRequest) -> Bool { true }
            override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
            override func startLoading() {
                Self.authorization = request.value(forHTTPHeaderField: "Authorization")
                let body = Data(#"{"serverUrl":"wss://x","participantToken":"t","roomName":"r"}"#.utf8)
                client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
                client?.urlProtocol(self, didLoad: body)
                client?.urlProtocolDidFinishLoading(self)
            }
            override func stopLoading() {}
        }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [Capture.self]
        let service = TokenService(baseURL: URL(string: "https://milo.test")!, accessKey: "s3cret", session: URLSession(configuration: config))
        let credentials = try await service.fetchCredentials(context: nil)
        XCTAssertEqual(credentials.participantToken, "t")
        XCTAssertEqual(Capture.authorization, "Bearer s3cret")
    }
}
