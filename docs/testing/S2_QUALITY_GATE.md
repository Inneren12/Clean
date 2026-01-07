# Sprint 2 Local Quality Gate

Run all local Android quality checks from one command.

## Commands
- **Windows:** `gradlew.bat s2QualityGate`
- **macOS/Linux:** `./gradlew s2QualityGate`

## What the gate runs
The `s2QualityGate` task depends on the following Gradle tasks:
- `:app:assembleDebug`
- `:app:assembleRelease`
- `:app:testDebugUnitTest`
- `:app:lintDebug`

## Optional instrumentation
Instrumentation is intentionally separate from the quality gate. If you need to run
instrumentation smoke tests, use one of the following:
- **Gradle Managed Devices:** `./gradlew <managedDeviceName>DebugAndroidTest`
- **Connected device/emulator:** `./gradlew :app:connectedDebugAndroidTest`

## Expected output
A successful run prints the task list and ends with `BUILD SUCCESSFUL`, for example:

```
> Task :app:assembleDebug
> Task :app:assembleRelease
> Task :app:lintDebug
> Task :app:testDebugUnitTest
> Task :s2QualityGate

BUILD SUCCESSFUL
```

## Common failures and fixes
- **Gradle distribution download fails**: Ensure the machine has internet access to `services.gradle.org` or pre-populate `~/.gradle/wrapper/dists` with the configured Gradle distribution.
- **Android SDK missing or misconfigured**: Verify `ANDROID_HOME`/`ANDROID_SDK_ROOT` and that required build tools and platforms are installed.
- **Lint or unit test failures**: Run the failing task (`:app:lintDebug` or `:app:testDebugUnitTest`) directly to inspect details and fix the reported issues.
- **Instrumentation fails to start**: Ensure a managed device or connected emulator is available before running instrumentation tasks.
