# Plan: Native Google and Apple Authentication (Mobile & Web)

**Specification**: [codev/specs/324-native-google-apple-auth.md](../specs/324-native-google-apple-auth.md)

---

## Executive Summary

This plan implements the ratified specification for native Google and Apple authentication across mobile (`apps/frontend`) and web, backed by `packages/auth` and `apps/auth`.

Key architectural principles:
- **Native Modules on Mobile (Non-Negotiable)**: On native iOS and Android, authentication uses native OS dialogs. Apple Sign-In uses `expo-apple-authentication` (iOS). Google Sign-In uses `react-native-nitro-google-signin` (powered by Nitro Modules / JSI, integrating Android Credential Manager and native iOS Google Sign-In SDK). Web redirects (`expo-web-browser` / Custom Tabs) on native mobile are prohibited.
- **Web Social Auth**: Standard desktop and mobile web browsers use Better Auth's standard web redirect flow (`authClient.signIn.social`).
- **Two-Token Protocol Parity**: Third-party logins produce a Better Auth `sessionToken` on `apps/auth`. The frontend exchanges this for a 15-minute EdDSA JWT via `GET /api/auth/token` for stateless `apps/api` access, preserving 401 single-flight token refresh.
- **Zero Schema Migrations**: The `auth.account` table in `packages/auth/src/schema.ts` already contains all necessary columns (`provider_id`, `account_id`, `id_token`, `access_token`, etc.) with `unique(user_id, provider_id)`.
- **Account Linking & Collision Safety**: Verified emails automatically link provider accounts under existing `auth.users` rows without creating duplicates or disrupting password authentication.

---

## Phases (Machine Readable)

```json
{
  "phases": [
    {
      "id": "phase_1",
      "title": "Backend environment, Better Auth social providers, and native ID token verification"
    },
    {
      "id": "phase_2",
      "title": "Frontend native module dependencies and EAS prebuild configuration"
    },
    {
      "id": "phase_3",
      "title": "Frontend auth client layer and native token exchange"
    },
    {
      "id": "phase_4",
      "title": "AuthSheet UI wiring, cancellation UX, and error handling"
    },
    {
      "id": "phase_5",
      "title": "Automated tests, contract verification, and workspace verification"
    }
  ]
}
```

---

## Phase Breakdown

### Phase 1: Backend Environment, Better Auth Social Providers, and Native ID Token Verification

**Dependencies**: None

#### Objective
Enable Google and Apple social providers in `packages/auth` with native ID token verification, multi-audience support (iOS bundle ID `chat.ansari.app` vs web client ID), and account linking.

#### Files to Create / Modify
- `packages/auth/src/env.ts`:
  - Add optional OAuth schema keys to `envSchema`:
    - `GOOGLE_CLIENT_ID`: Web Client ID (audience for Android and web ID tokens).
    - `GOOGLE_CLIENT_SECRET`: Client secret for web OAuth flow.
    - `GOOGLE_IOS_CLIENT_ID`: Optional client ID for iOS Google Sign-In if configured separately.
    - `APPLE_CLIENT_ID`: Services ID for web sign-in.
    - `APPLE_CLIENT_SECRET`: Generated client secret JWT or secret for web flow.
    - `APPLE_BUNDLE_ID`: Defaults to `'chat.ansari.app'` (audience for iOS native Apple ID tokens).
    - `APPLE_TEAM_ID` & `APPLE_KEY_ID`: For Apple web client secret generation if needed.
- `apps/auth/.env.example`: Document the new Google and Apple environment variables.
- `turbo.json`: Add new environment variables to `globalEnv`.
- `packages/auth/src/index.ts`:
  - Configure `socialProviders` in `createAuth()`:
    - `google`: `clientId: env.GOOGLE_CLIENT_ID`, `clientSecret: env.GOOGLE_CLIENT_SECRET`. Configure `idToken` verification or `verifyIdToken` override accepting audiences `[env.GOOGLE_CLIENT_ID, env.GOOGLE_IOS_CLIENT_ID]`.
    - `apple`: `clientId: env.APPLE_CLIENT_ID`, `clientSecret: env.APPLE_CLIENT_SECRET`. Configure `idToken` verification accepting audiences `[env.APPLE_CLIENT_ID, env.APPLE_BUNDLE_ID]`.
  - Ensure `POST /api/auth/sign-in/social` handles `{ provider, idToken: { token, nonce, user } }`:
    - Validates token against Apple JWKS (`https://appleid.apple.com/auth/keys`) or Google certs (`https://www.googleapis.com/oauth2/v3/certs`).
    - Maps claims (`email`, `emailVerified`, `name`).
    - On first Apple sign-in, persists `name` (`firstName`, `lastName`) from `idToken.user.name`.
    - Handles Apple Private Relay emails (`@privaterelay.appleid.com`).
    - Handles account linking when `auth.users` row already exists with the same verified email.
  - User creation hook (`databaseHooks.user.create.before`) records `registered_via` from `X-Ansari-Client` header.
