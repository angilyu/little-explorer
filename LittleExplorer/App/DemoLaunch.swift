import Foundation

/// DEBUG-only launch arguments used to take screenshots without tapping, e.g.
/// `xcrun simctl launch booted com.angilyu.littleexplorer -demoBook paris -demoPage 5 -demoLove -demoVoice listening`
/// `-demoVoice live` turns real voice mode on at launch (needs the server running).
/// `-demoVoice nap` / `-demoVoice rest` shows Milo turning itself off (idle / daily limit).
/// `-demoVoice star` turns Milo on and has him give a star for the opening page.
/// `-demoStars 23` seeds that many saved stars; `-demoPassport` opens the Passport.
/// `-demoRead` starts reading the opening page aloud, to show the word highlight.
/// `-demoShelf paris-french` scrolls the shelf to that book's cover (without opening it);
/// `-demoShelf grown-ups` scrolls to the Grown-ups button at the bottom.
/// `-demoCoins 2` seeds that many coins (made from 10 stars each, so they come off `-demoStars`).
/// `-demoCalendar` opens the Passport at its star calendar (with 60 stars over the last few weeks
/// unless `-demoStars` says otherwise).
/// `-demoBadges` opens the Passport at its badges, with some earned and some still to go (40 stars,
/// an early-morning star and 3 loved pages unless `-demoStars` says otherwise).
/// `-demoGate` opens the grown-ups gate; `-demoParent` opens the grown-ups corner without it.
/// `-demoAccount welcome|email|code` shows the signed-out welcome screen (and sign-in's email or code step);
/// `-demoAccount new` signs in as a new account with only the default books. Other demos run signed in to a
/// family account with every book.
struct DemoLaunch: Equatable {
    enum AccountScreen: String { case welcome, email, code, new }


    var bookId: String?
    var pageIndex: Int = 0
    var lovePage = false
    var voiceActivity: BuddyActivity?
    var voiceEnd: String?
    var liveVoice = false
    var readAloud = false
    var voiceStar = false
    var seedStars = 0
    var openPassport = false
    var shelfBook: String?
    var seedCoins = 0
    var openGate = false
    var openParent = false
    var showCalendar = false
    var showBadges = false
    var accountScreen: AccountScreen?

    /// Who the demo is signed in as: nobody on the welcome and sign-in screens.
    var account: Account? {
        switch accountScreen {
        case .welcome, .email, .code: nil
        case .new: Account(id: "demo-new", email: "parent@example.com", books: Account.defaultBooks)
        case nil: Account(id: "demo-family", email: "family@example.com", books: nil)
        }
    }

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
        if let stars = value(after: "-demoStars").flatMap(Int.init) { demo.seedStars = max(0, stars); found = true }
        if arguments.contains("-demoPassport") { demo.openPassport = true; found = true }
        if let book = value(after: "-demoShelf") { demo.shelfBook = book; found = true }
        if let coins = value(after: "-demoCoins").flatMap(Int.init) { demo.seedCoins = max(0, coins); found = true }
        if arguments.contains("-demoGate") { demo.openGate = true; found = true }
        if arguments.contains("-demoParent") { demo.openParent = true; found = true }
        if let screen = value(after: "-demoAccount").flatMap(AccountScreen.init(rawValue:)) {
            demo.accountScreen = screen; found = true
        }
        if arguments.contains("-demoCalendar") {
            demo.showCalendar = true
            demo.openPassport = true
            if demo.seedStars == 0 { demo.seedStars = 60 }
            found = true
        }
        if arguments.contains("-demoBadges") {
            demo.showBadges = true
            demo.openPassport = true
            if demo.seedStars == 0 { demo.seedStars = 40 }
            found = true
        }
        if let voice = value(after: "-demoVoice") {
            if voice == "star" {
                demo.voiceActivity = .listening
                demo.voiceStar = true
                found = true
            } else if voice == "live" {
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
