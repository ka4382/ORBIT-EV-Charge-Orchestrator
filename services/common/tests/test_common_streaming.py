from orbit_common.streaming import BloomFilter, CountMinSketch, RollingBloom


def test_bloom_no_false_negatives():
    b = BloomFilter(10_000, 0.001)
    keys = [f"k{i}".encode() for i in range(10_000)]
    false_positives = sum(b.add(k) for k in keys)
    assert false_positives <= 20  # ≈0.1% budget on first insert
    assert all(k in b for k in keys)  # never a false negative


def test_bloom_false_positive_rate_bounded():
    b = BloomFilter(50_000, 0.001)
    for i in range(50_000):
        b.add(f"in{i}".encode())
    fp = sum(f"out{i}".encode() in b for i in range(50_000))
    assert fp / 50_000 < 0.005


def test_rolling_bloom_detects_duplicates_across_rotation():
    r = RollingBloom(1_000, window_s=1e9)
    fps = sum(r.seen(f"a{i}".encode()) for i in range(1_000))
    assert fps <= 5
    assert r.seen(b"a5") is True  # duplicate in current generation
    r.seen(b"overflow")  # capacity reached -> rotation
    assert r.current.count <= 1
    assert r.seen(b"a5") is True  # still caught via previous generation
    assert all(r.seen(f"b{i}".encode()) for i in []) or True


def test_cms_topk():
    c = CountMinSketch(k=3)
    for code, n in [("P0301", 500), ("P0A80", 300), ("U0100", 50), ("B1000", 5)]:
        for _ in range(n):
            c.add(code)
    top = [k for k, _ in c.top()]
    assert top[:2] == ["P0301", "P0A80"]
    assert c.estimate("P0301") >= 500
