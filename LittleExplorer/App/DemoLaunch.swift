import Foundation

/// DEBUG-only launch arguments used to take screenshots without tapping, e.g.
/// `xcrun simctl launch booted com.angilyu.littleexplorer -demoBook paris -demoPage 5 -demoLove -demoVoice listening`
/// `-demoVoice live` turns real voice mode on at launch (needs the server running).
/// `-demoVoice nap` / `-demoVoice rest` shows Milo turning itself off (idle / daily limit).
/// `-demoRead` starts reading the opening page aloud, to show the word highlight.
struct DemoLaunch: Equatable {
    var bookId: String?
    var pageIndex: Int = 0
    var lovePage = false
    var voiceActivity: BuddyActivity?
    var voiceEnd: String?
    var liveVoice = false
    var readAloud = false

    var startsWithVoice: Bool { voiceActivity != nil || liveVoice }

    static let current: DemoLaunch? = {
        #if DEBUG
        return parse(ProcessInfo.processInfo.arguments)
        #else
        return nil
        #endif
    }()

    static func parse(_ arguments: [String]) -> DemoLaunch? {
        func value(after flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        var demo = DemoLaunch()
        var found = false
        if let book = value(after: "-demoBook") { demo.bookId = book; found = true }
        if let page = value(after: "-demoPage").flatMap(Int.init) { demo.pageIndex = max(0, page - 1); found = true }
        if arguments.contains("-demoLove") { demo.lovePage = true; found = true }
        if arguments.contains("-demoRead") { demo.readAloud = true; found = true }
        if let voice = value(after: "-demoVoice") {
            if voice == "live" {
                demo.liveVoice = true; found = true
            } else if voice == "nap" || voice == "rest" {
                demo.voiceActivity = .listening
                demo.voiceEnd = voice == "nap" ? "idle" : "limit"
                found = true
            } else if let activity = BuddyActivity(rawValue: voice) {
                demo.voiceActivity = activity; found = true
            }
        }
        return found ? demo : nil
    }
}
