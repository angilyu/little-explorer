import SwiftUI

/// Parent-only screen: what he loved, voice server settings, and photo credits with links.
struct ParentCornerView: View {
    let books: [Book]

    @Environment(HeartStore.self) private var hearts
    @Environment(\.dismiss) private var dismiss
    @Environment(ContentStore.self) private var content
    @AppStorage(VoiceSettings.tokenServerKey) private var tokenServer = VoiceSettings.defaultTokenServer
    @State private var device: DeviceInfo?

    var body: some View {
        NavigationStack {
            List {
                Section("Loved pages") {
                    let total = books.reduce(0) { $0 + hearts.lovedCount(bookId: $1.id) }
                    if total == 0 {
                        Text("No hearts yet. When he taps the heart on a page, it shows up here.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(books) { book in
                        let loved = hearts.lovedPages(in: book)
                        if !loved.isEmpty {
                            DisclosureGroup("\(book.shelfTitle) · \(loved.count)") {
                                ForEach(loved) { page in
                                    HStack(alignment: .top, spacing: 12) {
                                        PhotoView(photo: page.photo, tint: Color(hex: book.coverColor))
                                            .frame(width: 56, height: 56)
                                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                                        Text(page.text)
                                    }
                                    .padding(.vertical, 4)
                                }
                            }
                        }
                    }
                }

                Section {
                    TextField("Token server URL", text: $tokenServer)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button("Reset to default") { tokenServer = VoiceSettings.defaultTokenServer }
                    if let device {
                        LabeledContent("This device") {
                            Text(device.id)
                                .font(.caption.monospaced())
                                .textSelection(.enabled)
                        }
                        Button("Copy device id") { UIPasteboard.general.string = device.id }
                    }
                } header: {
                    Text("Voice buddy (Milo)")
                } footer: {
                    Text("Milo's server hands out voice tokens and new books. The default is the hosted server; for a server on your Mac use http://127.0.0.1:8787 (simulator) or the Mac's address, e.g. http://192.168.1.20:8787. Audio is not recorded. Milo's minutes are counted per device: approve this device with `tools/devices.py approve <id>` for the longer daily limit.")
                }

                Section {
                    LabeledContent("Books version", value: content.version.map { String($0.prefix(8)) } ?? "Built in")
                    LabeledContent("Last check", value: statusText)
                    Button("Check for new books") { Task { await content.refresh(force: true) } }
                        .disabled(content.status == .checking)
                } header: {
                    Text("Books")
                } footer: {
                    Text("New and updated books download from Milo's server when the app opens, so they don't need an app update.")
                }

                Section("Photo credits") {
                    ForEach(books) { book in
                        DisclosureGroup(book.shelfTitle) {
                            ForEach(book.pages) { page in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(page.photo.author ?? "Unknown author").font(.subheadline.bold())
                                    Text(page.photo.license ?? "License pending").font(.caption)
                                    if let source = page.photo.source {
                                        Link(source.absoluteString, destination: source)
                                            .font(.caption)
                                            .lineLimit(1)
                                            .truncationMode(.middle)
                                    }
                                }
                                .padding(.vertical, 2)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Grown-ups corner")
            .task { device = DeviceIdentity.current() }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }

    private var statusText: String {
        switch content.status {
        case .idle: "Not yet"
        case .checking: "Checking…"
        case .upToDate: "Up to date"
        case let .updated(count): "Updated (\(count) new files)"
        case .failed: "Couldn't reach the server"
        }
    }
}
