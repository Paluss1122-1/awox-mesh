# Contributing

Thanks for considering a contribution.

## Scope

This project exists for one reason: controlling AwoX/EGLO BLE mesh lamps
locally, without the vendor cloud. Additions that serve that goal are welcome —
especially evidence from **other models and firmware versions**, because the
protocol here was derived from a single lamp.

## Ground rules

1. **Never commit credentials.** No mesh names, mesh passwords, MAC addresses or
   `config.env`. `config.env` is git-ignored; do not defeat that. Tests use
   placeholders such as `meshname` / `meshpass`.
2. **Never commit vendor material.** No APKs, DEX files, native libraries,
   decompiled source or HCI captures. These belong in your own local scratch
   space, not in the repository. Describe findings in prose instead.
3. **The protocol layer stays pure.** `awox_mesh/protocol.py` must not import
   anything Bluetooth-related. That is what makes it testable without hardware.
4. **Report state only when you know it.** If a value is a cache, mark it as
   such. Guessing is worse than answering `503`.

## Development

```bash
uv run --with pycryptodome --with pytest python -m pytest tests -q
uv run --with ruff ruff check .
```

The tests need no hardware. If you change the protocol layer, extend the tests —
they should pin the layout field by field, not just smoke-test that something was
produced.

## Reporting a working lamp

The most valuable report is a model plus firmware that works. Please include:

- model name and firmware version (from the vendor app or the advertisement),
- the BLE name,
- whether the pairing handshake matched the documented scheme,
- whether commands take effect, and whether `/state` returns anything,
- the mesh address, if you are comfortable sharing it.

If a lamp does **not** work, that is equally useful — especially the pairing
response byte and whether the lamp advertises at all.

## Reverse engineering and scope

This software was written for interoperability: controlling your own hardware
locally. Keep contributions on that track. Do not add functionality whose purpose
is impersonating the vendor service or bypassing an access control the vendor
themselves put in place.