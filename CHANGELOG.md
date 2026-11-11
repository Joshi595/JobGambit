# Changelog







## 0.20.1 (2026-11-09)

- **Calmer pages.** On the Board, each posting's Apply is a quiet button that turns primary on the posting you're on;
  My applications' "Apply myself" and the Apply page's "Change the documents" are no longer primary next to the main
  action. Small grey text is darker, readable to WCAG AA in light and dark mode. Small icon buttons are finger-sized on
  touch screens.
- **Search reads more postings right.** A German date with the full year ("23.10.2026") counts for the 60-day age
  filter, and a page saying "nicht mehr vakant" marks the posting closed.





## 0.18.7 (2026-11-09)

- **A logo of its own**: two squares, the one left behind and the one moved to, a gambit's first move. It shows in the
  sidebar, the browser tab and the desktop shortcut (new installs; reinstall to get it on an existing shortcut).


## 0.18.5 (2026-11-08)

- oauthlib 4.0.1: fixes a known security issue (CVE-2026-49272) in a library that notifications (apprise) use.

## 0.18.4 (2026-11-07)

- A word in a mail's legal footer no longer files a receipt as a rejection ("anderweitig" now needs a decision around it).
- A receipt for an application kept as not sent moves it to applied: it went out.


## 0.17.18 (2026-11-06)

- A CV uses its pages: the word target now matches what two full pages hold (it was far too low), and automatic fixes cut only
  what runs over. With room left on the last page, lines the writer removed come back, courses first, and a page that stays
  half empty shows as a warning. No AI for any of this.
- A Profile with one flagged term stays the one written for this job, with a warning, instead of being replaced by the first
  summary of the master CV.
- The standard cost level writes the CV and letter on Sonnet too: under the new CV rules a blind comparison of 5 applications
  found them as good as Opus's, for 56% less. Premium still writes on Opus.
- Designs › Look › Text alignment: left, centred, right or justified, as in a word processor, for the Profile, the lines under
  each entry and the letter's paragraphs. Headings and dates keep the design's; left is the default, so nothing else moves.

## 0.17.17 (2026-11-06)

- AI usage includes every model and final thinking totals; incomplete usage and unknown cost are labelled explicitly.
- A draft interrupted during review is reused when its inputs match. Drafts receive page and length targets and one
  relevant approved wording per bullet; automatic repairs affect only failing documents and stop without progress.
- Review and automatic repair use medium effort. Opus drafting and the independent reviewer are retained.


## 0.17.8 (2026-11-03)

- **My applications:** an application not sent yet has **Apply myself**: it opens on the Apply page with its CV and
  letter (also put in the Current application folder); “I applied” there marks that same application sent.
- **Current application folder:** opening an application always takes the previous one's CV and letter out, also
  when the new one has no PDFs yet (they are made once its Review is done); Your documents says so.

## 0.17.7 (2026-11-03)

- **Your documents** (Profile › Application details) also lists the CV and cover letter of the application you
  have open, as they are in the upload folder.

## 0.17.6 (2026-11-03)

- **German:** Settings › Appearance › App language › Deutsch. Every page is in German; server messages, alerts and
  mail summaries follow later.
- **Fit score:** how much of what a posting requires you have, with reasons like "meets 5 of 6 skill requirements ·
  missing: AUTOSAR". Old postings and second listings of the same job go to Less relevant.
- **Honest keywords:** each keyword of a posting is checked against your skills (in your CV, you have it, related, not
  in your profile). A tailored CV never claims a skill you don't have.
- **Review:** each CV section before and after, side by side; the letter next to the facts it uses.
- **Writing** has its own place in the sidebar, with the AI helpers for your style, one letter-length setting and
  better default instructions.
- **CV upload:** protected PDFs and photos of a CV work; clear messages instead of server errors.
- **Email:** step-by-step help to connect Gmail, Outlook, Yahoo, iCloud, GMX, WEB.DE, Zoho and Fastmail. A refused
  password says to use an app password, instead of showing the provider's raw error.
- **Needs you:** a job alert is no longer taken for an interview invitation; "Not about an application" clears a mail
  in one click; a card's title opens the posting.
