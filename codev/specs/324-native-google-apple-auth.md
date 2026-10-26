# Specification: Native Google and Apple Authentication (Mobile & Web)

<!--
SPEC vs PLAN BOUNDARY:
This spec defines WHAT and WHY. The plan defines HOW and WHEN.
Implementation phases, detailed code changes, and sequencing live in codev/plans/324-*.md.
-->

## Metadata

- **ID**: spec-324-native-google-apple-auth
- **Status**: draft
- **Created**: 2026-10-25
- **Source**: GitHub issue #324 (`iaser-ai/ansari#324`)
- **Protocol**: PIR

---

## Owner & Architect Guidance (Review Amendments)

1. **Native Modules on Mobile (Non-Negotiable)**: On native iOS and Android, third-party authentication **must use native modules**, not web browser redirects (`expo-web-browser`, Custom Tabs, or `ASWebAuthenticationSession`). Tapping "Continue with Apple" or "Continue with Google" must trigger the OS-native dialogs directly (`expo-apple-authentication` for Apple on iOS; `react-native-nitro-google-signin` for Google on Android and iOS). Web browser redirect OAuth applies strictly to the web build.
2. **Modern Identity Frameworks (Nitro Modules)**: Google authentication on mobile uses `react-native-nitro-google-signin` (Nitro Modules / JSI), which leverages Android's modern Credential Manager (`GetSignInWithGoogleOption`) rather than deprecated legacy Play Services Auth, and the native Google Sign-In SDK on iOS.
3. **Apple HIG & App Store Compliance**: Apple Sign-In is required on iOS under App Store Review Guideline 4.8 because the app offers third-party authentication. Button appearance and placement must strictly conform to Apple's Human Interface Guidelines.
4. **Two-Token Protocol Parity**: Social sign-in must integrate seamlessly into the monorepo's existing two-token architecture established in #300. Sessions created via Google or Apple must receive a Better Auth `sessionToken` and exchange it for an `apps/api` EdDSA JWT via `GET /api/auth/token`.
5. **No Disruption to Existing Auth**: Existing email/password authentication, password reset, guest account auto-provisioning, and account deletion contracts must remain unchanged.

---

## Problem Statement

In issue #245, "Continue with Apple" and "Continue with Google" buttons were introduced to the sign-in sheet (`apps/frontend/components/AuthSheet.tsx`) as presentational UI stubs above the email/password form. When pressed, they currently display an inline notice that the provider is unavailable.

Inspection of the authentication architecture reveals the underlying gaps:

1. **Backend Service (`apps/auth` & `packages/auth`)**:
   - Configures Better Auth solely with `emailAndPassword`. The `socialProviders` configuration is completely absent from `packages/auth/src/index.ts`.
   - `packages/auth/src/env.ts` has no environment variables declared for Google or Apple client IDs or secrets.
   - The backend does not yet validate native OpenID Connect / ID tokens sent directly from mobile SDKs.
2. **Frontend App (`apps/frontend`)**:
   - `lib/auth/api.ts` only supports email/password operations (`/sign-in/email`, `/sign-up/email`).
   - The app has no native authentication modules wired for Apple (`expo-apple-authentication`) or Google (`react-native-nitro-google-signin`).
   - Browser redirect OAuth on mobile introduces poor UX: it switches users out of the application into Safari/Chrome, prompts for third-party cookie permissions, requires deep-link URL scheme handling (`ansarichat://auth/callback`), and fails App Store review standards for frictionless native auth.

### Who is affected and how:
- **Mobile Users (iOS & Android)**: Cannot use single-tap biometric or native OS account sign-in; forced to manually type email and password or remain in unauthenticated guest mode.
- **Product & App Store Release**: Mobile submissions to the Apple App Store will fail Guideline 4.8 if third-party authentication is offered without equivalent native Apple Sign-In support.
- **Web Users**: Lack standard OAuth convenience buttons.
- **Security & Infrastructure**: Must ensure incoming third-party tokens are verified fail-closed against provider JWKS/certs with strict audience pinning, preserving the Two-Token protocol invariant.

---

## Current State

Verified directly against the codebase at branch base:

### Backend (`packages/auth` & `apps/auth`)

- **Database Schema (`packages/auth/src/schema.ts`)**:
  - The `auth.account` table already exists with columns suited for OAuth credentials: `id`, `user_id`, `account_id`, `provider_id`, `access_token`, `refresh_token`, `id_token`, `access_token_expires_at`, `refresh_token_expires_at`, `scope`, and a unique constraint on `(user_id, provider_id)`.
  - The `auth.users` table stores `id` (UUID), `email`, `name`, `first_name`, `last_name`, `email_verified`, `image`, and `registered_via`.
  - No database migration is required for social credentials.
