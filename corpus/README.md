# Corpus

The project corpus is public. Download these into `corpus/data/` (text-layer PDFs preferred):

| File to save as | Document | Where |
|---|---|---|
| `code_on_social_security_2020.pdf` | The Code on Social Security, 2020 | India Code portal / Ministry of Labour & Employment |
| `posh_act_2013.pdf` | Sexual Harassment of Women at Workplace (PPR) Act, 2013 | India Code portal |
| `payment_of_gratuity_act_1972.pdf` | Payment of Gratuity Act, 1972 (repealed, kept deliberately) | India Code portal |
| `commencement_notification.pdf` | Notification commencing the Labour Codes (21 Nov 2025) | Ministry of Labour & Employment |

Exact URLs are not hard-coded here because the portals reorganise; search the title on indiacode.nic.in or labour.gov.in.
Scanned PDFs need OCR first (`pip install pytesseract pdf2image`; the parser falls back to OCR automatically if installed).

`corpus/samples/` holds **synthetic paraphrased fixtures** so the system starts from a clean clone. They are not the official text.
Once the real PDFs are in `corpus/data/`, run `docker compose run --rm indexer` and set `REQUIRE_OFFICIAL_CORPUS=true`.
`sources.yaml` declares status (in force / repealed), validity dates and supersession for each document; check those against the notification.

After swapping in official text, **re-verify** `eval/scenarios.jsonl` gold clause ids (they are `doc_id:section:...` and must match real section numbers).
