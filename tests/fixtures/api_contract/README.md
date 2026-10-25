# Release Manager contract fixture (public test data only)

Taken unchanged from the contract bundle `AKENO_3_0_API_CONTRACT_BUNDLE.zip` (2026-10-25), after review, with
the SHA-256 values of its `SHA256SUMS.txt` (checked by `tests/test_api_contract.py`). Nothing else from the
bundle is here: no backend source, no logs, no private key.

* `vectors/*/portable.zip`, `vectors/*/canonical-records.utf8.json`, `vector-index.json`: the five official
  `portable.content_sha256` vectors (text and UTF-8 names, a valid portable package, the same package in another
  entry order, a top folder kept in the names, Unicode code point order against UTF-16 order). The `AKENO.exe` in
  them is a 1024-byte PE header stub (42 non-zero bytes), not a program.
* `example-test-envelope.json`: the official example release (3.0.1), signed with a **test-only** key.
* `example-test-public-key.json`: that test key (public half; key id `4f0b6765af0b6fa0`).

The test key is never one of the build's release keys (`akeno/data/release-keys.json` holds only the production
key `d6139235b220a4aa`), and `tools/check_build_config.py` refuses a build that trusts it or carries this fixture.
Never publish the example as a release.