- **Better Auth Instance (`packages/auth/src/index.ts`)**:
  - Configures `emailAndPassword`, `session` (30 days, 1-day slide), and plugins: `expo()`, `bearer()`, `jwt()` (EdDSA, 15m expiry), and `admin()`.
  - `trustedOrigins` includes `ansarichat://`, `CORS_ORIGIN`, and dev origins.
  - `socialProviders` is **unset**. Better Auth 1.7.7 provides built-in `verifyProviderIdToken` and `signInSocial` supporting native `idToken` payloads at `POST /api/auth/sign-in/social`, but it is currently inactive.
- **Environment Schema (`packages/auth/src/env.ts`)**:
  - Validates `DATABASE_URL`, `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `CORS_ORIGIN`, `AUTH_LAYER_ORIGIN`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `RESEND_API_KEY`, `FRONTEND_URL`. No OAuth variables exist.

### Frontend (`apps/frontend`)

- **Components (`components/AuthSheet.tsx` & `components/BrandMarks.tsx`)**:
  - Official vector marks `AppleMark` and `GoogleMark` are rendered inside `ProviderButton`.
  - Light and dark styles adhere to Apple HIG and Google branding guidelines.
  - `onPress` sets local state `unavailable` and displays an inline notice.
- **Client Auth Layer (`lib/auth/`)**:
  - `lib/auth/api.ts` calls `customFetch` for `/sign-in/email`, `/sign-up/email`, `/token`, `/sign-out`, and password reset.
  - `lib/auth/context.tsx` provides `AuthProvider`. `startSession()` accepts an `AuthSession`, calls `authApi.mintJwt(sessionToken)`, persists credentials via `saveSession()`, and clears the React Query cache.
  - `lib/auth/store.ts` persists `accessToken`, `sessionToken`, and user names to `expo-secure-store` (native) or `localStorage` (web).

### API Backend (`apps/api`)

- `apps/api` validates incoming `Authorization: Bearer <jwt>` against `apps/auth`'s public JWKS endpoint (`/api/auth/jwks`) statelessly, reading `{ sub, sid }`. It has no dependency on how the session was created and requires no code changes.

---

## Desired State

### 1. Unified Authentication Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                  │
├───────────────────────────────┬────────────────────────────────────────┤
│     iOS & Android Native      │                  Web                   │
│   (expo-apple-authentication  │      (Better Auth Web Client)          │
│    react-native-nitro-        │                                        │
│     google-signin)            │                                        │
└───────────────┬───────────────┴───────────────────┬────────────────────┘
                │ Native ID Token                   │ Web OAuth Redirect
                │ (Direct JSON POST)                │ / Callback
                ▼                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        apps/auth (Better Auth)                         │
│  - POST /api/auth/sign-in/social                                       │
│  - Verifies Apple ID Token via Apple JWKS (aud: chat.ansari.app)       │
│  - Verifies Google ID Token via Google Certs (aud: GOOGLE_CLIENT_ID)   │
│  - Links/creates user in auth.users and auth.account                   │
│  - Issues Better Auth sessionToken                                     │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ GET /api/auth/token
                                │ (Bearer sessionToken)
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        apps/auth JWT Engine                            │
│  - Mints 15-minute EdDSA JWT containing { sub: user.id, sid }          │
└───────────────────────────────┬────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        apps/frontend Storage                           │
│  - Persists sessionToken & accessToken in SecureStore / localStorage   │
│  - Attaches Bearer accessToken to all apps/api calls                   │
└────────────────────────────────────────────────────────────────────────┘
```

### 2. Native Mobile Authentication (iOS & Android)

- **Apple Sign-In (iOS)**:
  - Uses `expo-apple-authentication`.
  - When pressed, checks `AppleAuthentication.isAvailableAsync()`. On iOS 13+, invokes `AppleAuthentication.signInAsync({ requestedScopes: [FULL_NAME, EMAIL] })`.
  - Retrieves `identityToken` (Apple-signed JWT) and `fullName` (`givenName`, `familyName`).
  - Calls `POST /api/auth/sign-in/social` with payload:
    ```json
    {
      "provider": "apple",
      "idToken": {
        "token": "<apple_identity_token>",
        "user": {
          "name": {
            "firstName": "<givenName>",
            "lastName": "<familyName>"
          }
        }
      }
    }
    ```
  - Never opens a browser window, Custom Tab, or `ASWebAuthenticationSession`.
