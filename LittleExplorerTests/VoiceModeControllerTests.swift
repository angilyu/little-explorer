import XCTest
@testable import LittleExplorer

final class MockTransport: VoiceTransport {
    var onEvent: ((VoiceTransportEvent) -> Void)?
    var connectError: Error?
    var connectGate: CheckedContinuation<Void, Never>?
    var holdConnect = false
    private(set) var connectCount = 0
    private(set) var disconnectCount = 0
    private(set) var pages: [PageContext] = []
    private(set) var openingPage: PageContext?

    func connect(page: PageContext?) async throws {
        connectCount += 1
        openingPage = page
        if holdConnect {
            await withCheckedContinuation { connectGate = $0 }
        }
        if let connectError { throw connectError }
    }

    func releaseConnect() {
        connectGate?.resume()
        connectGate = nil
    }

    func updatePage(_ context: PageContext) async throws { pages.append(context) }
    private(set) var microphoneChanges: [Bool] = []
    func setMicrophone(enabled: Bool) async { microphoneChanges.append(enabled) }
    func disconnect() async { disconnectCount += 1 }

    func send(_ event: VoiceTransportEvent) { onEvent?(event) }
}

@MainActor
final class VoiceModeControllerTests: XCTestCase {
    private var transports: [MockTransport] = []

    private func makeController(configure: ((MockTransport) -> Void)? = nil) -> VoiceModeController {
        VoiceModeController { [unowned self] in
            let transport = MockTransport()
            configure?(transport)
            transports.append(transport)
            return transport
        }
    }

    private func page(_ n: Int) -> PageContext {
        PageContext(bookId: "paris", pageId: "p\(n)", pageNumber: n, city: "Paris", text: "Page \(n)")
    }

    func testStartsOff() {
        let voice = makeController()
        XCTAssertEqual(voice.state, .off)
        XCTAssertFalse(voice.isEnabled)
        XCTAssertTrue(transports.isEmpty)
    }

    func testTurnOnConnectsAndSendsCurrentPage() async {
        let voice = makeController()
        await voice.pageDidChange(page(1))
        await voice.turnOn()
        XCTAssertEqual(voice.state, .waitingForBuddy)
        XCTAssertTrue(voice.isEnabled)
        XCTAssertEqual(transports.first?.openingPage, page(1), "the opening page travels with the token")
        XCTAssertEqual(transports.first?.pages, [])
    }

    func testStaysOnAcrossPageTurnsAndForwardsEachPage() async {
        let voice = makeController()
        await voice.pageDidChange(page(1))
        await voice.turnOn()
        await voice.pageDidChange(page(2))
        await voice.pageDidChange(page(3))
        await voice.pageDidChange(page(3))
        XCTAssertTrue(voice.isEnabled)
        XCTAssertEqual(transports.count, 1, "page turns must not reconnect")
        XCTAssertEqual(transports[0].openingPage?.pageNumber, 1)
        XCTAssertEqual(transports[0].pages.map(\.pageNumber), [2, 3])
    }

    func testPagesAreNotSentWhileOff() async {
        let voice = makeController()
        await voice.pageDidChange(page(1))
        await voice.pageDidChange(page(2))
        XCTAssertTrue(transports.isEmpty)
        XCTAssertEqual(voice.currentPage, page(2))
    }

    func testToggleTurnsOffAndDisconnects() async {
        let voice = makeController()
        await voice.toggle()
        XCTAssertTrue(voice.isEnabled)
        await voice.toggle()
        XCTAssertEqual(voice.state, .off)
        XCTAssertEqual(transports[0].disconnectCount, 1)
        await voice.pageDidChange(page(9))
        XCTAssertTrue(transports[0].pages.isEmpty)
    }

    func testBuddyActivityEventsDriveState() async {
        let voice = makeController()
        await voice.turnOn()
        let transport = transports[0]
        transport.send(.buddyActivity(.listening))
        XCTAssertEqual(voice.state, .listening)
        transport.send(.buddyActivity(.thinking))
        XCTAssertEqual(voice.state, .thinking)
        transport.send(.buddyActivity(.speaking))
        XCTAssertEqual(voice.state, .speaking)
        XCTAssertEqual(voice.statusText, "Milo is talking")
    }

    func testConnectFailureShowsFriendlyMessageAndRetryWorks() async {
        var failNext = true
        let voice = makeController { transport in
            if failNext { transport.connectError = URLError(.cannotConnectToHost) }
            failNext = false
        }
        await voice.turnOn()
        XCTAssertEqual(voice.state, .failed("Milo can't reach the voice server."))
        XCTAssertTrue(voice.isEnabled, "failed stays visible so the child can tap to retry")
        XCTAssertEqual(transports[0].disconnectCount, 1)

        await voice.retry()
        XCTAssertEqual(voice.state, .waitingForBuddy)
        XCTAssertEqual(transports.count, 2)
    }

