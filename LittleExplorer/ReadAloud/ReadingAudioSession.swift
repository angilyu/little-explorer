import AVFoundation
#if canImport(LiveKit) && os(iOS)
import LiveKit
#endif

/// Holds the shared `AVAudioSession` for as long as the book is being read aloud.
@MainActor
protocol ReadingAudioSession: AnyObject {
    /// Called before a clip or the system voice starts. Calling it again while reading is a no-op.
    func begin()
    /// Called once reading stops or finishes.
    func end()
}

@MainActor
func makeReadingAudioSession() -> ReadingAudioSession {
    #if canImport(LiveKit) && os(iOS)
    LiveKitReadingAudioSession()
    #else
    PlaybackReadingAudioSession()
    #endif
}

#if canImport(LiveKit) && os(iOS)
/// LiveKit's `AudioSessionEngineObserver` owns the audio session for Milo, so read-aloud asks it for
/// playback instead of setting the category itself. It does the same for its own `SoundPlayer`.
///
/// When Milo's call ends, LiveKit deactivates the session but leaves the category at `.playAndRecord`
/// with the `.videoChat` mode. That mode uses the quieter call volume, and without Milo's voice
/// processing nothing makes up for it. If read-aloud only set its own category when it didn't find
/// `.playAndRecord`, every clip after Milo was very quiet. Holding a playback requirement:
/// - with Milo off: LiveKit sets `.playback` / `.spokenAudio`, at full media volume through the speaker;
/// - with Milo on: LiveKit keeps the call's `.playAndRecord` session as it is, so echo cancellation is kept;
/// - if Milo turns off while a page is being read: LiveKit switches to `.playback` straight away and
///   doesn't deactivate the session under the playing clip.
final class LiveKitReadingAudioSession: ReadingAudioSession {
    private let session: AudioSessionEngineObserver
    private var handle: SessionRequirementHandle?

    init(session: AudioSessionEngineObserver = AudioManager.shared.audioSession) {
        self.session = session
    }

    var isHeld: Bool { handle != nil }

    func begin() {
        guard handle == nil else { return }
        handle = try? session.acquire(requirement: .playbackOnly)
    }

    func end() {
        let handle = self.handle
        self.handle = nil
        try? handle?.release()
    }
}
#endif

/// Used when LiveKit isn't linked, so nothing else configures the session.
/// Uses a playback session, so the sentence is heard even with the silent switch on.
final class PlaybackReadingAudioSession: ReadingAudioSession {
    private var isActive = false

    func begin() {
        guard !isActive else { return }
        isActive = true
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? session.setActive(true)
    }

    func end() {
        guard isActive else { return }
        isActive = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
