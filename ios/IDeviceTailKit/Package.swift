// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "IDeviceTailKit",
    platforms: [
        .iOS(.v15),
        .macCatalyst(.v15),
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
