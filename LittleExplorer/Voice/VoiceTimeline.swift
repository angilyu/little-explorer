import Foundation
import os

/// The steps of waking Milo up, timed from the tap.
enum VoiceMilestone: String, Sendable, CaseIterable {
    case intro
    case token
    case connected
    case agentJoined = "agent joined"
    case microphone = "mic on"
    case listening
}

/// Times each step from the tap that turned Milo on, so the wait can be measured on a real device:
/// `Milo timing: token +0.42s`, and once he listens one summary line with every step. Filter the
/// device console (or `log stream`) on the `voice-timing` category. Only times, never words.
struct VoiceTimeline {
    static let log = Logger(subsystem: Bundle.main.bundleIdentifier ?? "LittleExplorer", category: "voice-timing")

    private let start: Date
    private(set) var marks: [VoiceMilestone: TimeInterval] = [:]

    init(tap: Date) {
        start = tap
        Self.log.notice("Milo timing: tap")
    }

    /// Records the first time `milestone` happens; later repeats are ignored.
    mutating func mark(_ milestone: VoiceMilestone, at date: Date) {
        guard marks[milestone] == nil else { return }
        let elapsed = date.timeIntervalSince(start)
        marks[milestone] = elapsed
        Self.log.notice("Milo timing: \(milestone.rawValue, privacy: .public) +\(elapsed, format: .fixed(precision: 2), privacy: .public)s")
        if milestone == .listening {
            let line = summary
            Self.log.notice("Milo timing: \(line, privacy: .public)")
        }
    }

    /// e.g. "tap→listening 2.31s (intro 0.04, token 0.52, connected 0.98, agent joined 1.80, mic on 5.25)".
    var summary: String {
        let steps = VoiceMilestone.allCases.filter { $0 != .listening }.compactMap { step in
            marks[step].map { "\(step.rawValue) \(String(format: "%.2f", $0))" }
        }
        let total = marks[.listening].map { String(format: "%.2fs", $0) } ?? "?"
        return "tap→listening \(total) (\(steps.joined(separator: ", ")))"
    }
}