- **Board:** ⭐ moves a posting to the Starred list, with Undo. Once you applied, the same job listed under another
  name of the employer is hidden. Re-scoring postings can't be cut off halfway.

## 0.16.2 (2026-10-31)

- pypdf 6.21.4: fixes seven known security issues in reading PDF files (CVE-2026-119203 to -119209).

## 0.16.1 (2026-10-31)

- JobQuill is now **JobGambit**: the old name belongs to another product. The first start moves your data folder,
  documents and saved passwords over; the scheduled tasks are made again under the new name.
- Coming from 0.13–0.14: Help › Update can't install the renamed app. Run the installer once more (README), it removes
  the old JobQuill copy and keeps your data.

## 0.13.0 (2026-10-24)

- **CareerPilot is now JobQuill.** Thousands of projects on GitHub share the old name. The command is now `jobquill`,
  and the data folder and environment variables are named after JobQuill too (`JOBQUILL_HOME`).
- Updating from CareerPilot: run the installer again (the update button of 0.12 can't install the renamed app). It
  removes the old copy, and the first start moves your data folder, saved passwords and documents over. The old
  scheduled tasks are replaced by new ones.

## 0.12.4 (2026-10-24)

- Ready to be public: test examples use fictional employers, and the docs say exactly what a failed document check
  does (it is shown before you send; it doesn't stop you).

## 0.12.3 (2026-10-24)

- A version tag no longer tries to publish to PyPI (it failed, as no PyPI project is set up). Publishing waits for the
  repository variable PUBLISH_PYPI = true, set when the project goes public.

## 0.12.2 (2026-10-24)

- Updated: fastapi 0.156.8 (the local server) and httpx2 2.16.0 (used by the tests); the test machines' actions/checkout
  7.0.1 and setup-uv 10.3.1, still pinned to exact commits.

## 0.12.1 (2026-10-24)

- Review cards read as whole sentences: the new line in full with the added words marked, the original below it
  with the removed words struck through (the Documents step's changes too).
- A computer without a password store (Linux without a Secret Service) no longer breaks Needs you: it reads as
  nothing saved.
- Tests no longer need an AI app installed (they failed on CI).

## 0.12.0 (2026-10-24)

**Never miss an interview**
- New **Needs you** page: interviews coming up, mails to act on (each with a short summary and the mail itself), mails
  whose application isn't clear (every candidate with its reasons), and what CareerPilot filed by itself.
- Mails are linked by the job number in their text, not only by the role's words; receipts are filed by themselves.
- Interview, test, offer and "action needed" alerts are always on, are kept and sent again until they arrive, and
  also show on your desktop. Reminders the evening before, that morning and three hours before.
- English and numeric interview times are read ("October 26, 2026 at 2:00 PM", "26/10/2026 14:00").
- The hourly email check no longer depends on the daily search, repairs itself when its task is missing, and warns
  you when it stopped running.
- Optional AI for mail sorts the mails the rules can't.

**Applying**
- Review cards mark exactly which words changed, with a tag for the kind of change, a summary and filters.
- My applications: cleaner rows and drawer; the job description is shown for interviews.
- How many people already applied, where LinkedIn shows it publicly.

**Interview prep**
- Practice opens on your next interview: a brief on the job, STAR stories from your CV, a drill on every line you
  sent, and a mock interview with a timer and a summary.

**Search**
- Every search word now comes round within each board's look-back window; Settings › Search shows the coverage.
- Postings filtered on their title alone are judged again once their text is read; an optional AI second look.
- With several places in one country, the boards search the whole country and the distance decides; with one place,
  your radius is passed to the boards.
- Google Jobs is left out while the scraper gets no results.

**Security**
- The page opens only with a launch code, so other accounts on the same computer can't use it; one CareerPilot per
  data folder.
- Install and Update use tagged releases only. CI actions are pinned, dependencies audited.
- Prompts can't pull untrusted text out of its fence; documents with password- or key-shaped text fail their checks.
- The Current application folder is readable by your account only. Problem reports leave out your name, email,
  phone and home folder.

## 0.11.0 and earlier

See the commit history.
