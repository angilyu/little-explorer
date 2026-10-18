import SwiftUI

/// A sum a grown-up can do in their head in a few seconds but a 6-year-old can't guess:
/// a two-digit number times a single digit, plus a two-digit number (e.g. 23 × 7 + 38).
/// The answer is typed, not picked, so there is nothing to guess from.
struct ParentGateQuestion: Equatable {
    let a: Int
    let b: Int
    let c: Int

    var answer: Int { a * b + c }
    var text: String { "\(a) × \(b) + \(c)" }
    var spoken: String { "What is \(a) times \(b), plus \(c)?" }

    static let aRange = 12...29
    static let bRange = 3...9
    static let cRange = 11...49
    static let maxDigits = 4

    func isAnswer(_ typed: String) -> Bool { Int(typed) == answer }

    static func random() -> ParentGateQuestion {
        var generator = SystemRandomNumberGenerator()
        return random(using: &generator)
    }

    static func random<G: RandomNumberGenerator>(using generator: inout G) -> ParentGateQuestion {
        ParentGateQuestion(
            a: Int.random(in: aRange, using: &generator),
            b: Int.random(in: bRange, using: &generator),
            c: Int.random(in: cRange, using: &generator)
        )
    }
}

/// After a few wrong answers the gate rests for a while, so tapping numbers at random doesn't work.
/// Saved, so closing and reopening the gate doesn't start over.
struct ParentGateLock {
    static let maxMisses = 3
    static let cooldown: TimeInterval = 30

    private let defaults: UserDefaults
    private let missesKey = "parentGate.misses"
    private let lockedKey = "parentGate.lockedUntil"

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    var misses: Int { defaults.integer(forKey: missesKey) }
    var triesLeft: Int { Self.maxMisses - misses }

    func lockedUntil(now: Date = Date()) -> Date? {
        let until = defaults.double(forKey: lockedKey)
        return until > now.timeIntervalSince1970 ? Date(timeIntervalSince1970: until) : nil
    }

    /// Whole seconds left to wait (0 when open).
    func secondsLeft(now: Date = Date()) -> Int {
        guard let until = lockedUntil(now: now) else { return 0 }
        return Int(until.timeIntervalSince(now).rounded(.up))
    }

    /// A wrong answer; returns true if that locked the gate.
    @discardableResult
    func miss(now: Date = Date()) -> Bool {
        let count = misses + 1
        guard count >= Self.maxMisses else {
            defaults.set(count, forKey: missesKey)
            return false
        }
        defaults.set(0, forKey: missesKey)
        defaults.set(now.addingTimeInterval(Self.cooldown).timeIntervalSince1970, forKey: lockedKey)
        return true
    }

    func pass() {
        defaults.removeObject(forKey: missesKey)
        defaults.removeObject(forKey: lockedKey)
    }
}

/// Keeps grown-up screens (converting stars to coins, loved pages, settings, external links) away
/// from little fingers.
struct ParentGateView: View {
    let onPass: () -> Void
    var lock = ParentGateLock()

    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var question = ParentGateQuestion.random()
    @State private var typed = ""
    @State private var message: String?
    @State private var shake = 0

    private var regular: Bool { sizeClass == .regular }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait = lock.secondsLeft(now: context.date)
            ScrollView {
                VStack(spacing: 14) {
                    Image(systemName: "lock.shield.fill")
                        .font(.system(size: 44))
                        .foregroundStyle(.tint)
                    Text("Grown-ups only")
                        .font(.title.bold())
                    if wait > 0 {
                        locked(wait)
                    } else {
                        puzzle
                    }
                    Button("Cancel") { dismiss() }
                        .padding(.top, 4)
                }
                .padding(regular ? 24 : 20)
                .frame(maxWidth: 440)
                .frame(maxWidth: .infinity)
            }
        }
        .presentationDetents([.large])
        .passportSizing()
    }

    private func locked(_ wait: Int) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "hourglass")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text("Too many tries.")
                .font(.title3.bold())
            Text("Try again in \(wait) s.")
                .font(.title3.monospacedDigit())
                .foregroundStyle(.secondary)
                .contentTransition(.numericText())
        }
        .padding(.vertical, 40)
        .accessibilityElement(children: .combine)
    }

    private var puzzle: some View {
        VStack(spacing: 12) {
            Text("Type the answer to continue.")
                .foregroundStyle(.secondary)
            Text("\(question.text) = ?")
                .font(.system(size: regular ? 38 : 32, weight: .bold, design: .rounded).monospacedDigit())
                .accessibilityLabel(question.spoken)
            Text(typed.isEmpty ? " " : typed)
                .font(.system(size: regular ? 36 : 32, weight: .semibold, design: .rounded).monospacedDigit())
                .frame(width: 180, height: 56)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color(.secondarySystemBackground)))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(.tint, lineWidth: 2))
                .modifier(Shake(amount: CGFloat(shake)))
                .animation(.default, value: shake)
                .accessibilityLabel(typed.isEmpty ? "Answer, empty" : "Answer, \(typed)")
            Text(message ?? " ")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(minHeight: 22)
            keypad
        }
    }

    private var keypad: some View {
        let size: CGFloat = regular ? 56 : 64
        return Grid(horizontalSpacing: 14, verticalSpacing: 10) {
            ForEach([[1, 2, 3], [4, 5, 6], [7, 8, 9]], id: \.self) { row in
                GridRow {
                    ForEach(row, id: \.self) { digit in key("\(digit)", size: size) { type(digit) } }
                }
            }
            GridRow {
                key("delete.left", symbol: true, size: size) { if !typed.isEmpty { typed.removeLast() } }
                    .accessibilityLabel("Delete")
                key("0", size: size) { type(0) }
                key("OK", size: size, prominent: true) { submit() }
                    .disabled(typed.isEmpty)
            }
        }
    }

    private func key(_ label: String, symbol: Bool = false, size: CGFloat, prominent: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Group {
                if symbol { Image(systemName: label) } else { Text(label) }
            }
            .font(.system(size: 26, weight: .semibold, design: .rounded))
            .frame(width: size * 1.35, height: size)
        }
        .buttonStyle(.bordered)
        .tint(prominent ? .accentColor : .primary)
        .buttonBorderShape(.roundedRectangle(radius: 14))
    }

    private func type(_ digit: Int) {
        guard typed.count < ParentGateQuestion.maxDigits else { return }
        if typed == "0" { typed = "" }
        typed += "\(digit)"
    }

    private func submit() {
        guard !typed.isEmpty else { return }
        if question.isAnswer(typed) {
            lock.pass()
            onPass()
            return
        }
        shake += 1
        if lock.miss() {
            newQuestion(message: nil)
        } else {
            let left = lock.triesLeft
            newQuestion(message: "Not quite. Here's a new one (\(left) \(left == 1 ? "try" : "tries") left).")
        }
    }

    private func newQuestion(message: String?) {
        var next = ParentGateQuestion.random()
        while next == question { next = ParentGateQuestion.random() }
        question = next
        typed = ""
        self.message = message
    }
}

private struct Shake: GeometryEffect {
    var amount: CGFloat
    var animatableData: CGFloat {
        get { amount }
        set { amount = newValue }
    }

    func effectValue(size: CGSize) -> ProjectionTransform {
        ProjectionTransform(CGAffineTransform(translationX: 8 * sin(amount * .pi * 4), y: 0))
    }
}