- `packages/auth/tests/config/social-auth.test.ts` (new):
  - Unit tests using PGlite verifying ID token signature verification, audience checking, account linking, and error codes (401 on bad/expired tokens).

#### Deliverables
- [ ] `packages/auth/src/env.ts` validates optional Google and Apple OAuth credentials.
- [ ] `packages/auth/src/index.ts` configures `socialProviders` with native `idToken` verification and multiple audience support.
- [ ] First sign-in Apple name capture and Private Relay email persistence implemented.
- [ ] Account linking tested and verified in PGlite.

#### Acceptance Criteria
- [ ] Unset OAuth env vars do not prevent service startup or break existing email/password auth.
- [ ] Valid Google ID token sent to `POST /api/auth/sign-in/social` returns 200 with `{ token, user }`.
- [ ] Valid Apple ID token with `aud: 'chat.ansari.app'` returns 200 with `{ token, user }`.
- [ ] Invalid/expired ID token returns 401 `INVALID_TOKEN`.
- [ ] Token with unknown audience returns 401 `INVALID_TOKEN`.

---

### Phase 2: Frontend Native Module Dependencies and EAS Prebuild Configuration

**Dependencies**: Phase 1

#### Objective
Install and configure `expo-apple-authentication` and `react-native-nitro-google-signin` in `apps/frontend`, updating `app.json` config plugins and native entitlements.

#### Files to Create / Modify
- `apps/frontend/package.json`:
  - Add `expo-apple-authentication` (`~57.0.x`).
  - Add `react-native-nitro-google-signin` and peer dependency `react-native-nitro-modules`.
- `apps/frontend/app.json`:
  - Add `"expo-apple-authentication"` to `expo.plugins`.
  - Add iOS entitlement: `"ios.entitlements": { "com.apple.developer.applesignin": ["Default"] }` (or via config plugin).
  - Add `"react-native-nitro-google-signin"` to `expo.plugins`.
  - Add `googleServicesFile` entries under `android` (`./google-services.json`) and `ios` (`./GoogleService-Info.plist`), with fallback/conditional handling for local development where files are omitted.
- `apps/frontend/app.config.js` or `apps/frontend/plugins/` (if custom plugin logic needed for optional credentials).

#### Deliverables
- [ ] `pnpm install` succeeds across the workspace.
- [ ] `npx expo config --type public` in `apps/frontend` resolves without error.
- [ ] Web export (`pnpm --filter ansari-frontend build:web`) continues to compile cleanly.

#### Acceptance Criteria
- [ ] Workspace typecheck (`pnpm typecheck`) and lint (`pnpm lint`) pass with 0 errors.
- [ ] Native module imports do not crash web bundling (`react-native-web` compatibility).

---

### Phase 3: Frontend Auth Client Layer and Native Token Exchange

**Dependencies**: Phase 1, Phase 2

#### Objective
Implement the native sign-in adapters for Apple and Google, and connect them to `lib/auth/api.ts` and `lib/auth/context.tsx` to preserve the Two-Token Protocol.

#### Files to Create / Modify
- `apps/frontend/lib/auth/nativeApple.ts` & `apps/frontend/lib/auth/nativeApple.web.ts`:
  - Platform-split native Apple helper:
    - Native (`.ts` / `.native.ts`): checks `AppleAuthentication.isAvailableAsync()`, invokes `signInAsync({ requestedScopes: [FULL_NAME, EMAIL] })`, extracts `identityToken`, `fullName`, and calls auth API.
    - Web (`.web.ts`): falls back to web social redirect.
- `apps/frontend/lib/auth/nativeGoogle.ts` & `apps/frontend/lib/auth/nativeGoogle.web.ts`:
  - Platform-split native Google helper:
    - Native: configures `GoogleOneTapSignIn.configure({ webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID })`, invokes `GoogleOneTapSignIn.signIn()`, extracts `idToken`, and calls auth API.
    - Web: falls back to web social redirect.
- `apps/frontend/lib/auth/api.ts`:
  - Add `signInWithIdToken(provider: 'apple' | 'google', idToken: string, user?: { name?: { firstName?: string; lastName?: string } })`:
    - Sends `POST /api/auth/sign-in/social` with the Better Auth ID token payload.
    - Returns `{ token: string, user: AuthUser }`.
- `apps/frontend/lib/auth/context.tsx`:
  - Expose `signInWithApple()` and `signInWithGoogle()` on `AuthContext`.
  - When provider returns `sessionToken`, invoke `startSession({ sessionToken, user })` which:
    1. Calls `authApi.mintJwt(sessionToken)` (`GET /api/auth/token`).
    2. Persists `accessToken` and `sessionToken` in `expo-secure-store` / `localStorage`.
    3. Clears React Query cache and sets user state.

#### Deliverables
- [ ] Platform-split native Apple and Google auth services implemented.
- [ ] `lib/auth/api.ts` implements `signInWithIdToken`.
- [ ] `lib/auth/context.tsx` provides unified social sign-in methods adhering to Two-Token Protocol.

