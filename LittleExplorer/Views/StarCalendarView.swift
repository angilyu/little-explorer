import SwiftUI

/// The last Passport section: a month calendar of how many stars he earned on each day (device
/// time zone), with the month's total; arrows or a sideways swipe go to earlier months.
struct StarCalendarView: View {
    let days: StarDays
    let regular: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: regular ? 14 : 10) {
            StarMonthGrid(days: days, regular: regular)
            if days.datedStars == 0 && days.undatedStars == 0 {
                note("Answer Milo's questions and your stars will show up here!")
            }
            if days.undatedStars > 0 {
                note(
                    days.undatedStars == 1
                        ? "1 older star doesn't have a day, so it isn't on the calendar."
                        : "\(days.undatedStars) older stars don't have a day, so they aren't on the calendar."
                )
            }
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.system(size: regular ? 17 : 14, weight: .semibold, design: .rounded))
            .foregroundStyle(Theme.ink.opacity(0.7))
            .frame(maxWidth: .infinity)
            .multilineTextAlignment(.center)
    }
}

private enum CalendarText {
    static func string(_ date: Date, template: String, calendar: Calendar) -> String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.locale = calendar.locale ?? .current
        formatter.setLocalizedDateFormatFromTemplate(template)
        return formatter.string(from: date)
    }

    static func stars(_ n: Int) -> String { n == 1 ? "1 star" : "\(n) stars" }

    /// "October 18, today, 3 stars" for VoiceOver.
    static func dayLabel(_ date: Date, stars: Int, isToday: Bool, calendar: Calendar) -> String {
        let day = string(date, template: "MMMMd", calendar: calendar)
        return [day, isToday ? "today" : nil, stars == 0 ? "no stars" : Self.stars(stars)]
            .compactMap { $0 }.joined(separator: ", ")
    }
}

/// Big round arrow buttons and a title between them; swiping the content also pages.
private struct PagerHeader: View {
    let title: String
    let canGoBack: Bool
    let canGoForward: Bool
    let backLabel: String
    let forwardLabel: String
    let regular: Bool
    let move: (Int) -> Void

    var body: some View {
        HStack {
            arrow("chevron.left", enabled: canGoBack, label: backLabel) { move(-1) }
            Spacer(minLength: 4)
            Text(title)
                .font(.system(size: regular ? 26 : 21, weight: .heavy, design: .rounded))
                .foregroundStyle(Theme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 4)
            arrow("chevron.right", enabled: canGoForward, label: forwardLabel) { move(1) }
        }
    }

    private func arrow(_ name: String, enabled: Bool, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: name)
                .font(.system(size: regular ? 22 : 18, weight: .heavy))
                .foregroundStyle(enabled ? Color.white : Theme.ink.opacity(0.25))
                .frame(width: regular ? 52 : 44, height: regular ? 52 : 44)
                .background(Circle().fill(enabled ? Color.orange : Theme.ink.opacity(0.08)))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

private struct TotalPill: View {
    let text: String
    let regular: Bool

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "star.fill").foregroundStyle(Color.starGold)
            Text(text)
        }
        .font(.system(size: regular ? 19 : 16, weight: .heavy, design: .rounded))
        .foregroundStyle(Theme.ink)
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(Capsule().fill(Color.starGold.opacity(0.2)))
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}

/// A gold star with the day's count written on it.
private struct CountStar: View {
    let count: Int
    let size: CGFloat

    var body: some View {
        ZStack {
            Image(systemName: "star.fill")
                .font(.system(size: size, weight: .bold))
                .foregroundStyle(Color.starGold)
                .shadow(color: .orange.opacity(0.35), radius: 1, y: 1)
            Text("\(count)")
                .font(.system(size: size * (count >= 10 ? 0.36 : 0.44), weight: .black, design: .rounded))
                .foregroundStyle(Theme.ink)
                .offset(y: size * 0.05)
        }
    }
}

private extension View {
    /// Pages left/right on a sideways swipe, leaving up/down for the Passport's scroll.
    func pageSwipe(_ move: @escaping (Int) -> Void) -> some View {
        simultaneousGesture(
            DragGesture(minimumDistance: 24).onEnded { value in
                let dx = value.translation.width
                guard abs(dx) > 50, abs(dx) > abs(value.translation.height) * 1.5 else { return }
                move(dx < 0 ? 1 : -1)
            }
        )
    }
}

private struct StarMonthGrid: View {
    let days: StarDays
    let regular: Bool

    @State private var monthStart: Date?
    @State private var forward = true

