import io

import pyarrow.parquet as pq
from archiver_fakes import NOW, VIN, ev
from orbit_archiver.domain.rows import (
    COPY_COLUMNS,
    COPY_SQL,
    PARQUET_SCHEMA,
    Partition,
    charging_code,
    copy_escape,
    copy_row,
    parquet_record,
    partition_for,
    pg_array_literal,
    row_key,
    should_persist,
    to_arrow_table,
)


def test_charging_code_mapping():
    assert [charging_code(s) for s in ("NONE", "AC", "DC")] == [0, 1, 2]


def test_pg_array_literal_and_escape():
    assert pg_array_literal([]) == "{}"
    assert pg_array_literal(["P0301", "P0A80"]) == '{"P0301","P0A80"}'
    assert pg_array_literal(['a"b', "c\\d"]) == '{"a\\"b","c\\\\d"}'
    assert copy_escape("a\tb\nc\\d\r") == "a\\tb\\nc\\\\d\\r"


def test_copy_row_layout_and_nulls():
    row = copy_row(ev(seq=7, charging_state="DC", charging_kw=48.5))
    assert row.endswith("\n")
    cols = row[:-1].split("\t")
    assert len(cols) == len(COPY_COLUMNS) == 15
    assert (
        cols[0] == "2026-09-25T10:15:02.120000+00:00"
        and cols[1] == VIN
        and cols[2] == "7"
        and cols[3] == "12"
    )
    assert cols[11] == "2" and cols[12] == "48.5" and cols[13] == '{"P0301"}' and cols[14] == "HARSH_BRAKE"
    row2 = copy_row(ev(soc_pct=None, soh_pct=None, ambient_c=None, evt=None, dtc=[]))
    cols2 = row2[:-1].split("\t")
    assert cols2[7] == cols2[8] == cols2[10] == cols2[14] == "\\N" and cols2[13] == "{}"
    assert "COPY telemetry (ts, vin, seq" in COPY_SQL


def test_copy_row_escapes_free_text():
    row = copy_row(ev(evt="TAB\tHERE", dtc=['X"1']))
    cols = row[:-1].split("\t")
    assert len(cols) == 15 and cols[14] == "TAB\\tHERE" and cols[13] == '{"X\\\\"1"}'


def test_row_key_and_sampling():
    e = ev(seq=30)
    assert row_key(e) == (VIN, 30, NOW)
    assert (
        should_persist(30, 1)
        and should_persist(30, 10)
        and not should_persist(31, 10)
        and should_persist(5, 0)
    )


def test_partition_and_object_key():
    p = partition_for(ev())
    assert p == Partition("2026-09-25", "10", 12)
    key = p.object_key("abc")
    assert key == "dt=2026-09-25/hour=10/tenant_id=12/part-abc.parquet"
    assert (
        p.object_key().startswith("dt=2026-09-25/hour=10/tenant_id=12/part-")
        and p.object_key() != p.object_key()
    )


def test_arrow_table_matches_schema_and_roundtrips():
    events = [ev(seq=1), ev(seq=2, soc_pct=None, evt=None, dtc=[], charging_state="AC", charging_kw=7.0)]
    table = to_arrow_table([parquet_record(e) for e in events])
    assert table.schema.equals(PARQUET_SCHEMA) and table.num_rows == 2
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    back = pq.read_table(io.BytesIO(sink.getvalue()))
    d = back.to_pylist()
    assert (
        d[0]["vin"] == VIN
        and d[0]["dtc"] == ["P0301"]
        and d[1]["soc_pct"] is None
        and d[1]["charging_state"] == "AC"
    )
    assert d[0]["ts"].isoformat() == "2026-09-25T10:15:02.120000+00:00"
    assert to_arrow_table([]).num_rows == 0
