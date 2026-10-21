import Foundation
import Security
import UIKit

/// Which iPad or iPhone is asking for a voice token or signing in. Milo's daily minutes and stars are
/// the signed-in account's; the server keeps `id` to list the account's devices (and builds from
/// before sign-in still go by it).
struct DeviceInfo: Encodable, Equatable, Sendable {
    let id: String
    let name: String
    let model: String
}

protocol DeviceIDStore {
    func read() -> String?
    func write(_ id: String)
}

/// Keeps the id in the Keychain so it survives deleting and reinstalling the app.
struct KeychainDeviceIDStore: DeviceIDStore {
    var service = "com.angilyu.littleexplorer.device"
    var account = "device-id"

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    func read() -> String? {
        var item: CFTypeRef?
        var lookup = query
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        guard SecItemCopyMatching(lookup as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func write(_ id: String) {
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = Data(id.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}

enum DeviceIdentity {
    /// This device's stable id, name and model.
    @MainActor
    static func current(store: DeviceIDStore = KeychainDeviceIDStore()) -> DeviceInfo {
        let device = UIDevice.current
        let id = stableID(store: store, seed: device.identifierForVendor?.uuidString)
        return DeviceInfo(id: id, name: String(device.name.prefix(80)), model: modelIdentifier(fallback: device.model))
    }

    /// The stored id if it is valid, otherwise the vendor id (or a fresh UUID), saved for next time.
    static func stableID(store: DeviceIDStore, seed: String?) -> String {
        if let saved = store.read(), isValid(saved) { return saved }
        let id = seed.flatMap { isValid($0) ? $0 : nil } ?? UUID().uuidString
        store.write(id)
        return id
    }

    /// Matches the server's rule: 1–64 letters, digits or dashes.
    static func isValid(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 64 && id.allSatisfy { ($0.isASCII && ($0.isLetter || $0.isNumber)) || $0 == "-" }
    }

    /// e.g. "iPad13,18", which tells a parent which iPad it is better than "iPad".
    static func modelIdentifier(fallback: String) -> String {
        if let simulated = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] { return simulated }
        var info = utsname()
        uname(&info)
        let machine = withUnsafeBytes(of: &info.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
        return machine.isEmpty ? fallback : machine
    }
}
