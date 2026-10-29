import SwiftUI

/// The reading level's look: a candy-striped ribbon in the level's color saying "LEVEL 2". On a cover it's a slim
/// sash across the top-leading corner (`coverLevelBadge`); in lists it's a short ribbon tag (`LevelBadge`).
/// VoiceOver reads the level with the cover or row ("Level 2"), so both are hidden from it.
enum LevelRibbon {
    static func color(_ level: Int) -> Color {
        switch level {
        case 1: Color(hex: "#1E8449")
        case 3: Color(hex: "#6A3FC7")
        default: Color(hex: "#1565C0")
        }
    }

    static func label(_ level: Int) -> String { "LEVEL \(level)" }

    /// The ribbon's fabric: the level's color with fine, soft candy stripes.
    static func fabric(level: Int, stripe: CGFloat) -> some View {
        ZStack {
            color(level)
            RibbonStripes(stripe: stripe).fill(.white.opacity(0.12))
        }
    }

    /// Thin, faint dashed stitching along a ribbon's two long edges, `inset` points in.
    static func stitching(inset: CGFloat, lineWidth: CGFloat, dash: [CGFloat]) -> some View {
        RibbonStitching(inset: inset)
            .stroke(.white.opacity(0.5), style: StrokeStyle(lineWidth: lineWidth, dash: dash))
    }

    static func text(_ level: Int, size: CGFloat, kerning: CGFloat) -> some View {
        Text(label(level))
            .font(.system(size: size, weight: .heavy, design: .rounded))
            .kerning(kerning)
            .foregroundStyle(.white)
            .shadow(color: .black.opacity(0.25), radius: 0.5, x: 0, y: 0.5)
            .lineLimit(1)
    }
}

extension View {
    /// The reading-level sash over a cover's top area (photo or "being made" art).
    func coverLevelBadge(level: Int, regular: Bool) -> some View {
        overlay(alignment: .topLeading) {
            CoverLevelSash(level: level, regular: regular)
        }
    }
}

/// A slim diagonal band across the cover's top-leading corner saying the level.
struct CoverLevelSash: View {
    let level: Int
    let regular: Bool

    var body: some View {
        // The band's centre line crosses the corner `inset` points in from each edge.
        let inset: CGFloat = regular ? 34 : 22
        let thickness: CGFloat = regular ? 21 : 14
        let length = inset * 2 * 1.414 + thickness * 2
        ZStack {
            LevelRibbon.fabric(level: level, stripe: regular ? 5 : 3)
            LevelRibbon.stitching(inset: regular ? 3 : 2, lineWidth: regular ? 0.75 : 0.5, dash: regular ? [3, 2.5] : [2, 2])
            LevelRibbon.text(level, size: regular ? 13 : 9, kerning: regular ? 0.8 : 0.4)
                // Clears the dark spine on the leading edge.
                .offset(x: regular ? 4 : 3)
        }
        .frame(width: length, height: thickness)
        .compositingGroup()
        .shadow(color: .black.opacity(0.15), radius: 1, x: 0, y: 1)
        .rotationEffect(.degrees(-45))
        .position(x: inset, y: inset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .clipped()
        .accessibilityHidden(true)
    }
}

/// The level outside a cover (Arrange library, Make a book): a short piece of the cover's ribbon with notched ends.
struct LevelBadge: View {
    let level: Int

    var body: some View {
        LevelRibbon.text(level, size: 11, kerning: 0.6)
            .padding(.horizontal, 11)
            .frame(height: 20)
            .background {
                ZStack {
                    LevelRibbon.fabric(level: level, stripe: 3)
                    LevelRibbon.stitching(inset: 2.5, lineWidth: 0.5, dash: [2, 2])
                }
                .clipShape(NotchedRibbon(notch: 4))
            }
            .shadow(color: .black.opacity(0.15), radius: 1, x: 0, y: 1)
            .fixedSize()
            .accessibilityHidden(true)
    }
}

/// Parallel stripes leaning right, `stripe` points wide with equal gaps.
private struct RibbonStripes: Shape {
    var stripe: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        var x = rect.minX - rect.height
        while x < rect.maxX {
            path.move(to: CGPoint(x: x, y: rect.maxY))
            path.addLine(to: CGPoint(x: x + rect.height, y: rect.minY))
            path.addLine(to: CGPoint(x: x + rect.height + stripe, y: rect.minY))
            path.addLine(to: CGPoint(x: x + stripe, y: rect.maxY))
            path.closeSubpath()
            x += stripe * 2
        }
        return path
    }
}

/// Two lines along the top and bottom edges, `inset` points in.
private struct RibbonStitching: Shape {
    var inset: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY + inset))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY + inset))
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY - inset))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - inset))
        return path
    }
}

/// A rectangle with a V cut `notch` points into each end, like a cut ribbon.
private struct NotchedRibbon: Shape {
    var notch: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - notch, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + notch, y: rect.midY))
        path.closeSubpath()
        return path
    }
}