    func testRemoteDisconnectFails() async {
        let voice = makeController()
        await voice.turnOn()
        transports[0].send(.disconnected(reason: nil))
        guard case .failed = voice.state else { return XCTFail("expected failed, got \(voice.state)") }
        transports[0].send(.buddyActivity(.listening))
        guard case .failed = voice.state else { return XCTFail("events after disconnect must be ignored") }
    }

    func testTurningOffDuringConnectIgnoresLateConnect() async {
        let voice = makeController { $0.holdConnect = true }
        let connecting = Task { await voice.turnOn() }
        while transports.first?.connectGate == nil { await Task.yield() }
        XCTAssertEqual(voice.state, .connecting)

        await voice.turnOff()
        transports[0].releaseConnect()
        await connecting.value

        XCTAssertEqual(voice.state, .off)
        XCTAssertGreaterThanOrEqual(transports[0].disconnectCount, 1)
        transports[0].send(.buddyActivity(.speaking))
        XCTAssertEqual(voice.state, .off, "stale transport events are ignored")
    }

    func testPageTurnDuringConnectIsSentOnceConnected() async {
        let voice = makeController { $0.holdConnect = true }
        await voice.pageDidChange(page(1))
        let connecting = Task { await voice.turnOn() }
        while transports.first?.connectGate == nil { await Task.yield() }
        await voice.pageDidChange(page(2))
        transports[0].releaseConnect()
        await connecting.value
        XCTAssertEqual(transports[0].openingPage, page(1))
        XCTAssertEqual(transports[0].pages, [page(2)], "the page turned to while connecting is sent once connected")
    }

    func testIdleEndTurnsOffWithNapNotice() async {
        let voice = makeController()
        await voice.turnOn()
        transports[0].send(.buddyActivity(.listening))
        transports[0].send(.ended(reason: "idle"))
        XCTAssertEqual(voice.state, .off, "the toggle switches off by itself")
        XCTAssertFalse(voice.isEnabled)
        XCTAssertEqual(voice.notice, VoiceModeController.napNotice)
        for _ in 0..<200 where transports[0].disconnectCount == 0 { try? await Task.sleep(nanoseconds: 5_000_000) }
        XCTAssertEqual(transports[0].disconnectCount, 1, "Milo's room is left")

        transports[0].send(.disconnected(reason: nil))
        XCTAssertEqual(voice.state, .off, "the room closing after Milo left is not an error")
        await voice.pageDidChange(page(4))
        XCTAssertTrue(transports[0].pages.isEmpty)

        await voice.toggle()
        XCTAssertEqual(voice.state, .waitingForBuddy, "turning it back on starts normally")
        XCTAssertNil(voice.notice)
        XCTAssertEqual(transports.count, 2)
    }

    func testLimitEndShowsRestNotice() async {
        let voice = makeController()
        await voice.turnOn()
        transports[0].send(.ended(reason: "limit"))
        XCTAssertEqual(voice.state, .off)
        XCTAssertEqual(voice.notice, VoiceModeController.restNotice)
        voice.dismissNotice()
        XCTAssertNil(voice.notice)
    }

    func testStarsReachTheAppOnlyFromTheLiveSession() async {
        let voice = makeController()
        var got: [String] = []
        voice.onStar = { got.append($0.id) }
        await voice.turnOn()
        let star = StarAward(id: "s1", book: "paris", page: "p1", city: "Paris", stars: 2)
        transports[0].send(.star(star))
        XCTAssertEqual(got, ["s1"])
        XCTAssertEqual(voice.state, .waitingForBuddy, "a star doesn't change what Milo is doing")
        await voice.turnOff()
        transports[0].send(.star(StarAward(id: "late", book: "paris", page: "p1", city: "Paris", stars: 1)))
        XCTAssertEqual(got, ["s1"], "nothing arrives after Milo is off")
    }

    func testRefusedTokenRestsInsteadOfFailing() async {
        let voice = makeController { $0.connectError = VoiceError.resting }
        await voice.turnOn()
        XCTAssertEqual(voice.state, .off, "no retry spinner: Milo is resting for today")
        XCTAssertEqual(voice.notice, "Milo needs a rest today. Let's talk again tomorrow!")
        XCTAssertEqual(transports[0].disconnectCount, 1)
    }

    func testTurningOffClearsNotice() async {
        let voice = makeController()
        await voice.turnOn()
        transports[0].send(.ended(reason: "idle"))
        await voice.bookDidClose()
        XCTAssertNil(voice.notice)
    }

    func testBookCloseTurnsOffAndForgetsPage() async {
        let voice = makeController()
        await voice.pageDidChange(page(1))
        await voice.turnOn()
        await voice.bookDidClose()
        XCTAssertEqual(voice.state, .off)
        XCTAssertNil(voice.currentPage)
    }
}

