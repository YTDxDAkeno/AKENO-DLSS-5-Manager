# Accounts: welcome screen and My Account

Offscreen renders (Linux, Qt's `offscreen` platform) made with

```
QT_QPA_PLATFORM=offscreen python tools/account_screenshots.py docs/screenshots/v3-accounts --lang en
QT_QPA_PLATFORM=offscreen python tools/account_screenshots.py docs/screenshots/v3-accounts --lang de --gate 900x640 --window 1280x680
```

The answers come from a scripted API in that tool, not from the AKENO server: no account was created and no e-mail
was sent. English at 1180×780 (welcome screen) and 1280×800 (main window); German at the smallest sizes, 900×640
and 1280×680.

| File | State |
|---|---|
| `*-01-signin` | first start: sign in, keep me signed in, forgot password, create an account, connection status |
| `en-04-register-checks` | registration: the server's rules checked before sending, strength meter, mismatch |
| `*-06-check-your-inbox` | after registration, when the server reported the confirmation e-mail as sent (masked address, resend countdown) |
| `en-07-verification-email-not-sent` | the server reported that the e-mail could not be sent: no "we've sent" |
| `*-09-reset-requested` | password reset requested: the same text for every address |
| `en-10-reset-email-unavailable` | the server cannot send e-mail at all: an honest error instead of a confirmation |
| `*-11-my-account` | My Account: profile, security, license at a glance, PCs |
| `en-13-session-expired` | the sign-in ended while the license keeps running: "Sign in again" |
