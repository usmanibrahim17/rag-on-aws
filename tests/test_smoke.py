def test_packages_import():
    # Placeholder so CI's pytest step has something to run (an empty suite exits 5).
    # Replaced by real chunker tests in M2.
    import app.common
    import app.ingest
    import app.query

    assert app.common and app.ingest and app.query
