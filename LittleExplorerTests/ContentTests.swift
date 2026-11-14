import UIKit
import XCTest
@testable import LittleExplorer

final class ContentTests: XCTestCase {
    private func books() throws -> [Book] { try BookLibrary.loadBundled() }

    func testLoadsAllBooksInShelfOrder() throws {
        XCTAssertEqual(try books().map(\.id), ["paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "barcelona-park-guell", "barcelona-sagrada", "barcelona-casa-batllo", "barcelona-tibidabo", "barcelona-montjuic", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu", "chengdu", "longyan", "beijing", "beijing-great-wall", "beijing-forbidden-city", "beijing-summer-palace", "beijing-temple-of-heaven", "shanghai", "israel-tel-aviv", "israel-dead-sea", "israel-hebrew", "travel-flight", "travel-packing", "travel-passport", "travel-hotel", "travel-jetlag"])
    }

    func testParisHasNineThemedBooks() throws {
        let paris = try books().filter { $0.city == "Paris" }
        XCTAssertEqual(paris.map(\.theme), ["Famous Places", "Yummy Paris", "Fun & Art", "Notre-Dame", "Eiffel Tower", "Jardin d'Acclimatation", "Louvre Museum", "Palace of Versailles", "Seine River Cruise", "Speak French"])
        XCTAssertEqual(paris.map(\.coverTitle), ["Famous Places", "Yummy Paris", "Fun & Art", "Notre-Dame", "Eiffel Tower", "Jardin d'Acclimatation", "Louvre Museum", "Palace of Versailles", "Seine River Cruise", "Speak French"])
        XCTAssertEqual(paris[1].shelfTitle, "Paris: Yummy Paris")
        XCTAssertEqual(paris[2].openLabel, "Open the Fun & Art book about Paris")
        XCTAssertEqual(Set(try books().map(\.coverColor)).count, try books().count, "themed covers need different colors")
        let london = try XCTUnwrap(try books().first { $0.id == "london" })
        XCTAssertNil(london.theme)
        XCTAssertEqual(london.coverTitle, "London")
        XCTAssertEqual(london.openLabel, "Open the London book")
    }

    func testShelvesGroupBooksByCityInOrder() throws {
        let shelves = BookLibrary.shelves(try books())
        XCTAssertEqual(shelves.map(\.city), ["Paris", "London", "Barcelona", "Lake Tahoe", "Hawaii", "Chengdu", "Longyan", "Beijing", "Shanghai", "Israel", "Travel"])
        XCTAssertEqual(shelves[0].books.map(\.id), ["paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french"])
        XCTAssertEqual(shelves[1].books.map(\.id), ["london", "london-big-ben"])
        XCTAssertEqual(shelves[1].books.map(\.coverTitle), ["London", "Big Ben"])
        XCTAssertEqual(shelves[2].books.map(\.id), ["barcelona", "barcelona-park-guell", "barcelona-sagrada", "barcelona-casa-batllo", "barcelona-tibidabo", "barcelona-montjuic"])
        XCTAssertEqual(shelves[2].books.map(\.coverTitle), ["Barcelona", "Park Güell", "Sagrada Família", "Casa Batlló", "Tibidabo", "Montjuïc Cable Car"])
        XCTAssertEqual(shelves[3].books.map(\.id), ["tahoe", "tahoe-heavenly", "tahoe-northstar"])
        XCTAssertEqual(shelves[3].books.map(\.coverTitle), ["Lake Tahoe", "Heavenly", "Northstar"])
        XCTAssertEqual(shelves[4].books.map(\.id), ["hawaii", "hawaii-honolulu"])
        XCTAssertEqual(shelves[4].books.map(\.coverTitle), ["Hawaii", "Honolulu"])
        XCTAssertEqual(shelves[5].books.map(\.id), ["chengdu"])
        XCTAssertEqual(shelves[6].books.map(\.id), ["longyan"])
        XCTAssertEqual(shelves[6].books.map(\.coverTitle), ["Longyan"])
        XCTAssertEqual(shelves[7].books.map(\.id), ["beijing", "beijing-great-wall", "beijing-forbidden-city", "beijing-summer-palace", "beijing-temple-of-heaven"])
        XCTAssertEqual(shelves[7].books.map(\.coverTitle), ["Beijing", "Great Wall", "Forbidden City", "Summer Palace", "Temple of Heaven"])
        XCTAssertEqual(shelves[8].books.map(\.id), ["shanghai"])
        XCTAssertEqual(shelves[8].books.map(\.coverTitle), ["Shanghai"])
        XCTAssertEqual(shelves[9].books.map(\.id), ["israel-tel-aviv", "israel-dead-sea", "israel-hebrew"])
        XCTAssertEqual(shelves[9].books.map(\.coverTitle), ["Tel Aviv", "Dead Sea", "Hebrew Words"])
        XCTAssertFalse(shelves[9].isTravel)
        XCTAssertEqual(shelves[10].books.map(\.coverTitle), ["Take a Flight", "Pack Your Bag", "Passport", "Hotel Stay", "Jet Lag"])
        XCTAssertTrue(shelves[10].isTravel)
        XCTAssertEqual(shelves.filter(\.isTravel).map(\.city), ["Travel"])
    }

