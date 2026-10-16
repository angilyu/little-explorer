import UIKit
import XCTest
@testable import LittleExplorer

final class ContentTests: XCTestCase {
    private func books() throws -> [Book] { try BookLibrary.loadBundled() }

    func testLoadsAllBooksInShelfOrder() throws {
        XCTAssertEqual(try books().map(\.id), ["paris", "paris-food", "paris-fun", "london", "barcelona"])
    }

    func testParisHasThreeThemedBooks() throws {
        let paris = try books().filter { $0.city == "Paris" }
        XCTAssertEqual(paris.map(\.theme), ["Famous Places", "Yummy Paris", "Fun & Art"])
        XCTAssertEqual(paris.map(\.coverTitle), ["Famous Places", "Yummy Paris", "Fun & Art"])
        XCTAssertEqual(paris[1].shelfTitle, "Paris: Yummy Paris")
        XCTAssertEqual(paris[2].openLabel, "Open the Fun & Art book about Paris")
        XCTAssertEqual(Set(paris.map(\.coverColor)).count, 3, "themed covers need different colors")
        let london = try XCTUnwrap(try books().first { $0.id == "london" })
        XCTAssertNil(london.theme)
        XCTAssertEqual(london.coverTitle, "London")
        XCTAssertEqual(london.openLabel, "Open the London book")
    }

    func testShelvesGroupBooksByCityInOrder() throws {
        let shelves = BookLibrary.shelves(try books())
        XCTAssertEqual(shelves.map(\.city), ["Paris", "London", "Barcelona"])
        XCTAssertEqual(shelves[0].books.map(\.id), ["paris", "paris-food", "paris-fun"])
        XCTAssertEqual(shelves[1].books.map(\.id), ["london"])
        XCTAssertEqual(shelves[2].books.map(\.id), ["barcelona"])
    }

    func testShelfRowsPackOneBookCitiesSideBySide() throws {
        let shelves = BookLibrary.shelves(try books())
        let cities = { (rows: [[CityShelf]]) in rows.map { $0.map(\.city) } }
        XCTAssertEqual(cities(BookLibrary.rows(shelves, columns: 3)), [["Paris"], ["London", "Barcelona"]])
        XCTAssertEqual(cities(BookLibrary.rows(shelves, columns: 2)), [["Paris"], ["London", "Barcelona"]])
        XCTAssertEqual(cities(BookLibrary.rows(shelves, columns: 1)), [["Paris"], ["London"], ["Barcelona"]])
        let reordered = [shelves[1], shelves[0], shelves[2]]
        XCTAssertEqual(cities(BookLibrary.rows(reordered, columns: 3)), [["London"], ["Paris"], ["Barcelona"]])
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

    func testDemoLaunchParsing() {
        XCTAssertNil(DemoLaunch.parse(["app"]))
        let demo = DemoLaunch.parse(["app", "-demoBook", "london", "-demoPage", "4", "-demoLove", "-demoVoice", "speaking"])
        XCTAssertEqual(demo, DemoLaunch(bookId: "london", pageIndex: 3, lovePage: true, voiceActivity: .speaking))
        XCTAssertEqual(DemoLaunch.parse(["app", "-demoBook", "paris-fun", "-demoRead"])?.readAloud, true)
        let live = DemoLaunch.parse(["app", "-demoBook", "paris", "-demoVoice", "live"])
        XCTAssertEqual(live?.liveVoice, true)
        XCTAssertNil(live?.voiceActivity)
        XCTAssertEqual(live?.startsWithVoice, true)
    }
}
