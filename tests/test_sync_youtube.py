import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from scripts import sync_youtube as s
from scripts.request_pages_build import request_build, BuildError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'scripts/youtube-sync-config.json').read_text())
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
PAGE = (ROOT / 'tests/fixtures/listening-room.html').read_text()
ORIGINAL = json.loads((ROOT / 'tests/fixtures/original-tracks.json').read_text())


def video(vid='NEWRELEASE1', date='2026-10-18T14:00:00Z', title='A New Ambient Soundscape | Focus Relax Sleep', duration='PT45M1S'):
    return {'id': vid, 'snippet': {'channelId': CONFIG['channel_id'], 'publishedAt': date, 'title': title, 'liveBroadcastContent': 'none', 'thumbnails': {'high': {'url': 'https://i.ytimg.com/vi/' + vid + '/hqdefault.jpg'}}}, 'status': {'privacyStatus': 'public', 'uploadStatus': 'processed', 'embeddable': True}, 'contentDetails': {'duration': duration}}


def history():
    return [video(t['id'], f'2026-09-{28-i:02}T14:00:00Z', t['title']) for i, t in enumerate(ORIGINAL)]


class MergeTests(unittest.TestCase):
    def test_noop_is_byte_identical_and_preserves_all_21(self):
        updated, added = s.render_update(PAGE, history(), CONFIG, NOW)
        self.assertEqual(updated, PAGE)
        self.assertEqual(added, [])
        self.assertEqual(s.read_page(updated)[0], ORIGINAL)

    def test_new_release_first_and_existing_metadata_and_artwork_preserved(self):
        # Simulate a manually customised cassette and an unknown metadata field.
        manual = PAGE.replace('class="case" data-index="3"', 'class="case" style="background-image:url(custom-art.webp)" data-index="3"')
        tracks, (start, end), _, old_buttons = s.read_page(manual)
        tracks[3]['customArtwork'] = 'art/my-tape.webp'
        tracks[3]['editorial'] = {'note': 'Keep this exact value'}
        manual = manual[:start] + json.dumps(tracks, ensure_ascii=False) + manual[end:]
        updated, added = s.render_update(manual, history() + [video()], CONFIG, NOW)
        new_tracks, _, _, buttons = s.read_page(updated)
        self.assertEqual(new_tracks[0]['id'], 'NEWRELEASE1')
        self.assertEqual(len(new_tracks), 22)
        self.assertEqual(len(added), 1)
        for original in tracks:
            self.assertIn(original, new_tracks)
        for vid, button in old_buttons.items():
            normalize = lambda b: re.sub(r'\d+', '#', re.sub(r'aria-pressed="(?:true|false)"', 'aria-pressed="#"', b))
            self.assertEqual(normalize(button), normalize(buttons[vid]))
        self.assertIn('custom-art.webp', buttons[tracks[3]['id']])
        self.assertIn('22 RECORDINGS', updated)
        self.assertIn('all 22 recordings', updated)
        self.assertIn('id="selected-title">A New Ambient Soundscape', updated)
        self.assertIn('watch?v=NEWRELEASE1', updated)

    def test_duplicates_idempotence_and_existing_metadata_not_refreshed(self):
        updated, _ = s.render_update(PAGE, history() + [video(), video()], CONFIG, NOW)
        again, added = s.render_update(updated, history() + [video(title='Renamed Ambient Title')], CONFIG, NOW)
        self.assertEqual(updated, again)
        self.assertEqual(added, [])
        self.assertEqual(len(s.read_page(again)[0]), 22)

    def test_missed_checks_and_old_backfill_sorted_by_publication_not_discovery(self):
        candidates = [video('OLDRELEASE1', '2026-08-01T14:00:00Z'), video('NEWRELEASE2', '2026-10-10T14:00:00Z'), video('NEWRELEASE1')]
        updated, added = s.render_update(PAGE, history() + candidates, CONFIG, NOW)
        ids = [t['id'] for t in s.read_page(updated)[0]]
        self.assertEqual(ids[:2], ['NEWRELEASE1', 'NEWRELEASE2'])
        self.assertEqual(ids[-1], 'OLDRELEASE1')
        self.assertEqual(len(added), 3)

    def test_missing_old_video_is_retained(self):
        updated, _ = s.render_update(PAGE, history()[1:] + [video()], CONFIG, NOW)
        self.assertIn(ORIGINAL[0], s.read_page(updated)[0])

    def test_new_button_uses_same_design_and_script_safely_escapes_remote_text(self):
        evil = video(title='Ambient </script><script>alert("x")</script> & rain')
        updated, _ = s.render_update(PAGE, history() + [evil], CONFIG, NOW)
        self.assertNotIn('</script><script>alert', updated)
        self.assertIn('&lt;/script&gt;', updated)
        self.assertEqual(s.read_page(updated)[0][0]['title'], evil['snippet']['title'])
        button = s.read_page(updated)[3]['NEWRELEASE1']
        self.assertIn('class="case"', button)
        for cls in ('case-index', 'case-name', 'case-duration'):
            self.assertIn('class="' + cls + '"', button)

    def test_fail_closed_on_manually_changed_architecture(self):
        with self.assertRaises(s.SyncError):
            s.render_update(PAGE.replace('id="shelf"', 'id="new-shelf"'), [video()], CONFIG, NOW)

    def test_all_eligible_additions_have_metadata(self):
        updated, _ = s.render_update(PAGE, history() + [video()], CONFIG, NOW)
        t = s.read_page(updated)[0][0]
        self.assertEqual(t['publishedAt'], '2026-10-18T14:00:00Z')
        self.assertTrue(t['thumbnail'].startswith('https://i.ytimg.com/'))
        self.assertEqual(t['duration'], '45:01')


