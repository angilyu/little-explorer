import XCTest
@testable import LittleExplorer

/// Waking Milo's agent when a book opens: only on a device that used Milo this week, at most once per
/// 10 minutes, not right after a session, never blocking or failing the reader.
@MainActor
final class MiloWarmerTests: XCTestCase {
    private var clock = Date(timeIntervalSince1970: 1_000_000)
    private var sent = 0
    private var defaults: UserDefaults!
    private let suite = "milo-warmer-tests"

    override func setUp() {
        defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        sent = 0
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
    }

    private func makeWarmer(failing: Bool = false) -> MiloWarmer {
        MiloWarmer(defaults: defaults, now: { [unowned self] in clock }) { [unowned self] in
            sent += 1
            if failing { throw URLError(.notConnectedToInternet) }
        }
    }

    private func used(ago seconds: TimeInterval) {
        defaults.set(clock.addingTimeInterval(-seconds), forKey: MiloWarmer.lastUsedKey)
    }

    func testNeverWarmsADeviceThatHasNotUsedMilo() async {
        let warmer = makeWarmer()
        XCTAssertFalse(warmer.warmIfUseful())
        await warmer.pending?.value
        XCTAssertEqual(sent, 0)
    }

    func testWarmsWhenMiloWasUsedThisWeekAtMostOncePerTenMinutes() async {
        let warmer = makeWarmer()
        used(ago: 24 * 60 * 60)
        XCTAssertTrue(warmer.warmIfUseful())
        await warmer.pending?.value
        XCTAssertEqual(sent, 1)

        clock += MiloWarmer.gap - 1
        XCTAssertFalse(warmer.warmIfUseful(), "debounced: reopening a book doesn't warm again")
        clock += 2
        XCTAssertTrue(warmer.warmIfUseful())
        await warmer.pending?.value
        XCTAssertEqual(sent, 2)
    }

    func testSkipsRightAfterASessionAndAfterAWeekWithoutMilo() {
        let warmer = makeWarmer()
        used(ago: 60)
        XCTAssertFalse(warmer.warmIfUseful(), "the replica that just served a session is probably still up")
        used(ago: 8 * 24 * 60 * 60)
        XCTAssertFalse(warmer.warmIfUseful())
        XCTAssertEqual(sent, 0)
    }

    func testMiloWasUsedStampsTheDevice() {
        let warmer = makeWarmer()
        warmer.miloWasUsed()
        XCTAssertEqual(defaults.object(forKey: MiloWarmer.lastUsedKey) as? Date, clock)
        clock += MiloWarmer.stillWarm
        XCTAssertTrue(warmer.warmIfUseful())
    }

    func testAFailedWarmUpIsIgnored() async {
        let warmer = makeWarmer(failing: true)
        used(ago: 60 * 60)
        XCTAssertTrue(warmer.warmIfUseful())
        await warmer.pending?.value
        XCTAssertEqual(sent, 1)
        XCTAssertFalse(warmer.warmIfUseful(), "a failure still waits out the gap")
    }

    // MARK: - The reader's trigger

    private func makeVoice(_ transports: Box<[MockTransport]>) -> VoiceModeController {
        let voice = VoiceModeController(makeTransport: {
            let transport = MockTransport()
            transports.value.append(transport)
            return transport
        }, now: { [unowned self] in clock })
        voice.warmer = makeWarmer()
        return voice
    }

    func testWarmUpOnlyWhileMiloIsAsleepAndAllowed() async {
        let transports = Box<[MockTransport]>([])
        let voice = makeVoice(transports)
        used(ago: 60 * 60)
        var allowed = false
        voice.mayTalk = { allowed }
        voice.warmUp()
        XCTAssertEqual(sent, 0, "no grown-up's yes, no warm-up")

        allowed = true
        await voice.turnOn()
        voice.warmUp()
        XCTAssertEqual(sent, 0, "Milo is already awake")
        XCTAssertEqual(transports.value.count, 1)
    }

    func testTalkingStampsTheDeviceSoTheNextBookSkipsTheWarmUp() async {
        let transports = Box<[MockTransport]>([])
        let voice = makeVoice(transports)
        await voice.turnOn()
        XCTAssertEqual(defaults.object(forKey: MiloWarmer.lastUsedKey) as? Date, clock)
        clock += 120
        await voice.turnOff()
        XCTAssertEqual(defaults.object(forKey: MiloWarmer.lastUsedKey) as? Date, clock)
        voice.warmUp()
        await voice.warmer?.pending?.value
        XCTAssertEqual(sent, 0, "a session just ended")

        clock += MiloWarmer.stillWarm
        voice.warmUp()
        await voice.warmer?.pending?.value
        XCTAssertEqual(sent, 1)
    }

    func testASessionMiloEndsStampsTheDevice() async {
        let transports = Box<[MockTransport]>([])
        let voice = makeVoice(transports)
        await voice.turnOn()
        clock += 300
        transports.value[0].send(.ended(reason: "quiet"))
        XCTAssertEqual(defaults.object(forKey: MiloWarmer.lastUsedKey) as? Date, clock)
    }
}

final class Box<Value> {
    var value: Value
    init(_ value: Value) { self.value = value }
}

final class TokenServiceWarmTests: XCTestCase {
    final class Capture: URLProtocol {
        nonisolated(unsafe) static var requests: [(path: String, session: String?, body: Data?)] = []
        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        override func startLoading() {
            var body = request.httpBody
            if let stream = request.httpBodyStream {
                stream.open()
                var data = Data()
                var buffer = [UInt8](repeating: 0, count: 4096)
                while stream.hasBytesAvailable {
                    let n = stream.read(&buffer, maxLength: buffer.count)
                    if n <= 0 { break }
                    data.append(buffer, count: n)
                }
                stream.close()
                body = data
            }
            Self.requests.append((request.url!.path, request.value(forHTTPHeaderField: AccountService.sessionHeader), body))
            let response = HTTPURLResponse(url: request.url!, statusCode: 202, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(#"{"warmed":true}"#.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }
        override func stopLoading() {}
    }

    private func service(token: String?) -> TokenService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [Capture.self]
        return TokenService(baseURL: URL(string: "https://milo.test")!, accessKey: nil, session: URLSession(configuration: config),
                            device: DeviceInfo(id: "ipad-1", name: "iPad", model: "iPad13,18"), sessionToken: { token })
    }

    func testWarmPostsTheDeviceWithTheSignIn() async throws {
        Capture.requests = []
        try await service(token: nil).warm()
        XCTAssertTrue(Capture.requests.isEmpty, "signed-out devices never warm")

        try await service(token: "session-abc").warm()
        XCTAssertEqual(Capture.requests.count, 1)
        let request = try XCTUnwrap(Capture.requests.first)
        XCTAssertEqual(request.path, "/milo/warm")
        XCTAssertEqual(request.session, "session-abc")
        let body = try XCTUnwrap(request.body.flatMap { try JSONSerialization.jsonObject(with: $0) as? [String: Any] })
        XCTAssertEqual((body["device"] as? [String: Any])?["id"] as? String, "ipad-1")
        XCTAssertNil(body["context"])
    }
}