final class TokenRequestTests: XCTestCase {
    func testTokenRequestCarriesOpeningPageForTheAgent() throws {
        let page = PageContext(bookId: "paris", pageId: "bonjour", pageNumber: 2, city: "Paris", text: "They say bonjour!")
        let body = try JSONEncoder().encode(TokenRequest(context: page.attributes))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: [String: String]])
        XCTAssertEqual(json["context"], [
            "book": "paris", "page": "bonjour", "page_number": "2", "city": "Paris", "page_text": "They say bonjour!",
        ])
    }

    func testTokenRequestWithoutPageIsEmptyObject() throws {
        let body = try JSONEncoder().encode(TokenRequest(context: nil))
        XCTAssertEqual(String(decoding: body, as: UTF8.self), "{}")
    }
}

final class MemoryDeviceIDStore: DeviceIDStore {
    var value: String?
    private(set) var writes = 0
    init(_ value: String? = nil) { self.value = value }
    func read() -> String? { value }
    func write(_ id: String) { value = id; writes += 1 }
}

final class DeviceIdentityTests: XCTestCase {
    func testFirstRunSavesVendorIDAndLaterRunsReuseIt() {
        let store = MemoryDeviceIDStore()
        let first = DeviceIdentity.stableID(store: store, seed: "1F0E2D3C-AAAA-BBBB-CCCC-000000000001")
        XCTAssertEqual(first, "1F0E2D3C-AAAA-BBBB-CCCC-000000000001")
        let again = DeviceIdentity.stableID(store: store, seed: "99999999-0000-0000-0000-000000000000")
        XCTAssertEqual(again, first, "a reinstall gets a new vendor id, but the Keychain id wins")
        XCTAssertEqual(store.writes, 1)
    }

    func testMissingOrBadSeedMakesAValidUUID() {
        let store = MemoryDeviceIDStore("not valid!")
        let id = DeviceIdentity.stableID(store: store, seed: nil)
        XCTAssertNotNil(UUID(uuidString: id))
        XCTAssertEqual(store.value, id)
    }

    func testValidationMatchesServer() {
        XCTAssertTrue(DeviceIdentity.isValid("e2e-smoke"))
        XCTAssertFalse(DeviceIdentity.isValid(""))
        XCTAssertFalse(DeviceIdentity.isValid("a b"))
        XCTAssertFalse(DeviceIdentity.isValid("é"))
        XCTAssertFalse(DeviceIdentity.isValid(String(repeating: "a", count: 65)))
    }

    func testKeychainRoundTrip() throws {
        let store = KeychainDeviceIDStore(service: "com.angilyu.littleexplorer.tests.\(UUID().uuidString)")
        store.write("ABC-123")
        guard let read = store.read() else { throw XCTSkip("Keychain unavailable in this test host") }
        XCTAssertEqual(read, "ABC-123")
        store.write("DEF-456")
        XCTAssertEqual(store.read(), "DEF-456")
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: store.service] as CFDictionary)
    }

    @MainActor
    func testCurrentDeviceIsStable() {
        let store = MemoryDeviceIDStore()
        let a = DeviceIdentity.current(store: store)
        let b = DeviceIdentity.current(store: store)
        XCTAssertEqual(a, b)
        XCTAssertTrue(DeviceIdentity.isValid(a.id))
        XCTAssertFalse(a.model.isEmpty)
    }
}

final class TokenServiceRefusalTests: XCTestCase {
    final class StubProtocol: URLProtocol {
        nonisolated(unsafe) static var status = 200
        nonisolated(unsafe) static var lastBody: Data?
        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        override func startLoading() {
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
                Self.lastBody = data
            } else {
                Self.lastBody = request.httpBody
            }
            let response = HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(#"{"error":"limit","message":"Milo needs a rest today."}"#.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }
        override func stopLoading() {}
    }

    private func service() -> TokenService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return TokenService(baseURL: URL(string: "https://milo.test")!, accessKey: nil, session: URLSession(configuration: config),
                            device: DeviceInfo(id: "ipad-1", name: "iPad", model: "iPad13,18"))
    }

    func testLimitAndBlockedBecomeResting() async {
        for status in [403, 429] {
            StubProtocol.status = status
            do {
                _ = try await service().fetchCredentials(context: nil)
                XCTFail("expected resting for \(status)")
            } catch VoiceError.resting {
            } catch {
                XCTFail("\(status): \(error)")
            }
        }
    }

    func testOtherErrorsStayServerErrors() async {
        StubProtocol.status = 500
        do {
            _ = try await service().fetchCredentials(context: nil)
            XCTFail("expected an error")
        } catch VoiceError.tokenServer(let status) {
            XCTAssertEqual(status, 500)
        } catch {
            XCTFail("\(error)")
        }
    }

    func testRequestSendsDevice() async throws {
        StubProtocol.status = 429
        _ = try? await service().fetchCredentials(context: nil)
        let body = try XCTUnwrap(StubProtocol.lastBody)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["device"] as? [String: String], ["id": "ipad-1", "name": "iPad", "model": "iPad13,18"])
    }
}
