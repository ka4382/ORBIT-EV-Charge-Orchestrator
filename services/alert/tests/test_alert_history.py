from orbit_alert.domain.history import SocRing, VinHistory


def test_soc_ring_window_and_rotation():
    r = SocRing(buckets=24, bucket_s=300)
    t0 = 1_000_000 // 300 * 300
    assert r.update(t0, 80.0) is True
    assert r.update(t0 + 10, 85.0) is False  # same bucket, keeps max
    assert r.update(t0 + 5, 70.0) is False
    assert r.max_in_window(t0 + 100) == 85.0
    assert r.update(t0 + 3600, 60.0) is True
    assert r.max_in_window(t0 + 3600) == 85.0  # both buckets inside 2 h
    assert r.max_in_window(t0 + 3600, window_s=600) == 60.0
    assert r.max_in_window(t0 + 7200 + 300) == 60.0  # first bucket aged out
    assert r.entries() == [(t0, 85.0), (t0 + 3600, 60.0)]


def test_soc_ring_ignores_event_for_rotated_bucket():
    r = SocRing(buckets=2, bucket_s=10)
    assert r.update(0, 50.0) and r.update(10, 40.0) and r.update(20, 30.0)  # slot 0 rotated to bucket 20
    assert r.update(0, 99.0) is False  # stale bucket 0 must not overwrite bucket 20
    assert r.max_in_window(20, window_s=100) == 40.0
    assert r.max_in_window(-50, window_s=10) is None


def test_vin_history_defaults():
    h = VinHistory(bucket_s=60)
    assert h.charge is None and h.soc.bucket_s == 60
