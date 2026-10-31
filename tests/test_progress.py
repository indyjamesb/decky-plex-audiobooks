import unittest

from progress import (
    AudioTrack,
    atempo_filters,
    play_pause_actions,
    snap_speed,
    sort_books,
    book_position,
    choose_resume,
    compute_position_ms,
    locate,
    playback_outcome,
    previous_target,
    reported_position_ms,
    skip_position,
    timeline_duration_ms,
    track_is_complete,
)


def track(duration, offset=0, views=0, key="1", title="Chapter"):
    return AudioTrack(
        rating_key=key,
        title=title,
        duration_ms=duration,
        view_offset_ms=offset,
        view_count=views,
        part_key=f"/library/parts/{key}",
    )


class ResumeTests(unittest.TestCase):
    def test_empty_book_is_finished(self):
        resume = choose_resume([])
        self.assertTrue(resume.finished)

    def test_unstarted_single_file(self):
        resume = choose_resume([track(10_000_000)])
        self.assertEqual((resume.index, resume.offset_ms, resume.finished), (0, 0, False))

    def test_partial_m4b(self):
        resume = choose_resume([track(10_000_000, offset=5_000_000)])
        self.assertEqual(resume.index, 0)
        self.assertEqual(resume.offset_ms, 5_000_000)
        self.assertFalse(resume.finished)

    def test_scrobbled_single_file_restarts(self):
        resume = choose_resume([track(10_000_000, views=1)])
        self.assertTrue(resume.finished)

    def test_near_end_is_finished(self):
        resume = choose_resume([track(100_000, offset=99_000)])
        self.assertTrue(resume.finished)

    def test_short_unstarted_file_is_not_complete(self):
        item = track(2_000)
        self.assertFalse(track_is_complete(item))
        resume = choose_resume([item])
        self.assertFalse(resume.finished)

    def test_multi_file_mid_chapter(self):
        tracks = [
            track(60_000, views=1, key="1"),
            track(60_000, offset=12_000, key="2"),
            track(60_000, key="3"),
        ]
        resume = choose_resume(tracks)
        self.assertEqual((resume.index, resume.offset_ms), (1, 12_000))

    def test_scrobbled_prefix_with_unstarted_next_file(self):
        tracks = [
            track(60_000, views=1, key="1"),
            track(60_000, views=1, key="2"),
            track(60_000, key="3"),
        ]
        resume = choose_resume(tracks)
        self.assertEqual((resume.index, resume.offset_ms, resume.finished), (2, 0, False))

    def test_later_anchor_wins_over_earlier_partial(self):
        tracks = [
            track(60_000, offset=10_000, key="1"),
            track(60_000, offset=1, key="2"),
        ]
        resume = choose_resume(tracks)
        self.assertEqual((resume.index, resume.offset_ms), (1, 1))

    def test_all_files_played(self):
        tracks = [track(60_000, views=1, key="1"), track(60_000, views=1, key="2")]
        self.assertTrue(choose_resume(tracks).finished)

    def test_file_at_end_advances_to_next(self):
        tracks = [
            track(100_000, offset=99_000, key="1"),
            track(100_000, key="2"),
        ]
        resume = choose_resume(tracks)
        self.assertEqual((resume.index, resume.offset_ms), (1, 0))


class SeekTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [track(60_000, key="1"), track(60_000, key="2"), track(30_000, key="3")]

    def test_skip_forward_inside_file(self):
        self.assertEqual(skip_position(self.tracks, 0, 10_000, 30_000), (0, 40_000))

    def test_skip_forward_crosses_boundary(self):
        self.assertEqual(skip_position(self.tracks, 0, 50_000, 30_000), (1, 20_000))

    def test_skip_back_crosses_boundary(self):
        self.assertEqual(skip_position(self.tracks, 1, 10_000, -30_000), (0, 40_000))

    def test_skip_clamps_to_start(self):
        self.assertEqual(skip_position(self.tracks, 0, 5_000, -30_000), (0, 0))

    def test_locate_exact_boundary_starts_next_file(self):
        self.assertEqual(locate(self.tracks, 60_000), (1, 0))

    def test_book_position_sums_earlier_files(self):
        self.assertEqual(book_position(self.tracks, 1, 15_000), (75_000, 150_000))

    def test_previous_restarts_when_underway(self):
        self.assertEqual(previous_target(2, 10_000), (2, 0))

    def test_previous_steps_back_near_the_start(self):
        self.assertEqual(previous_target(2, 1_000), (1, 0))

    def test_previous_on_first_file_restarts(self):
        self.assertEqual(previous_target(0, 500), (0, 0))


