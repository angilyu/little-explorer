import SwiftUI

/// What a signed-out device shows: a friendly screen whose only button is for grown-ups, behind the
/// number gate, that leads to signing in (or signing up: it's the same).
struct WelcomeView: View {
    let books: [Book]

    @Environment(AccountStore.self) private var account
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var showGate = false
    @State private var showSignIn = false

    private var regular: Bool { sizeClass == .regular }

    /// One flag per place a new account starts with, so the screen hints at what's inside.
    private var flags: [Book] {
        let starters = Set(Account.defaultBooks)
        var countries = Set<String>()
        return BookLibrary.shelves(books.filter { starters.contains($0.id) }).map(\.flagBook)
            .filter { countries.insert($0.country).inserted }
    }

    var body: some View {
        ScrollView {
            VStack(spacing: regular ? 28 : 20) {
                Text("Little Explorer")
                    .font(Theme.titleFont(regular: regular))
                    .foregroundStyle(Theme.ink)
                    .padding(.top, regular ? 80 : 40)
                Image(systemName: "globe.europe.africa.fill")
                    .font(.system(size: regular ? 120 : 88))
                    .symbolRenderingMode(.multicolor)
                    .foregroundStyle(.blue, .green)
                    .accessibilityHidden(true)
                HStack(spacing: regular ? 14 : 10) {
                    ForEach(flags) { FlagBadge(book: $0, height: regular ? 40 : 30) }
                }
                Text("Books about the world, and Milo to talk with.")
                    .font(.system(size: regular ? 26 : 20, weight: .semibold, design: .rounded))
                    .foregroundStyle(Theme.ink.opacity(0.75))
                    .multilineTextAlignment(.center)

                VStack(spacing: 14) {
                    Text("Hello, grown-ups!")
                        .font(.title2.bold())
                    Text("Sign in with your email to open the bookshelf. New here? The same steps make your free account.")
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                    Button {
                        showGate = true
                    } label: {
                        Label("Grown-ups: sign in", systemImage: "lock.fill")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityHint("For parents only")
                    Text("Just an email and a 6-digit code. No passwords.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(regular ? 28 : 20)
                .frame(maxWidth: 460)
                .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(.background.opacity(0.85)))
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 40)
            .frame(maxWidth: .infinity)
        }
        .background(
            LinearGradient(colors: [Theme.sky, Theme.paper], startPoint: .top, endPoint: .bottom).ignoresSafeArea()
        )
        .sheet(isPresented: $showGate) {
            ParentGateView {
                showGate = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { showSignIn = true }
            }
        }
        .sheet(isPresented: $showSignIn) {
            SignInView()
                .environment(account)
        }
        .task {
            guard let demo = DemoLaunch.current else { return }
            if demo.openGate { showGate = true }
            if demo.accountScreen == .email || demo.accountScreen == .code { showSignIn = true }
        }
    }
}

/// Email, then the 6-digit code from the email. Signing up and signing in are the same steps.
struct SignInView: View {
    @Environment(AccountStore.self) private var account
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""
    @State private var codeSentTo: String?
    @State private var code = ""
    @State private var busy = false
    @State private var message: String?
    @State private var resendAt = Date.distantPast
    @FocusState private var emailFocused: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    if let sentTo = codeSentTo {
                        codeStep(sentTo)
                    } else {
                        emailStep
                    }
                    if let message {
                        Text(message)
                            .font(.callout)
                            .foregroundStyle(.red)
                            .multilineTextAlignment(.center)
                            .accessibilityAddTraits(.updatesFrequently)
                    }
                }
                .padding(24)
                .frame(maxWidth: 440)
                .frame(maxWidth: .infinity)
            }
            .navigationTitle(codeSentTo == nil ? "Sign in" : "Enter code")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
        .interactiveDismissDisabled(busy)
        .task { applyDemo() }
    }

    private var emailStep: some View {
        VStack(spacing: 16) {
            Image(systemName: "envelope.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.tint)
            Text("Sign in or sign up")
                .font(.title.bold())
            Text("Type a grown-up's email. We'll email you a 6-digit code — no password needed.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            TextField("you@example.com", text: $email)
                .textContentType(.emailAddress)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.send)
                .focused($emailFocused)
                .onSubmit { Task { await sendCode() } }
                .font(.title3)
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color(.secondarySystemBackground)))
                .accessibilityLabel("Email")
            Button {
                Task { await sendCode() }
            } label: {
                HStack {
                    Text("Email me a code").font(.headline)
                    if busy { ProgressView().padding(.leading, 6) }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .disabled(busy || !Account.looksLikeEmail(email))
            Text("We only use this email to sign you in and to keep the books, stars, coins and hearts the same on all your devices. No ads, no newsletters.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    private func codeStep(_ sentTo: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "envelope.open.fill")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("Check your email")
                .font(.title.bold())
            Text("We sent a 6-digit code to \(sentTo). It works for 10 minutes.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            OneTimeCodeField(code: $code) { Task { await verify() } }
            Button {
                Task { await verify() }
            } label: {
                HStack {
                    Text("Sign in").font(.headline)
                    if busy { ProgressView().padding(.leading, 6) }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .disabled(busy || code.count != OneTimeCodeField.length)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let wait = Int(resendAt.timeIntervalSince(context.date).rounded(.up))
                Button(wait > 0 ? "Send a new code in \(wait) s" : "Send a new code") {
                    Task { await sendCode() }
                }
                .disabled(busy || wait > 0)
                .monospacedDigit()
            }
            Button("Use a different email") {
                codeSentTo = nil
                code = ""
                message = nil
            }
            .disabled(busy)
            Text("Can't find it? Look in spam, or check the email address.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private func sendCode() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let wait = try await account.sendCode(to: email)
            resendAt = Date().addingTimeInterval(TimeInterval(wait))
            codeSentTo = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            code = ""
            message = nil
        } catch let error as AccountError {
            message = error.message
        } catch {
            message = AccountError.offline.message
        }
    }

    private func verify() async {
        guard !busy, let sentTo = codeSentTo, code.count == OneTimeCodeField.length else { return }
        busy = true
        defer { busy = false }
        do {
            try await account.verify(email: sentTo, code: code, device: DeviceIdentity.current())
            dismiss()
        } catch let error as AccountError {
            message = error.message
            code = ""
        } catch {
            message = AccountError.offline.message
        }
    }

    private func applyDemo() {
        guard let demo = DemoLaunch.current else { emailFocused = true; return }
        if demo.accountScreen == .email {
            email = "parent@example.com"
        } else if demo.accountScreen == .code {
            email = "parent@example.com"
            codeSentTo = email
            code = "482"
            resendAt = Date().addingTimeInterval(42)
        }
    }
}

/// Six boxes over one hidden text field, so iOS can fill the code from Mail ("From Mail" above the keyboard).
struct OneTimeCodeField: View {
    static let length = 6

    @Binding var code: String
    var onComplete: () -> Void = {}
    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            TextField("", text: $code)
                .textContentType(.oneTimeCode)
                .keyboardType(.numberPad)
                .focused($focused)
                .foregroundStyle(.clear)
                .tint(.clear)
                .accessibilityLabel("6-digit code")
                .onChange(of: code) { _, typed in
                    let digits = String(typed.filter(\.isNumber).prefix(Self.length))
                    if digits != typed { code = digits }
                    if digits.count == Self.length { onComplete() }
                }
            HStack(spacing: 8) {
                ForEach(0..<Self.length, id: \.self) { i in
                    let chars = Array(code)
                    Text(i < chars.count ? String(chars[i]) : " ")
                        .font(.system(size: 30, weight: .semibold, design: .rounded).monospacedDigit())
                        .frame(width: 46, height: 58)
                        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color(.secondarySystemBackground)))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(
                                    i == min(code.count, Self.length - 1) && focused ? Color.accentColor : Color(.separator),
                                    lineWidth: i == min(code.count, Self.length - 1) && focused ? 2 : 1
                                )
                        )
                }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
        .onTapGesture { focused = true }
        .onAppear { focused = DemoLaunch.current == nil }
    }
}

/// Grown-ups corner: deleting the account needs a fresh code from the account's email, so a tap-happy
/// child (or a borrowed device) can't delete it.
struct DeleteAccountView: View {
    @Environment(AccountStore.self) private var account
    @Environment(\.dismiss) private var dismiss
    @State private var codeSent = false
    @State private var code = ""
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Deleting the account removes its email, books list, stars, coins and hearts from Milo's server for good. Every iPad and iPhone signed in to it is signed out. This can't be undone.")
                } header: {
                    Text("Delete \(account.account?.email ?? "account")")
                }
                Section {
                    if codeSent {
                        OneTimeCodeField(code: $code)
                            .frame(maxWidth: .infinity)
                        Button("Delete account for good", role: .destructive) { Task { await delete() } }
                            .disabled(busy || code.count != OneTimeCodeField.length)
                    } else {
                        Button("Email me a code to confirm", role: .destructive) { Task { await sendCode() } }
                            .disabled(busy)
                    }
                    if let message {
                        Text(message).font(.callout).foregroundStyle(.secondary)
                    }
                } footer: {
                    Text(codeSent ? "Enter the 6-digit code we just emailed." : "We email a code to the account's address to make sure it's you.")
                }
            }
            .navigationTitle("Delete account")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
    }

    private func sendCode() async {
        busy = true
        defer { busy = false }
        do {
            _ = try await account.sendDeleteCode()
            codeSent = true
            message = nil
        } catch let error as AccountError {
            message = error.message
        } catch {
            message = AccountError.offline.message
        }
    }

    private func delete() async {
        busy = true
        defer { busy = false }
        do {
            try await account.deleteAccount(code: code)
            dismiss()
        } catch let error as AccountError {
            message = error.message
            code = ""
        } catch {
            message = AccountError.offline.message
        }
    }
}
