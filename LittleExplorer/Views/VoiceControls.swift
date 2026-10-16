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
                    Text(voice.isEnabled ? "Stop talking" : "Talk to Pip")
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
        .accessibilityLabel(voice.isEnabled ? "Turn off Pip" : "Turn on Pip, your talking buddy")
    }
}

/// Always-visible indicator of what Pip is doing while voice mode is on.
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