class ClassificationTests(unittest.TestCase):
    def test_public_ambient_release(self):
        self.assertTrue(s.eligible(video(), CONFIG, NOW))

    def test_shorts_not_duration_only_and_nonambient_long_videos_rejected(self):
        for v in [video(duration='PT3M'), video(duration='PT59S'), video(title='Ambient #shorts', duration='PT40M'), video(title='Ambient channel teaser', duration='PT20M'), video(title='Studio equipment tutorial', duration='PT50M'), video(title='My holiday', duration='PT1H')]:
            with self.subTest(video=v):
                self.assertFalse(s.eligible(v, CONFIG, NOW))

    def test_private_unlisted_unreleased_unavailable_other_channel_live_and_unembeddable(self):
        changes = [('status', 'privacyStatus', 'private'), ('status', 'privacyStatus', 'unlisted'), ('status', 'uploadStatus', 'rejected'), ('status', 'embeddable', False), ('snippet', 'publishedAt', '2027-01-01T14:00:00Z'), ('snippet', 'channelId', 'OTHER_CHANNEL'), ('snippet', 'liveBroadcastContent', 'upcoming'), ('snippet', 'liveBroadcastContent', 'live')]
        for part, field, value in changes:
            v = video();v[part][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(s.eligible(v, CONFIG, NOW))
        v = video();v['liveStreamingDetails'] = {'scheduledStartTime': '2026-10-18T14:00:00Z'}
        self.assertFalse(s.eligible(v, CONFIG, NOW))

    def test_finished_premiere_can_be_a_release(self):
        v = video();v['liveStreamingDetails'] = {'actualEndTime': '2026-10-18T16:00:00Z'}
        self.assertTrue(s.eligible(v, CONFIG, NOW))

    def test_region_and_age_restricted_videos_are_not_added(self):
        for extra in [{'regionRestriction': {'blocked': ['DK']}}, {'contentRating': {'ytRating': 'ytAgeRestricted'}}]:
            v = video();v['contentDetails'].update(extra)
            self.assertFalse(s.eligible(v, CONFIG, NOW))

    def test_excluded_id_and_invalid_metadata(self):
        c = {**CONFIG, 'excluded_video_ids': ['NEWRELEASE1']}
        self.assertFalse(s.eligible(video(), c, NOW))
        with self.assertRaises(s.SyncError):
            s.eligible(video(duration='garbage'), CONFIG, NOW)
        with self.assertRaises(s.SyncError):
            s.eligible(video(date='garbage'), CONFIG, NOW)


class DiscoveryTests(unittest.TestCase):
    def client(self, pages):
        class Fake(s.YouTube):
            def __init__(self):self.calls = [];self.pages = iter(pages)
            def get(self, resource, **params):
                self.calls.append((resource, params))
                item = next(self.pages)
                if isinstance(item, Exception):raise item
                return item
        return Fake()

    def channel(self):
        return {'items': [{'id': CONFIG['channel_id'], 'contentDetails': {'relatedPlaylists': {'uploads': 'UPLOADS'}}}]}

    def test_paginate_beyond_latest_feed_and_handle_reordered_duplicates(self):
        v1, v2 = video(), video('NEWRELEASE2')
        c = self.client([self.channel(), {'items': [{'contentDetails': {'videoId': v1['id']}}], 'nextPageToken': 'older'}, {'items': [{'contentDetails': {'videoId': v1['id']}}, {'contentDetails': {'videoId': v2['id']}}]}, {'items': [v1, v2]}])
        self.assertEqual(len(c.discover(CONFIG)), 2)
        self.assertEqual(c.calls[2][1]['pageToken'], 'older')

    def test_mid_scan_failure_and_empty_feed_leave_disk_unchanged(self):
        for pages in [[self.channel(), {'items': []}], [self.channel(), {'items': [{'contentDetails': {'videoId': 'NEWRELEASE1'}}], 'nextPageToken': 'later'}, s.SyncError('YouTube unavailable')]]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d);(root/'scripts').mkdir()
                (root/'scripts/youtube-sync-config.json').write_text(json.dumps(CONFIG))
                (root/'listening-room.html').write_text(PAGE)
                with self.assertRaises(s.SyncError):s.synchronize(root, self.client(pages), now=NOW)
                self.assertEqual((root/'listening-room.html').read_text(), PAGE)

    def test_wrong_channel_aborts(self):
        c = self.channel();c['items'][0]['id'] = 'WRONG'
        with self.assertRaises(s.SyncError):self.client([c]).discover(CONFIG)

    def test_http_failures_retry_without_leaking_key(self):
        def bad(req, **kw):raise HTTPError(req.full_url, 503, 'error', {}, None)
        c = s.YouTube('SUPER_PRIVATE_KEY', opener=bad, sleeper=lambda _: None)
        with self.assertRaises(s.SyncError) as error:c.get('videos', id='NEWRELEASE1')
        self.assertNotIn('SUPER_PRIVATE_KEY', str(error.exception))

    def test_dry_run_then_write_then_noop(self):
        class Client:
            def discover(self, config):return history() + [video()]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'scripts').mkdir();(root/'scripts/youtube-sync-config.json').write_text(json.dumps(CONFIG));path=root/'listening-room.html';path.write_text(PAGE)
            self.assertEqual(len(s.synchronize(root, Client(), dry_run=True, now=NOW)), 1)
            self.assertEqual(path.read_text(), PAGE)
            s.synchronize(root, Client(), now=NOW);written=path.read_text()
            self.assertEqual(s.synchronize(root, Client(), now=NOW), [])
            self.assertEqual(path.read_text(), written)

    def test_concurrent_local_edit_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'page';p.write_text('my edit')
            with self.assertRaises(s.SyncError):s.atomic_write(p, 'sync', 'old page')
            self.assertEqual(p.read_text(), 'my edit')


