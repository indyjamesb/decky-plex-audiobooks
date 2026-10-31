import unittest

from plex_auth import (
    CANNOT_REACH,
    PlexError,
    attempt_order,
    local_candidate,
    order_connections,
    prefer_local,
    redact_secrets,
    run_failover,
)


LOCAL = {"uri": "http://10.0.0.91:32400", "local": True, "relay": False, "protocol": "http"}
REMOTE = {"uri": "https://1-2-3-4.aabbccdd.plex.direct:32400", "local": False, "relay": False, "protocol": "https"}
RELAY = {"uri": "https://relay.example:443", "local": False, "relay": True, "protocol": "https"}
SAVED = [RELAY, REMOTE, LOCAL]


class OrderingTests(unittest.TestCase):
    def test_local_then_remote_then_relay(self):
        ordered = [item["uri"] for item in order_connections(SAVED)]
        self.assertEqual(ordered, [LOCAL["uri"], REMOTE["uri"], RELAY["uri"]])

    def test_current_connection_is_tried_first(self):
        self.assertEqual(
            attempt_order(SAVED, RELAY["uri"]),
            [RELAY["uri"], LOCAL["uri"], REMOTE["uri"]],
        )

    def test_switch_back_picks_local_only_when_it_answers(self):
        self.assertEqual(local_candidate(SAVED, RELAY["uri"]), LOCAL["uri"])
        self.assertIsNone(local_candidate(SAVED, LOCAL["uri"]))
        self.assertEqual(prefer_local(SAVED, RELAY["uri"], True), LOCAL["uri"])
        self.assertEqual(prefer_local(SAVED, RELAY["uri"], False), RELAY["uri"])


class FailoverTests(unittest.TestCase):
    def test_falls_through_to_the_next_address(self):
        tried = []

        def operation(uri):
            tried.append(uri)
            if uri == LOCAL["uri"]:
                raise PlexError("timed out", unreachable=True)
            return "ok"

        result, uri, _connections, refetched = run_failover(SAVED, LOCAL["uri"], operation, lambda: [])
        self.assertEqual(result, "ok")
        self.assertEqual(uri, REMOTE["uri"])
        self.assertFalse(refetched)
        self.assertEqual(tried, [LOCAL["uri"], REMOTE["uri"]])

    def test_auth_failure_is_not_a_connection_problem(self):
        tried = []

        def operation(uri):
            tried.append(uri)
            raise PlexError("Plex rejected the token.", status=401)

        with self.assertRaises(PlexError) as caught:
            run_failover(SAVED, LOCAL["uri"], operation, lambda: [_boom()])
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(tried, [LOCAL["uri"]])

    def test_refetch_is_used_when_every_saved_address_fails(self):
        fresh = {"uri": "http://10.1.1.5:32400", "local": True, "relay": False, "protocol": "http"}

        def operation(uri):
            if uri == fresh["uri"]:
                return "back"
            raise PlexError("down", unreachable=True)

        def refetch():
            return [fresh]

        result, uri, connections, refetched = run_failover(SAVED, LOCAL["uri"], operation, refetch)
        self.assertEqual((result, uri, refetched), ("back", fresh["uri"], True))
        self.assertEqual(connections[0]["uri"], fresh["uri"])

    def test_everything_failed_is_a_plain_message(self):
        def operation(_uri):
            raise PlexError("timed out: http://10.0.0.91:32400 X-Plex-Token=secret", unreachable=True)

        with self.assertRaises(PlexError) as caught:
            run_failover(SAVED, LOCAL["uri"], operation, lambda: [])
        self.assertEqual(caught.exception.message, CANNOT_REACH)
        self.assertNotIn("secret", caught.exception.message)

    def test_redact_tokens(self):
        text = redact_secrets("nope X-Plex-Token=secret authToken=other")
        self.assertNotIn("secret", text)
        self.assertNotIn("other", text)
        self.assertIn("[redacted]", text)


def _boom():
    raise AssertionError("refetch should not run")


if __name__ == "__main__":
    unittest.main()
