import AudioToolbox

/// The soft "ding" when a star arrives.
enum StarChime {
    static func play() {
        AudioServicesPlaySystemSound(1057)
    }
}
