import Foundation

#if canImport(LiveKit)
import AVFAudio
import LiveKit

/// Joins a LiveKit room with the microphone on. The agent (server/) is dispatched into the room by
/// the token, which also carries the opening page. Page turns go to the agent as JSON on the
/// `context` data topic (the agent's `attach_room_context`), and the agent talks back as audio.
final class LiveKitVoiceTransport: NSObject, VoiceTransport, RoomDelegate, @unchecked Sendable {
    var onEvent: ((VoiceTransportEvent) -> Void)?

    private let tokenService: TokenService
    private var room: Room?
    private var isDisconnecting = false
    private let pageLock = NSLock()
    private var latestPage: PageContext?

    static let contextTopic = "context"
    static let starTopic = "milo.star"
    /// Set by the agent just before it leaves on purpose (see server agent.py `ENDED_ATTRIBUTE`).
    static let endedAttribute = "milo.ended"
    private var endReported = false

    init(tokenService: TokenService) {
        self.tokenService = tokenService
    }

    func connect(page: PageContext?) async throws {
        guard await AVAudioApplication.requestRecordPermission() else { throw VoiceError.microphoneDenied }
        setLatestPage(page)
        let credentials = try await tokenService.fetchCredentials(context: page)
        let room = Room(delegate: self)
        self.room = room
        try await room.connect(url: credentials.serverUrl, token: credentials.participantToken)
        _ = try await room.localParticipant.setMicrophone(enabled: true, captureOptions: Self.captureOptions)
        for participant in room.remoteParticipants.values {
            emitActivity(from: participant.attributes)
        }
    }

    func updatePage(_ context: PageContext) async throws {
        setLatestPage(context)
        guard let room else { return }
        try await send(context, in: room)
    }

    func setMicrophone(enabled: Bool) async {
        guard let room else { return }
        _ = try? await room.localParticipant.setMicrophone(enabled: enabled, captureOptions: Self.captureOptions)
    }

    /// On a device Apple's voice processing removes Milo's voice from the mic. The simulator has no
    /// voice processing, so the Mac mic heard Milo through the speakers and Milo answered (and cut
    /// itself off on) its own echo; use WebRTC's software echo cancellation there instead.
    static var captureOptions: AudioCaptureOptions {
        #if targetEnvironment(simulator)
        AudioCaptureOptions(echoCancellationMode: .software, noiseSuppressionMode: .software)
        #else
        AudioCaptureOptions()
        #endif
    }

    func disconnect() async {
        isDisconnecting = true
        let room = self.room
        self.room = nil
        await room?.disconnect()
    }

    private func setLatestPage(_ page: PageContext?) {
        pageLock.lock()
        latestPage = page
        pageLock.unlock()
    }

    private func currentPage() -> PageContext? {
        pageLock.lock()
        defer { pageLock.unlock() }
        return latestPage
    }

    private func send(_ context: PageContext, in room: Room) async throws {
        let data = try JSONEncoder().encode(context.attributes)
        let options = DataPublishOptions(topic: Self.contextTopic, reliable: true)
        try await room.localParticipant.publish(data: data, options: options)
    }

    // MARK: RoomDelegate

    /// Data messages are not stored, so when the agent (re)joins, tell it which page is open now.
    func room(_ room: Room, participantDidConnect participant: RemoteParticipant) {
        guard let page = currentPage() else { return }
        Task { [weak self] in try? await self?.send(page, in: room) }
    }

    func room(_ room: Room, participant: Participant, didUpdateAttributes changedAttributes: [String: String]) {
        guard participant is RemoteParticipant else { return }
        emitActivity(from: participant.attributes)
    }

    func room(
        _ room: Room, participant: RemoteParticipant?, didReceiveData data: Data, forTopic topic: String,
        encryptionType: EncryptionType
    ) {
        guard topic == Self.starTopic, let award = try? JSONDecoder().decode(StarAward.self, from: data) else { return }
        DispatchQueue.main.async { [weak self] in self?.onEvent?(.star(award)) }
    }

    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        guard !isDisconnecting else { return }
        let reason = error == nil ? nil : "Milo lost the connection. Tap to try again."
        DispatchQueue.main.async { [weak self] in self?.onEvent?(.disconnected(reason: reason)) }
    }

    private func emitActivity(from attributes: [String: String]) {
        if let reason = attributes[Self.endedAttribute], !reason.isEmpty, !endReported {
            endReported = true
            DispatchQueue.main.async { [weak self] in self?.onEvent?(.ended(reason: reason)) }
            return
        }
        guard !endReported, let raw = attributes["lk.agent.state"], let activity = BuddyActivity(rawValue: raw) else { return }
        DispatchQueue.main.async { [weak self] in self?.onEvent?(.buddyActivity(activity)) }
    }
}
#endif

@MainActor
func makeLiveVoiceTransport() -> VoiceTransport {
    #if canImport(LiveKit)
    let tokens = TokenService(baseURL: VoiceSettings.tokenServerURL(), device: DeviceIdentity.current())
    return LiveKitVoiceTransport(tokenService: tokens)
    #else
    return UnavailableVoiceTransport()
    #endif
}
