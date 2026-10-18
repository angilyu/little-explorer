import AVFoundation

/// Milo's recorded hello ("Hi, I'm Milo! You can read the page to me, or ask me anything you
/// wonder about."), played the moment he is turned on while the app connects in the background.
/// The clip is the agent's greeting in Milo's own realtime voice (scripts/generate_intro.py).
@MainActor
protocol IntroPlayer: AnyObject {
    /// Starts the intro. False if it can't play; then the agent greets him instead.
    /// `onFinish` runs once the intro has ended by itself (not after ``stop()``).
    func play(onFinish: @escaping @MainActor () -> Void) -> Bool
    func stop()
}

@MainActor
final class ClipIntroPlayer: NSObject, IntroPlayer, @preconcurrency AVAudioPlayerDelegate {
    static let file = "milo-intro.m4a"

    private let files: ContentFiles
    private let audioSession: ReadingAudioSession
    private var player: AVAudioPlayer?
    private var onFinish: (@MainActor () -> Void)?

    /// Plays through the same LiveKit-managed audio session as read-aloud, so it doesn't fight
    /// the call that is starting up.
    init(files: ContentFiles = .shared, audioSession: ReadingAudioSession? = nil) {
        self.files = files
        self.audioSession = audioSession ?? makeReadingAudioSession()
    }

    func play(onFinish: @escaping @MainActor () -> Void) -> Bool {
        stop()
        guard let url = files.url(Self.file) else { return false }
        audioSession.begin()
        guard let player = try? AVAudioPlayer(contentsOf: url) else {
            audioSession.end()
            return false
        }
        player.delegate = self
        guard player.play() else {
            audioSession.end()
            return false
        }
        self.player = player
        self.onFinish = onFinish
        return true
    }

    func stop() {
        guard let player else { return }
        player.delegate = nil
        player.stop()
        self.player = nil
        onFinish = nil
        audioSession.end()
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        finish(player)
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        finish(player)
    }

    private func finish(_ finished: AVAudioPlayer) {
        guard finished === player else { return }
        let done = onFinish
        player = nil
        onFinish = nil
        audioSession.end()
        done?()
    }
}
