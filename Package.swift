// swift-tools-version:5.9
import PackageDescription

// Root manifest so the Kit is consumable straight from the repo URL:
//
//   .package(url: "https://github.com/Rushabh5000/iDeviceTail.git", from: "0.1.0")
//   .product(name: "IDeviceTailKit", package: "iDeviceTail")
//
// The sources live under ios/IDeviceTailKit/ (see ios/README.md); this points at
// them by path. `ios/IDeviceTailKit/Package.swift` still works standalone too.

let package = Package(
    name: "iDeviceTail",
    platforms: [
        .iOS(.v15),
        .macCatalyst(.v15),
        .macOS(.v11),
    ],
    products: [
        .library(name: "IDeviceTailKit", targets: ["IDeviceTailKit"]),
    ],
    targets: [
        .target(
            name: "IDeviceTailKit",
            path: "ios/IDeviceTailKit/Sources/IDeviceTailKit"
        ),
        .testTarget(
            name: "IDeviceTailKitTests",
            dependencies: ["IDeviceTailKit"],
            path: "ios/IDeviceTailKit/Tests/IDeviceTailKitTests"
        ),
    ]
)
