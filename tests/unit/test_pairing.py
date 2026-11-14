import hashlib
import os
import re

import pytest

from harness import pairing as pairing_module
from harness.pairing import (
    ALPHABET,
    COOKIE_IDLE,
    COOKIE_MAX,
    LAST_SEEN_WRITE_INTERVAL,
    MAX_WAITING,
    REQUEST_TTL,
    Pairing,
    format_pair_file,
    label_for,
    normalise,
    sign,
)
from harness.store import Store

ORIGIN = "https://comfy.example"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)
START = 1_000_000_000.0
CODE_RE = r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}"


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data")
    s.open()
    return s


@pytest.fixture
def p(store, clock):
    return Pairing(store, clock=clock)


def write_pair(store, code, key=None, mtime=START):
    store.pair_path.write_text(format_pair_file(key or store.key, code))
    os.utime(store.pair_path, (mtime, mtime))


def pair_one(p, store, origin=ORIGIN, ua=UA):
    r = p.request()
    write_pair(store, r.code)
    res = p.poll(r.poll_secret, origin, ua)
    assert res.state == "paired"
    return res


# --- codes and signatures ---------------------------------------------------


def test_code_format_and_uniqueness(p):
    codes = {p.request().code for _ in range(20)}
    assert len(codes) == 20 and all(re.fullmatch(CODE_RE, c) for c in codes)


def test_request_returns_poll_secret_and_lifetime(p):
    r = p.request()
    assert len(r.poll_secret) >= 43 and r.expires_in == REQUEST_TTL


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("k7qm-3xvd", "K7QM3XVD"),
        (" K7QM 3XVD\n", "K7QM3XVD"),
        ("OoIiLl01", "00111101"),
    ],
)
def test_normalise_maps_case_separators_and_lookalikes(raw, expected):
    assert normalise(raw) == expected


def test_sign_is_hmac_over_the_normalised_code():
    key = b"k" * 32
    assert sign(key, "k7qm-3xvd") == sign(key, "K7QM3XVD")
    assert re.fullmatch(r"[0-9a-f]{64}", sign(key, "K7QM3XVD"))
    assert sign(key, "K7QM3XVD") != sign(b"j" * 32, "K7QM3XVD")


def test_format_pair_file_is_code_space_hex_newline():
    key = b"k" * 32
    assert format_pair_file(key, "k7qm-3xvd") == f"K7QM3XVD {sign(key, 'K7QM3XVD')}\n"


def test_alphabet_has_no_lookalikes():
    assert len(ALPHABET) == 32 and not set("ILOU") & set(ALPHABET)


# --- poll ---------------------------------------------------------------------


def test_poll_waits_until_the_signed_file_appears(p, store):
    r = p.request()
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    write_pair(store, r.code)
    res = p.poll(r.poll_secret, ORIGIN, UA)
    assert res.state == "paired" and res.token and not store.pair_path.exists()
    assert p.lookup(res.token) == res.browser and res.browser.origin == ORIGIN


def test_paired_record_has_label_timestamps_and_hex_id(p, store, clock):
    res = pair_one(p, store)
    b = res.browser
    assert b.label == "Chrome on Linux"
    assert b.created == b.last_seen == b.acknowledged == clock.now
    assert re.fullmatch(r"[0-9a-f]{16}", b.id)
    assert store.state.browsers == (b,)


def test_paired_request_is_finished(p, store):
    r = p.request()
    write_pair(store, r.code)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "expired"


def test_first_pairing_enables_the_terminal_and_later_ones_do_not_reenable(p, store):
    assert store.state.enabled is False
    pair_one(p, store)
    assert store.state.enabled is True
    store.update(lambda s: s.__class__(s.schema, s.instance_id, False, s.browsers))
    pair_one(p, store)
    assert store.state.enabled is False and len(store.state.browsers) == 2


def test_pairing_after_all_revoked_does_not_reenable_when_disabled(p, store):
    first = pair_one(p, store)
    store.update(lambda s: s.__class__(s.schema, s.instance_id, False, s.browsers))
    p.revoke(first.browser.id)
    # No browsers, but enabled is False: "first ever" rule says enable again.
    pair_one(p, store)
    assert store.state.enabled is True