#### Acceptance Criteria
- [ ] Calling `signInWithIdToken` against `apps/auth` establishes a valid session and mints an EdDSA JWT for `apps/api`.
- [ ] 401 token refresh functions identically to email/password sessions.

---

### Phase 4: AuthSheet UI Wiring, Cancellation UX, and Error Handling

**Dependencies**: Phase 3

#### Objective
Connect `ProviderButton` in `AuthSheet.tsx` to the native/web social flows, replacing the "unavailable" stub with real sign-in, smooth cancellation handling, and inline error reporting.

#### Files to Create / Modify
- `apps/frontend/components/AuthSheet.tsx`:
  - Remove `unavailable` local state stub and message.
  - Wire Apple `onPress` to `handleAppleSignIn`.
  - Wire Google `onPress` to `handleGoogleSignIn`.
  - Handle user cancellation:
    - Apple: ignore `ERR_REQUEST_CANCELED` (code 1001) without displaying an error.
    - Google: ignore user cancellation without displaying an error.
  - On network or verification failure, display inline error banner using existing error styling.
  - On success, close the sheet via `closeAuthSheet()`.
- `apps/frontend/components/AuthSheet.test.tsx`:
  - Update unit tests: remove tests expecting "not available yet" stub notice.
  - Add tests verifying provider button click triggers `signInWithApple` / `signInWithGoogle`.
  - Add tests verifying cancellation does not set error state.
  - Add tests verifying backend error sets inline error message.

#### Deliverables
- [ ] `AuthSheet.tsx` buttons wired to real authentication handlers.
- [ ] Cancellation UX leaves sheet open with no errors.
- [ ] Success UX dismisses sheet cleanly.
- [ ] Updated component test suite in `apps/frontend/components/AuthSheet.test.tsx`.

#### Acceptance Criteria
- [ ] Pressing Apple on iOS launches native Apple dialog.
- [ ] Pressing Google launches native Google dialog via Credential Manager on Android and native SDK on iOS.
- [ ] Cancelling native dialog shows no error alert or toast.
- [ ] Component tests pass in `vitest`.

---

### Phase 5: Automated Tests, Contract Verification, and Workspace Verification

**Dependencies**: Phase 1, Phase 2, Phase 3, Phase 4

#### Objective
Execute full test suites across all packages, verify contract invariants, and update operator self-hosting documentation.

#### Files to Create / Modify
- `packages/auth/tests/config/social-auth.test.ts`:
  - Full end-to-end integration tests in PGlite:
    - ID token verification $\to$ session creation $\to$ `GET /api/auth/token` JWT minting $\to$ JWKS validation.
    - Account linking: user registered with email/password signs in with Google ID token sharing the same email; asserts single `auth.users` row and linked `auth.account` row.
    - Negative tests: tampered JWT signature, expired token, mismatched audience, missing email claim.
- `docs/self-hosting.md`:
  - Document setup steps for Google Cloud Console (OAuth Web Client ID, Android package name, SHA-1 fingerprint) and Apple Developer Portal (Services ID, Bundle ID, Key).
- Monorepo gate verification.

#### Deliverables
- [ ] Complete automated test suite covering positive and negative cases.
- [ ] Documentation updated in `docs/self-hosting.md`.
- [ ] All workspace checks pass cleanly.

#### Acceptance Criteria
- [ ] `pnpm --filter @ansari/auth test` passes (all PGlite tests green).
- [ ] `pnpm --filter ansari-frontend test` passes.
- [ ] `pnpm --filter ansari-frontend build:web` succeeds.
- [ ] Workspace-wide commands pass: `pnpm typecheck`, `pnpm lint`, `pnpm test`.

---

## Verification Checklist

| Layer | Check | Command | Target Outcome |
| :--- | :--- | :--- | :--- |
| **Auth Package** | PGlite social auth tests | `pnpm --filter @ansari/auth test` | All tests pass, zero regressions on email auth |
| **Frontend Unit** | AuthSheet & component tests | `pnpm --filter ansari-frontend test` | All tests pass |
| **Web Build** | Metro web export | `pnpm --filter ansari-frontend build:web` | Compiles `dist/` cleanly |
| **Workspace Lint** | ESLint across packages | `pnpm lint` | 0 errors |
| **Workspace Types** | TypeScript typecheck | `pnpm typecheck` | 0 errors |
| **Workspace Tests** | Full test suite | `pnpm test` | All suites green |

---

## Rollback & Failure Modes

- **Invalid ID Token**: Returns 401 `INVALID_TOKEN`. Client displays inline error: "Unable to verify sign-in credentials. Please try again."
- **Unverified Provider Email**: Fails closed with 401 `USER_EMAIL_NOT_FOUND`. Account is not created.
- **Provider Outage / Network Error**: Client catches network failure, preserves typed fields, and displays inline error notice.
- **Missing Google Play Services (Android)**: Handled gracefully by `react-native-nitro-google-signin`, surfacing an actionable error without crashing the app.
- **OAuth Credentials Unset on Server**: If self-hoster has not provided Google/Apple credentials, `socialProviders` is inactive; email/password authentication and guest mode continue to function normally.
