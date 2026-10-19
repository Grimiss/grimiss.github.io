# Automatic Listening Room releases

## Status and review

This change is prepared on `feature/youtube-listening-room-sync`. It must be
reviewed and merged before the daily schedule exists on `main`. No site styling,
layout, artwork, animation or player controls have been replaced.

There is no API key in this repository. A real YouTube API sync and authenticated
Pages build cannot be verified until the repository owner configures the secret.
The automated tests use realistic API responses and never change the live site.

## One-time setup

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a
   project and enable **YouTube Data API v3**.
2. Create an API key. Restrict it to **YouTube Data API v3**. Do not add browser
   referrer restrictions: this key runs only on GitHub's server, not on the site.
   Do not paste it into chat, a source file, workflow input, issue or pull request.
3. In the [repository Actions secrets settings](https://github.com/Grimiss/grimiss.github.io/settings/secrets/actions),
   choose **New repository secret**, name it **`YOUTUBE_API_KEY`**, and paste the
   key there. No paid hosting, OAuth sign-in or browser API key is needed.
4. Review and merge the pull request. Keep Pages on **Deploy from a branch →
   main → /(root)**. The workflow intentionally refuses to create a second
   deployment path if those settings change.
5. In **Actions → Listening Room YouTube sync → Run workflow**, first leave
   **dry_run** checked. Review the number and IDs of eligible additions. Then
   run again with **dry_run** unchecked to activate the first real update.
   Branch protection may need to allow this repo's GitHub Actions bot to push
   the updated `listening-room.html`; do not disable protection blindly. If it
   rejects the push, ask for a reviewed PR-based update policy instead.

The workflow requests `contents: write` and `pages: write` for the sync job only;
it uses GitHub's short-lived built-in token. If repository/organisation policy
denies those permissions, the run fails clearly and leaves remote source safe.
No personal access token is required in the proposed setup.

## Daily behaviour

- Runs at **17:05 Europe/Copenhagen**, automatically **15:05 UTC in summer** and
  **16:05 UTC in winter**. GitHub supports the IANA timezone field. Actual starts
  may be late; it is intentionally not on the busy start of the hour.
- Resolves `@grimiss-ambient` with the official YouTube API, verifies the expected
  channel ID `UCR6yRA2FI4lSefDMnSdmaDg`, then scans **every page** of its uploads
  playlist, not just a short RSS window. It does not rely on a last-run watermark.
- Retrieves title, video ID, thumbnail, published date, duration and public/
  embedding status. No credentials are delivered to website visitors.
- Adds only unknown eligible IDs, ordered by actual video publication time.
  A backlog discovered after missed checks is processed on the next successful run.
- Existing editorial names, durations, notes, extra metadata and customised
  cassette button HTML remain intact. Renumbering and selection attributes follow
  the new order, just as with manually inserting a tape. Generic tape colour
  variations continue to follow the existing CSS; custom inline artwork is kept.
- New tapes use exactly the same `.case`, `.case-index`, `.case-name` and
  `.case-duration` structure. The player keeps its existing YouTube iframe,
  playback controls, privacy-enhanced embed URL, selection and unload behaviour.
- Counts and first-selected tape details update together. No browser fetch or
  new loading screen is introduced. Relative asset paths work on a custom domain.
- No eligible new videos means **no page rewrite and no commit**.
- After a bot commit, requests a build through the **existing branch-based Pages
  publisher**. Bot-token commits do not themselves trigger a Pages build, so this
  explicit request matters. No `deploy-pages` workflow is introduced.
- If the push loses a race to a manual edit, it fails without a force-push or
  automatic merge. The next check starts from the current default branch.
- If publication fails after the commit succeeds, the next successful sync also
  checks whether the current commit has been built, and requests a retry if needed.
  A build already queued/running is left alone. Nothing builds on a dry-run/PR.

## What counts as an eligible long-form ambient release?

The YouTube Data API does not expose a documented `isShort` classification.
YouTube currently categorises eligible square/vertical uploads of up to three
minutes as Shorts. Duration alone also cannot distinguish an ambient release
from an equipment tutorial or a channel announcement.

The conservative automatic policy therefore requires **all** of:

1. Owned by the verified Grimiss Ambient channel.
2. Public, processed, embeddable, and published at or before the check time.
3. Not currently live or an upcoming premiere. Completed premieres/replays may
   qualify only after their actual end time and with ordinary release metadata.
4. **Longer than three minutes** (strictly more than 180 seconds).
5. A title containing an established release marker: **ambient**, **soundscape**,
   **FRS**, **SRS** or **tape loop**.
6. No explicit **Short/Shorts**, teaser, trailer, tutorial, walkthrough, preview,
   promo or announcement marker in the title; no Short/Shorts tag.
7. Not listed in `excluded_video_ids` in `scripts/youtube-sync-config.json`.

This covers the existing 30-minute-to-eight-hour FRS style without requiring a
playlist. It is deliberately **not a universal Shorts detector**: a genuine
widescreen two-minute release, or a release with none of those title markers,
will be skipped. Use one established marker in normal long-form titles. If your
publishing format changes, review this policy; a curated release playlist is the
more precise alternative. A video titled as an ambient release but actually
containing a tutorial cannot be identified from public metadata alone.

The first sync can also find older qualifying releases absent from the current
21-tape collection. The dry-run shows IDs before any site update.

## Manual edits and failure handling

The page's existing `const tracks=[...]` remains the collection source of truth.
You can still edit a tape's name, title, note, duration or cassette HTML directly.
The sync never refreshes existing records from YouTube. Unknown metadata fields
survive unchanged. A tape-specific `note` is supported (Entropy uses it).

Adding/removing a cassette manually requires keeping the JSON track array and
matching `data-index` buttons consistent, as before. If those structures no longer
agree, automation stops for review instead of regenerating the page. If you want
to remove an auto-added release permanently, add its ID to `excluded_video_ids`
before manually removing it; existing tapes are never deleted by the automation.

Network errors, quota/authentication failures, invalid responses, malformed local
data and changed page structure stop before the atomic write. The script never
replaces the collection with an API response, even an empty one. A private,
deleted or unavailable existing tape is retained rather than silently removed.

GitHub can disable schedules in public repositories after **60 days without
repository activity**. A successful no-change check intentionally does not make
dummy commits. Monitor Actions notifications; re-enable the workflow if GitHub
disables it. Workflow email notifications should be enabled for failures.

## Tests and local checks

```sh
python -m unittest discover -s tests -v
node --test tests/player.test.cjs
python scripts/sync_youtube.py --dry-run
```

The last command needs `YOUTUBE_API_KEY` set securely in the environment.
The Python and Node tests need no API key or third-party packages. They cover
preservation, custom artwork/fields, Shorts/promos/private/future exclusions,
all-page discovery, duplicates, publication order, missed checks, no-op stability,
safe HTML/JavaScript escaping, mid-scan failure, manual edit protection, player
selection and iframe controls, Copenhagen DST and Pages retry decisions.

## Reference documentation

- [GitHub schedule timezone and inactivity behaviour](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [YouTube channels by handle and uploads](https://developers.google.com/youtube/data/reference/rest/v3/channels/list)
- [YouTube playlist pagination](https://developers.google.com/youtube/data/reference/rest/v3/playlistItems/list)
- [YouTube public video metadata](https://developers.google.com/youtube/data/reference/rest/v3/videos)
- [YouTube Shorts classification](https://support.google.com/youtube/answer/15424877)
- [GitHub token does not automatically build Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Requesting an existing Pages build](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build)
