import Foundation
import Network

/// Finds the desktop app on the LAN by browsing for `_idevtail-host._tcp`
/// (published by `desktop/idevicetail/discovery.py:publish_host`). Emits the
/// first resolvable endpoint and keeps watching so we can fail over if the
/// host's IP changes.
public final class HostBrowser: @unchecked Sendable {

    public typealias Found = (NWEndpoint) -> Void

    private var browser: NWBrowser?
    private let onFound: Found
    private let queue = DispatchQueue(label: "idevicetail.hostbrowser")

    public init(onFound: @escaping Found) {
        self.onFound = onFound
    }

    public func start() {
        let params = NWParameters.tcp
        params.includePeerToPeer = true
        let b = NWBrowser(
            for: .bonjour(type: "_idevtail-host._tcp", domain: nil),
            using: params
        )
        b.browseResultsChangedHandler = { [weak self] results, _ in
            guard let self else { return }
            // Prefer a result advertising an IPv4/host endpoint; any will do.
            if let first = results.first {
                self.onFound(first.endpoint)
            }
        }
        b.stateUpdateHandler = { state in
            if case .failed = state { }   // NWBrowser auto-retries; nothing to do
        }
        self.browser = b
        b.start(queue: queue)
    }

    public func stop() {
        browser?.cancel()
        browser = nil
    }
}
