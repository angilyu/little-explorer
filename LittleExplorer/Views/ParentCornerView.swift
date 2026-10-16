import SwiftUI

/// Parent-only screen: what he loved, voice server settings, and photo credits with links.
struct ParentCornerView: View {
    let books: [Book]

    @Environment(HeartStore.self) private var hearts
    @Environment(\.dismiss) private var dismiss
    @AppStorage(VoiceSettings.tokenServerKey) private var tokenServer = VoiceSettings.defaultTokenServer

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
                } header: {
                    Text("Voice buddy (Pip)")
                } footer: {
                    Text("Pip runs on your own server (see server/README.md in the repo). On the simulator use http://127.0.0.1:8787. On a real iPhone or iPad use your Mac's address, for example http://192.168.1.20:8787. Audio is not recorded.")
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
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
