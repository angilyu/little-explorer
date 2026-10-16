import SwiftUI

enum Theme {
    static let paper = Color(red: 1.0, green: 0.98, blue: 0.93)
    static let ink = Color(red: 0.16, green: 0.15, blue: 0.22)
    static let sky = Color(red: 0.86, green: 0.93, blue: 1.0)

    static func readingFont(regular: Bool) -> Font {
        .system(size: regular ? 46 : 30, weight: .bold, design: .rounded)
    }

    static func titleFont(regular: Bool) -> Font {
        .system(size: regular ? 52 : 36, weight: .heavy, design: .rounded)
    }
}

extension Color {
    /// Accepts "#RRGGBB" or "RRGGBB"; falls back to a friendly blue.
    init(hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet(charactersIn: "# "))
        guard cleaned.count == 6, let value = UInt32(cleaned, radix: 16) else {
            self = Color(red: 0.23, green: 0.43, blue: 0.65)
            return
        }
        self = Color(
            red: Double((value >> 16) & 0xFF) / 255,
            green: Double((value >> 8) & 0xFF) / 255,
            blue: Double(value & 0xFF) / 255
        )
    }
}

/// The country's flag as a small picture with a white border.
struct FlagBadge: View {
    let book: Book
    var height: CGFloat = 32

    var body: some View {
        if let name = book.flagImage, let image = UIImage(named: name) {
            Image(uiImage: image)
                .resizable()
                .scaledToFit()
                .frame(height: height)
                .clipShape(RoundedRectangle(cornerRadius: height * 0.15, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: height * 0.15, style: .continuous)
                        .strokeBorder(.white, lineWidth: 2)
                )
                .accessibilityLabel("Flag of \(book.country)")
        }
    }
}
