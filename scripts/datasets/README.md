# Public datasets for OCR and diagram tests

`fetch.py` downloads the public handwritten material listed in `docs/requirements.md`
("Public handwritten material for development") into `var/datasets/` (git-ignored).
It never crawls, never downloads without `--accept-terms`, and writes a provenance record
for every file. Tarn may use these sets internally for testing; none may be committed,
published or redistributed. Where the licence is not stated on the source page, the script
says so: read the page before accepting.

```bash
python scripts/datasets/fetch.py list
python scripts/datasets/fetch.py info fc-offline
python scripts/datasets/fetch.py fetch fc-offline --accept-terms --url <direct link from the page>
```

Pages of a downloaded PDF or image go into a ground-truth set with
`tarn truth prefill FILE --set var/groundtruth/<set> --label <name> --capture <type> --pages …`
(`docs`: CLAUDE.md, "OCR benchmark (P11)").
