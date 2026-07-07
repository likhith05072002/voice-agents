# Going Live: Google Sign-In for External Users

*How to move SonusLabs from "only I can log in" (Testing) to "any customer can log
in cleanly" (Production). We use only non-sensitive scopes (`email`, `profile`,
`openid`), so this does NOT require Google's paid/weeks-long security assessment.*

---

## Prerequisites (built into the app already)

- **Privacy Policy:** https://sonuslabs.online/privacy ✅ (live route)
- **Terms of Service:** https://sonuslabs.online/terms ✅ (live route)
- **App homepage:** https://sonuslabs.online ✅
- **Registered redirect URIs** in the OAuth client:
  - `http://localhost:8001/auth/google/callback` (local dev)
  - `https://sonuslabs.online/auth/google/callback` (prod)

> Locally these pages are at `http://localhost:5173/privacy` and `/terms`. Google
> needs the **public** `https://sonuslabs.online/...` URLs — so this step only fully
> completes once the app is deployed to the Pi with those pages live.

---

## Step-by-step (Google Cloud Console → your SonusLabs project)

1. **Verify domain ownership**
   - Google Search Console (search.google.com/search-console) → add property
     `sonuslabs.online` → verify via the DNS TXT record (add it in Cloudflare).
   - In the OAuth consent screen, add `sonuslabs.online` under **Authorized domains**.

2. **Complete the OAuth consent screen**
   - App name: `SonusLabs`
   - User support email: your email
   - App logo: upload the SonusLabs mark (optional but removes blank-app feel;
     uploading a logo triggers a one-time Google brand review, usually a few days)
   - App home page: `https://sonuslabs.online`
   - Privacy policy: `https://sonuslabs.online/privacy`
   - Terms of service: `https://sonuslabs.online/terms`
   - Authorized domains: `sonuslabs.online`
   - Developer contact email: your email
   - Scopes: confirm ONLY `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
     `openid` are listed. **Do not add anything else** — adding a sensitive scope is
     what forces the heavy verification.

3. **Publish**
   - OAuth consent screen → **Publish App** → confirm → status becomes
     **"In production."**
   - Because we use only non-sensitive scopes, external users can now sign in.

4. **Result**
   - New users see a normal consent screen: *"SonusLabs wants to access your name,
     email address, language preference and profile picture."* → Allow → done.
   - The "Google hasn't verified this app" warning disappears once the domain is
     verified and (if you uploaded a logo) the brand review clears.

---

## Notes / gotchas

- **Test-mode 7-day token expiry does NOT affect us.** That gotcha is about Google
  refresh tokens; we don't store them — we mint our own 30-day server-side sessions
  and only call Google once at login.
- **100 test-user cap** only applies in Testing mode. Publishing removes it.
- **Redirect-URI mismatch** is the #1 error: the URI must match character-for-
  character (scheme, host, port, path, no trailing slash). Prod is
  `https://sonuslabs.online/auth/google/callback`.
- **`prompt=select_account`** is set, so returning users get an account picker.
- **Rotate the client secret before launch** (it was shared in chat during setup):
  Console → Credentials → the OAuth client → reset/add secret → update
  `.env.production` on the Pi.
- If you later add features needing sensitive scopes (e.g. reading a customer's
  Google Calendar to book appointments), THAT triggers Google's full verification —
  plan for it separately.

---

## Deploy dependency

The public `/privacy` and `/terms` URLs must be reachable before Google will accept
them. So the order is: **deploy the current build to the Pi** (which serves these
pages at sonuslabs.online) → then complete the consent screen + publish. Until then,
keep the app in Testing with your own email as a test user (works today).
