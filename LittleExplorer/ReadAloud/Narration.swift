import AVFoundation
import Foundation

/// A pre-recorded reading of one page sentence (OpenAI "coral" voice, made by
/// `scripts/generate_narration.py`) with the time each word is spoken.
struct Narration: Decodable, Equatable {
    struct Word: Decodable, Equatable {
        /// UTF-16 offset and length of the word in `text`, as `NSRange` wants.
        let location: Int
        let length: Int
        let start: TimeInterval
        let end: TimeInterval

        var range: NSRange { NSRange(location: location, length: length) }
    }

    let file: String
    let text: String
    let duration: TimeInterval
    let words: [Word]

    /// The word to highlight at `time`: the last one that has started, so it stays lit through short pauses.
    func word(at time: TimeInterval) -> Word? {
        words.last { $0.start <= time }
    }
}

/// The narration manifest (downloaded or bundled), keyed by ``key(bookId:pageId:)``: `book/page`.
struct NarrationLibrary {
    static func key(bookId: String, pageId: String) -> String { "\(bookId)/\(pageId)" }

    private struct Manifest: Decodable {
        let clips: [String: Narration]
    }

    let clips: [String: Narration]
    private let files: ContentFiles

    /// The current library; replaced when new books are downloaded.
    @MainActor static private(set) var current = NarrationLibrary(files: .shared)

    @MainActor static func reload() { current = NarrationLibrary(files: .shared) }

    /// Only the clips inside the app.
    static let bundled = NarrationLibrary(bundle: .main)

    init(bundle: Bundle) {
        self.init(files: .bundleOnly(bundle))
    }

    init(files: ContentFiles) {
        self.files = files
        if let data = files.data("narration.json"),
           let manifest = try? JSONDecoder().decode(Manifest.self, from: data) {
            clips = manifest.clips
        } else {
            clips = [:]
        }
    }

    /// The clip for this page, only if it still reads the page's current sentence.
    func narration(for pageId: String, text: String) -> Narration? {
        guard let clip = clips[pageId], clip.text == text, url(for: clip) != nil else { return nil }
        return clip
    }

    func url(for clip: Narration) -> URL? {
        files.url("\(clip.file).m4a")
    }
}

/// Plays a narration clip and reports the word being spoken, like `SpeechEngine` does for the system voice.
@MainActor
protocol NarrationPlayer: AnyObject {
    var events: ReadAloudController.EngineEvents? { get set }
    /// Returns false if the clip can't be played (the caller then falls back to the system voice).
    func play(_ narration: Narration) -> Bool
    func stop()
}

/// `AVAudioPlayer` behind `NarrationPlayer`; a display link drives the word highlight.
@MainActor
final class ClipPlayer: NSObject, NarrationPlayer, @preconcurrency AVAudioPlayerDelegate {
    var events: ReadAloudController.EngineEvents?
    private let library: NarrationLibrary?
    private var player: AVAudioPlayer?
    private var narration: Narration?
    private var link: CADisplayLink?
    private var lastWord: Int?

    /// `library` nil uses whatever `NarrationLibrary.current` is at play time.
    init(library: NarrationLibrary? = nil) {
        self.library = library
    }

    func play(_ narration: Narration) -> Bool {
        stop()
        guard let url = (library ?? NarrationLibrary.current).url(for: narration), let player = try? AVAudioPlayer(contentsOf: url) else { return false }
        player.delegate = self
        player.prepareToPlay()
        guard player.play() else { return false }
        self.player = player
        self.narration = narration
        let link = CADisplayLink(target: self, selector: #selector(tick))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 15, maximum: 30)
        link.add(to: .main, forMode: .common)
        self.link = link
        return true
    }

    func stop() {
        link?.invalidate()
        link = nil
        player?.delegate = nil
        player?.stop()
        player = nil
        narration = nil
        lastWord = nil
    }

    @objc private func tick() {
        guard let player, let narration, let word = narration.word(at: player.currentTime) else { return }
        guard word.location != lastWord else { return }
        lastWord = word.location
        events?.willSpeak(word.range)
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        guard player === self.player else { return }
        stop()
        events?.finished()
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        guard player === self.player else { return }
        stop()
        events?.finished()
    }
}
