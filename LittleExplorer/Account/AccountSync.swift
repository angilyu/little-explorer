import Foundation

/// Where each account's copy of the stars and coins is saved on the device. The copy from before
/// accounts keeps its old key and is never deleted.
enum AccountKeys {
    static func key(_ base: String, _ accountId: String?) -> String {
        accountId.map { "account.\($0).\(base)" } ?? base
    }
}

/// Keeps the stars, coins and reading progress (for the badges) on screen in step with who is signed in.
/// The server keeps the account's; this device keeps a copy per account so they show offline.
///
/// When an account claims this device (its first account, e.g. Francis's family account on his iPad),
/// what the device saved before accounts joins the account here and is sent with `POST /account/import`
/// until the server has it.
@MainActor
struct AccountSync {
    let account: AccountStore
    let stars: StarStore
    let coins: CoinStore
    var reading: ReadingActivity?
    var defaults: UserDefaults = .standard
    var baseURL: () -> URL = { VoiceSettings.tokenServerURL() }
    var deviceId: () -> String = { DeviceIdentity.current().id }
    var session: URLSession = .shared

    /// Hooks the stores to the account and shows the signed-in account's saved data.
    func start() {
        account.willSignIn = { result in signingIn(result) }
        account.onSignIn = { _ in await refresh() }
        account.onSignOut = { id in signedOut(id) }
        show(account.account?.id)
    }

    func signingIn(_ result: SignInResult) {
        let id = result.account.id
        let legacyStars = stars.legacyAwards
        let seen = stars.legacySeenBadges
        show(id)
        guard result.claimedDevice else { return }
        stars.merge(legacyStars)
        stars.markBadgesSeen(seen)
        if !legacyStars.isEmpty { defaults.set(true, forKey: importKey(id)) }
    }

    /// Checks the sign-in and fetches the account's books, stars, coins and reading progress.
    func refresh() async {
        await account.refresh()
        guard let id = account.account?.id, let token = account.token else { return }
        show(id)
        if defaults.bool(forKey: importKey(id)) {
            let service = AccountService(baseURL: baseURL(), session: session)
            if (try? await service.importDevice(token: token, stars: stars.legacyAwards)) != nil {
                defaults.removeObject(forKey: importKey(id))
            }
        }
        async let saved: Void = stars.restore(from: StarBackup(baseURL: baseURL(), deviceId: deviceId(), session: session, token: token))
        async let balance: Bool = coins.refresh(using: CoinService(baseURL: baseURL(), deviceId: deviceId(), session: session, token: token))
        _ = await (saved, balance)
        await reading?.restore(using: ReadingService(baseURL: baseURL(), session: session), token: token)
    }

    func signedOut(_ accountId: String) {
        show(nil)
        stars.forgetAccount(accountId)
        coins.forgetAccount(accountId)
        reading?.forgetAccount(accountId)
        defaults.removeObject(forKey: importKey(accountId))
    }

    private func show(_ accountId: String?) {
        stars.switchAccount(accountId)
        coins.switchAccount(accountId)
        reading?.switchAccount(accountId)
    }

    private func importKey(_ accountId: String) -> String { "account.\(accountId).importPending" }
}
