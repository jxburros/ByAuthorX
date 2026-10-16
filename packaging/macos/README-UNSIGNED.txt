ByAuthorX (unsigned build)

This copy of ByAuthorX was built without an Apple Developer ID signature and has not been
notarized, so macOS Gatekeeper will refuse to open it the first time.

To open it anyway:
  1. Drag ByAuthorX to Applications.
  2. Control-click (or right-click) ByAuthorX and choose Open, then choose Open again.
     On macOS 15 and later: try to open it once, then use System Settings > Privacy & Security >
     "Open Anyway".
  3. Or, from Terminal: xattr -dr com.apple.quarantine /Applications/ByAuthorX.app

Only do this with a build you downloaded from the project's GitHub Releases page. Compare the
file's SHA-256 with SHA256SUMS.txt on the release before you open it:
  shasum -a 256 ByAuthorX-*-macos-arm64-UNSIGNED.dmg
