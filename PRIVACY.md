# Little Explorer: Privacy and Terms

Last updated: October 22, 2026

Little Explorer is a picture-book app about cities and travel for young children. A grown-up signs in, and the
child reads, listens and, if the grown-up turns it on, talks with Milo, a voice buddy.

## What we collect

- **The grown-up's email address.** It is the only personal information we ask for. We never ask for the
  child's name, age, photo or anything else about the child.
- **The family's progress:** which books the account can see, and the stars and coins earned in the app. A
  star records the book, the page, when it was earned, and a short note from Milo about the answer (a few
  words, not a recording). Earlier versions of the app also saved the pages the child marked with a heart
  ("hearts"). The app doesn't do that anymore; hearts already saved stay with the account until it is deleted.
- **Milo time:** how many minutes each iPad or iPhone talked with Milo each day, and which account it was
  signed in to, so we can apply the account's daily time limit (shared by its devices) and see what the voice
  service costs. Each device is known by a random id, its name and model.
- **Sign-in codes.** A code works for 10 minutes. We keep only a scrambled (hashed) form of it and clear it
  within two days. To stop abuse we also keep a scrambled form of the network address that asked for a code.

## How we use it

- The email is used only to send 6-digit sign-in codes and to keep the books, stars and coins the same
  on all of the family's devices.
- No ads, no tracking, no analytics companies, and we never sell or share your information for marketing.
  The app's privacy manifest (`PrivacyInfo.xcprivacy`) lists the same data and declares no tracking.
- Sign-in emails are sent from the app owner's Gmail account through the Gmail API. The app has permission
  only to send email; it cannot read your mail or anyone else's.

## Talking with Milo

Milo is an AI voice buddy, and it is **off until a grown-up allows it**. Right after a grown-up signs in, the app
explains what Milo sends and keeps and asks **Allow Milo** or **Not now**. On devices that were already signed in
before this step existed, the same explanation appears behind the grown-up check the first time **Talk to Milo**
is tapped. "Not now" keeps Milo off; reading and read-aloud work the same either way.

While Milo is on and the child has tapped **Talk to Milo**, the child's speech and the page being read are
streamed through [LiveKit](https://livekit.io) (the voice connection) to [OpenAI](https://openai.com) (the AI)
so Milo can understand and answer. The app doesn't record or keep the audio, and it doesn't save conversations.
The voice service may keep short text of what was said in its logs for a limited time to fix problems. OpenAI
handles the audio under its own API terms, which say API data isn't used to train its models by default. When
Milo is off, nothing is streamed.

The grown-up's choice is saved with the account, so it counts on every iPad and iPhone signed in with that email.
To turn Milo off, open the **Grown-ups** corner (behind the grown-up check) and switch off **Milo, the AI voice
buddy**. Our server then refuses to start Milo for that account until a grown-up turns it back on.

## Where it is stored

On the device, and on our server (hosted by [Render](https://render.com), with the database at
[Neon](https://neon.tech)), over encrypted connections. The sign-in token stays in the device's Keychain.

## Deleting your account

In the app, open the **Grown-ups** corner (behind the grown-up check) and tap **Delete account…**. After you
type a new code we email you, the account is deleted for good: its email, books list, stars, coins, hearts and
sign-ins are removed from the server right away, and every device signed in to it is signed out. Backups made
before that are overwritten over time. The minutes each device used are unlinked from the account and kept
only with the device's random id; email us to remove them too.

## Children

The app is made to be used by a child together with a grown-up. Only the grown-up signs in and gives an email,
and sign-in, sign-out, account deletion and turning Milo on or off sit behind a grown-up check.

## Terms of use

- Little Explorer is a personal, family project. You may use it for your own family, free of charge.
- It is provided "as is", without warranties of any kind. Books, facts and Milo's answers may contain mistakes,
  and the service can change, pause or stop at any time.
- Please use it kindly: don't try to break, overload or misuse the app or its server.
- We may update this page; the date at the top shows the latest version.

## Contact

Questions, or to ask what we hold about you or to delete it: [angilyu@gmail.com](mailto:angilyu@gmail.com).
