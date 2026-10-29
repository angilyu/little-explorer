import Foundation

/// DEBUG-only launch arguments used to take screenshots without tapping, e.g.
/// `xcrun simctl launch booted com.angilyu.littleexplorer -demoBook paris -demoPage 5 -demoVoice listening`
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
/// an early-morning star and a few weeks of reading unless `-demoStars` says otherwise) and two of them
/// new, so the "New badge!" celebration shows. `-demoBadges seen` has none new; `-demoBadges reading`
/// (any badge category id) has none new and opens that category.
/// `-demoGate` opens the grown-ups gate on the shelf; `-demoParent` opens the grown-ups corner without it.
/// `-demoAccount welcome|email|code` shows the signed-out welcome screen (and sign-in's email or code step);
/// `-demoAccount new` signs in as a new account with only the default books. Other demos run signed in to a
/// family account with every book.
/// `-demoMilo signin` shows Milo's consent screen right after a new email signs in; `-demoMilo talk` (with
/// `-demoBook`) taps "Talk to Milo" on an account that hasn't allowed Milo, which opens the grown-up check;
/// `-demoMilo talk-consent` shows the consent screen after it; `-demoMilo off` is an account whose grown-up
/// turned Milo off (with `-demoParent`, the Grown-ups corner's Milo switch is off); `-demoMilo turn-on` opens
/// the Grown-ups corner with Milo off and the consent its switch shows. Other demos have Milo allowed.
/// `-demoHide paris-french,london` hides those books; `-demoSections Tahoe,Paris` puts those sections first;
/// `-demoOrder Paris:paris-food,paris` orders a section's books (repeat the flag for more sections).
/// `-demoArrange` opens the grown-ups corner at "Arrange library" (with `-demoShelf`, scrolled to that book). `-demoLevels paris:1,london:3` sets those
/// books' reading levels (the bundled ones are all level 2).
/// `-demoUpgrade required` shows the update screen for a build that's no longer supported; `-demoUpgrade
/// recommended` shows the "Maybe later" update prompt.
struct DemoLaunch: Equatable {
    enum AccountScreen: String { case welcome, email, code, new }
    enum Upgrade: String { case required, recommended }
    enum Milo: String {
        case signin, talk, off
        case talkConsent = "talk-consent"
        case turnOn = "turn-on"

        /// The account's consent in this demo.
        var consent: Bool? {
            switch self {
            case .signin, .talk, .talkConsent: nil
            case .off, .turnOn: false
            }
        }

        /// Taps "Talk to Milo" when the book opens.
        var talks: Bool { self == .talk || self == .talkConsent }
    }


    var bookId: String?
    var pageIndex: Int = 0
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
    /// With `showBadges`: every earned badge already seen (no celebration).
    var badgesSeen = false
    /// With `showBadges`: the badge category to open.
    var badgeCategory: String?
    var accountScreen: AccountScreen?
    var milo: Milo?
    var upgrade: Upgrade?
    var hiddenBooks: [String] = []
    var sectionOrder: [String] = []
    var bookOrders: [String: [String]] = [:]
    var openArrange = false
    var levels: [String: Int] = [:]

    /// `books` with the demo's reading levels.
    func leveled(_ books: [Book]) -> [Book] {
        guard !levels.isEmpty else { return books }
        return books.map { book in
            var book = book
            if let level = levels[book.id] { book.level = BookLevel(level) }
            return book
        }
    }

    /// The shelf arrangement the demo starts with.
    var library: LibraryPrefs {
        var prefs = LibraryPrefs()
        for id in hiddenBooks { prefs.setHidden(id, true, at: 1) }
        if !sectionOrder.isEmpty { prefs.setSectionOrder(sectionOrder, at: 1) }
        for (section, order) in bookOrders { prefs.setBookOrder(order, in: section, at: 1) }
        return prefs
    }

    /// Who the demo is signed in as: nobody on the welcome and sign-in screens.
    var account: Account? {
        switch accountScreen {
        case .welcome, .email, .code: nil
        case .new: Account(id: "demo-new", email: "parent@example.com", books: Account.defaultBooks, miloConsent: miloConsent)
        case nil: Account(id: "demo-family", email: "family@example.com", books: nil, miloConsent: miloConsent)
        }
    }

    private var miloConsent: Bool? { milo.map(\.consent) ?? true }

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
        if arguments.contains("-demoRead") { demo.readAloud = true; found = true }
        if let stars = value(after: "-demoStars").flatMap(Int.init) { demo.seedStars = max(0, stars); found = true }
        if arguments.contains("-demoPassport") { demo.openPassport = true; found = true }
        if let book = value(after: "-demoShelf") { demo.shelfBook = book; found = true }
        if let coins = value(after: "-demoCoins").flatMap(Int.init) { demo.seedCoins = max(0, coins); found = true }
        if arguments.contains("-demoGate") { demo.openGate = true; found = true }
        if arguments.contains("-demoParent") { demo.openParent = true; found = true }
        func list(_ text: String) -> [String] {
            text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        }
        if let hide = value(after: "-demoHide") { demo.hiddenBooks = list(hide); found = true }
        if let sections = value(after: "-demoSections") { demo.sectionOrder = list(sections); found = true }
        for (index, flag) in arguments.enumerated() where flag == "-demoOrder" && index + 1 < arguments.count {
            let parts = arguments[index + 1].split(separator: ":", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            demo.bookOrders[parts[0]] = list(parts[1])
            found = true
        }
        if let levels = value(after: "-demoLevels") {
            for pair in list(levels) {
                let parts = pair.split(separator: ":").map(String.init)
                if parts.count == 2, let level = Int(parts[1]) { demo.levels[parts[0]] = level }
            }
            found = true
        }
        if arguments.contains("-demoArrange") { demo.openArrange = true; demo.openParent = true; found = true }
        if let screen = value(after: "-demoAccount").flatMap(AccountScreen.init(rawValue:)) {
            demo.accountScreen = screen; found = true
        }
        if let milo = value(after: "-demoMilo").flatMap(Milo.init(rawValue:)) {
            demo.milo = milo
            if milo == .signin, demo.accountScreen == nil { demo.accountScreen = .new }
            if milo == .turnOn { demo.openParent = true }
            found = true
        }
        if let upgrade = value(after: "-demoUpgrade").flatMap(Upgrade.init(rawValue:)) {
            demo.upgrade = upgrade; found = true
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
            if let option = value(after: "-demoBadges"), !option.hasPrefix("-") {
                demo.badgesSeen = true
                if option != "seen" { demo.badgeCategory = option }
            }
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
