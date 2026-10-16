import SwiftUI

struct ParentGateQuestion: Equatable {
    let a: Int
    let b: Int
    let choices: [Int]

    var answer: Int { a * b }

    static func random() -> ParentGateQuestion {
        var generator = SystemRandomNumberGenerator()
        return random(using: &generator)
    }

    static func random<G: RandomNumberGenerator>(using generator: inout G) -> ParentGateQuestion {
        let a = Int.random(in: 6...9, using: &generator)
        let b = Int.random(in: 6...9, using: &generator)
        var choices: Set<Int> = [a * b]
        while choices.count < 4 {
            let offset = Int.random(in: -12...12, using: &generator)
            if offset != 0 { choices.insert(a * b + offset) }
        }
        return ParentGateQuestion(a: a, b: b, choices: Array(choices).shuffled(using: &generator))
    }
}

/// Keeps grown-up screens (loved pages, settings, external links) away from little fingers.
struct ParentGateView: View {
    let onPass: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var question = ParentGateQuestion.random()
    @State private var wrong = false

    var body: some View {
        VStack(spacing: 24) {
            Image(systemName: "lock.shield.fill")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("Grown-ups only")
                .font(.title.bold())
            Text("What is \(question.a) × \(question.b)?")
                .font(.title2)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                ForEach(question.choices, id: \.self) { choice in
                    Button("\(choice)") {
                        if choice == question.answer {
                            onPass()
                        } else {
                            wrong = true
                            question = ParentGateQuestion.random()
                        }
                    }
                    .font(.title2.monospacedDigit())
                    .frame(maxWidth: .infinity, minHeight: 56)
                    .buttonStyle(.bordered)
                }
            }
            if wrong {
                Text("Not quite. Try a new one.")
                    .foregroundStyle(.secondary)
            }
            Button("Cancel") { dismiss() }
                .padding(.top, 8)
        }
        .padding(32)
        .frame(maxWidth: 480)
        .presentationDetents([.medium, .large])
    }
}