- **Google Sign-In (Android & iOS)**:
  - Uses `react-native-nitro-google-signin` (powered by Nitro Modules / JSI, integrating Android Credential Manager and native iOS Google Sign-In SDK).
  - Initialized with `GoogleOneTapSignIn.configure({ webClientId: GOOGLE_WEB_CLIENT_ID })` (or `'autoDetect'`).
  - Invokes `GoogleOneTapSignIn.signIn()` which renders the OS-native Google account picker sheet (One Tap / Credential Manager).
  - Retrieves `idToken` from response and calls `POST /api/auth/sign-in/social` with payload:
    ```json
    {
      "provider": "google",
      "idToken": {
        "token": "<google_id_token>"
      }
    }
    ```
  - Never opens an in-app browser (`expo-web-browser`) or Custom Tabs.
- **Cancellation & Failure UX**:
  - If a user cancels or dismisses the native dialog (e.g. `ERR_REQUEST_CANCELED` on Apple or user cancellation on Google), the sheet remains open without an error banner.
  - If network or server verification fails, an inline error banner is displayed in `AuthSheet`.

### 3. Web Social Authentication

- In standard desktop and mobile browsers:
  - Uses Better Auth client `authClient.signIn.social({ provider, callbackURL: window.location.origin })`.
  - Better Auth redirects to provider OAuth login.
  - On return to `callbackURL`, Better Auth sets session cookies and completes session establishment.
  - Frontend mints the initial `apps/api` EdDSA JWT via `GET /api/auth/token` and redirects to the application root.

### 4. Backend ID Token Verification & User Resolution

- **ID Token Verification**:
  - `apps/auth` verifies incoming `idToken`s fail-closed against provider official public keys:
    - Apple: `https://appleid.apple.com/auth/keys`. Validates `iss === 'https://appleid.apple.com'`, non-expired, and `aud` matches `chat.ansari.app` (iOS native Bundle ID) or `APPLE_CLIENT_ID` (Web Services ID).
    - Google: `https://www.googleapis.com/oauth2/v3/certs`. Validates `iss` in `['accounts.google.com', 'https://accounts.google.com']`, non-expired, and `aud` matches `GOOGLE_CLIENT_ID` (Web Client ID) or `GOOGLE_IOS_CLIENT_ID`.
- **Account Linking & Collision Policy**:
  - Both Google and Apple guarantee email verification (`email_verified: true`).
  - If an account already exists in `auth.users` with the same email (e.g. created via email/password or guest promotion):
    - The provider account row is linked in `auth.account` under the same `user_id`.
    - No duplicate user row is created.
    - Password authentication continues to work for that user.
- **Name Handling & Apple Special Case**:
  - Apple only returns `fullName` on the **first authorization**. Subsequent Apple logins omit the name.
  - On first sign-in, any provided `firstName` and `lastName` must be immediately saved to `auth.users`.
  - Apple Private Relay emails (`@privaterelay.appleid.com`) are treated as valid unique email addresses and saved as-is.
- **Client Attribution**:
  - User creation hooks inspect `X-Ansari-Client` to stamp `registered_via` (`ios`, `android`, or `web`).

### 5. Two-Token Protocol Parity

- The response from `apps/auth` contains the Better Auth `sessionToken` and user details:
  ```json
  {
    "redirect": false,
    "token": "<sessionToken>",
    "user": { "id": "<uuid>", "email": "...", "name": "..." }
  }
  ```
- Frontend calls `authApi.mintJwt(sessionToken)` to obtain the 15-minute `apps/api` EdDSA JWT.
- Tokens are saved to `expo-secure-store` (native) or `localStorage` (web).
- When `apps/api` returns `401`, the existing single-flight `refresh()` routine calls `GET /api/auth/token` with `sessionToken` and obtains a fresh JWT without user interruption.

---

## Stakeholders

- **Mobile Users (iOS & Android)**: Seamless one-tap native sign-in without leaving the app.
- **Web Users**: Familiar Google and Apple sign-in options.
- **App Review Teams (Apple App Store / Google Play)**: Native compliance with Apple Guideline 4.8 and Google Play Services standards.
- **Security & Engineering Maintainers**: Robust ID token signature verification, strict audience validation, and clean separation between auth sessions and API access tokens.

---

## Success Criteria

- [ ] **Native Apple Sign-In (iOS)**:
  - Tapping "Continue with Apple" on iOS launches the native system sheet via `expo-apple-authentication`.
  - No web browser, custom tab, or Safari view controller is ever opened.
  - Successful sign-in creates or links the user, securely storing tokens in `expo-secure-store`.
  - Works with Apple Private Relay emails (`@privaterelay.appleid.com`).
  - User names provided on first sign-in are captured in `auth.users`.