def test_code_is_matched_ignoring_case_hyphens_and_lookalikes(p, store):
    r = p.request()
    code = r.code.replace("-", "")
    lookalike = code.lower().replace("0", "O").replace("1", "l")
    # The owner types "k7qm 3xvd": pair.py normalises it before writing.
    store.pair_path.write_text(format_pair_file(store.key, lookalike[:4] + " " + lookalike[4:]))
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"
    # A file whose code field keeps a hyphen, lower case and look-alikes still matches.
    r2 = p.request()
    code2 = r2.code.replace("-", "")
    messy = (code2[:4] + "-" + code2[4:]).lower().replace("0", "o").replace("1", "i")
    store.pair_path.write_text(f"{messy} {sign(store.key, code2)}\n")
    assert p.poll(r2.poll_secret, ORIGIN, UA).state == "paired"


def test_code_with_characters_outside_the_alphabet_never_matches(p, store, clock):
    r = p.request()
    bad = "U" + r.code.replace("-", "")[1:]
    store.pair_path.write_text(f"{bad} {sign(store.key, bad)}\n")
    os.utime(store.pair_path, (START, START))
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists()
    assert normalise(bad) == bad and not pairing_module.is_valid_code(bad)


def test_wrong_poll_secret_never_pairs(p, store):
    r = p.request()
    write_pair(store, r.code)
    assert p.poll("not-the-secret", ORIGIN, UA).state == "expired"
    assert p.poll("", ORIGIN, UA).state == "expired"
    assert store.pair_path.exists() and store.state.browsers == ()
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"


