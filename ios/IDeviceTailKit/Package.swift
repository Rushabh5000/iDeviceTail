// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "IDeviceTailKit",
    platforms: [
        .iOS(.v15),
        .macCatalyst(.v15),
        .macOS(.v11),   // so `swift test` / `swift build` work on a plain Mac
    ],
    products: [
        .library(name: "IDeviceTailKit", targets: ["IDeviceTailKit"]),
    ],
    targets: [
        .target(
            name: "IDeviceTailKit",
            path: "Sources/IDeviceTailKit"
        ),
        .testTarget(
            name: "IDeviceTailKitTests",
            dependencies: ["IDeviceTailKit"],
            path: "Tests/IDeviceTailKitTests"
        ),
    ]
)