- [ ] **Native Google Sign-In (Android & iOS)**:
  - Tapping "Continue with Google" on Android and iOS launches the native account picker via `react-native-nitro-google-signin` (Nitro Modules).
  - No web browser or Custom Tab is opened on mobile devices.
  - Successfully verifies `idToken` on `apps/auth` and creates/links the user.
- [ ] **Web Social Sign-In**:
  - Google and Apple sign-in work in desktop and mobile web browsers via Better Auth's standard web redirect flow.
- [ ] **Two-Token Parity**:
  - All social sign-ins produce a Better Auth `sessionToken` and immediately mint an `apps/api` EdDSA JWT.
  - Requests to `apps/api` (`/api/v2/threads`, SSE streaming) succeed with the minted JWT.
  - 401 token refresh functions identically to email/password sessions.
- [ ] **Account Linking**:
  - Signing in with Google or Apple using an email already registered via email/password links the provider account without errors or orphaned duplicate users.
  - A user with both email/password and social login can sign in with either credential to access the exact same account.
- [ ] **Cancellation UX**:
  - User dismissing the native Apple or Google dialog leaves the `AuthSheet` intact without displaying an error alert.
- [ ] **Negative / Error Handling Tests**:
  - ID token with an invalid cryptographic signature returns 401 `INVALID_TOKEN`.
  - ID token with expired timestamp returns 401 `INVALID_TOKEN`.
  - ID token with mismatched audience returns 401 `INVALID_TOKEN`.
  - Provider returning no verified email fails closed with 401 `USER_EMAIL_NOT_FOUND`.
- [ ] **Automated Testing & Workspace Verification**:
  - Real DB (pglite) tests in `packages/auth` asserting ID token verification, account linking, and JWT minting.
  - Frontend component tests in `apps/frontend` verifying button handlers and cancellation safety.
  - Monorepo checks pass: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`.

---

## Constraints

1. **Native-Only on Mobile**: Mobile platforms must never fall back to browser redirects for Google or Apple.
2. **App Store Guideline 4.8**: Apple Sign-In button must use Apple's standard design rules and must be positioned prominently.
3. **EAS Prebuild / Native Config**:
   - `expo-apple-authentication` requires the config plugin and Apple Developer entitlement (`com.apple.developer.applesignin`).
   - `react-native-nitro-google-signin` requires `react-native-nitro-modules`, its Expo config plugin (`"react-native-nitro-google-signin"` in `app.json`), and Google Services configuration (`google-services.json` on Android, `GoogleService-Info.plist` on iOS).
4. **Secret Management**:
   - No client secrets may be bundled in the native mobile apps. Mobile SDKs acquire ID tokens signed by Apple/Google, which `apps/auth` verifies using public key sets.
   - Provider credentials (`GOOGLE_CLIENT_SECRET`, Apple private keys) live strictly on the backend (`apps/auth`).
5. **Database Conventions**:
   - Zero schema migrations needed (`auth.account` already contains all necessary columns: `provider_id`, `id_token`, etc.). Any future schema modifications must strictly use `drizzle-kit generate` (never `db:push`).
6. **No Anonymous API Bypass**: `apps/api` continues to require a valid Bearer JWT on all protected routes; social auth changes only how sessions are established on `apps/auth`.

---

## Assumptions

1. **Token Cryptography**: Apple and Google issue standard OpenID Connect compliant JWTs whose signatures are verifiable against their published JWKS endpoints (`https://appleid.apple.com/auth/keys` and `https://www.googleapis.com/oauth2/v3/certs`).
2. **Audience Configuration**:
   - For Apple on iOS: `aud` equals `chat.ansari.app` (the iOS Bundle ID).
   - For Apple on Web: `aud` equals `APPLE_CLIENT_ID` (the Apple Services ID).
   - For Google: `aud` equals `GOOGLE_CLIENT_ID` (the Web Client ID configured in Google Cloud Console and passed to `react-native-nitro-google-signin`).
3. **Email Verification Guarantee**: Google and Apple ID tokens only carry emails that have been verified by the respective identity provider (`email_verified: true`), allowing automatic account linking by email.
4. **Availability on Devices**:
   - Apple Authentication is available on iOS devices running iOS 13+.
   - Google Sign-In is supported on Android devices with Google Play Services (via Credential Manager) and iOS devices running modern iOS.
5. **Stateless API Gateway**: `apps/api` requires zero code modifications or deployments for this feature, as it verifies EdDSA JWTs minted by `apps/auth` statelessly.

---

## Out of Scope

- Additional third-party OAuth providers (GitHub, Microsoft, Facebook, etc.).
- Changes to email/password authentication or guest auto-provisioning rules.
- Native Google Sign-In on Web (Web uses standard Google OAuth redirect flow).
- Account unlinking UI in settings (deferred to future account management features).