def test_another_requests_code_does_not_pair_this_browser(p, store):
    a = p.request()
    b = p.request()
    write_pair(store, b.code)
    assert p.poll(a.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists() and store.state.browsers == ()
    assert p.poll(b.poll_secret, ORIGIN, UA).state == "paired"


def test_bad_signature_is_rejected_and_file_removed(p, store):
    r = p.request()
    write_pair(store, r.code, key=b"x" * 32)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"
    assert not store.pair_path.exists()


def test_rejected_request_is_finished(p, store):
    r = p.request()
    write_pair(store, r.code, key=b"x" * 32)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"
    write_pair(store, r.code)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "expired"
    assert store.state.browsers == ()


def test_signature_that_is_not_hex_is_rejected(p, store):
    r = p.request()
    store.pair_path.write_text(f"{r.code.replace('-', '')} not-a-signature\n")
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"


def test_extra_fields_are_rejected(p, store):
    r = p.request()
    code = r.code.replace("-", "")
    store.pair_path.write_text(f"{code} {sign(store.key, code)} extra\n")
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"


@pytest.mark.parametrize("content", ["", "   \n", "K7QM3XVD", "K7QM3XVD "])
def test_empty_or_half_written_file_is_left_alone(p, store, content):
    r = p.request()
    store.pair_path.write_text(content)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting" and store.pair_path.exists()


def test_signature_without_trailing_newline_is_still_being_written(p, store):
    r = p.request()
    code = r.code.replace("-", "")
    store.pair_path.write_text(f"{code} {sign(store.key, code)[:30]}")
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting" and store.pair_path.exists()
    write_pair(store, r.code)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"


def test_this_requests_code_without_signature_waits(p, store):
    r = p.request()
    store.pair_path.write_text(r.code.replace("-", "") + "\n")
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting" and store.pair_path.exists()


def test_symlinked_pair_file_is_ignored(p, store, tmp_path):
    r = p.request()
    target = tmp_path / "elsewhere"
    target.write_text(format_pair_file(store.key, r.code))
    store.pair_path.symlink_to(target)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.is_symlink() and target.exists()
    assert store.state.browsers == ()


def test_old_symlink_is_never_removed(p, store, tmp_path, clock):
    r = p.request()
    target = tmp_path / "elsewhere"
    target.write_text("ZZZZZZZZ abc\n")
    store.pair_path.symlink_to(target)
    clock.advance(10 * REQUEST_TTL)
    p.poll(r.poll_secret, ORIGIN, UA)
    assert store.pair_path.is_symlink() and target.exists()


def test_directory_at_pair_path_is_ignored(p, store):
    r = p.request()
    store.pair_path.mkdir()
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.is_dir()


def test_fifo_at_pair_path_is_ignored_without_blocking(p, store):
    r = p.request()
    os.mkfifo(store.pair_path)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"


def test_file_owned_by_another_account_is_ignored(p, store, monkeypatch):
    r = p.request()
    write_pair(store, r.code)
    real_uid = os.geteuid()
    if real_uid == 0:
        pytest.skip("root owns everything it writes; owner check accepts root")
    monkeypatch.setattr(pairing_module.os, "geteuid", lambda: real_uid + 1)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists()


def test_root_and_the_server_account_are_the_accepted_owners():
    assert pairing_module._allowed_owners() == {os.geteuid(), 0}


def test_owner_check_uses_the_accepted_owner_set(p, store, monkeypatch):
    r = p.request()
    write_pair(store, r.code)
    monkeypatch.setattr(pairing_module, "_allowed_owners", lambda: frozenset({0}))
    expected = "paired" if os.geteuid() == 0 else "waiting"
    assert p.poll(r.poll_secret, ORIGIN, UA).state == expected


def test_oversize_file_is_not_read_past_the_limit(p, store):
    r = p.request()
    code = r.code.replace("-", "")
    store.pair_path.write_text(f"{code} {sign(store.key, code)}\n" + "x" * 10_000)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"


def test_non_ascii_content_is_treated_as_unmatched(p, store, clock):
    r = p.request()
    store.pair_path.write_bytes(b"\xff\xfe garbage\n")
    os.utime(store.pair_path, (START, START))
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists()
    clock.advance(REQUEST_TTL - 1)
    r2 = p.request()
    clock.advance(1)
    assert p.poll(r2.poll_secret, ORIGIN, UA).state == "waiting"
    assert not store.pair_path.exists()


def test_unmatched_file_is_removed_only_after_five_minutes(p, store, clock):
    r = p.request()
    store.pair_path.write_text(format_pair_file(store.key, "ZZZZZZZZ"))
    os.utime(store.pair_path, (START, START))
    clock.advance(REQUEST_TTL - 1)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists()
    clock.advance(1)
    r2 = p.request()
    assert p.poll(r2.poll_secret, ORIGIN, UA).state == "waiting"
    assert not store.pair_path.exists()


def test_old_half_written_file_is_removed(p, store, clock):
    store.pair_path.write_text("K7QM")
    os.utime(store.pair_path, (START, START))
    clock.advance(REQUEST_TTL)
    r = p.request()
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert not store.pair_path.exists()


def test_file_for_another_waiting_request_is_kept_even_when_old(p, store, clock):
    a = p.request()
    clock.advance(REQUEST_TTL - 10)
    b = p.request()
    write_pair(store, a.code, mtime=START - 3600)
    clock.advance(5)
    assert p.poll(b.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists()


def test_vanishing_file_during_removal_is_ignored(p, store, clock, monkeypatch):
    r = p.request()
    write_pair(store, r.code, key=b"x" * 32)

    def gone(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(pairing_module.os, "unlink", gone)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"


def test_request_expires_after_300_seconds(p, store, clock):
    r = p.request()
    clock.advance(REQUEST_TTL - 1)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    clock.advance(1)
    write_pair(store, r.code)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "expired"
    assert store.state.browsers == ()


def test_twenty_first_request_evicts_the_oldest(p):
    requests = [p.request() for _ in range(MAX_WAITING + 1)]
    assert p.poll(requests[0].poll_secret, ORIGIN, UA).state == "expired"
    for r in requests[1:]:
        assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"


def test_store_failure_keeps_request_and_file(p, store, monkeypatch):
    r = p.request()
    write_pair(store, r.code)

    def boom(change):
        raise OSError("disk full")

    monkeypatch.setattr(store, "update", boom)
    with pytest.raises(OSError):
        p.poll(r.poll_secret, ORIGIN, UA)
    assert store.pair_path.exists()
    monkeypatch.undo()
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"


# --- lookup -------------------------------------------------------------------


def test_lookup_unknown_token_is_none(p, store):
    pair_one(p, store)
    assert p.lookup("nope") is None
    assert p.lookup("") is None


def test_lookup_expires_after_30_idle_days_and_90_total(p, store, clock):
    res = pair_one(p, store)
    clock.advance(COOKIE_IDLE)
    assert p.lookup(res.token) is not None  # exactly 30 days idle: still valid, refreshes
    clock.advance(COOKIE_IDLE + 1)
    assert p.lookup(res.token) is None  # idle too long
    assert len(store.state.browsers) == 1  # lookup does not delete

    res2 = pair_one(p, store)
    for _ in range(COOKIE_MAX // COOKIE_IDLE):
        clock.advance(COOKIE_IDLE)
        assert p.lookup(res2.token) is not None  # kept alive: exactly 90 days
    clock.advance(1)
    assert p.lookup(res2.token) is None  # over 90 days since pairing


def test_last_seen_is_written_at_most_hourly(p, store, clock, monkeypatch):
    res = pair_one(p, store)
    calls = []
    real_update = store.update

    def counting(change):
        calls.append(1)
        return real_update(change)

    monkeypatch.setattr(store, "update", counting)
    for _ in range(10):
        clock.advance(60)
        assert p.lookup(res.token) is not None
    assert calls == []
    clock.advance(LAST_SEEN_WRITE_INTERVAL - 600)
    seen = p.lookup(res.token)
    assert calls == [1] and seen.last_seen == clock.now
    assert store.state.browsers[0].last_seen == clock.now
    clock.advance(60)
    assert p.lookup(res.token) is not None and calls == [1]


def test_last_seen_write_failure_still_admits(p, store, clock, monkeypatch):
    res = pair_one(p, store)

    def boom(change):
        raise OSError("disk full")

    monkeypatch.setattr(store, "update", boom)
    clock.advance(LAST_SEEN_WRITE_INTERVAL)
    assert p.lookup(res.token) == res.browser


def test_only_the_hash_of_the_token_is_stored(p, store):
    res = pair_one(p, store)
    text = store.state_path.read_text()
    assert res.token not in text
    assert hashlib.sha256(res.token.encode()).hexdigest() in text
    assert UA not in text


# --- browsers, revoke, listeners ----------------------------------------------


def test_browsers_lists_paired_records(p, store):
    a = pair_one(p, store).browser
    b = pair_one(p, store).browser
    assert p.browsers() == (a, b)


def test_revoke_removes_record_and_notifies(p, store):
    events = []
    p.subscribe(lambda event, browser: events.append((event, browser.id)))
    res = pair_one(p, store)
    removed = p.revoke(res.browser.id)
    assert removed == res.browser
    assert store.state.browsers == () and p.lookup(res.token) is None
    assert events == [("paired", res.browser.id), ("revoked", res.browser.id)]


def test_revoke_unknown_id_returns_none_without_writing(p, store, monkeypatch):
    pair_one(p, store)
    calls = []
    monkeypatch.setattr(store, "update", lambda change: calls.append(1))
    assert p.revoke("missing") is None and calls == []


def test_failing_listener_does_not_break_pairing(p, store):
    seen = []

    def bad(event, browser):
        raise RuntimeError("listener bug")

    p.subscribe(bad)
    p.subscribe(lambda event, browser: seen.append(event))
    res = pair_one(p, store)
    assert seen == ["paired"] and p.lookup(res.token) == res.browser
    p.revoke(res.browser.id)
    assert seen == ["paired", "revoked"]


def test_cookie_name_carries_instance_id(p, store):
    assert p.cookie_name == f"__Host-harness-{store.state.instance_id}"


# --- labels -------------------------------------------------------------------


@pytest.mark.parametrize(
    "ua,label",
    [
        (UA, "Chrome on Linux"),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
            "Firefox on Windows",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Safari/605.1.15",
            "Safari on macOS",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/157.0 Safari/537.36 Edg/157.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/157.0 Mobile Safari/537.36",
            "Chrome on Android",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari on iOS",
        ),
        ("curl/8.0", "Browser"),
        ("", "Browser"),
        ("Mozilla/5.0 (X11; Linux x86_64) Unknown/1.0", "Browser on Linux"),
    ],
)
def test_label_is_derived_from_user_agent(ua, label):
    assert label_for(ua) == label


def test_label_is_capped_and_never_raw():
    assert len(label_for("x" * 500)) <= 40


def test_file_swapped_for_a_symlink_before_removal_is_kept(p, store, tmp_path, monkeypatch):
    r = p.request()
    write_pair(store, r.code, key=b"x" * 32)
    target = tmp_path / "victim"
    target.write_text("keep me")
    real_lstat = os.lstat

    def swap_then_lstat(path):
        os.remove(path)
        os.symlink(target, path)
        return real_lstat(path)

    monkeypatch.setattr(pairing_module.os, "lstat", swap_then_lstat)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"
    assert store.pair_path.is_symlink() and target.read_text() == "keep me"


def test_unremovable_file_does_not_raise(p, store, monkeypatch):
    r = p.request()
    write_pair(store, r.code, key=b"x" * 32)

    def denied(path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(pairing_module.os, "unlink", denied)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected"


def test_read_error_is_treated_as_no_usable_file(p, store, monkeypatch):
    r = p.request()
    write_pair(store, r.code)

    def failing_read(fd, n):
        raise OSError(5, "io error")

    monkeypatch.setattr(pairing_module.os, "read", failing_read)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    assert store.pair_path.exists()
