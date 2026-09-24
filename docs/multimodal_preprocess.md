# Multimodal preprocess

User dumps (CSV, JSON, logs, text, images, PDFs, STL/CAD, video) are ingested
into gallery artifacts, then reduced to a bounded `EvidenceExtract` before
heuristic or LLM diagnosis. Raw bytes, full tables, full transcripts, pixel
buffers, meshes, and video frames do not enter the model context.

```
upload → classify → modality extractor → EvidenceExtract
                                              ↓
                         diagnose / chat prompts (hard-capped)
                                              ↓
                         session.view.extracts  +  chat omission note
raw file stays on disk for the gallery (/api/files/{id})
```

## EvidenceExtract

Defined on `ExperimentArtifact.evidence` (`epidebug/schema.py`) and built in
`epidebug/engine/preprocess.py`.

| Field | Role | Cap |
| --- | --- | --- |
| `modality` | `text`, `log`, `csv`, `json`, `table`, `image`, `pdf`, `stl`, `cad`, `video`, `notebook`, `gcode`, `other` | label |
| `filename` | Original name | |
| `summary` | One short description | 320 chars |
| `stats` | Counts, ranges, geometry, container metadata | small scalars and at most 8 numeric columns |
| `anomalies` | Spike, trend, fault, alarm cues | 6 × 160 chars |
| `excerpts` | Token-budgeted citations, not the source | 3 × 160 chars, 400 chars total |
| `omitted` | What the model did **not** see | up to 6 short lines |
| `citations` | `{label, locator}` pointers (header row, fault line, container header) | 3 |
| `size_bytes` | Original object size | |
| `truncated` | Parse window, mesh scan, or prompt budget was hit | |
| `char_budget` | Per-file prompt budget | 1100 chars |

`artifact_blob` / `prompt_from_artifact` render this record for the LLM.
They do not append `extracted_text`. A legacy artifact with no `evidence`
still withholds `extracted_text` (`raw text withheld`). All artifacts in one
dossier stop at 6000 characters (`MAX_DOSSIER_ARTIFACT_CHARS`); the diagnose
prompt then slices the whole dossier to 8000 characters.

`extracted_text` on the artifact is only the gallery preview (excerpts or the
short summary), not the source file.

## Modality matrix

| Upload | Modality | Model sees | Omitted |
| --- | --- | --- | --- |
| `.txt`, `.md`, notes | `text` | Summary, heading, a few flagged lines | Rest of the text |
| `.log` and console dumps | `log` | Line count, severity, alarm codes, short fault lines | Full transcript |
| `.csv`, `.tsv` | `csv` | Schema, row count, min/max/mean, spike and trend cues, header citation | Full table, including head/tail data rows |
| `.json` sensor arrays | `json` | Keys, record count, numeric ranges | Full JSON payload |
| `.xlsx`, parquet, HDF5, TDMS, NPZ | `table` | Filename and byte size | Binary table (export CSV/JSON for stats) |
| Images (PNG, JPEG, …) | `image` | Pixel size, mode, format, EXIF labels, filename cues | Pixel buffer; no base64 |
| SVG | `image` | Title, viewBox, a few text labels | Raw markup and embedded images |
| PDF | `pdf` | Page count and a short opening excerpt | Remaining pages and bytes |
| STL | `stl` | Triangle count, bounding box, volume when the scan covers the whole file, degenerate-triangle count | Raw mesh. Scan cap 8000 triangles |
| STEP, IGES, DXF, other CAD | `cad` | Product names, schema, entity counts from the header text | Raw CAD body. No geometry kernel |
| G-code / NC | `gcode` | Tools, feed range, spindle range | Toolpath body |
| Notebooks | `notebook` | Cell counts and first heading | Sources and outputs |
| MP4, MOV, WebM/MKV, AVI | `video` | Container brand, byte size, duration when the header is inside the scan window | Every frame and audio sample. No decoder |

Video is `ArtifactKind.VIDEO`. It counts toward the photos coverage slot
because it is visual evidence. The gallery groups it under Video. Setup and
result filename cues still map to the existing photo roles.

## Where it is wired

- `ingest_bytes` always attaches `evidence` and stores the raw upload beside
  the session for `/api/files/{id}`.
- `EpistemicDebuggingEngine` diagnose (heuristic and LLM) and the chat
  dossier call `prompt_from_artifacts`, not the raw extract.
- `session.view.extracts` lists the extracts. Each gallery card includes
  `evidence` plus an on-screen “Kept out of the model” line.
- The first diagnosis message, a dossier refresh, and “add artifacts”
  tell the researcher what was extracted and what was omitted.

## Size and Vercel

| Limit | Value |
| --- | --- |
| Local upload cap | 25 MB per file (`web.app.MAX_FILE_BYTES`) |
| Vercel request body | about 4.5 MB. Larger dumps fail with `413` before preprocess runs |
| Text parse window | 400 KB (`MAX_READ_BYTES`). The omission list says the tail was not parsed |
| Video header scan | 2 MB. Duration is read from `mvhd` (MP4/MOV) or `avih` (AVI) only when that box sits inside the scan |
| STL triangle scan | 8000 triangles for bbox/volume |
| Function disk | `VERCEL=1` writes sessions and uploads under `/tmp/epidebug-uploads` |

`/tmp` on Vercel does not survive cold starts or other instances. Extracts
are small enough to sit in the session JSON; the raw object is not.

## Video gaps

- No frame sampling, contact sheets, or vision captions.
- No audio transcription.
- WebM/Matroska is recognized (`EBML` magic) but duration is not parsed.
- MP4/MOV duration is missed when `moov` sits past the 2 MB scan (typical of files that were not “faststart”).
- No transcoding. A video the platform rejects for the 4.5 MB body limit never reaches this layer.

## Follow-ups (out of scope)

- Durable object storage so gallery files survive Vercel cold starts.
- A bounded vision caption for images and a handful of video frames, still
  without putting pixels in the prompt.
- A STEP/IGES geometry kernel. Today CAD evidence is header text only.
- CSV/JSON beyond the 400 KB parse window (streamed reservoir stats).