class WorkflowTests(unittest.TestCase):
    def test_copenhagen_summer_winter_and_dst_boundaries(self):
        workflow=(ROOT/'.github/workflows/youtube-listening-room.yml').read_text()
        self.assertIn("cron: '5 17 * * *'", workflow)
        self.assertIn('timezone: Europe/Copenhagen', workflow)
        for day, utc_hour in [('2026-07-01',15),('2026-01-01',16),('2026-03-29',15),('2026-10-25',16)]:
            local=datetime.fromisoformat(day+'T17:05:00').replace(tzinfo=ZoneInfo('Europe/Copenhagen'))
            self.assertEqual(local.astimezone(timezone.utc).hour, utc_hour)

    def test_pages_reuses_existing_publisher_and_retries_failed_build(self):
        settings={'build_type':'legacy','source':{'branch':'main','path':'/'}}
        for latest, expected in [({'status':'built','commit':'head'},False),({'status':'errored','commit':'head'},True),({'status':'built','commit':'old'},True),({'status':'building','commit':'head'},False)]:
            calls=[];responses=iter([settings,latest,{}])
            def opener(req, **kw):calls.append(req.method);return io.BytesIO(json.dumps(next(responses)).encode())
            request_build('Grimiss/grimiss.github.io','SECRET','head','main',opener)
            self.assertEqual('POST' in calls, expected)

    def test_changed_pages_configuration_fails_safely(self):
        def opener(req, **kw):return io.BytesIO(b'{"build_type":"workflow"}')
        with self.assertRaises(BuildError):request_build('owner/repo','SECRET','head','main',opener)


if __name__ == '__main__':unittest.main()
