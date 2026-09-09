import SwiftUI

@main
struct IDeviceTailAgentApp: App {
    @StateObject private var model = AgentModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
    }
}
