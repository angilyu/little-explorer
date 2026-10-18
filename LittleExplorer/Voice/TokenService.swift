import Foundation

enum VoiceSettings {
    static let tokenServerKey = "voice.tokenServerURL"
    /// The Milo server (tokens and book updates) from Config/Milo.xcconfig; the grown-ups corner can
    /// override it, e.g. http://127.0.0.1:8787 for a server running on this Mac.
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
        let raw = defaults.string(forKey: tokenServerKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let raw, !raw.isEmpty, let url = URL(string: raw), url.scheme != nil { return url }
        return URL(string: defaultTokenServer)!
    }
}

struct TokenRequest: Encodable {
    let context: [String: String]?
    var device: DeviceInfo? = nil
    /// "app" when the app plays Milo's recorded hello itself, so the agent doesn't greet again.
    var intro: String? = nil
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
    /// Sent so the server can count Milo's minutes per device (and refuse a blocked one).
    var device: DeviceInfo?

    /// `context` is the page open when voice mode starts; the server puts it in the token so the
    /// agent knows the page from its first word. `appPlaysIntro` tells the agent to skip its spoken
    /// greeting because the app already played the recorded one (older builds leave it out).
    func fetchCredentials(context: PageContext?, appPlaysIntro: Bool = false) async throws -> VoiceCredentials {
        var request = URLRequest(url: baseURL.appendingPathComponent("token"))
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let accessKey { request.setValue("Bearer \(accessKey)", forHTTPHeaderField: "Authorization") }
        request.httpBody = try JSONEncoder().encode(TokenRequest(
            context: context?.attributes, device: device, intro: appPlaysIntro ? "app" : nil
        ))
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        // 403 = blocked device, 429 = used up today's minutes.
        if status == 403 || status == 429 { throw VoiceError.resting }
        guard (200..<300).contains(status) else { throw VoiceError.tokenServer(status: status) }
        return try JSONDecoder().decode(VoiceCredentials.self, from: data)
    }
}
