import UIKit
import XCTest
@testable import LittleExplorer

final class WelcomeTests: XCTestCase {
    private func books() throws -> [Book] { try BookLibrary.loadBundled() }

    func testWelcomeFanUsesBundledVerifiedPhotosFromStarterBooks() throws {
        let cards = WelcomeCard.cards(from: try books())
        XCTAssertEqual(cards.map(\.title), ["Croissants", "Big Ben", "Mosaic dragon", "Eiffel Tower", "Giant panda", "Great Wall", "Sagrada Família"])
        let starters = Set(Account.defaultBooks)
        for card in cards {
            XCTAssertTrue(starters.contains(card.flagBook.id), "\(card.title) isn't in a new account's books")
            XCTAssertTrue(card.photo.isVerified, "\(card.title) photo license isn't verified")
            let file = try XCTUnwrap(card.photo.file, card.title)
            XCTAssertNotNil(UIImage(named: file), "\(file) missing from bundle")
        }
    }

    func testSmallerFansKeepTheEiffelTowerInTheMiddle() throws {
        let books = try books()
        XCTAssertEqual(WelcomeCard.cards(from: books, count: 5).map(\.title), ["Big Ben", "Mosaic dragon", "Eiffel Tower", "Giant panda", "Great Wall"])
        XCTAssertEqual(WelcomeCard.cards(from: books, count: 3).map(\.title), ["Mosaic dragon", "Eiffel Tower", "Giant panda"])
        XCTAssertEqual(WelcomeCard.cards(from: books, count: 99).count, WelcomeCard.picks.count)
        XCTAssertTrue(WelcomeCard.cards(from: books, count: 0).isEmpty)
    }

    func testMissingBooksAreSkipped() throws {
        let cards = WelcomeCard.cards(from: try books().filter { !$0.id.hasPrefix("beijing") })
        XCTAssertEqual(cards.map(\.title), ["Croissants", "Big Ben", "Mosaic dragon", "Eiffel Tower", "Sagrada Família"])
        XCTAssertTrue(WelcomeCard.cards(from: []).isEmpty)
    }

    func testCreditsNameEveryPhotographerAndLicense() throws {
        let cards = WelcomeCard.cards(from: try books())
        let credits = WelcomeCard.credits(cards)
        XCTAssertTrue(credits.hasPrefix("Photos from Wikimedia Commons."))
        for card in cards {
            XCTAssertTrue(credits.contains("\(card.title): \(try XCTUnwrap(card.photo.author)), \(try XCTUnwrap(card.photo.license))"))
        }
    }

    /// App Store guideline 2.3.8 keeps "For Kids" and "For Children" for the Kids category's own labels.
    func testWelcomeWordsAvoidReservedPhrases() {
        let words = ([WelcomeView.headline, WelcomeView.subline] + WelcomeView.features.flatMap { [$0.title, $0.text] })
            .joined(separator: " ").lowercased()
        XCTAssertFalse(words.contains("for kids"))
        XCTAssertFalse(words.contains("for children"))
    }
}