    func testShelfRowsPackOneBookCitiesSideBySide() throws {
        let shelves = BookLibrary.shelves(try books().filter { ["Paris", "London", "Barcelona"].contains($0.city) && $0.id != "london-big-ben" && !$0.id.hasPrefix("barcelona-") })
        let cities = { (rows: [[CityShelf]]) in rows.map { $0.map(\.city) } }
        XCTAssertEqual(cities(BookLibrary.rows(shelves, columns: 3)), [["Paris"], ["London", "Barcelona"]])
        XCTAssertEqual(cities(BookLibrary.rows(shelves, columns: 2)), [["Paris"], ["London", "Barcelona"]])
        XCTAssertEqual(cities(BookLibrary.rows(shelves, columns: 1)), [["Paris"], ["London"], ["Barcelona"]])
        let reordered = [shelves[1], shelves[0], shelves[2]]
        XCTAssertEqual(cities(BookLibrary.rows(reordered, columns: 3)), [["London"], ["Paris"], ["Barcelona"]])
        let all = BookLibrary.shelves(try books())
        XCTAssertEqual(cities(BookLibrary.rows(all, columns: 3)), [["Paris"], ["London"], ["Barcelona"], ["Lake Tahoe"], ["Hawaii"], ["Chengdu", "Longyan"], ["Beijing"], ["Shanghai"], ["Israel"], ["Travel"]])
    }

    func testEachBookHasTenToFourteenUniquePages() throws {
        for book in try books() {
            XCTAssertTrue((10...14).contains(book.pages.count), "\(book.id) has \(book.pages.count) pages")
            XCTAssertEqual(Set(book.pages.map(\.id)).count, book.pages.count, "\(book.id) has duplicate page ids")
        }
    }

    func testPageTextIsShortEnoughForEarlyReaders() throws {
        for book in try books() {
            for page in book.pages {
                let words = page.text.split(whereSeparator: \.isWhitespace).count
                XCTAssertTrue((4...12).contains(words), "\(book.id)/\(page.id): \(words) words")
                XCTAssertGreaterThan(page.about.count, page.text.count, "\(book.id)/\(page.id) needs buddy notes")
                XCTAssertFalse(page.emoji.isEmpty)
            }
        }
    }

    func testVisibleTextHasNoPronunciationHints() throws {
        // The read-aloud voice says each word, so pages don't spell out sounds like "Honu (HO-noo)".
        for book in try books() {
            for label in [book.coverTitle, book.tagline] {
                XCTAssertNil(label.rangeOfCharacter(from: CharacterSet(charactersIn: "()")), "\(book.id): \(label)")
            }
            for page in book.pages {
                XCTAssertNil(page.text.rangeOfCharacter(from: CharacterSet(charactersIn: "()")), "\(book.id)/\(page.id): \(page.text)")
            }
        }
    }

    func testEveryPhotoIsVerifiedCreditedAndBundled() throws {
        var files = Set<String>()
        var titles = Set<String>()
        for book in try books() {
            for page in book.pages {
                let photo = page.photo
                let key = "\(book.id)/\(page.id)"
                XCTAssertTrue(photo.isVerified, "\(key) license not verified")
                XCTAssertNotNil(photo.author, key)
                XCTAssertNotNil(photo.license, key)
                XCTAssertNotNil(photo.source, key)
                let license = photo.license ?? ""
                XCTAssertFalse(license.contains("NC") || license.contains("ND"), "\(key) has non-free license \(license)")
                let file = try XCTUnwrap(photo.file, key)
                XCTAssertTrue(files.insert(file).inserted, "\(file) used twice")
                let title = try XCTUnwrap(photo.commonsTitle, key)
                XCTAssertTrue(titles.insert(title).inserted, "\(key) reuses \(title)")
                XCTAssertNotNil(UIImage(named: file), "\(file) missing from bundle")
            }
        }
    }