    private var calendar: Calendar { days.calendar }
    private var thisMonth: Date { calendar.dateInterval(of: .month, for: Date())?.start ?? Date() }
    private var firstMonth: Date {
        guard let first = days.firstDate, let start = calendar.dateInterval(of: .month, for: first)?.start else {
            return thisMonth
        }
        return min(start, thisMonth)
    }
    private var shown: Date { monthStart ?? thisMonth }

    var body: some View {
        VStack(spacing: regular ? 14 : 10) {
            PagerHeader(
                title: CalendarText.string(shown, template: "MMMMyyyy", calendar: calendar),
                canGoBack: shown > firstMonth, canGoForward: shown < thisMonth,
                backLabel: "Previous month", forwardLabel: "Next month", regular: regular, move: move
            )
            TotalPill(
                text: "\(CalendarText.stars(days.stars(inMonthOf: shown))) in \(CalendarText.string(shown, template: "MMMM", calendar: calendar))",
                regular: regular
            )
            weekdayHeader
            grid
                .id(shown)
                .transition(.push(from: forward ? .trailing : .leading))
        }
        .padding(regular ? 22 : 12)
        .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(.white))
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .contentShape(Rectangle())
        .pageSwipe(move)
    }

    private func move(_ step: Int) {
        guard let next = calendar.date(byAdding: .month, value: step, to: shown),
              next >= firstMonth, next <= thisMonth else { return }
        forward = step > 0
        withAnimation(.easeInOut(duration: 0.3)) { monthStart = next }
    }

    private var columns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: regular ? 8 : 4), count: 7)
    }

    private var weekdayHeader: some View {
        let symbols = calendar.veryShortStandaloneWeekdaySymbols
        let first = calendar.firstWeekday - 1
        let ordered = Array(symbols[first...] + symbols[..<first])
        return LazyVGrid(columns: columns, spacing: 0) {
            ForEach(Array(ordered.enumerated()), id: \.offset) { _, symbol in
                Text(symbol)
                    .font(.system(size: regular ? 17 : 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(Theme.ink.opacity(0.55))
            }
        }
        .accessibilityHidden(true)
    }

    private var grid: some View {
        let count = calendar.range(of: .day, in: .month, for: shown)?.count ?? 30
        let leading = (calendar.component(.weekday, from: shown) - calendar.firstWeekday + 7) % 7
        let today = days.day(of: Date())
        return LazyVGrid(columns: columns, spacing: regular ? 8 : 4) {
            ForEach(0..<leading, id: \.self) { i in Color.clear.frame(height: cellHeight).id("blank-\(i)") }
            ForEach(0..<count, id: \.self) { offset in
                let date = calendar.date(byAdding: .day, value: offset, to: shown) ?? shown
                let key = days.day(of: date)
                MonthDayCell(
                    number: offset + 1, stars: days.counts[key] ?? 0, isToday: key == today, isFuture: key > today,
                    height: cellHeight, regular: regular,
                    label: CalendarText.dayLabel(date, stars: days.counts[key] ?? 0, isToday: key == today, calendar: calendar)
                )
            }
        }
    }

    private var cellHeight: CGFloat { regular ? 84 : 54 }
}

private struct MonthDayCell: View {
    let number: Int
    let stars: Int
    let isToday: Bool
    let isFuture: Bool
    let height: CGFloat
    let regular: Bool
    let label: String

    var body: some View {
        VStack(spacing: 0) {
            Text("\(number)")
                .font(.system(size: regular ? 17 : 12, weight: isToday ? .black : .bold, design: .rounded))
                .foregroundStyle(isToday ? Color.white : Theme.ink.opacity(0.6))
                .padding(.horizontal, 5)
                .background(Capsule().fill(isToday ? Color.orange : .clear))
            Spacer(minLength: 0)
            if stars > 0 {
                CountStar(count: stars, size: regular ? 46 : 30)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, regular ? 6 : 3)
        .frame(maxWidth: .infinity)
        .frame(height: height)
        .background(
            RoundedRectangle(cornerRadius: regular ? 14 : 10, style: .continuous)
                .fill(stars > 0 ? Color.starGold.opacity(0.16) : Theme.sky.opacity(0.35))
        )
        .overlay(
            RoundedRectangle(cornerRadius: regular ? 14 : 10, style: .continuous)
                .strokeBorder(isToday ? Color.orange : .clear, lineWidth: 3)
        )
        .opacity(isFuture ? 0.4 : 1)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
    }
}
