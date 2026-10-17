import SwiftUI

/// The one big switch for voice mode. It stays on across page turns until tapped again.
struct VoiceToggleButton: View {
    let voice: VoiceModeController
    var compact = false

    var body: some View {
        Button {
            Task { await voice.toggle() }
        } label: {
            HStack(spacing: 8) {
                Image(systemName: voice.isEnabled ? "mic.fill" : "mic.slash.fill")
                    .font(.system(size: 24, weight: .bold))
                if !compact {
                    Text(voice.isEnabled ? "Stop talking" : "Talk to Milo")
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                }
            }
            .padding(.horizontal, compact ? 0 : 20)
            .frame(minWidth: 56, minHeight: 56)
            .background(Capsule().fill(voice.isEnabled ? Color.green : Color.white))
            .foregroundStyle(voice.isEnabled ? Color.white : Theme.ink)
            .shadow(color: .black.opacity(0.12), radius: 4, x: 0, y: 2)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(voice.isEnabled ? "Turn off Milo" : "Turn on Milo, your talking buddy")
    }
}

/// Always-visible indicator of what Milo is doing while voice mode is on.
struct VoiceStatusView: View {
    let voice: VoiceModeController
    var compact = false

    var body: some View {
        if voice.isEnabled {
            Button {
                if case .failed = voice.state { Task { await voice.retry() } }
            } label: {
                HStack(spacing: 8) {
                    icon
                        .font(.system(size: 24, weight: .bold))
                        .frame(width: 30)
                    if !compact {
                        Text(voice.statusText)
                            .font(.system(size: 18, weight: .semibold, design: .rounded))
                            .lineLimit(1)
                    }
                }
                .padding(.horizontal, 14)
                .frame(minHeight: 56)
                .background(Capsule().fill(background))
                .foregroundStyle(Theme.ink)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(voice.statusText)
            .transition(.scale.combined(with: .opacity))
        }
    }

    @ViewBuilder
    private var icon: some View {
        if voice.isAppReadingAloud, voice.isConnected {
            Image(systemName: "book.fill")
                .foregroundStyle(.blue)
        } else {
            stateIcon
        }
    }

    @ViewBuilder
    private var stateIcon: some View {
        switch voice.state {
        case .listening:
            Image(systemName: "ear.fill")
                .symbolEffect(.pulse, options: .repeating)
                .foregroundStyle(.green)
        case .thinking:
            Image(systemName: "ellipsis.bubble.fill")
                .symbolEffect(.variableColor.iterative, options: .repeating)
                .foregroundStyle(.orange)
        case .speaking:
            Image(systemName: "speaker.wave.3.fill")
                .symbolEffect(.variableColor.iterative, options: .repeating)
                .foregroundStyle(.blue)
        case .failed:
            Image(systemName: "arrow.clockwise.circle.fill")
                .foregroundStyle(.red)
        case .connecting, .waitingForBuddy, .off:
            Image(systemName: "sparkles")
                .symbolEffect(.pulse, options: .repeating)
                .foregroundStyle(.purple)
        }
    }

    private var background: Color {
        switch voice.state {
        case .failed: Color.red.opacity(0.15)
        case .listening: Color.green.opacity(0.18)
        default: Color.white
        }
    }
}

/// Shown after Milo turned itself off: a nap after a quiet spell, or rest for the day.
/// Tapping it dismisses it; the mic button turns Milo back on.
struct VoiceNoticeBanner: View {
    let voice: VoiceModeController

    var body: some View {
        if !voice.isEnabled, let notice = voice.notice {
            Button {
                withAnimation { voice.dismissNotice() }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: notice == VoiceModeController.restNotice ? "moon.stars.fill" : "zzz")
                        .font(.system(size: 26, weight: .bold))
                        .foregroundStyle(.purple)
                    Text(notice)
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 12)
                .background(Capsule().fill(.white))
                .foregroundStyle(Theme.ink)
                .shadow(color: .black.opacity(0.15), radius: 6, x: 0, y: 3)
            }
            .buttonStyle(.plain)
            .padding(12)
            .accessibilityLabel(notice)
            .accessibilityHint("Tap to hide")
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }
}