    func testDecodeRejectsEmptyBook() {
        let json = ##"{"id":"x","city":"X","country":"Y","flag":"🏳️","coverColor":"#000000","tagline":"t","pages":[]}"##
        XCTAssertThrowsError(try BookLibrary.decode(Data(json.utf8))) { error in
            XCTAssertEqual(error as? ContentError, .emptyBook("x"))
        }
    }

    func testMissingBookThrows() {
        XCTAssertThrowsError(try BookLibrary.loadBundled(ids: ["atlantis"])) { error in
            XCTAssertEqual(error as? ContentError, .missingBook("atlantis"))
        }
    }

    func testPageContextAttributesMatchAgentKeys() throws {
        let paris = try XCTUnwrap(try books().first)
        let context = PageContext(book: paris, index: 4)
        XCTAssertEqual(context.pageNumber, 5)
        XCTAssertEqual(context.attributes["book"], "paris")
        XCTAssertEqual(context.attributes["page"], paris.pages[4].id)
        XCTAssertEqual(context.attributes["page_number"], "5")
        XCTAssertEqual(context.attributes["page_text"], paris.pages[4].text)
    }

    func testPhotoCreditDetails() throws {
        let photo = PhotoInfo(file: "pup.jpg", commonsTitle: "File:Dalmatian_puppy at the park.JPG", query: "dalmatian",
                              author: "leo gonzales", license: "CC BY-NC 2.0",
                              licenseUrl: "https://creativecommons.org/licenses/by-nc/2.0", sourceUrl: nil, verified: true)
        XCTAssertEqual(photo.displayTitle, "Dalmatian puppy at the park")
        XCTAssertEqual(photo.licenseLink?.absoluteString, "https://creativecommons.org/licenses/by-nc/2.0")
        XCTAssertEqual(photo.source?.host(), "commons.wikimedia.org")
        XCTAssertEqual(photo.creditLine, "Photo: leo gonzales · CC BY-NC 2.0 · Wikimedia Commons")
        let bare = PhotoInfo(file: nil, commonsTitle: nil, query: nil, author: nil, license: nil,
                             licenseUrl: "javascript:alert(1)", sourceUrl: nil, verified: nil)
        XCTAssertNil(bare.displayTitle)
        XCTAssertNil(bare.licenseLink)
        XCTAssertNil(bare.source)
        XCTAssertFalse(bare.hasCredit)
    }

