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
        XCTAssertEqual(voice.statusText, "Pip is talking")
    }

    func testConnectFailureShowsFriendlyMessageAndRetryWorks() async {
        var failNext = true
        let voice = makeController { transport in
            if failNext { transport.connectError = URLError(.cannotConnectToHost) }
            failNext = false
        }
        await voice.turnOn()
        XCTAssertEqual(voice.state, .failed("Pip can't reach the voice server."))
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
