import Foundation

enum VoiceSettings {
    /// Debug builds only: a developer's own server, e.g. launch with
    /// `-voice.tokenServerURL http://127.0.0.1:8787` for one on this Mac. Release builds ignore it.
    static let tokenServerKey = "voice.tokenServerURL"
    /// The Milo server (tokens, accounts and book updates) from Config/Milo.xcconfig.
    static let defaultTokenServer: String = {
        let value = (Bundle.main.object(forInfoDictionaryKey: "PipServerURL") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty || value.contains("$(") ? "http://127.0.0.1:8787" : value
    }()

    /// Sent as a bearer token so only this app gets voice tokens (Config/Local.xcconfig).
    static let accessKey: String? = {
        let value = (Bundle.main.object(forInfoDictionaryKey: "PipAccessKey") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty || value.contains("$(") ? nil : value
    }()

    static func tokenServerURL(defaults: UserDefaults = .standard) -> URL {
        #if DEBUG
        if let url = debugOverride(defaults) { return url }
        #endif
        return URL(string: defaultTokenServer)!
    }

    static func debugOverride(_ defaults: UserDefaults) -> URL? {
        let raw = defaults.string(forKey: tokenServerKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let raw, !raw.isEmpty, let url = URL(string: raw), url.scheme != nil else { return nil }
        return url
    }
}

struct TokenRequest: Encodable {
    let context: [String: String]?
    var device: DeviceInfo? = nil
    /// "app" when the app plays Milo's recorded hello itself, so the agent doesn't greet again.
    var intro: String? = nil
    /// The device's time zone (e.g. "America/Los_Angeles"): stars count up to 5 per page per local day.
    var tz: String? = nil
}

struct VoiceCredentials: Decodable, Equatable {
    let serverUrl: String
    let participantToken: String
    let roomName: String?
}

/// Fetches a short-lived LiveKit token from our own server so the app never holds LiveKit secrets.
struct TokenService {
    var baseURL: URL
    var accessKey: String? = VoiceSettings.accessKey
    var session: URLSession = .shared
    /// Which iPad or iPhone is asking (the owner's device list; old builds' limits went by it).
    var device: DeviceInfo?
    /// The account sign-in, read on every request: Milo's daily minutes and stars are the account's.
    var sessionToken: () -> String? = { nil }

    /// `context` is the page open when voice mode starts; the server puts it in the token so the
    /// agent knows the page from its first word. `appPlaysIntro` tells the agent to skip its spoken
    /// greeting because the app already played the recorded one (older builds leave it out).
    func fetchCredentials(context: PageContext?, appPlaysIntro: Bool = false) async throws -> VoiceCredentials {
        var request = URLRequest(url: baseURL.appendingPathComponent("token"))
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let accessKey { request.setValue("Bearer \(accessKey)", forHTTPHeaderField: "Authorization") }
        if let token = sessionToken() { request.setValue(token, forHTTPHeaderField: AccountService.sessionHeader) }
        request.httpBody = try JSONEncoder().encode(TokenRequest(
            context: context?.attributes, device: device, intro: appPlaysIntro ? "app" : nil,
            tz: TimeZone.current.identifier
        ))
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        // 403 milo_off = a grown-up turned Milo off; other 403 = blocked account, 429 = used up today's
        // minutes, 401 = the sign-in expired.
        if status == 403, Self.errorCode(data) == "milo_off" { throw VoiceError.miloOff }
        if status == 403 || status == 429 { throw VoiceError.resting }
        if status == 401, AccountService.error(status: status, data: data) == .signedOut { throw VoiceError.signedOut }
        guard (200..<300).contains(status) else { throw VoiceError.tokenServer(status: status) }
        return try JSONDecoder().decode(VoiceCredentials.self, from: data)
    }

    /// Asks the server to wake Milo's agent ahead of a likely tap (see ``MiloWarmer``). Signed-in only.
    func warm() async throws {
        guard let token = sessionToken() else { return }
        var request = URLRequest(url: baseURL.appendingPathComponent("milo/warm"))
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let accessKey { request.setValue("Bearer \(accessKey)", forHTTPHeaderField: "Authorization") }
        request.setValue(token, forHTTPHeaderField: AccountService.sessionHeader)
        request.httpBody = try JSONEncoder().encode(TokenRequest(context: nil, device: device))
        _ = try await session.data(for: request)
    }

    static func errorCode(_ data: Data) -> String? {
        ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?["error"] as? String
    }
}