    func testOpenversePhotoCreditNamesWhereItCameFrom() throws {
        let json = """
        {"file": "hnl-p02.jpg", "title": "Hawaiian Sea Turtle 2", "query": "Hawaiian sea turtle", "verified": true,
         "author": "Edmund Garman", "license": "CC BY 2.0", "licenseUrl": "https://creativecommons.org/licenses/by/2.0/",
         "sourceUrl": "https://www.flickr.com/photos/40315625@N08/16188765882", "sourceName": "Flickr (via Openverse)",
         "authorUrl": "https://www.flickr.com/photos/40315625@N08", "provider": "flickr", "openverseId": "abc"}
        """
        let photo = try JSONDecoder().decode(PhotoInfo.self, from: Data(json.utf8))
        XCTAssertTrue(photo.hasCredit)
        XCTAssertNil(photo.commonsTitle)
        XCTAssertNil(photo.remoteURL, "an Openverse photo has no Commons file to fall back to")
        XCTAssertEqual(photo.displayTitle, "Hawaiian Sea Turtle 2")
        XCTAssertEqual(photo.sourceLabel, "Flickr (via Openverse)")
        XCTAssertEqual(photo.source?.host(), "www.flickr.com")
        XCTAssertEqual(photo.creditLine, "Photo: Edmund Garman · CC BY 2.0 · Flickr (via Openverse)")
        let commons = try JSONDecoder().decode(PhotoInfo.self, from: Data(#"{"commonsTitle": "File:A.jpg"}"#.utf8))
        XCTAssertEqual(commons.sourceLabel, "Wikimedia Commons")
        XCTAssertTrue(commons.hasCredit)
    }

    func testMadeBooksShowTheCreditBehindAButtonAndListEveryPhoto() throws {
        let page = try String(contentsOf: Self.appFile("Views/PageView.swift"), encoding: .utf8)
        XCTAssertTrue(page.contains("if book.isMade, page.photo.hasCredit { creditButton }"))
        XCTAssertTrue(page.contains("if !book.isMade {"), "bundled books keep the credit line under the photo")
        XCTAssertTrue(page.contains("\"info.circle\"") && page.contains(".accessibilityLabel(\"Photo credit\")"))
        let made = try String(contentsOf: Self.appFile("MakeBook/MadeBookViews.swift"), encoding: .utf8)
        XCTAssertTrue(made.contains("MadeBookCreditsView(book: book)"), "the made book's menu lists every photo credit")
        XCTAssertTrue(made.contains("PhotoCreditRows(photo: page.photo, opensLinks: true)"))
    }

    private static func appFile(_ path: String) -> URL {
        URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("LittleExplorer").appendingPathComponent(path)
    }

    func testDemoLaunchParsing() throws {
        let credit = try XCTUnwrap(DemoLaunch.parse(["app", "-demoMake", "made", "-demoBook", MakeDemo.bookId, "-demoPage", "3", "-demoCredit"]))
        XCTAssertEqual(credit.showCredit, true)
        XCTAssertEqual(credit.pageIndex, 2)
        let credits = try XCTUnwrap(DemoLaunch.parse(["app", "-demoMake", "credits"]))
        XCTAssertEqual(credits.make, .credits)
        XCTAssertEqual(credits.openParent, true)
        XCTAssertNil(DemoLaunch.parse(["app"]))
        let demo = DemoLaunch.parse(["app", "-demoBook", "london", "-demoPage", "4", "-demoVoice", "speaking"])
        XCTAssertEqual(demo, DemoLaunch(bookId: "london", pageIndex: 3, voiceActivity: .speaking))
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoBook", "paris-fun", "-demoRead"])?.readAloud, true)
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoShelf", "paris-french"])?.shelfBook, "paris-french")
        let live = DemoLaunch.parse(["app", "-demoBook", "paris", "-demoVoice", "live"])
        XCTAssertEqual(live?.liveVoice, true)
        XCTAssertNil(live?.voiceActivity)
        XCTAssertEqual(live?.startsWithVoice, true)
        let star = DemoLaunch.parse(["app", "-demoStars", "23", "-demoPassport", "-demoVoice", "star"])
        XCTAssertEqual(star?.seedStars, 23)
        XCTAssertEqual(star?.openPassport, true)
        XCTAssertEqual(star?.voiceStar, true)
        XCTAssertEqual(star?.voiceActivity, .listening)
        let badges = DemoLaunch.parse(["app", "-demoBadges"])
        XCTAssertEqual(badges, DemoLaunch(seedStars: 40, openPassport: true, showBadges: true))
        let signIn = try XCTUnwrap(DemoLaunch.parse(["app", "-demoMilo", "signin"]))
        XCTAssertEqual(signIn.accountScreen, .new)
        XCTAssertNil(signIn.account?.miloConsent)
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoMilo", "talk-consent"])?.milo, .talkConsent)
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoMilo", "off"])?.account?.miloConsent, false)
        let turnOn = try XCTUnwrap(DemoLaunch.parse(["app", "-demoMilo", "turn-on"]))
        XCTAssertEqual(turnOn.milo, .turnOn)
        XCTAssertTrue(turnOn.openParent)
        XCTAssertEqual(turnOn.account?.miloConsent, false)
        XCTAssertNil(DemoLaunch.parse(["app", "-demoMilo", "confirm"]), "the already-on version is gone")
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoGate"])?.account?.miloAllowed, true)
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoStars", "12", "-demoBadges"])?.seedStars, 12)
        let seen = try XCTUnwrap(DemoLaunch.parse(["app", "-demoBadges", "seen"]))
        XCTAssertTrue(seen.badgesSeen)
        XCTAssertNil(seen.badgeCategory)
        let places = try XCTUnwrap(DemoLaunch.parse(["app", "-demoBadges", "places"]))
        XCTAssertTrue(places.badgesSeen)
        XCTAssertEqual(places.badgeCategory, "places")
        let followed = try XCTUnwrap(DemoLaunch.parse(["app", "-demoBadges", "-demoStars", "12"]))
        XCTAssertFalse(followed.badgesSeen, "the next flag is not a badge option")
        XCTAssertEqual(followed.seedStars, 12)
    }
}
