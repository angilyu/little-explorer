# Little Explorer: Privacy and Terms

Last updated: October 23, 2026

Little Explorer is a picture-book app about cities and travel for young children. A grown-up signs in, and the
child reads, listens and, if the grown-up turns it on, talks with Milo, a voice buddy.

## What we collect

- **The grown-up's email address.** It is the only personal information we ask for. We never ask for the
  child's name, age, photo or anything else about the child.
- **The family's progress:** which books the account can see, and the stars and coins earned in the app. A
  star records the book, the page, when it was earned, whether it was for an answer or a question, and a
  short note from Milo about it (a few words, not a recording). Earlier versions of the app also saved the
  pages the child marked with a heart ("hearts"). The app doesn't do that anymore; hearts already saved stay with the account until it is deleted.
- **Milo time:** how many minutes each iPad or iPhone talked with Milo each day, and which account it was
  signed in to, so we can apply the account's daily time limit (shared by its devices) and see what the voice
  service costs. Each device is known by a random id, its name and model.
- **Reading activity**, while signed in: which pages of which books were shown and for how long (up to 10
  minutes a page), whether Milo was on, when read-aloud was started, finished or stopped early on a page (and
  whether the recorded voice or the device's voice read it), and the first time each day the app was opened on
  each device. Each event is stored with the account and the device's random id. It doesn't include the page's
  text, anything the child said, or any audio. Nothing is recorded while signed out.
- **Sign-in codes.** A code works for 10 minutes. We keep only a scrambled (hashed) form of it and clear it
  within two days. To stop abuse we also keep a scrambled form of the network address that asked for a code.

## How we use it

- The email is used only to send 6-digit sign-in codes and to keep the books, stars and coins the same
  on all of the family's devices.
- Reading activity is used only by the app's owner, to see how the books and read-aloud are used (for example
  how many families read each day, which books and pages they open, and how often read-aloud is finished) and
  to make the books better, and to show the child's reading badges in the Passport (for example "Read 25 pages"
  or "Read 3 days in a row") the same on every iPad and iPhone signed in to the account. For the badges the
  device keeps a short list of which pages were read, listened to or read with Milo, and on which days; signing
  out removes it from the device. It stays on our own server.
- No ads, no tracking, no analytics companies, and we never sell or share your information for marketing.
  The app's privacy manifest (`PrivacyInfo.xcprivacy`) lists the same data and declares no tracking.
- Sign-in emails are sent from the app owner's Gmail account through the Gmail API. The app has permission
  only to send email; it cannot read your mail or anyone else's.

## Talking with Milo

Milo is an AI voice buddy, and it is **off until a grown-up allows it**. After a grown-up signs in, the app
explains what Milo sends and keeps and asks **Allow Milo** or **Not now**. This is asked at every sign-in until a
grown-up allows Milo; once allowed, it isn't asked again on any iPad or iPhone signed in with that email. "Not now"
keeps Milo off and saves nothing. While Milo isn't allowed, tapping **Talk to Milo** shows the grown-up check and
then the same explanation, so a child can't turn Milo on alone. Reading and read-aloud work the same either way.

While Milo is on and the child has tapped **Talk to Milo**, the child's speech and the page being read are
streamed through [LiveKit](https://livekit.io) (the voice connection) to [OpenAI](https://openai.com) (the AI)
so Milo can understand and answer. The app doesn't record or keep the audio, and it doesn't save conversations.
Milo also tells LiveKit not to record its sessions, so LiveKit keeps no audio, transcripts or session traces. The only
sessions it records are on our own test accounts.
The voice service may keep short text of what was said in its logs for a limited time to fix problems. OpenAI
handles the audio under its own API terms, which say API data isn't used to train its models by default. When
Milo is off, nothing is streamed.

The grown-up's choice is saved with the account, so it counts on every iPad and iPhone signed in with that email.
To turn Milo on or off later, open the **Grown-ups** corner (behind the grown-up check) and use **Milo, the AI voice
buddy**; turning it on shows the same explanation and asks **Allow Milo**. After a grown-up turns Milo off there,
our server refuses to start Milo for that account.

## Library arrangement

A grown-up can hide books and change the order of the shelves and books in the **Grown-ups** corner. That
arrangement (book ids, shelf names and when each was changed) is saved with the account on our server, so every
device signed in to it shows the same shelf, and a copy is kept on the device until you sign out.

## Books made for your family

Some accounts can ask for a new book to be written: the grown-up gives a title, a shelf name, a reading level and,
if they like, a short note about what the book should be about. To write it:

- That request is sent to [OpenAI](https://openai.com), along with the book's text as it is written. OpenAI writes
  the text, checks its facts with web search, checks that everything is suitable for a child, and records the
  read-aloud voice.
- Photos come from [Wikimedia Commons](https://commons.wikimedia.org) under free licenses, with the credit shown
  under each photo.
- Nothing about the child is sent. Please don't put names or other personal details in the title or note.

The book's text is kept on our server. Its photos and recordings are kept in a private
[Cloudflare R2](https://www.cloudflare.com/developer-platform/r2/) storage bucket. They stay until the book or the
account is deleted. A made book is shown only on the shelf of the account it was made for (and any account the owner
grants it to). Its text and files are still part of the app's shared book list, though, so they aren't secret. That's one more reason to
keep personal details out of the request. We record what each book cost to make, with no content.

## Where it is stored

On the device, and on our server (hosted by [Render](https://render.com), with the database at
[Neon](https://neon.tech), and the files of made books at Cloudflare R2), over encrypted connections. The sign-in token stays in the device's Keychain.

## Deleting your account

In the app, open the **Grown-ups** corner (behind the grown-up check) and tap **Delete account…**. After you
type a new code we email you, the account is deleted for good: its email, books list, stars, coins, hearts,
reading activity, library arrangement, sign-ins, and the books made for it with their files are removed from the server right away, and every device signed in to it is
signed out. Backups made before that are overwritten over time. The minutes each device used are unlinked from the account and kept
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