class ReportMathTests(unittest.TestCase):
    def test_timeline_duration_is_inflated_until_the_file_finishes(self):
        self.assertGreater(timeline_duration_ms(3_600_000, 60_000, finishing=False), 3_600_000)
        self.assertEqual(timeline_duration_ms(3_600_000, 3_600_000, finishing=True), 3_600_000)

    def test_zero_position_is_anchored_while_listening(self):
        self.assertEqual(reported_position_ms(0, "playing"), 1)
        self.assertEqual(reported_position_ms(0, "paused"), 1)
        self.assertEqual(reported_position_ms(0, "stopped"), 0)
        self.assertEqual(reported_position_ms(12_000, "playing"), 12_000)

    def test_clock_position_respects_speed_and_pause(self):
        # 2 seconds of wall time at 1.5x from a 10s base.
        self.assertEqual(
            compute_position_ms(10_000, started_at=100.0, now=102.0, paused_total=0, pause_started=None, speed=1.5, duration_ms=60_000),
            13_000,
        )
        # Paused at t=101, checked at t=105: only 1s of the 1.5x rate counts.
        self.assertEqual(
            compute_position_ms(10_000, started_at=100.0, now=105.0, paused_total=0, pause_started=101.0, speed=1.5, duration_ms=60_000),
            11_500,
        )

    def test_position_clamps_to_duration(self):
        self.assertEqual(
            compute_position_ms(0, started_at=0, now=100, paused_total=0, pause_started=None, speed=1, duration_ms=5_000),
            5_000,
        )

    def test_atempo_chain(self):
        self.assertEqual(atempo_filters(1.0), [])
        self.assertEqual(atempo_filters(1.15), ["atempo=1.1500"])
        self.assertEqual(atempo_filters(1.2), ["atempo=1.2000"])
        self.assertEqual(atempo_filters(1.5), ["atempo=1.5000"])
        self.assertEqual(atempo_filters(2.5), ["atempo=2.0", "atempo=1.2500"])
        self.assertEqual(atempo_filters(2.55), ["atempo=2.0", "atempo=1.2750"])
        self.assertEqual(atempo_filters(3.0), ["atempo=2.0", "atempo=1.5000"])
        self.assertEqual(atempo_filters(0.5), ["atempo=0.5000"])
        self.assertEqual(atempo_filters(0.75), ["atempo=0.7500"])

    def test_speed_steps_include_one_point_two(self):
        self.assertEqual(snap_speed(1.2), 1.2)
        self.assertEqual(snap_speed(1.17), 1.15)
        self.assertEqual(snap_speed(1.18), 1.2)
        self.assertEqual(snap_speed(0.49), 0.5)
        self.assertEqual(snap_speed(3.2), 3.0)
        self.assertEqual(snap_speed(2.55), 2.55)

    def test_play_pause_binding_migrates_names_to_actions(self):
        self.assertEqual(play_pause_actions({"play_pause_mode": "back", "play_pause_back": "R4"}), [45])
        self.assertEqual(
            play_pause_actions({"play_pause_buttons": ["R4"]}),
            [45],
        )
        self.assertEqual(
            play_pause_actions({"play_pause_mode": "combo", "play_pause_combo": ["Select", "Y"]}),
            [35, 3],
        )
        self.assertEqual(play_pause_actions({"play_pause_mode": "off"}), [])
        self.assertEqual(play_pause_actions({"play_pause_actions": [45]}), [45])

    def test_book_sort_recently_played_falls_back(self):
        books = [
            {"title": "Zebra", "author": "Ann", "last_viewed_at": 0, "added_at": 50},
            {"title": "Alpha", "author": "Bea", "last_viewed_at": 10, "added_at": 1},
            {"title": "Middle", "author": "Ann", "last_viewed_at": 10, "added_at": 99},
            {"title": "New", "author": "Cara", "last_viewed_at": 0, "added_at": 80},
        ]
        played = [book["title"] for book in sort_books(books, "recently_played")]
        self.assertEqual(played, ["Middle", "Alpha", "New", "Zebra"])
        self.assertEqual(
            [book["title"] for book in sort_books(books, "title")],
            ["Alpha", "Middle", "New", "Zebra"],
        )
        self.assertEqual(
            [book["title"] for book in sort_books(books, "author")],
            ["Middle", "Zebra", "Alpha", "New"],
        )
        self.assertEqual(
            [book["title"] for book in sort_books(books, "recently_added")],
            ["Middle", "New", "Zebra", "Alpha"],
        )

    def test_immediate_exit_on_a_long_file_is_failure(self):
        self.assertEqual(playback_outcome(0, elapsed_wall_s=0.2, remaining_ms=3_600_000, speed=1), "failed")

    def test_clean_exit_after_the_file_is_the_end(self):
        self.assertEqual(playback_outcome(0, elapsed_wall_s=30, remaining_ms=30_000, speed=1), "ended")

    def test_nonzero_exit_early_is_failure(self):
        self.assertEqual(playback_outcome(1, elapsed_wall_s=1.2, remaining_ms=600_000, speed=1), "failed")


if __name__ == "__main__":
    unittest.main()
