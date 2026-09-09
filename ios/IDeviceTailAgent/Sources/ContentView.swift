import SwiftUI
import IDeviceTailKit

struct ContentView: View {
    @EnvironmentObject var model: AgentModel

    var body: some View {
        NavigationView {
            Form {
                Section("Connection") {
                    Picker("Discovery", selection: $model.mode) {
                        ForEach(AgentModel.Mode.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .disabled(model.running)

                    if model.mode == .manual {
                        TextField("Desktop IP (e.g. 192.168.1.50)", text: $model.host)
                            .keyboardType(.numbersAndPunctuation)
                            .textInputAutocapitalization(.never)
                            .disabled(model.running)
                        TextField("Port", text: $model.port)
                            .keyboardType(.numberPad)
                            .disabled(model.running)
                    }

                    HStack {
                        Circle().fill(color).frame(width: 10, height: 10)
                        Text(stateText).foregroundStyle(.secondary)
                    }
                }

                Section {
                    Button(model.running ? "Stop streaming" : "Start streaming") {
                        model.toggle()
                    }
                    .frame(maxWidth: .infinity)
                    .font(.headline)

                    Button("Emit test logs") { model.emitTestLogs() }
                        .disabled(!model.running)
                }

                Section("Stats") {
                    LabeledContent("Records sent", value: "\(model.sent)")
                    LabeledContent("Dropped (backpressure)", value: "\(model.dropped)")
                }

                Section("What this streams") {
                    Text("""
                    Only **this app's own** os_log / Logger output, plus anything you send \
                    via LogForwarder.log(...). iOS does not let a third-party app read other \
                    apps' or system logs — for that use the desktop's Engine A (pymobiledevice3) \
                    over Wi-Fi. No cable, pairing, or Developer Mode is needed for this agent.
                    """)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("iDeviceTail Agent")
        }
        .navigationViewStyle(.stack)
    }

    private var color: Color {
        switch model.connection {
        case .connected: return .green
        case .connecting, .waiting: return .yellow
        case .idle: return .gray
        }
    }
    private var stateText: String {
        switch model.connection {
        case .idle: return "idle"
        case .connecting: return "connecting…"
        case .connected: return "connected — streaming"
        case .waiting: return "waiting / reconnecting…"
        }
    }
}
