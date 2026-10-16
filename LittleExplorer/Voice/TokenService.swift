import Foundation

enum VoiceSettings {
    static let tokenServerKey = "voice.tokenServerURL"
    /// The token server runs next to the agent (see server/README). 127.0.0.1 works from the simulator;
    /// on a real device set your Mac's LAN address in the grown-ups corner.
    static let defaultTokenServer = "http://127.0.0.1:8787"

    static func tokenServerURL(defaults: UserDefaults = .standard) -> URL {
        let raw = defaults.string(forKey: tokenServerKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let raw, !raw.isEmpty, let url = URL(string: raw), url.scheme != nil { return url }
        return URL(string: defaultTokenServer)!
    }
}

struct TokenRequest: Encodable {
    let context: [String: String]?
}

struct VoiceCredentials: Decodable, Equatable {
    let serverUrl: String
    let participantToken: String
    let roomName: String?
}

/// Fetches a short-lived LiveKit token from our own server so the app never holds LiveKit secrets.
struct TokenService {
    var baseURL: URL
    var session: URLSession = .shared

    /// `context` is the page open when voice mode starts; the server puts it in the token so the
    /// agent knows the page from its first word.
    func fetchCredentials(context: PageContext?) async throws -> VoiceCredentials {
        var request = URLRequest(url: baseURL.appendingPathComponent("token"))
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(TokenRequest(context: context?.attributes))
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw VoiceError.tokenServer(status: status) }
        return try JSONDecoder().decode(VoiceCredentials.self, from: data)
    }
}
